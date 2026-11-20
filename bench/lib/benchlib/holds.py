"""Persistent holds. Mutations serialize on the slot operation lock.

Call check() under the caller's slot_lock for mutating device operations.
put()/release() acquire that lock themselves; do not nest them inside it.
since/until are Unix timestamps in seconds.
"""
import json
import os
import tempfile
import time
from . import config, locks


class Held(RuntimeError):
    """Another client holds this slot (CLI exit 69)."""
    def __init__(self, hold):
        self.hold = hold
        super().__init__(f"held by {hold['client']}: {hold['reason']} (until {hold['until']})")


def _path(slot):
    return config.root("state") / "holds" / (config.validate_slot(slot) + ".json")


def get(slot):
    try:
        hold = json.loads(_path(slot).read_text())
    except FileNotFoundError:
        return None
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
        if not force:
            check(slot, client)
        try:
            _path(slot).unlink()
            return True
        except FileNotFoundError:
            return False