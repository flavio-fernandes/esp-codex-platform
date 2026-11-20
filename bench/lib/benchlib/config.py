"""Strict configuration readers; environment overrides are evaluated per call."""
import os
import re
from pathlib import Path

SLOT_RE = re.compile(r"SLOT[0-9]+\Z")
PORT_RE = re.compile(r"[0-9]+-[0-9]+(?:\.[0-9]+)*\Z")
DEFAULTS = {"etc": "/etc/bench", "run": "/run/bench",
            "state": "/var/lib/bench", "sysfs": "/sys"}


def root(name):
    """Return a Path for etc/run/state/sysfs, honoring BENCH_<NAME>."""
    return Path(os.environ.get("BENCH_" + name.upper(), DEFAULTS[name]))


def validate_slot(slot):
    if not SLOT_RE.fullmatch(slot):
        raise ValueError(f"invalid slot: {slot!r}")
    return slot


def validate_port(path):
    if not PORT_RE.fullmatch(path):
        raise ValueError(f"invalid USB port path: {path!r}")
    return path


def _lines(path):
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if line and not line.startswith("#"):
            yield number, line


def load_slots(path=None):
    """Return {label: {path, monitor, baud}}; invalid lines raise ValueError."""
    slots, paths = {}, set()
    for number, line in _lines(path if path is not None else root("etc") / "slots.conf"):
        try:
            words = line.split()
            if len(words) < 2:
                raise ValueError("expected label and USB port path")
            label, port = validate_slot(words[0]), validate_port(words[1])
            if label in slots or port in paths:
                raise ValueError("duplicate label or USB port path")
            options = {"path": port, "monitor": "auto", "baud": 115200}
            seen = set()
            for word in words[2:]:
                key, sep, value = word.partition("=")
                if not sep or key not in {"monitor", "baud"} or key in seen:
                    raise ValueError(f"unknown or duplicate option: {word}")
                seen.add(key)
                if key == "monitor":
                    if value not in {"auto", "on", "off"}:
                        raise ValueError("monitor must be auto, on or off")
                    options[key] = value
                else:
                    if not re.fullmatch(r"[0-9]+", value) or int(value) < 1:
                        raise ValueError("baud must be a positive integer")
                    options[key] = int(value)
            slots[label] = options
            paths.add(port)
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from exc
    return slots


def load_env(path=None):
    """Return literal KEY=VALUE strings; no shell expansion or evaluation."""
    result = {}
    for number, line in _lines(path if path is not None else root("etc") / "bench.env"):
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or key in result:
            raise ValueError(f"line {number}: invalid or duplicate KEY=VALUE")
        result[key] = value
    return result