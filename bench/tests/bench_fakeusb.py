"""Fake RP2 mode switching (app <-> BOOTSEL) in a fake sysfs, driven by a JSON state file.

Shared by the rp2flash tests (the fake 1200-baud touch) and the fake picotool script.
Each value is written atomically and in an order that never shows a half-switched device:
BOOTSEL loses its tty before its PID changes; the application gains its tty last.
"""
import json
import os
from pathlib import Path

PIDS = {"app": "000a", "bootsel": "0003"}


def _write(path, value):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(value + "\n")
    os.replace(tmp, path)


def load(state_file):
    return json.loads(Path(state_file).read_text())


def set_mode(state_file, path, mode):
    state = load(state_file)
    dev = state["devices"][path]
    base = Path(state["sysfs"]) / "bus/usb/devices"
    device = base / path
    link = base / (path + ":1.0") / "tty" / dev["tty"]
    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        link.rmdir()
    _write(device / "devnum", str(dev[mode + "_devnum"]))
    _write(device / "idProduct", PIDS[mode])
    if mode == "app":
        link.parent.mkdir(parents=True, exist_ok=True)
        if dev.get("target"):
            link.symlink_to(dev["target"])
        else:
            link.mkdir()
    dev["mode"] = mode
    tmp = Path(str(state_file) + ".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, state_file)


def find(state_file, busnum, devnum):
    """Return (path, device) currently at bus/address, or (None, None)."""
    for path, dev in load(state_file)["devices"].items():
        if busnum == 1 and dev[dev["mode"] + "_devnum"] == devnum:
            return path, dev
    return None, None
