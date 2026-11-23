"""No hardware: real pyserial/pty I/O, fake sysfs, real concurrent Unix clients."""
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import threading
import time

import pytest
import serial
from benchlib import serialclient as client
from benchlib.serialowner import SerialOwner, monitor_allowed, open_serial


@pytest.fixture(autouse=True)
def short_socket_path(roots, monkeypatch):
    # sockaddr_un paths are limited to 108 bytes; pytest temp roots may be longer.
    # A relative run path keeps all files in the same isolated temp directory.
    monkeypatch.chdir(roots["run"].parent)
    monkeypatch.setenv("BENCH_RUN", "run")


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class RecordingSerial:
    def __init__(self):
        object.__setattr__(self, "calls", [])

    def __setattr__(self, key, value):
        self.calls.append((key, value))
        object.__setattr__(self, key, value)

    def open(self):
        self.calls.append(("open",))

    def close(self):
        self.calls.append(("close",))


@pytest.mark.parametrize("kind,high", [("rp2", True), ("esp-uart", False), ("esp-usb", False)])
def test_modem_order(kind, high):
    tty = open_serial({"tty": "/dev/ttyACM0", "kind": kind}, 9600, RecordingSerial)
    assert tty.calls == [("port", "/dev/ttyACM0"), ("baudrate", 9600),
                         ("dtr", high), ("rts", high), ("timeout", 0),
                         ("write_timeout", 2), ("open",)]


@pytest.mark.parametrize("kind", ["rp2", "esp-uart", "esp-usb", "rp2-bootsel", "unknown"])
@pytest.mark.parametrize("monitor", ["auto", "on", "off"])
def test_policy(kind, monitor):
    assert monitor_allowed(kind, monitor) == (
        monitor != "off" and (kind in {"rp2", "esp-uart"} or (kind == "esp-usb" and monitor == "on")))


@pytest.fixture
def running(roots, usb):
    master, slave = os.openpty()
    tty_path = os.ttyname(slave)
    device = usb(tty="ttyACM0")
    # Production resolution returns /dev/ttyACM0. Map only that devnode to the
    # fake sysfs tty link, avoiding creation of privileged /dev entries.
    link = roots["sysfs"] / "bus/usb/devices/1-1.1:1.0/tty/ttyACM0"
    link.rmdir()
    link.symlink_to(tty_path)
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\n")
    instances = []

    class PtySerial(serial.Serial):
        def open(self):
            assert self.port == "/dev/ttyACM0"
            self.port = str(link.resolve())
            super().open()
            instances.append(self)

    owner = SerialOwner("SLOT1", poll_interval=0.02, log_bytes=2048, serial_factory=PtySerial)
    thread = threading.Thread(target=owner.serve_forever)
    thread.start()
    try:
        assert owner.ready.wait(3)
        eventually(lambda: client.state("SLOT1")["open"])
        yield owner, master, device, instances
    finally:
        owner.stop()
        thread.join(3)
        os.close(master)
        os.close(slave)
        assert not thread.is_alive()
        assert not owner.socket_path.exists()
        assert all(not instance.is_open for instance in instances)


def raw(owner, payload):
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(3)
        conn.connect(str(owner.socket_path))
        conn.sendall(payload + b"\n")
        with conn.makefile("rb") as stream:
            return json.loads(stream.readline())


def test_lines_tail_wait_partial_and_write(running):
    owner, master, _, instances = running
    assert owner.socket_path.stat().st_mode & 0o777 == 0o660
    before = client.state("SLOT1")["seq"]
    os.write(master, b"hello\r\nboot ready\ninvalid \xff\npartial")
    eventually(lambda: client.state("SLOT1")["seq"] == before + 4)
    lines = client.tail("SLOT1", 1000)
    assert [line["text"] for line in lines] == ["hello", "boot ready", "invalid \ufffd", "partial"]
    assert [line["seq"] for line in lines] == list(range(before + 1, before + 5))
    assert all(line["ts"].endswith("Z") and "." in line["ts"] for line in lines)
    assert client.wait("SLOT1", "ready", 0, before) == lines[1]
    assert client.wait("SLOT1", "ready", 0.03, lines[1]["seq"]) is None
    assert client.wait("SLOT1", "hello", 0.03) is None
    assert client.wait("SLOT1", "absent", 0.03, before) is None
    assert client.tail("SLOT1", 0) == []
    assert client.write("SLOT1", "hello") == 7
    assert select.select([master], [], [], 2)[0]
    assert os.read(master, 100) == b"hello\r\n"
    assert len(instances) == 1  # clients never reopen the tty


def test_wait_new_and_follow_two_clients(running):
    owner, master, _, _ = running
    # Synchronize after cursor capture (the condition wait releases the lock).
    results = {}
    threads = [threading.Thread(target=lambda: results.update(wait=client.wait("SLOT1", "hit", 1))),
               threading.Thread(target=lambda: results.update(follow=list(client.follow("SLOT1", 0.3))))]
    for thread in threads:
        thread.start()
    eventually(lambda: len(owner.clients) == 2)
    time.sleep(0.05)
    os.write(master, b"hit\nnext\n")
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert results["wait"]["text"] == "hit"
    assert [line["text"] for line in results["follow"]] == ["hit", "next"]


def test_pause_close_resume_and_errors(running):
    owner, _, _, instances = running
    with pytest.raises(RuntimeError, match="test exit"):
        with client.paused("SLOT1", "tester"):
            assert not instances[-1].is_open
            state = client.state("SLOT1")
            assert state["paused"] and state["paused_by"] == "tester" and not state["open"]
            for operation in [lambda: client.write("SLOT1", "x"),
                              lambda: client.paused("SLOT1", "other").__enter__()]:
                with pytest.raises(client.ProtocolError) as error:
                    operation()
                assert error.value.code == "paused"
            raise RuntimeError("test exit")
    eventually(lambda: client.state("SLOT1")["open"])
    assert not client.state("SLOT1")["paused"]
    assert len(instances) == 2
    with pytest.raises(client.ProtocolError) as error:
        client.reset("SLOT1")
    assert error.value.code == "unsupported"


def test_hotplug_and_read_error(running):
    owner, master, device, instances = running
    vid = (device / "idVendor").read_text()
    (device / "idVendor").unlink()
    eventually(lambda: not client.state("SLOT1")["present"])
    assert not instances[-1].is_open
    with pytest.raises(client.ProtocolError) as error:
        client.write("SLOT1", "x")
    assert error.value.code == "absent"
    (device / "idVendor").write_text(vid)
    eventually(lambda: client.state("SLOT1")["open"])
    os.write(master, b"returned\n")
    assert client.wait("SLOT1", "returned", 1, 0)["text"] == "returned"
    # A read failure closes and reopens even when the sysfs device persists.
    def broken_read(_):
        raise serial.SerialException("injected read error")
    instances[-1].read = broken_read
    eventually(lambda: len(instances) >= 3)
    assert client.state("SLOT1")["open"]
    text = (owner.log.baseFilename and Path(owner.log.baseFilename).read_text())
    assert "attach" in text and "detach" in text and "injected read error" in text


def test_ring_rotation(running):
    owner, _, _, _ = running
    with owner.cv:
        for index in range(5100):
            owner._line(f"line {index} ".encode() + b"x" * 100)
    assert len(owner.ring) == 5000
    assert owner.ring[0]["seq"] == 101
    lines = client.tail("SLOT1", 1000)
    assert len(lines) == 1000 and lines[-1]["seq"] == 5100
    path = Path(owner.log.baseFilename)
    assert all(Path(str(path) + f".{n}").exists() for n in range(1, 6))
    assert not Path(str(path) + ".6").exists()
    assert "line 5099" in path.read_text()


@pytest.mark.parametrize("payload", [b"{", b"[]", b"null", b'{"op":"bogus"}',
    b'{"op":"tail","lines":1001}', b'{"op":"tail","lines":true}',
    b'{"op":"tail","lines":-1}', b'{"op":"tail","lines":1.5}',
    b'{"op":"follow","seconds":601}', b'{"op":"follow","seconds":NaN}',
    b'{"op":"wait","pattern":1,"timeout":0}', b'{"op":"wait","pattern":"x","timeout":-1}',
    b'{"op":"wait","pattern":"x","timeout":0,"since_seq":-1}',
    b'{"op":"write","text":null}', b'{"op":"pause","by":""}', b'\xff', b'x' * 65537,
    b'{"op":"tail","lines":' + b'9' * 400 + b'}',
    b'{"op":"write","text":"\\ud800"}', b'{"op":"follow","seconds":"1"}'])
def test_bad_requests(running, payload):
    result = raw(running[0], payload)
    assert result["ok"] is False and result["error"] == "bad-request"
    assert client.state("SLOT1")["open"]


def test_monitor_off_not_open_and_reset(running):
    owner, _, device, _ = running
    with owner.cv:
        owner.options["monitor"] = "off"
        owner._close()
    with pytest.raises(client.ProtocolError) as error:
        client.write("SLOT1", "x")
    assert error.value.code == "not-open"
    (device / "idVendor").write_text("10c4")
    (device / "idProduct").write_text("ea60")
    eventually(lambda: client.state("SLOT1")["kind"] == "esp-uart")
    fake = RecordingSerial()
    with owner.cv:
        owner.tty = fake
        # Reset is serialized with reader I/O; fake offers nonblocking read.
        fake.read = lambda _: b""
    assert client.reset("SLOT1") == {"ok": True}
    assert [(key, value) for key, value in fake.calls if key in {"dtr", "rts"}] == [
        ("dtr", False), ("rts", True), ("rts", False)]
    with owner.cv:
        def timeout(_):
            raise serial.SerialTimeoutException("injected timeout")
        fake.write = timeout
    with pytest.raises(client.ProtocolError) as error:
        client.write("SLOT1", "x")
    assert error.value.code == "timeout"


def test_missing_socket():
    with pytest.raises(client.NotRunning, match="SLOT1"):
        client.state("SLOT1")


@pytest.mark.parametrize("vid,pid,kind,monitor,opened", [
    ("303a", "0001", "esp-usb", "auto", False),
    ("303a", "0001", "esp-usb", "on", True),
    ("ffff", "ffff", "unknown", "on", False),
    ("2e8a", "0003", "rp2-bootsel", "on", False),
    ("10c4", "ea60", "esp-uart", "auto", True),
    ("10c4", "ea60", "esp-uart", "off", False)])
def test_owner_kind_monitor(running, vid, pid, kind, monitor, opened):
    owner, _, device, _ = running
    with owner.cv:
        owner.options["monitor"] = monitor
        (device / "idVendor").write_text(vid)
        (device / "idProduct").write_text(pid)
    eventually(lambda: client.state("SLOT1")["kind"] == kind)
    assert client.state("SLOT1")["open"] is opened


def test_pause_survives_idle_timeout_and_client_death(running):
    owner, _, _, instances = running
    conn = socket.socket(socket.AF_UNIX)
    conn.settimeout(2)
    try:
        conn.connect(str(owner.socket_path))
        conn.sendall(b'{"op":"pause","by":"test-client"}\n')
        assert json.loads(conn.recv(4096))["paused"]
        time.sleep(5.1)  # longer than the ordinary request socket timeout
        assert not instances[-1].is_open
        assert client.state("SLOT1")["paused"]
    finally:
        conn.close()
    eventually(lambda: client.state("SLOT1")["open"])


def test_live_socket_not_removed_by_second_owner(running):
    owner, _, _, _ = running
    other = SerialOwner("SLOT1")
    with pytest.raises(OSError):
        other.serve_forever()
    assert owner.socket_path.exists() and client.state("SLOT1")["open"]


def test_stale_socket_replaced(roots):
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\n")
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind("run/SLOT1.sock")
    owner = SerialOwner("SLOT1")
    errors = []
    def serve():
        try:
            owner.serve_forever()
        except OSError as exc:
            errors.append(exc)
            owner.ready.set()
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        assert owner.ready.wait(3)
        assert not errors
        assert client.state("SLOT1")["ok"]
        assert "stale socket removed" in Path(owner.log.baseFilename).read_text()
    finally:
        owner.stop()
        thread.join(3)
        assert not thread.is_alive()


def test_sigterm_entrypoint(roots):
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\n")
    entrypoint = Path(__file__).resolve().parents[1] / "bin/bench-serial"
    process = subprocess.Popen([sys.executable, str(entrypoint), "SLOT1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        eventually(lambda: (roots["run"] / "SLOT1.sock").exists())
        assert not client.state("SLOT1")["present"]
        process.terminate()
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, stderr.decode()
        assert stdout == b"" and stderr == b""
        assert not (roots["run"] / "SLOT1.sock").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_a_newline_free_stream_is_split_at_max_line(running):
    """PR review 2026-11-23: without newlines or a 500 ms gap the partial line grew without bound."""
    from benchlib.serialowner import MAX_LINE
    owner, master, _, _ = running
    before = client.state("SLOT1")["seq"]
    stop = time.monotonic() + 1.5
    while time.monotonic() < stop:          # ~20 KiB, never a newline, never a 500 ms gap
        os.write(master, b"x" * 512)
        time.sleep(0.03)
        assert len(owner.partial) < MAX_LINE + 65536   # bounded while data keeps arriving
    lines = eventually(lambda: [l for l in client.tail("SLOT1", 1000) if l["seq"] > before])
    assert lines and all(len(line["text"]) <= MAX_LINE for line in lines)
    assert any(len(line["text"]) == MAX_LINE for line in lines)
