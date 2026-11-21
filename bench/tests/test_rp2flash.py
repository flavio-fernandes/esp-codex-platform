"""bench-flash-rp2 with a fake picotool on PATH, a fake sysfs that switches app <-> BOOTSEL,
and W2's real serial owner on a pty. No hardware."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
import serial
import bench_fakeusb
from benchlib import history, holds, locks, rp2flash
from benchlib import serialclient as client
from benchlib.serialowner import SerialOwner

TESTS = Path(__file__).resolve().parent
LIB = TESTS.parent / "lib"
CLIENT = "bench-flash-rp2 client-a"


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class Bench:
    def __init__(self, tmp_path, state_file, fake_dir, touches):
        self.tmp_path, self.state_file, self.fake_dir = tmp_path, state_file, fake_dir
        self.touches = touches
        self.touch_works = True

    def scenario(self, **specs):
        (self.fake_dir / "scenario.json").write_text(json.dumps(
            {key.replace("_", "-"): value for key, value in specs.items()}))

    def calls(self):
        log = self.fake_dir / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def mode(self, path, mode):
        bench_fakeusb.set_mode(self.state_file, path, mode)

    def uf2(self, data=b"UF2\n" + bytes(508)):
        path = self.tmp_path / "fw.uf2"
        path.write_bytes(data)
        return path


@pytest.fixture
def bench(roots, usb, tmp_path, monkeypatch):
    # sockaddr_un paths are limited to 108 bytes: keep the run root relative.
    monkeypatch.chdir(roots["run"].parent)
    monkeypatch.setenv("BENCH_RUN", "run")
    master, slave = os.openpty()
    pty = os.ttyname(slave)
    usb(path="1-1.1", tty="ttyACM0")
    usb(path="1-1.2", tty="ttyACM1")
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-1.2\n")
    state_file = tmp_path / "fakeusb.json"
    state_file.write_text(json.dumps({"sysfs": str(roots["sysfs"]), "devices": {
        "1-1.1": {"slot": "SLOT1", "tty": "ttyACM0", "target": pty, "mode": "app",
                  "app_devnum": 7, "bootsel_devnum": 8},
        "1-1.2": {"slot": "SLOT2", "tty": "ttyACM1", "target": None, "mode": "app",
                  "app_devnum": 5, "bootsel_devnum": 9}}}))
    fake_dir = tmp_path / "picotool"
    (fake_dir / "bin").mkdir(parents=True)
    script = fake_dir / "bin/picotool"
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.path[:0] = [{str(TESTS)!r}, {str(LIB)!r}]\n"
                      "from bench_fake_picotool import main\nsys.exit(main(sys.argv[1:]))\n")
    script.chmod(0o755)
    for key, value in {"PATH": f"{fake_dir / 'bin'}:{os.environ['PATH']}",
                       "FAKE_PT_DIR": str(fake_dir), "FAKE_USB_STATE": str(state_file),
                       "BENCH_CLIENT": "client-a", "BENCH_RP2_BOOTSEL_WAIT": "1",
                       "BENCH_RP2_APP_WAIT": "2", "BENCH_RP2_OPEN_WAIT": "3",
                       "BENCH_RP2_LINE_WAIT": "3", "BENCH_RP2_PICOTOOL_TIMEOUT": "20",
                       "BENCH_RP2_REBOOT_TIMEOUT": "20"}.items():
        monkeypatch.setenv(key, value)
    touches = []
    fake = Bench(tmp_path, state_file, fake_dir, touches)
    fake.mode("1-1.1", "app")
    fake.mode("1-1.2", "app")

    class TouchSerial:
        """The 1200-baud touch: switches the addressed slot to BOOTSEL when it works."""
        def open(self):
            touches.append((self.port, self.baudrate))
            if fake.touch_works:
                path = {"/dev/ttyACM0": "1-1.1", "/dev/ttyACM1": "1-1.2"}[self.port]
                fake.mode(path, "bootsel")

        def close(self):
            pass

    monkeypatch.setattr(rp2flash, "serial_factory", TouchSerial)
    link = roots["sysfs"] / "bus/usb/devices/1-1.1:1.0/tty/ttyACM0"
    instances = []

    class PtySerial(serial.Serial):
        def open(self):
            assert self.port == "/dev/ttyACM0"
            self.port = str(link.resolve())
            super().open()
            instances.append(self)

    owner = SerialOwner("SLOT1", poll_interval=0.02, serial_factory=PtySerial)
    thread = threading.Thread(target=owner.serve_forever)
    thread.start()
    stop = threading.Event()

    def booter():
        # Print a boot line once the owner reopens the tty after a pause.
        seen = False
        while not stop.is_set():
            try:
                state = client.state("SLOT1")
            except (OSError, client.ProtocolError):
                state = {}
            if state.get("paused"):
                seen = True
            elif seen and state.get("open"):
                os.write(master, b"booted 4321\r\n")
                seen = False
            time.sleep(0.01)

    boot_thread = threading.Thread(target=booter)
    try:
        assert owner.ready.wait(3)
        eventually(lambda: client.state("SLOT1")["open"])
        boot_thread.start()
        yield fake
    finally:
        stop.set()
        if boot_thread.is_alive():
            boot_thread.join(3)
        owner.stop()
        thread.join(3)
        os.close(master)
        os.close(slave)
        assert not thread.is_alive()
        assert all(not instance.is_open for instance in instances)


def loc(address):
    return ["--bus", "1", "--address", str(address)]


def last_history():
    return history.tail(1)[0]


def test_load_touch_two_bootsel_by_path_and_pause(bench, capsys):
    bench.mode("1-1.2", "bootsel")  # another board already in BOOTSEL on a different path
    firmware = bench.uf2()
    assert rp2flash.main(["load", "SLOT1", str(firmware)]) == 0
    out = capsys.readouterr()
    assert bench.touches == [("/dev/ttyACM0", 1200)]
    calls = bench.calls()
    assert [c["argv"] for c in calls] == [["load", "-v", "-x", str(firmware), *loc(8)]]
    assert calls[0]["path"] == "1-1.1" and calls[0]["paused_by"] == CLIENT
    assert "first-line: booted 4321" in out.out
    assert "100%" not in out.err and "OK" in out.err
    state = client.state("SLOT1")
    assert not state["paused"] and state["open"]
    record = last_history()
    data = firmware.read_bytes()
    assert record | {"ts": None, "seconds": None} == {
        "ts": None, "client": "client-a", "verb": "rp2-load", "slot": "SLOT1", "kind": "rp2",
        "usb_serial": "TESTSERIAL01", "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data), "rc": 0, "seconds": None, "note": None}


def test_info_already_bootsel_chosen_by_path(bench, capsys):
    bench.mode("1-1.1", "bootsel")
    bench.mode("1-1.2", "bootsel")
    assert rp2flash.main(["info", "SLOT2"]) == 0
    out = capsys.readouterr()
    assert bench.touches == []
    # Found in BOOTSEL (e.g. a blank board): info leaves it there and waits for no application.
    assert [c["argv"] for c in bench.calls()] == [["info", "-a", *loc(9)]]
    assert "left in BOOTSEL" in out.err
    assert "Program Information" in out.out  # info data on stdout
    assert "first-line: (board in BOOTSEL)" in out.out
    assert "bench-serial is not running for SLOT2" in out.err
    assert last_history()["kind"] == "rp2-bootsel"


def test_save_reboots_after(bench, capsys):
    target = bench.tmp_path / "backup.uf2"
    assert rp2flash.main(["save", "SLOT1", str(target)]) == 0
    assert target.read_bytes().startswith(b"UF2\n")
    calls = bench.calls()
    assert [c["argv"] for c in calls] == [["save", "-a", str(target), *loc(8)], ["reboot", *loc(8)]]
    assert all(c["paused_by"] == CLIENT for c in calls)
    out = capsys.readouterr()
    assert "Wrote" not in out.out and "first-line: booted 4321" in out.out


def test_touch_fails_picotool_reboot_fallback(bench, capsys):
    bench.touch_works = False
    assert rp2flash.main(["load", "SLOT1", str(bench.uf2())]) == 0
    calls = bench.calls()
    assert [c["argv"][:4] for c in calls] == [["reboot", "-f", "-u", "--bus"], ["load", "-v", "-x", str(bench.uf2())]]
    assert calls[0]["argv"] == ["reboot", "-f", "-u", *loc(7)] and calls[1]["argv"][-4:] == loc(8)
    assert "fallback" in last_history()["note"]


def test_never_bootsel_67(bench, capsys):
    bench.touch_works = False
    bench.scenario(reboot_u={"rc": 1, "then": None})
    assert rp2flash.main(["info", "SLOT1"]) == 67
    assert [c["argv"][0] for c in bench.calls()] == ["reboot"]
    assert not client.state("SLOT1")["paused"]
    assert last_history()["rc"] == 67


@pytest.mark.parametrize("out,expected", [("Loading\n  OK\nchecksum warning", 0), ("Loading failed", 68)])
def test_load_rc_254(bench, capsys, out, expected):
    bench.scenario(load={"rc": 254, "out": out, "then": "app"})
    assert rp2flash.main(["load", "SLOT1", str(bench.uf2())]) == expected
    err = capsys.readouterr().err
    assert ("filesystem-only UF2" in err) == (expected == 0)
    assert last_history()["rc"] == expected


def test_picotool_fails_68_reboots_and_records(bench, capsys):
    bench.scenario(load={"rc": 1, "out": "ERROR: verify failed", "then": None})
    assert rp2flash.main(["load", "SLOT1", str(bench.uf2())]) == 68
    assert [c["argv"][0] for c in bench.calls()] == ["load", "reboot"]
    record = last_history()
    assert record["rc"] == 68 and "rc=1" in record["note"]
    assert "first-line: booted 4321" in capsys.readouterr().out


def test_app_never_back_71(bench, capsys):
    bench.scenario(load={"then": None})
    assert rp2flash.main(["load", "SLOT1", str(bench.uf2())]) == 71
    assert not client.state("SLOT1")["paused"]
    assert last_history()["rc"] == 71
    assert "first-line" not in capsys.readouterr().out


def test_busy_65(bench):
    with locks.slot_lock("SLOT1"):
        assert rp2flash.main(["info", "SLOT1"]) == 65
    assert bench.calls() == [] and bench.touches == []
    assert last_history()["rc"] == 65


def test_pause_held_by_other_65(bench):
    with client.paused("SLOT1", "someone-else"):
        assert rp2flash.main(["info", "SLOT1"]) == 65
    assert bench.calls() == [] and bench.touches == []


def test_held_by_other_client_69(bench, capsys):
    holds.put("SLOT1", "client-b", 5, "soak test")
    assert rp2flash.main(["info", "SLOT1"]) == 69
    assert "client-b: soak test" in capsys.readouterr().err
    assert bench.calls() == [] and last_history()["rc"] == 69


def test_own_hold_allows(bench, capsys):
    holds.put("SLOT1", "client-a", 5, "mine")
    assert rp2flash.main(["info", "SLOT1"]) == 0


def test_absent_66(bench, roots):
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-1.9\n")
    assert rp2flash.main(["info", "SLOT2"]) == 66
    assert last_history()["rc"] == 66 and last_history()["kind"] is None


def test_wrong_kind_70(bench, roots):
    device = roots["sysfs"] / "bus/usb/devices/1-1.2"
    (device / "idVendor").write_text("10c4\n")
    (device / "idProduct").write_text("ea60\n")
    assert rp2flash.main(["info", "SLOT2"]) == 70
    assert last_history()["kind"] == "esp-uart" and bench.touches == []


@pytest.mark.parametrize("argv", [
    [], ["info"], ["flash", "SLOT1"], ["info", "SLOT1", "extra"], ["save", "SLOT1"],
    ["load", "SLOT1"], ["info", "SLOT9"], ["info", "slot1"],
    ["load", "SLOT1", "missing.uf2"], ["load", "SLOT1", "EMPTY"], ["load", "SLOT1", "BIN"]])
def test_usage_2(bench, argv):
    (bench.tmp_path / "empty.uf2").write_bytes(b"")
    (bench.tmp_path / "fw.bin").write_bytes(b"\x7fELF" + bytes(100))
    names = {"EMPTY": str(bench.tmp_path / "empty.uf2"), "BIN": str(bench.tmp_path / "fw.bin")}
    assert rp2flash.main([names.get(word, word) for word in argv]) == 2
    assert bench.calls() == [] and history.tail(1) == []


def test_bad_wait_env_2(bench, monkeypatch):
    monkeypatch.setenv("BENCH_RP2_APP_WAIT", "soon")
    assert rp2flash.main(["info", "SLOT1"]) == 2


def test_entry_point_usage():
    script = TESTS.parent / "bin/bench-flash-rp2"
    result = subprocess.run([str(script), "load", "SLOT1"], capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode == 2 and "need one" in result.stderr


def test_save_blank_bootsel_board_stays_in_bootsel(bench, capsys):
    """Argon W8 2026-11-21: a blank RP2040 in BOOTSEL made pico-info/pico-save fail with 71."""
    bench.mode("1-1.1", "bootsel")
    target = bench.tmp_path / "blank.uf2"
    assert rp2flash.main(["save", "SLOT1", str(target)]) == 0
    assert [c["argv"][0] for c in bench.calls()] == ["save"]
    assert "left in BOOTSEL" in capsys.readouterr().err
    assert last_history()["rc"] == 0
