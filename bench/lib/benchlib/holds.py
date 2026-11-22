"""Persistent holds. Mutations serialize on the slot operation lock.

Call check() under the caller's slot_lock for mutating device operations.
put()/release() acquire that lock themselves; do not nest them inside it.
since/until are Unix timestamps in seconds.

The holds directory is group-writable and bench-usb reads it as root, so a hold file is opened
without following links and without blocking, must be a small regular file, and must have the
expected fields. Anything else is an UNREADABLE hold: it fails closed (every client gets 69, and
health is not idle) without echoing the file, and any client may release it.
"""
import json
import math
import os
import stat
import tempfile
import time
from . import config, locks

MAX_BYTES = 4096


class Held(RuntimeError):
    """Another client holds this slot (CLI exit 69)."""
    def __init__(self, hold):
        self.hold = hold
        until = "" if hold.get("until") is None else f" (until {hold['until']})"
        super().__init__(f"held by {hold['client']}: {hold['reason']}{until}")


def _path(slot):
    return config.root("state") / "holds" / (config.validate_slot(slot) + ".json")


def _unreadable(slot, why):
    return {"client": "(unreadable)", "unreadable": True, "since": None, "until": None,
            "reason": f"unreadable hold file ({why}); `release {slot}` clears it"}


def _valid(hold):
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    return (isinstance(hold, dict) and isinstance(hold.get("client"), str) and hold["client"]
            and isinstance(hold.get("reason"), str) and number(hold.get("since"))
            and number(hold.get("until")))


def get(slot):
    """The active hold, an UNREADABLE marker hold, or None."""
    path = _path(slot)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP for a planted symlink, EACCES, ...
        return _unreadable(slot, exc.strerror or "cannot open")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            return _unreadable(slot, "not a small regular file")
        raw = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    try:
        hold = json.loads(raw)
    except ValueError:
        return _unreadable(slot, "not JSON")
    if not _valid(hold):
        return _unreadable(slot, "unexpected fields")
    return hold if hold["until"] > time.time() else None


def check(slot, client):
    hold = get(slot)
    if hold and hold["client"] != client:
        raise Held(hold)


def put(slot, client, minutes, reason):
    if type(minutes) is not int or not 1 <= minutes <= 720:
        raise ValueError("minutes must be an integer from 1 to 720")
    if not isinstance(client, str) or not client or not isinstance(reason, str):
        raise ValueError("client must be nonempty and reason must be text")
    with locks.slot_lock(slot):
        check(slot, client)
        now = time.time()
        hold = {"client": client, "reason": reason, "since": now, "until": now + minutes * 60}
        path = _path(slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=slot + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o660)
                json.dump(hold, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return hold


def release(slot, client, force=False):
    with locks.slot_lock(slot):
        if not force and not (get(slot) or {}).get("unreadable"):
            check(slot, client)
        try:
            _path(slot).unlink()
            return True
        except FileNotFoundError:
            return False