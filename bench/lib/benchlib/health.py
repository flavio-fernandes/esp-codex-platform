"""Bench health snapshot; probes are individually replaceable for tests.

External commands have a three-second timeout; serial state uses the client's
five-second timeout. Local proc/sysfs reads and nonblocking lock probes do not
launch workers. collect() reads, but does not advance, the kernel baseline;
main() publishes both files after successful collection.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from . import config, history, holds, locks, serialclient, usbdev
from .serialowner import monitor_allowed

PROBE_TIMEOUT = 3
KERNEL_PATTERNS = {"error -71": r"error -71\b", "error -110": r"error -110\b",
                   "USB disconnect": r"USB disconnect", "over-current": r"over-current"}


def command(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT,
                          check=True).stdout


def probe_throttled():
    text = command(["vcgencmd", "get_throttled"]).strip()
    return hex(int(text.removeprefix("throttled="), 16))


def probe_temp():
    return float((config.root("sysfs") / "class/thermal/thermal_zone0/temp").read_text()) / 1000


def probe_disk():
    usage = shutil.disk_usage("/")
    return round(100 * usage.free / usage.total, 2)


def probe_uptime():
    return float(Path("/proc/uptime").read_text().split()[0])


def probe_hub(env):
    paths = env.get("BENCH_HUB_PATHS", "").split()
    for path in paths:
        config.validate_port(path)
    return bool(paths) and all((config.root("sysfs") / "bus/usb/devices" / p).exists() for p in paths)


def probe_camera(env):
    path = env.get("BENCH_CAMERA_DEVICE")
    return bool(path) and Path(path).exists()


def probe_service(slot):
    result = subprocess.run(["systemctl", "is-active", "bench-serial@" + slot],
                            capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    if result.returncode not in (0, 3):
        raise OSError("systemctl failed: " + result.stderr.strip())
    return result.stdout.strip() or "unknown"


def probe_kernel():
    try:
        text = command(["journalctl", "-k", "-b", "-o", "cat"])
    except (OSError, subprocess.SubprocessError):
        text = command(["dmesg"])
    return {key: len(re.findall(pattern, text)) for key, pattern in KERNEL_PATTERNS.items()}


def probe_locks(slots):
    busy = [slot for slot in slots if locks.lock_busy(slot)]
    try:
        with locks.camera_lock():
            pass
    except locks.Busy:
        busy.append("camera")
    return busy


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o664)
            json.dump(value, stream, allow_nan=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def collect(*, probes=None, now=None):
    """Return a snapshot. Override named probes with callables (same arguments)."""
    overrides = probes or {}
    notes, problems = [], []
    def probe(name, function, *args, default=None):
        try:
            return overrides.get(name, function)(*args)
        except (OSError, ValueError, subprocess.SubprocessError, serialclient.ProtocolError) as exc:
            notes.append(f"{name}: {exc}")
            return default

    now = time.time() if now is None else now
    slots, env = config.load_slots(), config.load_env()
    result = {"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
              "uptime_s": probe("uptime_s", probe_uptime),
              "throttled": probe("throttled", probe_throttled),
              "temp_c": probe("temp_c", probe_temp),
              "disk_free_pct": probe("disk_free_pct", probe_disk),
              "hub_present": probe("hub_present", probe_hub, env, default=False),
              "camera_present": probe("camera_present", probe_camera, env, default=False),
              "slots": {}, "kernel": probe("kernel", probe_kernel),
              "holds": {}, "locks_busy": probe_locks(slots)}
    for slot, options in slots.items():
        device = usbdev.resolve(options["path"])
        state = probe("state", serialclient.state, slot, default={})
        info = {"present": device is not None, "kind": device["kind"] if device else None,
                "tty": device["tty"] if device else None,
                "serial_service": probe("serial_service", probe_service, slot),
                "open": state.get("open", False), "paused": state.get("paused", False)}
        result["slots"][slot] = info
        # A paused slot (a flash or backup in progress) is closed on purpose: not a problem.
        if (device and monitor_allowed(device["kind"], options["monitor"]) and not info["paused"]
                and (info["serial_service"] != "active" or not info["open"])):
            problems.append(f"{slot}: serial monitor should be open")
        hold = holds.get(slot)
        if hold:
            result["holds"][slot] = hold
    last = history.last_ts()
    last_time = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp() if last else None
    result["idle"] = not result["holds"] and not result["locks_busy"] and (last_time is None or now - last_time > 600)
    if result["throttled"] is not None and int(result["throttled"], 16) != 0:
        problems.append("throttled is nonzero")
    if result["temp_c"] is not None and result["temp_c"] >= 80:
        problems.append("temperature >= 80 C")
    if result["disk_free_pct"] is not None and result["disk_free_pct"] < 10:
        problems.append("disk free < 10%")
    for field in ("hub_present", "camera_present"):
        if not result[field]:
            problems.append(field.removesuffix("_present") + " missing")
    try:
        previous = json.loads((config.root("run") / "health.prev.json").read_text())
        if not isinstance(previous, dict):
            raise ValueError("expected kernel counter object")
        for key in KERNEL_PATTERNS:
            if type(previous.get(key)) is not int or previous[key] < 0:
                raise ValueError("invalid previous kernel counters")
    except FileNotFoundError:
        previous = None
    except ValueError as exc:
        notes.append(f"previous kernel counters: {exc}")
        previous = None
    if result["kernel"] is not None and previous is not None:
        for key in ("error -71", "error -110"):
            if result["kernel"][key] > previous[key]:
                problems.append(f"kernel {key} count grew")
    result.update(ok=not problems, problems=problems, notes=notes)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = collect()
        atomic_json(config.root("run") / "health.json", result)
        if result["kernel"] is not None:
            atomic_json(config.root("run") / "health.prev.json", result["kernel"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"bench-health: cannot run: {exc}", file=sys.stderr)
        return 2
    if not (args.quiet and result["ok"]):
        print(json.dumps(result, sort_keys=True) if args.json else
              "bench " + ("OK" if result["ok"] else "DEGRADED") +
              f"; idle={str(result['idle']).lower()}" +
              ("; " + "; ".join(result["problems"]) if result["problems"] else ""))
    for problem in result["problems"]:
        try:
            command(["logger", "-t", "bench-health", "--", problem])
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"bench-health: logger unavailable: {exc}", file=sys.stderr)
    return 0 if result["ok"] else 1
