"""Nonblocking advisory locks on pre-created read-only lock files."""
from contextlib import contextmanager
import fcntl
from . import config


class Busy(RuntimeError):
    """The operation lock is busy (CLI exit 65)."""


@contextmanager
def _lock(name):
    with (config.root("run") / name).open("rb") as stream:
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