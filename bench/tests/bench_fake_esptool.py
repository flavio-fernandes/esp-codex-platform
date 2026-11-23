"""Fake esptool (v5 CLI): logs argv and the slot's pause state, plays scripted outcomes.

FAKE_ESP_DIR holds scenario.json ({"write-flash": [{"rc": 2, "reenum": "ttyACM2"}, {}], ...};
the nth call of a command uses the nth entry, the last one repeating) and receives calls.jsonl.
The --port tty must exist in the fake sysfs (BENCH_SYSFS) or the call fails as the real tool
would. Per entry: "rc", "out", "reenum" (rename the tty: the board re-enumerated) and "rom"
(the board is left in ESP32-S2 ROM download mode, 303a:0002).
"""
import json
import os
from pathlib import Path
import sys

from benchlib import config, serialclient

COMMANDS = ("chip-id", "flash-id", "read-flash", "write-flash")
FLASH_ID = ("Connected to ESP32-S3 on {port}:\nChip type:          ESP32-S3 (QFN56) (revision v0.2)\n"
            "Features:           Wi-Fi, BT 5 (LE), Dual Core + LP Core, 240MHz\n"
            "Crystal frequency:  40MHz\nMAC:                aa:bb:cc:dd:ee:ff\n"
            "Stub flasher running.\nFlash Memory Information:\n=========================\n"
            "Manufacturer: 20\nDevice: 4016\nDetected flash size: 4MB\nHard resetting via RTS pin...")
DEFAULTS = {"chip-id": "Chip type:          ESP32-S3 (QFN56) (revision v0.2)\n"
                       "Chip ID: 0x00000000\nHard resetting via RTS pin...",
            "flash-id": FLASH_ID,
            "read-flash": "Reading from 0x00000000 [=====>      ]  40.0% 4096/8192 bytes...\r"
                          "Read 8192 bytes from 0x00000000 in 0.1 seconds to {file}.",
            "write-flash": "Writing at 0x00010000 [===========>  ]  80.0% 1024/1280 bytes...\r"
                           "Wrote 1280 bytes at 0x00010000.\nHash of data verified."}
FLASH_BYTES = 4 * 1024 * 1024


def device_with_tty(name):
    """Return (port path, tty node) of the fake device that has this tty."""
    base = config.root("sysfs") / "bus/usb/devices"
    for node in base.glob("*:1.*/**/tty*"):
        if node.name == name:
            return node.relative_to(base).parts[0].split(":")[0], node
    return None, None


def main(args):
    folder = Path(os.environ["FAKE_ESP_DIR"])
    scenario_file, log = folder / "scenario.json", folder / "calls.jsonl"
    scenario = json.loads(scenario_file.read_text()) if scenario_file.exists() else {}
    command = next(word for word in args if word in COMMANDS)
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    entries = scenario.get(command, [{}])
    spec = {"rc": 0, "out": DEFAULTS[command],
            **entries[min(sum(c["command"] == command for c in calls), len(entries) - 1)]}
    port = args[args.index("--port") + 1]
    path, node = device_with_tty(os.path.basename(port))
    paused = None
    slots = {options["path"]: label for label, options in config.load_slots().items()}
    if path in slots:
        try:
            paused = serialclient.state(slots[path])["paused_by"]
        except serialclient.NotRunning:
            paused = "no-owner"
    with log.open("a") as stream:
        stream.write(json.dumps({"argv": args, "command": command, "path": path,
                                 "paused_by": paused}) + "\n")
    if node is None:
        print(f"A fatal error occurred: Could not open {port}, the port is busy or doesn't exist.")
        return 2
    rest = args[args.index(command) + 1:]
    if command == "read-flash" and spec["rc"] == 0:
        size = FLASH_BYTES if rest[1] == "ALL" else int(rest[1], 0)
        Path(rest[2]).write_bytes(bytes([0xFF]) * size)
    print(spec["out"].format(port=port, file=rest[-1] if rest else ""))
    if spec.get("reenum"):
        node.rename(node.with_name(spec["reenum"]))
    if spec.get("rom"):
        (config.root("sysfs") / "bus/usb/devices" / path / "idProduct").write_text("0002\n")
    return spec["rc"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
