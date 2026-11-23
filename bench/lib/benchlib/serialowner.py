"""Per-slot serial ownership and newline-delimited JSON socket service."""
from collections import deque
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import signal
import socket
import threading
import time

import serial
from . import config, usbdev

MAX_REQUEST = 65536
# A board that streams without newlines (binary, progress bars) must not grow the partial line
# forever: past this many bytes it is published as a line of its own. Worst-case ring memory is
# then 5000 lines x 4 KiB (PR review, 2026-11-23).
MAX_LINE = 4096


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def monitor_allowed(kind, monitor):
    return monitor != "off" and (kind in {"rp2", "esp-uart"} or
                                 (kind == "esp-usb" and monitor == "on"))


def open_serial(device, baud, factory=serial.Serial):
    """Configure modem lines before open; never construct with a port argument."""
    tty = factory()
    try:
        tty.port = device["tty"]
        tty.baudrate = baud
        tty.dtr = device["kind"] == "rp2"
        tty.rts = device["kind"] == "rp2"
        tty.timeout = 0
        tty.write_timeout = 2
        tty.open()
        return tty
    except Exception:
        tty.close()
        raise


class RequestError(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


class SerialOwner:
    def __init__(self, slot, *, poll_interval=0.1, log_bytes=5 * 1024 * 1024,
                 serial_factory=serial.Serial, resolver=usbdev.resolve):
        self.slot = config.validate_slot(slot)
        self.options = config.load_slots()[slot]
        self.socket_path = config.root("run") / (slot + ".sock")
        log_path = config.root("state") / "serial" / (slot + ".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = RotatingFileHandler(log_path, maxBytes=log_bytes, backupCount=5,
                                       encoding="utf-8")
        self.log.setFormatter(logging.Formatter("%(message)s"))
        self.poll_interval = poll_interval
        self.factory, self.resolve = serial_factory, resolver
        self.cv = threading.Condition(threading.RLock())
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.ring = deque(maxlen=5000)
        self.seq = 0
        self.device = self.tty = self.paused_by = None
        self.pause_token = None
        self.partial = bytearray()
        self.last_byte = 0
        self.reader = None
        self.clients = set()
        self.workers = []

    def _log(self, text, ts=None):
        self.log.handle(logging.LogRecord("bench.serial", logging.INFO, "", 0,
                                         f"{ts or timestamp()} {text}", (), None))

    def _line(self, raw):
        """Publish raw bytes as one or more lines of at most MAX_LINE bytes. Every path that
        publishes (newline, partial overflow, idle flush, close) goes through here, so no line in
        the ring, the log or a reply can exceed the bound (PR review, 2026-11-23)."""
        for start in range(0, max(len(raw), 1), MAX_LINE):
            self._publish(raw[start:start + MAX_LINE])

    def _publish(self, raw):
        self.seq += 1
        line = {"seq": self.seq, "ts": timestamp(),
                "text": raw.decode("utf-8", errors="replace").replace("\r", "")}
        self.ring.append(line)
        self._log(line["text"], line["ts"])
        self.cv.notify_all()

    def _close(self):
        if self.tty is not None:
            try:
                self.tty.close()
            except (OSError, serial.SerialException):
                pass
            self.tty = None
        if self.partial:
            self._line(bytes(self.partial))
            self.partial.clear()

    def _reader(self):
        while not self.stop_event.is_set():
            with self.cv:
                try:
                    device = self.resolve(self.options["path"])
                    if device != self.device:
                        self._close()
                        if self.device is not None:
                            self._log("detach")
                        self.device = device
                        if device is not None:
                            self._log("attach")
                    if (self.tty is None and self.pause_token is None and device and
                            device["tty"] and monitor_allowed(device["kind"], self.options["monitor"])):
                        self.tty = open_serial(device, self.options["baud"], self.factory)
                    if self.tty is not None:
                        data = self.tty.read(65536)
                        if data:
                            self.partial.extend(data)
                            self.last_byte = time.monotonic()
                            while b"\n" in self.partial:
                                raw, _, rest = self.partial.partition(b"\n")
                                self.partial = bytearray(rest)
                                self._line(raw)
                            while len(self.partial) >= MAX_LINE:
                                self._line(bytes(self.partial[:MAX_LINE]))
                                del self.partial[:MAX_LINE]
                        if self.partial and time.monotonic() - self.last_byte >= 0.5:
                            self._line(bytes(self.partial))
                            self.partial.clear()
                except (OSError, ValueError, serial.SerialException) as exc:
                    self._close()
                    self._log(f"detach: {exc}")
                    self.device = None
                self.cv.notify_all()
                self.cv.wait_for(self.stop_event.is_set, timeout=self.poll_interval)
        with self.cv:
            self._close()
            self.cv.notify_all()

    def state(self):
        with self.cv:
            return {"ok": True, "present": self.device is not None,
                    "kind": self.device["kind"] if self.device else None,
                    "tty": self.device["tty"] if self.device else None,
                    "open": self.tty is not None and self.reader is not None and self.reader.is_alive(),
                    "paused": self.pause_token is not None, "paused_by": self.paused_by,
                    "seq": self.seq}

    @staticmethod
    def _send(conn, value):
        conn.sendall((json.dumps(value, ensure_ascii=True) + "\n").encode())

    @staticmethod
    def _number(req, key, maximum=None, integer=False):
        value = req.get(key)
        try:
            finite = math.isfinite(value)
        except (TypeError, OverflowError):
            finite = False
        if (isinstance(value, bool) or not isinstance(value, int if integer else (int, float))
                or not finite or value < 0
                or (maximum is not None and value > maximum)):
            raise RequestError("bad-request", f"invalid {key}")
        return value

    def _require_open(self):
        if self.pause_token is not None:
            raise RequestError("paused", "serial owner is paused")
        if self.device is None:
            raise RequestError("absent", "device is absent")
        if self.tty is None:
            raise RequestError("not-open", "monitor is not open")

    def _stream(self, conn, req, op):
        seconds = self._number(req, "timeout" if op == "wait" else "seconds",
                               None if op == "wait" else 600)
        pattern = req.get("pattern")
        if op == "wait" and not isinstance(pattern, str):
            raise RequestError("bad-request", "pattern must be a string")
        with self.cv:
            cursor = (self._number(req, "since_seq", integer=True)
                      if op == "wait" and "since_seq" in req else self.seq)
        deadline = time.monotonic() + seconds
        while not self.stop_event.is_set():
            with self.cv:
                lines = [line for line in self.ring if line["seq"] > cursor]
                remaining = deadline - time.monotonic()
                if not lines and remaining > 0:
                    self.cv.wait(min(remaining, 0.1))
                    continue
            for line in lines:
                cursor = line["seq"]
                if op == "wait":
                    if pattern in line["text"]:
                        self._send(conn, {"ok": True, "matched": True, "line": line})
                        return
                else:
                    self._send(conn, line)
            if time.monotonic() >= deadline:
                break
        self._send(conn, {"ok": True, "matched": False} if op == "wait"
                   else {"ok": True, "end": True})

    def _dispatch(self, conn, req):
        if not isinstance(req, dict) or not isinstance(req.get("op"), str):
            raise RequestError("bad-request", "expected an object with an op")
        op = req["op"]
        if op == "state":
            self._send(conn, self.state())
        elif op == "tail":
            n = int(self._number(req, "lines", 1000, integer=True))
            with self.cv:
                lines = list(self.ring)[-n:] if n else []
            for line in lines:
                self._send(conn, line)
            self._send(conn, {"ok": True, "end": True})
        elif op in {"wait", "follow"}:
            self._stream(conn, req, op)
        elif op == "pause":
            by = req.get("by")
            if not isinstance(by, str) or not by:
                raise RequestError("bad-request", "by must be a nonempty string")
            with self.cv:
                if self.pause_token is not None:
                    raise RequestError("paused", "another client holds the pause")
                self.pause_token, self.paused_by = conn, by
                self._close()
            self._send(conn, {"ok": True, "paused": True})
            # No lease timeout: an idle live connection must retain its pause.
            conn.settimeout(0.1)
            while not self.stop_event.is_set():
                try:
                    if not conn.recv(1024):
                        break
                except socket.timeout:
                    continue
        elif op in {"write", "reset"}:
            if op == "write" and not isinstance(req.get("text"), str):
                raise RequestError("bad-request", "text must be a string")
            if op == "write":
                try:
                    data = (req["text"] + "\r\n").encode("utf-8")
                except UnicodeError as exc:
                    raise RequestError("bad-request", "text must be valid Unicode") from exc
            with self.cv:
                if op == "reset" and self.device and self.device["kind"] != "esp-uart":
                    raise RequestError("unsupported", "reset requires esp-uart")
                self._require_open()
                assert self.tty is not None
                try:
                    if op == "write":
                        count = self.tty.write(data)
                        if count != len(data):
                            raise RequestError("timeout", "incomplete serial write")
                        result = {"ok": True, "bytes": count}
                    else:
                        # USB UART control outputs are active-low. Keep GPIO0 high,
                        # pull EN low, then high; finish with both deasserted.
                        self.tty.dtr = False
                        self.tty.rts = True
                        time.sleep(0.1)
                        self.tty.rts = False
                        result = {"ok": True}
                except serial.SerialTimeoutException as exc:
                    raise RequestError("timeout", str(exc)) from exc
                except (OSError, serial.SerialException) as exc:
                    self._close()
                    raise RequestError("not-open", str(exc)) from exc
            self._send(conn, result)
        else:
            raise RequestError("bad-request", "unknown op")

    def _client(self, conn):
        try:
            conn.settimeout(5)
            data = bytearray()
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data.extend(chunk)
                if len(data) > MAX_REQUEST:
                    raise RequestError("bad-request", "request too large")
            try:
                req = json.loads(data.partition(b"\n")[0])
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise RequestError("bad-request", "invalid JSON") from exc
            self._dispatch(conn, req)
        except RequestError as exc:
            try:
                self._send(conn, {"ok": False, "error": exc.code, "detail": exc.detail})
            except OSError:
                pass
        except socket.timeout:
            try:
                self._send(conn, {"ok": False, "error": "timeout", "detail": "socket timeout"})
            except OSError:
                pass
        except OSError:
            pass  # Client vanished; finally also releases its pause.
        finally:
            with self.cv:
                if self.pause_token is conn:
                    self.pause_token = self.paused_by = None
                    self.cv.notify_all()
                self.clients.discard(conn)
            conn.close()

    def stop(self):
        self.stop_event.set()
        with self.cv:
            self.cv.notify_all()

    def serve_forever(self):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound = False
        try:
            if self.socket_path.exists():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    try:
                        probe.connect(str(self.socket_path))
                    except (ConnectionRefusedError, FileNotFoundError):
                        self.socket_path.unlink(missing_ok=True)
                        self._log("stale socket removed")
                    else:
                        raise OSError(f"live serial owner already listening: {self.socket_path}")
            listener.bind(str(self.socket_path))
            bound = True
            os.chmod(self.socket_path, 0o660)
            listener.listen()
            listener.settimeout(0.1)
            self.reader = threading.Thread(target=self._reader, name=f"{self.slot}-reader")
            self.reader.start()
            self.ready.set()
            while not self.stop_event.is_set():
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                with self.cv:
                    self.clients.add(conn)
                    self.workers = [worker for worker in self.workers if worker.is_alive()]
                    worker = threading.Thread(target=self._client, args=(conn,))
                    self.workers.append(worker)
                    worker.start()
        finally:
            self.stop()
            listener.close()
            if self.reader:
                self.reader.join()
            with self.cv:
                for conn in self.clients:
                    try:
                        conn.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            for worker in self.workers:
                worker.join()
            if bound:
                self.socket_path.unlink(missing_ok=True)
            self.log.close()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot")
    args = parser.parse_args(argv)
    try:
        owner = SerialOwner(args.slot)
    except (KeyError, ValueError, OSError) as exc:
        parser.error(str(exc))
    signal.signal(signal.SIGTERM, lambda *_: owner.stop())
    owner.serve_forever()
    return 0
