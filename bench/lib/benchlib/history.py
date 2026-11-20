"""Append-only JSON history; timestamps are UTC ISO8601 with milliseconds."""
from collections import deque
from datetime import datetime, timezone
import fcntl
import json
import os
from . import config

FIELDS = ("ts", "client", "verb", "slot", "kind", "usb_serial", "sha256", "size", "rc", "seconds", "note")


def _path():
    return config.root("state") / "flash-history.jsonl"


def append(**fields):
    if fields.keys() - set(FIELDS):
        raise ValueError("unknown history fields")
    record: dict[str, object] = {name: None for name in FIELDS}
    record.update(fields)
    if record["ts"] is None:
        record["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    payload = (json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n").encode()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o660)
    except FileExistsError:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    else:
        # Both bench and wbagent append; do not let the creator's umask
        # silently remove the shared group's write permission.
        try:
            os.fchmod(fd, 0o660)
        except BaseException:
            os.close(fd)
            raise
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise OSError("zero-byte history write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return record


def tail(n):
    """Return up to n records, oldest first; malformed history fails loudly."""
    if type(n) is not int or n < 0:
        raise ValueError("n must be a nonnegative integer")
    if n == 0:
        return []
    try:
        with _path().open() as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return [json.loads(line) for line in deque(stream, maxlen=n)]
    except FileNotFoundError:
        return []


def last_ts():
    records = tail(1)
    return records[0]["ts"] if records else None