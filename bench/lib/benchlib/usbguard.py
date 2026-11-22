"""bench-usb: the only privileged bench code (root through sudo). Small and strict.

usb-reset SLOTn  disable/re-enable the slot's hub port through sysfs, then report what came back.
power-cycle      uhubctl power cycle of the bench hub; opt-in with BENCH_POWER_CYCLE=1.

Every guard refuses with exit 2 before anything is touched: an unconfigured slot, a root-hub
parent, a mass-storage interface (class 08) anywhere in the affected subtree, a hub location
outside BENCH_HUB_PATHS, and any other argv.
"""
from contextlib import ExitStack
import getpass
import os
import shutil
import subprocess
import sys
import time

from . import config, history, holds, locks, usbdev

OK, REFUSED, BUSY, ABSENT, TOOL_FAILED, HELD = 0, 2, 65, 66, 68, 69
DISABLE_SECONDS = 1
REAPPEAR_WAIT = 10
UHUBCTL_TIMEOUT = 60
USAGE_TEXT = "usage: bench-usb usb-reset SLOTn | power-cycle"
sleep = time.sleep  # tests observe the port state during the pause


class Refused(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def log(message):
    print(f"bench-usb: {message}", file=sys.stderr, flush=True)


def port_control(path):
    """Guards for usb-reset of one port path; return its sysfs `disable` file."""
    parent, port = usbdev.split_port(path)
    if usbdev.is_root_hub(parent):
        raise Refused(REFUSED, f"refused: {path} hangs off root hub {parent}; root-hub ports are never disabled")
    if usbdev.subtree_has_mass_storage(path):
        raise Refused(REFUSED, f"refused: a mass-storage interface (class 08) is in the subtree of {path}")
    control = config.root("sysfs") / "bus/usb/devices" / f"{parent}:1.0" / f"{parent}-port{port}" / "disable"
    if not control.is_file():
        raise Refused(REFUSED, f"refused: no port control file {control}")
    return control


def plan_usb_reset(slot):
    try:
        slots = config.load_slots()
        path = slots[config.validate_slot(slot)]["path"]
    except KeyError:
        raise Refused(REFUSED, f"refused: {slot} is not a configured slot") from None
    except (ValueError, OSError) as exc:
        raise Refused(REFUSED, f"refused: {exc}") from None
    return path, port_control(path)


def plan_power_cycle():
    try:
        env = config.load_env()
    except (ValueError, OSError) as exc:
        raise Refused(REFUSED, f"refused: bench.env: {exc}") from None
    if env.get("BENCH_POWER_CYCLE") != "1":
        raise Refused(REFUSED, "power-cycle is disabled (set BENCH_POWER_CYCLE=1 in bench.env "
                               "only where the hub really switches power)")
    location = env.get("BENCH_HUB_LOCATION", "")
    if not config.PORT_RE.fullmatch(location) or usbdev.is_root_hub(location):
        raise Refused(REFUSED, f"refused: BENCH_HUB_LOCATION {location!r} is not a hub port path")
    if location not in env.get("BENCH_HUB_PATHS", "").split():
        raise Refused(REFUSED, f"refused: hub location {location} is not in BENCH_HUB_PATHS")
    if usbdev.subtree_has_mass_storage(location):
        raise Refused(REFUSED, f"refused: a mass-storage interface (class 08) is in hub {location}'s subtree")
    return location


def describe(device):
    return " ".join(f"{key}={device[key]}" for key in ("vid", "pid", "kind", "tty", "serial"))


def usb_reset(slot, client, record):
    path, control = plan_usb_reset(slot)
    try:
        with locks.slot_lock(slot):
            try:
                holds.check(slot, client)
            except holds.Held as exc:
                raise Refused(HELD, f"{slot} {exc}") from None
            before = usbdev.resolve(path)
            if before:
                record.update(kind=before["kind"], usb_serial=before["serial"])
            log(f"{slot}: disabling {control.parent.name} for {DISABLE_SECONDS}s")
            control.write_text("1")
            try:
                sleep(DISABLE_SECONDS)
            finally:
                control.write_text("0")
            deadline = time.monotonic() + REAPPEAR_WAIT
            while (after := usbdev.resolve(path)) is None and time.monotonic() < deadline:
                sleep(0.1)
    except locks.Busy:
        raise Refused(BUSY, f"{slot} is busy (another operation holds its lock)") from None
    if after is None:
        print(f"{slot} {path}: nothing came back within {REAPPEAR_WAIT}s", flush=True)
        return ABSENT
    print(f"{slot} {path}: {describe(after)}", flush=True)
    return OK


def affected_slots(location):
    """Configured slots on or beneath the hub at `location`."""
    slots = config.load_slots()
    return sorted(label for label, options in slots.items()
                  if options["path"] == location or options["path"].startswith(location + "."))


def power_cycle(record, client):
    location = plan_power_cycle()
    record["note"] = f"hub {location}"
    tool = shutil.which("uhubctl")
    if tool is None:
        raise Refused(TOOL_FAILED, "uhubctl is not installed")
    # Every board under the hub loses power: honour every slot's hold and wait for no one, and
    # keep all their locks until the cycle is over (PR review, 2026-11-22).
    with ExitStack() as held:
        for slot in affected_slots(location):
            try:
                held.enter_context(locks.slot_lock(slot))
            except locks.Busy:
                raise Refused(BUSY, f"{slot} is busy (another operation holds its lock)") from None
            try:
                holds.check(slot, client)
            except holds.Held as exc:
                raise Refused(HELD, f"{slot} {exc}") from None
        return _run_uhubctl(tool, location)


def _run_uhubctl(tool, location):
    try:
        proc = subprocess.run([tool, "-l", location, "-a", "cycle", "-d", "3"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=UHUBCTL_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise Refused(TOOL_FAILED, f"uhubctl timed out after {UHUBCTL_TIMEOUT}s") from None
    sys.stderr.write(proc.stdout.decode("utf-8", errors="replace"))
    if proc.returncode != 0:
        raise Refused(TOOL_FAILED, f"uhubctl failed (rc={proc.returncode})")
    print(f"power-cycled hub {location}", flush=True)
    return OK


def main(argv=None):
    if os.geteuid() == 0:
        # Running as root (via sudo): paths come only from the compiled-in defaults.
        for name in ("BENCH_ETC", "BENCH_RUN", "BENCH_STATE", "BENCH_SYSFS"):
            os.environ.pop(name, None)
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["power-cycle"]:
        verb, slot = "power-cycle", None
    elif len(argv) == 2 and argv[0] == "usb-reset":
        verb, slot = "usb-reset", argv[1]
    else:
        log(USAGE_TEXT)
        return REFUSED
    client = os.environ.get("BENCH_CLIENT") or os.environ.get("SUDO_USER") or getpass.getuser()
    record = {"kind": None, "usb_serial": None, "note": None}
    started = time.monotonic()
    try:
        rc = usb_reset(slot, client, record) if slot else power_cycle(record, client)
    except Refused as exc:
        log(str(exc))
        if exc.code == REFUSED:
            return REFUSED  # a guard refused: nothing was touched, nothing to record
        rc = exc.code
        record["note"] = "; ".join(filter(None, [record["note"], str(exc)]))
    history.append(client=client, verb=verb, slot=slot, rc=rc,
                   seconds=round(time.monotonic() - started, 3), **record)
    return rc
