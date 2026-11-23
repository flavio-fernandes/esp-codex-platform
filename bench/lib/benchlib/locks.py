"""Nonblocking advisory locks on pre-created read-only lock files."""
from contextlib import contextmanager
import fcntl
import os
import stat
from . import config


class Busy(RuntimeError):
    """The operation lock is busy (CLI exit 65)."""


@contextmanager
def _lock(name):
    # Opened by the root helper too, in a group-writable directory: no symlinks, no FIFOs or
    # devices (a blocking or side-effecting open), only the pre-created regular file.
    path = config.root("run") / name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"refusing lock {path}: not a regular file")
    with os.fdopen(fd, "rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy(f"busy: {name}") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def slot_lock(slot):
    return _lock(config.validate_slot(slot) + ".lock")


def camera_lock():
    return _lock("camera.lock")


def lock_busy(slot):
    try:
        with slot_lock(slot):
            return False
    except Busy:
        return True