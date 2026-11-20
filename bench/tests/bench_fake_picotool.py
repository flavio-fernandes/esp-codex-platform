"""Fake picotool: logs argv (and the slot's pause state) and plays scripted outcomes.

FAKE_PT_DIR holds scenario.json ({"load": {"rc": 254, "out": "...", "then": null}, ...};
key "reboot-u" for `reboot -f -u`) and receives calls.jsonl. FAKE_USB_STATE is the
bench_fakeusb state file. "then" switches the addressed device to "app" or "bootsel".
"""
import json
import os
from pathlib import Path
import sys

import bench_fakeusb
from benchlib import serialclient

LOAD_OUT = ("Loading into Flash: [==============================]  100%\r"
            "Verifying Flash:    [==============================]  100%\n  OK\n\n"
            "The device was rebooted to start the application.")
DEFAULTS = {"info": {"out": "Program Information\n name: fake-app"},
            "save": {"out": "Saving file: [==============================]  100%\nWrote 4096 bytes"},
            "load": {"out": LOAD_OUT, "then": "app"},
            "reboot": {"out": "The device was rebooted into application mode.", "then": "app"},
            "reboot-u": {"out": "The device was rebooted into BOOTSEL mode.", "then": "bootsel"}}


def main(args):
    folder, state_file = Path(os.environ["FAKE_PT_DIR"]), os.environ["FAKE_USB_STATE"]
    scenario_file = folder / "scenario.json"
    scenario = json.loads(scenario_file.read_text()) if scenario_file.exists() else {}
    key = args[0] + ("-u" if args[0] == "reboot" and "-u" in args else "")
    spec = {"rc": 0, "then": None, **DEFAULTS[key], **scenario.get(key, {})}
    path, dev = bench_fakeusb.find(state_file, int(args[args.index("--bus") + 1]),
                                   int(args[args.index("--address") + 1]))
    paused = None
    if dev and dev.get("slot"):
        try:
            paused = serialclient.state(dev["slot"])["paused_by"]
        except serialclient.NotRunning:
            paused = "no-owner"
    with (folder / "calls.jsonl").open("a") as stream:
        stream.write(json.dumps({"argv": args, "path": path, "paused_by": paused}) + "\n")
    wants_bootsel = args[0] in {"info", "save", "load"} or (key == "reboot")
    if dev is None or (wants_bootsel and dev["mode"] != "bootsel"):
        print("No accessible RP-series devices in BOOTSEL mode were found.")
        return 249
    if args[0] == "save" and spec["rc"] == 0:
        Path(args[2]).write_bytes(b"UF2\nsaved-image")
    print(spec["out"])
    if spec["then"]:
        bench_fakeusb.set_mode(state_file, path, spec["then"])
    return spec["rc"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
