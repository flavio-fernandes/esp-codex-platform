"""Improv-over-serial provisioning (bench-wifi-provision) against a fake device. No hardware."""
import io
import json

import pytest
from benchlib import holds, improv, locks, serialowner, wifiprov

SSID, PASSWORD = "bench-net", "s3cret-pass"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeDevice:
    """Speaks improv like ESPHome's improv_serial, with log noise in between."""

    def __init__(self, clock=None, answer_state=True, initial=improv.STATE_READY, error=None,
                 boot_reads=0, url="http://192.0.2.50"):
        self.clock, self.answer_state, self.initial, self.error = clock, answer_state, initial, error
        self.boot_reads, self.url = boot_reads, url
        self.out, self.written, self.timeout, self.closed = bytearray(), [], 0, False
        self.wifi = None

    def write(self, data):
        self.written.append(bytes(data))
        frames = improv.Reader().feed(bytes(data))
        for ptype, body in frames:
            assert ptype == improv.TYPE_RPC
            command, payload = body[0], body[2:2 + body[1]]
            if command == improv.CMD_STATE and self.answer_state and self.boot_reads <= 0:
                self.out += b"[I][app]: still booting\r\n" + improv.packet(improv.TYPE_STATE, bytes([self.initial]))
            elif command == improv.CMD_WIFI:
                ssid_len = payload[0]
                self.wifi = (payload[1:1 + ssid_len].decode(), payload[2 + ssid_len:].decode())
                self.out += improv.packet(improv.TYPE_STATE, bytes([improv.STATE_PROVISIONING]))
                self.out += b"[D][wifi]: connecting\r\n"
                if self.error is not None:
                    self.out += improv.packet(improv.TYPE_ERROR, bytes([self.error]))
                else:
                    self.out += improv.packet(improv.TYPE_STATE, bytes([improv.STATE_PROVISIONED]))
                    url = self.url.encode()
                    self.out += improv.packet(improv.TYPE_RESULT, bytes([improv.CMD_WIFI, len(url) + 1, len(url)]) + url)
        return len(data)

    def flush(self):
        pass

    def read(self, n):
        if self.clock is not None:
            self.clock.now += 0.5
        self.boot_reads -= 1
        chunk, self.out[:] = bytes(self.out[:n]), self.out[n:]
        return chunk

    def close(self):
        self.closed = True


def test_packet_layout_and_checksum():
    frame = improv.rpc(improv.CMD_STATE)
    assert frame[:6] == b"IMPROV" and frame[6] == 1 and frame[7] == improv.TYPE_RPC
    assert frame[8] == 2 and frame[9:11] == bytes([improv.CMD_STATE, 0])
    assert frame[11] == sum(frame[:11]) & 0xFF and frame.endswith(b"\n")


def test_wifi_payload_limits():
    assert improv.wifi_payload("ab", "") == b"\x02ab\x00"
    for ssid, password in (("", "x"), ("a" * 33, "x"), ("ok", "p" * 65)):
        with pytest.raises(ValueError):
            improv.wifi_payload(ssid, password)


def test_reader_skips_log_noise_split_frames_and_bad_checksums():
    good = improv.packet(improv.TYPE_STATE, b"\x02")
    bad = bytearray(improv.packet(improv.TYPE_STATE, b"\x04"))
    bad[-2] ^= 0xFF
    stream = b"boot IMPRO" + b"V junk " + bytes(bad) + b"log line\r\n" + good
    reader, got = improv.Reader(), []
    for i in range(0, len(stream), 3):
        got += reader.feed(stream[i:i + 3])
    assert got == [(improv.TYPE_STATE, b"\x02")]


def test_provision_success_returns_url_and_sends_credentials():
    clock = FakeClock()
    device = FakeDevice(clock=clock, boot_reads=6)   # the first state requests go unanswered
    result = improv.provision(device, SSID, PASSWORD, clock=clock)
    assert result == {"state": "provisioned", "urls": ["http://192.0.2.50"]}
    assert device.wifi == (SSID, PASSWORD)
    assert sum(1 for w in device.written if w[9] == improv.CMD_STATE) >= 2


def test_provision_device_error():
    clock = FakeClock()
    with pytest.raises(improv.ImprovError, match="unable to connect"):
        improv.provision(FakeDevice(clock=clock, error=0x03), SSID, PASSWORD, clock=clock)


def test_provision_no_answer_is_bounded():
    clock = FakeClock()
    with pytest.raises(improv.ImprovError, match="no improv answer"):
        improv.provision(FakeDevice(clock=clock, answer_state=False), SSID, PASSWORD, clock=clock)
    assert clock.now <= 20


def test_provision_refuses_authorization_state():
    clock = FakeClock()
    with pytest.raises(improv.ImprovError, match="authorization"):
        improv.provision(FakeDevice(clock=clock, initial=improv.STATE_AUTHORIZE), SSID, PASSWORD, clock=clock)


def test_read_credentials():
    assert wifiprov.read_credentials(io.StringIO(f"{SSID}\r\n{PASSWORD}\n")) == (SSID, PASSWORD)
    assert wifiprov.read_credentials(io.StringIO("open-net\n")) == ("open-net", "")
    for bad in ("", "\nx\n", "x" * 600, "a" * 40 + "\npw\n"):
        with pytest.raises((wifiprov.Failed, ValueError)):
            wifiprov.read_credentials(io.StringIO(bad))


@pytest.fixture
def slot(roots, usb, monkeypatch):
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-1.2\n")
    usb("1-1.1", vid="10c4", pid="ea60", tty="ttyUSB7", acm=False)
    opened = {}

    def fake_open(device, baud, **_kw):
        opened.update(device=device, baud=baud, port=FakeDevice())
        return opened["port"]

    monkeypatch.setattr(serialowner, "open_serial", fake_open)
    monkeypatch.setenv("BENCH_CLIENT", "tester")
    return opened


def history_rows(roots):
    return [json.loads(line) for line in (roots["state"] / "flash-history.jsonl").read_text().splitlines()]


def test_main_provisions_and_never_records_credentials(roots, slot, capsys):
    rc = wifiprov.main(["SLOT1"], stdin=io.StringIO(f"{SSID}\n{PASSWORD}\n"))
    out, err = capsys.readouterr()
    assert rc == 0
    assert "url: http://192.0.2.50" in out and "state: provisioned" in out
    assert slot["device"]["tty"] == "/dev/ttyUSB7" and slot["baud"] == 115200
    assert slot["port"].wifi == (SSID, PASSWORD) and slot["port"].closed
    row = history_rows(roots)[-1]
    assert row["verb"] == "wifi-provision" and row["rc"] == 0 and row["client"] == "tester"
    dump = out + err + (roots["state"] / "flash-history.jsonl").read_text()
    assert SSID not in dump and PASSWORD not in dump


def test_main_refusals(roots, slot, capsys):
    assert wifiprov.main([], stdin=io.StringIO("")) == wifiprov.USAGE
    assert wifiprov.main(["SLOT1"], stdin=io.StringIO("")) == wifiprov.USAGE
    assert wifiprov.main(["SLOT2"], stdin=io.StringIO("a\nb\n")) == wifiprov.ABSENT
    holds.put("SLOT1", "someone-else", 10, "testing")
    assert wifiprov.main(["SLOT1"], stdin=io.StringIO("a\nb\n")) == 69
    holds.release("SLOT1", "someone-else")
    with locks.slot_lock("SLOT1"):
        assert wifiprov.main(["SLOT1"], stdin=io.StringIO("a\nb\n")) == 65
    out, err = capsys.readouterr()
    assert "b\n" not in out
    rows = history_rows(roots)
    assert [r["rc"] for r in rows] == [2, 66, 69, 65]   # argv usage errors are not recorded
