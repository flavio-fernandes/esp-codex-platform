"""Small timeout-bounded client for bench-serial's local JSON protocol."""
from contextlib import contextmanager
import json
import socket
from . import config

SOCKET_TIMEOUT = 5


class NotRunning(ConnectionError):
    """The slot has no listening serial owner."""


class ProtocolError(RuntimeError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


@contextmanager
def _request(slot, request, timeout=SOCKET_TIMEOUT):
    path = config.root("run") / (config.validate_slot(slot) + ".sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        try:
            conn.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise NotRunning(f"bench-serial is not running for {slot}: {path}") from exc
        conn.sendall((json.dumps(request, allow_nan=False) + "\n").encode())
        with conn.makefile("rb") as stream:
            yield stream


def _read(stream):
    raw = stream.readline()
    if not raw:
        raise ProtocolError("not-open", "owner closed the connection without a response")
    value = json.loads(raw)
    if value.get("ok") is False:
        raise ProtocolError(value["error"], value.get("detail", ""))
    return value


def state(slot):
    """Return the complete state response."""
    with _request(slot, {"op": "state"}) as stream:
        return _read(stream)


def tail(slot, n):
    """Return up to n buffered line dictionaries, oldest first (n <= 1000)."""
    with _request(slot, {"op": "tail", "lines": n}) as stream:
        result = []
        while True:
            value = _read(stream)
            if value.get("end"):
                return result
            result.append(value)


def wait(slot, pattern, timeout, since_seq=None):
    """Return the matched line or None; without since_seq only new lines match."""
    request = {"op": "wait", "pattern": pattern, "timeout": timeout}
    if since_seq is not None:
        request["since_seq"] = since_seq
    with _request(slot, request, max(SOCKET_TIMEOUT, timeout + SOCKET_TIMEOUT)) as stream:
        result = _read(stream)
        return result.get("line") if result["matched"] else None


def follow(slot, seconds):
    """Yield new line dictionaries for seconds (<=600); close generator to cancel."""
    with _request(slot, {"op": "follow", "seconds": seconds},
                  max(SOCKET_TIMEOUT, seconds + SOCKET_TIMEOUT)) as stream:
        while True:
            value = _read(stream)
            if value.get("end"):
                return
            yield value


def write(slot, text):
    """Write text plus CRLF; return the byte count."""
    with _request(slot, {"op": "write", "text": text}) as stream:
        return _read(stream)["bytes"]


def reset(slot):
    """Pulse esp-uart EN; return the success response, or raise ProtocolError."""
    with _request(slot, {"op": "reset"}) as stream:
        return _read(stream)


@contextmanager
def paused(slot, by):
    """Yield after tty closure; keep ownership until context exit (also on error)."""
    with _request(slot, {"op": "pause", "by": by}) as stream:
        response = _read(stream)
        yield response
