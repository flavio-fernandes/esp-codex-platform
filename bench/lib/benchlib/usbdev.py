"""Resolve physical USB ports without opening devices."""
import re
from pathlib import Path
from . import config

KIND_TABLE = {("2e8a", "0003"): "rp2-bootsel", ("2e8a", "000f"): "rp2-bootsel",
              **{pair: "esp-uart" for pair in [("10c4", "ea60"), ("1a86", "7523"),
                 ("1a86", "55d4"), ("0403", "6001"), ("0403", "6015")]}}


def kind(vid, pid):
    vid, pid = vid.lower(), pid.lower()
    return KIND_TABLE.get((vid, pid), {"2e8a": "rp2", "303a": "esp-usb"}.get(vid, "unknown"))


def _devices(sysfs_root):
    return (Path(sysfs_root) if sysfs_root is not None else config.root("sysfs")) / "bus/usb/devices"


def _read(path):
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def _interfaces(devices, path):
    # Real sysfs exposes interfaces both beneath devices and as bus symlinks.
    return sorted(set(devices.glob(path + ":*")) | set((devices / path).glob(path + ":*")))


def resolve(path, sysfs_root=None):
    """Return device fields, or None for absent/disappearing devices.

    VID/PID are lowercase hex strings; busnum/devnum are ints or None;
    tty/block are /dev paths or None. Missing optional strings are None.
    """
    config.validate_port(path)
    devices = _devices(sysfs_root)
    device = devices / path
    vid, pid = _read(device / "idVendor"), _read(device / "idProduct")
    if vid is None or pid is None:
        return None
    result = {"vid": vid.lower(), "pid": pid.lower(), "kind": kind(vid, pid),
              "serial": _read(device / "serial"), "product": _read(device / "product"),
              "tty": None, "block": None}
    for name in ("busnum", "devnum"):
        value = _read(device / name)
        result[name] = int(value) if value is not None else None
    ttys, blocks = set(), set()
    for interface in _interfaces(devices, path):
        for node in interface.rglob("*"):
            if re.fullmatch(r"tty(?:ACM|USB)[0-9]+", node.name):
                ttys.add("/dev/" + node.name)
            if node.parent.name == "block":
                blocks.add("/dev/" + node.name)
    result["tty"] = min(ttys) if ttys else None
    result["block"] = min(blocks) if blocks else None
    return result


def split_port(path):
    config.validate_port(path)
    if "." in path:
        parent, port = path.rsplit(".", 1)
    else:
        bus, port = path.split("-")
        parent = "usb" + bus
    return parent, int(port)


def is_root_hub(name):
    return re.fullmatch(r"usb[0-9]+", name) is not None


def _storage_owner_counts(devices, interface_name):
    """A class-08 interface counts unless its device is an RP2 boot ROM (BOOTSEL)."""
    owner = devices / interface_name.split(":", 1)[0]
    vid, pid = _read(owner / "idVendor"), _read(owner / "idProduct")
    return vid is None or pid is None or kind(vid, pid) != "rp2-bootsel"


def subtree_has_mass_storage(path, sysfs_root=None):
    """True if a real mass-storage interface sits at path or below it.

    RP2040/RP2350 BOOTSEL (2e8a:0003/000f) exposes a class-08 interface; it is no disk, so it
    is exempt. Every other class-08 interface counts, including one whose device is unreadable.
    """
    config.validate_port(path)
    devices = _devices(sysfs_root)
    # Match boundaries, so 1-1.1 never includes its sibling 1-1.10.
    for device in devices.iterdir():
        name = device.name.split(":", 1)[0]
        if name == path or name.startswith(path + "."):
            if _read(device / "bInterfaceClass") == "08" and _storage_owner_counts(devices, device.name):
                return True
            if any(_read(p) == "08" and _storage_owner_counts(devices, p.parent.name)
                   for p in device.rglob("bInterfaceClass")):
                return True
    return False


def find_bootsel_at(path, sysfs_root=None):
    device = resolve(path, sysfs_root)
    return device if device and device["kind"] == "rp2-bootsel" else None