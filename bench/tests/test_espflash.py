"""bench-flash-esp with a fake esptool, a fake sysfs and W2's real serial owner on a pty.
No hardware. SLOT1 is an esp-uart board (CP2102, owner running); SLOT2 a native-USB ESP32-S3."""
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
from benchlib import espflash, history, holds, locks
from benchlib import serialclient as client
from benchlib.serialowner import SerialOwner

TESTS = Path(__file__).resolve().parent
LIB = TESTS.parent / "lib"
PAUSER = "bench-flash-esp client-a"
HEAD = ["--chip", "auto", "--before", "default-reset", "--after", "hard-reset"]


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class Bench:
    def __init__(self, tmp_path, fake_dir, tool):
        self.tmp_path, self.fake_dir, self.tool = tmp_path, fake_dir, tool

    def scenario(self, **specs):
        (self.fake_dir / "scenario.json").write_text(json.dumps(
            {key.replace("_", "-"): value for key, value in specs.items()}))

    def calls(self):
        log = self.fake_dir / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def argv(self):
        return [call["argv"] for call in self.calls()]

    def image(self, data=b"\xe9" + bytes(1279), name="fw.bin"):
        path = self.tmp_path / name
        path.write_bytes(data)
        return path


@pytest.fixture
def bench(roots, usb, tmp_path, monkeypatch):
    # sockaddr_un paths are limited to 108 bytes: keep the run root relative.
    monkeypatch.chdir(roots["run"].parent)
    monkeypatch.setenv("BENCH_RUN", "run")
    master, slave = os.openpty()
    pty = os.ttyname(slave)
    usb(path="1-1.1", vid="10c4", pid="ea60", tty="ttyUSB0", acm=False)
    usb(path="1-1.2", vid="303a", pid="1001", tty="ttyACM1")
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-1.2\n")
    fake_dir = tmp_path / "esptool"
    (fake_dir / "bin").mkdir(parents=True)
    tool = fake_dir / "bin/esptool"
    tool.write_text(f"#!{sys.executable}\nimport sys\nsys.path[:0] = [{str(TESTS)!r}, {str(LIB)!r}]\n"
                    "from bench_fake_esptool import main\nsys.exit(main(sys.argv[1:]))\n")
    tool.chmod(0o755)
    for key, value in {"BENCH_ESPTOOL": str(tool), "FAKE_ESP_DIR": str(fake_dir),
                       "BENCH_CLIENT": "client-a", "BENCH_ESP_REENUM_WAIT": "1",
                       "BENCH_ESP_USB_SETTLE": "0", "BENCH_ESP_APP_WAIT": "1",
                       "BENCH_ESP_OPEN_WAIT": "3", "BENCH_ESP_LINE_WAIT": "3",
                       "BENCH_ESP_TOOL_TIMEOUT": "30"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(espflash, "sleep", lambda seconds: None)
    instances = []

    class PtySerial(serial.Serial):
        def open(self):
            assert self.port == "/dev/ttyUSB0"
            self.port = pty
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
        yield Bench(tmp_path, fake_dir, str(tool))
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


def last_history():
    return history.tail(1)[0]


def test_write_flash_uart_identity_first_paused_first_line(bench, capsys):
    firmware = bench.image()
    assert espflash.main(["SLOT1", "write-flash", "0x10000", str(firmware)]) == 0
    out = capsys.readouterr()
    assert bench.argv() == [
        ["--port", "/dev/ttyUSB0", *HEAD, "flash-id"],
        ["--port", "/dev/ttyUSB0", *HEAD, "--baud", "921600", "write-flash", "0x10000", str(firmware)]]
    assert all(call["paused_by"] == PAUSER for call in bench.calls())
    assert "first-line: booted 4321" in out.out
    assert "ESP32-S3" in out.err and "identity: chip=ESP32-S3 (QFN56) (revision v0.2) flash=4194304" in out.err
    assert "MAC" not in out.err and "aa:bb" not in out.err and "80.0%" not in out.err
    assert "Hash of data verified." in out.err
    state = client.state("SLOT1")
    assert not state["paused"] and state["open"]
    data = firmware.read_bytes()
    assert last_history() | {"ts": None, "seconds": None} == {
        "ts": None, "client": "client-a", "verb": "esp-write-flash", "slot": "SLOT1",
        "kind": "esp-uart", "usb_serial": "TESTSERIAL01", "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data), "rc": 0, "seconds": None, "note": None}


@pytest.mark.parametrize("action", ["chip-id", "flash-id"])
def test_identity_verbs_data_on_stdout_no_first_line(bench, capsys, action):
    assert espflash.main(["SLOT1", action]) == 0
    out = capsys.readouterr()
    assert bench.argv() == [["--port", "/dev/ttyUSB0", *HEAD, action]]
    assert "Chip type:" in out.out and "first-line" not in out.out
    assert last_history()["verb"] == "esp-" + action and not client.state("SLOT1")["paused"]


def test_read_flash_default_size_is_detected_size(bench, capsys):
    target = bench.tmp_path / "backup.bin"
    assert espflash.main(["SLOT1", "read-flash", str(target)]) == 0
    assert bench.argv()[1] == ["--port", "/dev/ttyUSB0", *HEAD, "--baud", "921600", "read-flash", "0", "0x400000", str(target)]
    assert target.stat().st_size == 4 * 1024 * 1024
    assert "read 4194304 bytes" in capsys.readouterr().out
    record = last_history()
    assert record["verb"] == "esp-read-flash" and record["size"] == 4194304
    assert record["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_read_flash_all_when_size_not_detected(bench):
    bench.scenario(flash_id=[{"out": "Chip type: ESP32\nFlash size unknown"}])
    target = bench.tmp_path / "backup.bin"
    assert espflash.main(["SLOT1", "read-flash", str(target)]) == 0
    assert bench.argv()[1][-4:] == ["read-flash", "0", "ALL", str(target)]


def test_read_flash_explicit_size_and_over_size(bench):
    target = bench.tmp_path / "backup.bin"
    assert espflash.main(["SLOT1", "read-flash", str(target), "0x1000"]) == 0
    assert bench.argv()[1][-4:] == ["read-flash", "0", "0x1000", str(target)]
    assert espflash.main(["SLOT1", "read-flash", str(target), "0x800000"]) == 2
    assert bench.argv()[-1][-1] == "flash-id" and last_history()["rc"] == 2


def test_write_refuses_file_over_flash_size(bench, capsys):
    big = bench.image(b"\xe9" + bytes(4 * 1024 * 1024), name="big.bin")
    assert espflash.main(["SLOT1", "write-flash", "0x0", str(big)]) == 2
    assert [call["command"] for call in bench.calls()] == ["flash-id"]
    assert "do not fit" in capsys.readouterr().err and last_history()["rc"] == 2


def test_esptool_failure_68_uart_single_attempt(bench, capsys):
    bench.scenario(write_flash=[{"rc": 2, "out": "A fatal error occurred: Failed to connect"}])
    assert espflash.main(["SLOT1", "write-flash", "0x10000", str(bench.image())]) == 68
    assert [call["command"] for call in bench.calls()] == ["flash-id", "write-flash"]
    record = last_history()
    assert record["rc"] == 68 and "rc=2" in record["note"]
    assert not client.state("SLOT1")["paused"]


def test_identity_failure_68_no_write(bench):
    bench.scenario(flash_id=[{"rc": 2, "out": "A fatal error occurred: No serial data received."}])
    assert espflash.main(["SLOT1", "write-flash", "0x10000", str(bench.image())]) == 68
    assert [call["command"] for call in bench.calls()] == ["flash-id"]


def test_esp_usb_reenumeration_retry_no_reset(bench, capsys):
    # The S3 drops off the bus during the first write and comes back as ttyACM2.
    bench.scenario(write_flash=[{"rc": 2, "out": "A fatal error occurred: device disconnected",
                                 "reenum": "ttyACM2"}, {}])
    firmware = bench.image()
    assert espflash.main(["SLOT2", "write-flash", "0x0", str(firmware)]) == 0
    assert bench.argv() == [
        ["--port", "/dev/ttyACM1", *HEAD, "flash-id"],
        ["--port", "/dev/ttyACM1", *HEAD, "write-flash", "0x0", str(firmware)],
        ["--port", "/dev/ttyACM2", "--chip", "auto", "--before", "no-reset", "--after", "hard-reset",
         "write-flash", "0x0", str(firmware)]]
    assert all(call["paused_by"] == "no-owner" for call in bench.calls())
    out = capsys.readouterr()
    assert "attempt 2/3, before=no-reset" in out.err and "first-line: (no output yet)" in out.out
    assert last_history()["rc"] == 0 and last_history()["kind"] == "esp-usb"


def test_esp_usb_bounded_retries_last_without_stub(bench):
    bench.scenario(flash_id=[{"rc": 2, "out": "A fatal error occurred: Failed to connect"}])
    assert espflash.main(["SLOT2", "flash-id"]) == 68
    argv = bench.argv()
    assert len(argv) == 3
    assert [a[a.index("--before") + 1] for a in argv] == ["default-reset", "no-reset", "no-reset"]
    assert ["--no-stub" in a for a in argv] == [False, False, True]
    assert argv[2] == ["--port", "/dev/ttyACM1", "--chip", "auto", "--before", "no-reset",
                       "--after", "hard-reset", "--no-stub", "flash-id"]


def test_esp_usb_left_in_rom_71(bench, capsys):
    bench.scenario(write_flash=[{"rom": True}])
    assert espflash.main(["SLOT2", "write-flash", "0x0", str(bench.image())]) == 71
    assert "still in ROM download mode" in capsys.readouterr().err
    assert last_history()["rc"] == 71


def test_esp_usb_tty_absent_waits_then_66(bench, roots):
    (roots["sysfs"] / "bus/usb/devices/1-1.2:1.0/tty/ttyACM1").rmdir()
    assert espflash.main(["SLOT2", "chip-id"]) == 66
    assert bench.calls() == [] and last_history()["rc"] == 66


def test_busy_65(bench):
    with locks.slot_lock("SLOT1"):
        assert espflash.main(["SLOT1", "chip-id"]) == 65
    assert bench.calls() == [] and last_history()["rc"] == 65


def test_pause_held_by_other_65(bench):
    with client.paused("SLOT1", "someone-else"):
        assert espflash.main(["SLOT1", "chip-id"]) == 65
    assert bench.calls() == []


def test_held_by_other_client_69(bench, capsys):
    holds.put("SLOT1", "client-b", 5, "soak test")
    assert espflash.main(["SLOT1", "write-flash", "0x0", str(bench.image())]) == 69
    assert "client-b: soak test" in capsys.readouterr().err
    assert bench.calls() == [] and last_history()["rc"] == 69


def test_absent_66(bench, roots):
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-1.9\n")
    assert espflash.main(["SLOT2", "chip-id"]) == 66
    assert last_history()["rc"] == 66 and last_history()["kind"] is None


def test_wrong_kind_70(bench, roots):
    device = roots["sysfs"] / "bus/usb/devices/1-1.2"
    (device / "idVendor").write_text("2e8a\n")
    (device / "idProduct").write_text("000a\n")
    assert espflash.main(["SLOT2", "chip-id"]) == 70
    assert last_history()["kind"] == "rp2" and bench.calls() == []


@pytest.mark.parametrize("argv", [
    [], ["SLOT1"], ["SLOT1", "erase-flash"], ["SLOT1", "chip-id", "extra"], ["SLOT9", "chip-id"],
    ["slot1", "chip-id"], ["SLOT1", "read-flash"], ["SLOT1", "read-flash", "NODIR/x.bin"],
    ["SLOT1", "read-flash", "OUT", "4MB"], ["SLOT1", "read-flash", "OUT", "0"],
    ["SLOT1", "write-flash", "FW"], ["SLOT1", "write-flash", "10000", "FW"],
    ["SLOT1", "write-flash", "0x1g", "FW"], ["SLOT1", "write-flash", "0x0", "EMPTY"],
    ["SLOT1", "write-flash", "0x0", "missing.bin"]])
def test_usage_2(bench, argv):
    (bench.tmp_path / "empty.bin").write_bytes(b"")
    names = {"EMPTY": str(bench.tmp_path / "empty.bin"), "FW": str(bench.image()),
             "OUT": str(bench.tmp_path / "out.bin"), "NODIR/x.bin": str(bench.tmp_path / "no/x.bin")}
    assert espflash.main([names.get(word, word) for word in argv]) == 2
    assert bench.calls() == [] and history.tail(1) == []


def test_bad_wait_env_2(bench, monkeypatch):
    monkeypatch.setenv("BENCH_ESP_ATTEMPTS", "0")
    assert espflash.main(["SLOT1", "chip-id"]) == 2


@pytest.mark.parametrize("baud", ["fast", "0", "1e6", "99999999"])
def test_bad_baud_env_is_usage_not_a_traceback(bench, monkeypatch, baud):
    monkeypatch.setenv("BENCH_ESP_BAUD", baud)
    assert espflash.main(["SLOT1", "chip-id"]) == 2


def test_entry_point_usage():
    script = TESTS.parent / "bin/bench-flash-esp"
    result = subprocess.run([str(script), "SLOT1", "write-flash", "0x0"], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 2 and "usage" in result.stderr


def test_uart_bulk_baud_override(bench, monkeypatch):
    """Argon W9 2026-11-21: at 115200 a 4 MB read took ~6.5 min; bulk transfers use 921600 (env override)."""
    monkeypatch.setenv("BENCH_ESP_BAUD", "460800")
    target = bench.tmp_path / "save.bin"
    assert espflash.main(["SLOT1", "read-flash", str(target), "0x1000"]) == 0
    assert "--baud" in bench.argv()[1] and bench.argv()[1][bench.argv()[1].index("--baud") + 1] == "460800"
    assert "--baud" not in bench.argv()[0]  # identity stays at the default speed


def test_fast_baud_transfer_error_falls_back_to_115200(bench, capsys):
    """Argon W9 2026-11-21: a clone CP2102 failed every read at 230400-921600 and passed at 115200."""
    bench.scenario(read_flash=[{"rc": 2, "out": "Reading from 0x00031000 \u2501 4.8% 196.00kB/4.00MB [2s]\n"
                                            "A fatal error occurred: Packet content transfer stopped"}, {}])
    target = bench.tmp_path / "save.bin"
    assert espflash.main(["SLOT1", "read-flash", str(target), "0x1000"]) == 0
    reads = [argv for argv in bench.argv() if "read-flash" in argv]
    assert "--baud" in reads[0] and "--baud" not in reads[1]
    err = capsys.readouterr().err
    assert "retried at the 115200 default" in err
    assert "Reading from" not in err  # esptool 5 progress bars are filtered


@pytest.mark.parametrize("size, sent", [("010", "0xa"), ("4096", "0x1000")])
def test_read_flash_size_forms_are_normalized(bench, size, sent):
    """A decimal SIZE with a leading zero crashed int(size, 0) before any history was written."""
    target = bench.tmp_path / "save.bin"
    assert espflash.main(["SLOT1", "read-flash", str(target), size]) == 0
    assert bench.argv()[1][-4:] == ["read-flash", "0", sent, str(target)]


@pytest.mark.parametrize("size", ["0", "000", "0x0", "0x40000001", "9" * 40, "-1", "1e3"])
def test_read_flash_bad_sizes_are_usage_errors(bench, size):
    assert espflash.main(["SLOT1", "read-flash", str(bench.tmp_path / "save.bin"), size]) == 2
