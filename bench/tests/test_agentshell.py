"""bench-agent-shell end to end: the real entry point as a subprocess, fake tools (and a fake
logger) on PATH, and a stub bench-serial socket. No hardware."""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import threading

import pytest
import bench_fake_tool as fake
from benchlib import history, holds, locks

TESTS = Path(__file__).resolve().parent
SHELL = TESTS.parent / "bin/bench-agent-shell"
TOOLS = ("bench-flash-rp2", "bench-flash-esp", "bench-camera", "bench-health", "sudo", "logger", "bench-wifi-provision")
UF2 = b"UF2\n" + bytes(508)


class StubSerial:
    """A bench-serial stand-in: one JSON request per connection, canned answers."""

    def __init__(self, path):
        self.requests = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn, conn.makefile("rwb") as stream:
                request = json.loads(stream.readline())
                self.requests.append(request)
                for answer in self.answer(request):
                    stream.write((json.dumps(answer) + "\n").encode())
                stream.flush()

    @staticmethod
    def answer(request):
        line = {"seq": 7, "ts": "2026-11-20T00:00:00.000Z", "text": "ready now"}
        op = request["op"]
        if op == "state":
            return [{"ok": True, "present": True, "kind": "rp2", "tty": "/dev/ttyACM0", "open": True,
                     "paused": False, "paused_by": None, "seq": 7}]
        if op in ("tail", "follow"):
            return [{**line, "text": "boot 1"}, {**line, "text": "boot 2"}, {"ok": True, "end": True}]
        if op == "wait":
            return [{"ok": True, "matched": True, "line": line} if request["pattern"] in line["text"]
                    else {"ok": True, "matched": False}]
        if op == "write":
            return [{"ok": True, "bytes": len(request["text"]) + 2}]
        if op == "reset":
            return [{"ok": True}]
        return [{"ok": False, "error": "bad-request", "detail": op}]


class Bench:
    def __init__(self, tmp_path, fake_dir):
        self.tmp_path, self.fake_dir = tmp_path, fake_dir
        self.env = {}
        self.serial = {}

    def run(self, command, stdin=b"", client="claude", **env):
        full = {**os.environ, "SSH_ORIGINAL_COMMAND": command, "SSH_CONNECTION": "192.0.2.10 50000 192.0.2.1 22",
                "BENCH_RUN": "run", **self.env, **env}
        args = [sys.executable, str(SHELL)] + ([client] if client is not None else [])
        return subprocess.run(args, input=stdin, capture_output=True, env=full, cwd=self.tmp_path,
                              timeout=60, check=False)

    def calls(self):
        log = self.fake_dir / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def audit(self):
        log = self.fake_dir / "audit.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def last_audit(self):
        argv = self.audit()[-1]
        assert argv[:3] == ["-t", "bench-agent", "--"]
        return argv[3]

    def stub(self, slot):
        self.serial[slot] = StubSerial(f"run/{slot}.sock")
        return self.serial[slot]


@pytest.fixture
def bench(roots, usb, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # sockaddr_un paths are short: bind relative to the run root
    fake_dir, bin_dir = tmp_path / "fake", tmp_path / "fakebin"
    fake_dir.mkdir()
    bin_dir.mkdir()
    for name in TOOLS:
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(TESTS)!r})\n"
                        f"import bench_fake_tool\nbench_fake_tool.main({name!r})\n")
        tool.chmod(0o755)
    usb(path="1-1.1", vid="2e8a", pid="000a", tty="ttyACM0")
    usb(path="1-1.2", vid="10c4", pid="ea60", tty="ttyUSB0", acm=False)
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-1.2\nSLOT3 1-1.3\n")
    (roots["etc"] / "bench.env").write_text(f"BENCH_CAMERA_DEVICE={tmp_path}/video9\nBENCH_MAX_FW_BYTES=4096\n")
    (roots["run"] / "SLOT3.lock").touch(mode=0o444)
    result = Bench(tmp_path, fake_dir)
    result.env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_TOOL_DIR": str(fake_dir)}
    return result


# ---- input rules -------------------------------------------------------------------------------

@pytest.mark.parametrize("char", list(";|&$`'\"<>(){}*?\\") + ["\n", "\t", "\x1b", "\u00e9"])
def test_forbidden_characters_refused_and_audited(bench, char):
    result = bench.run(f"serial-tail SLOT1{char}reboot")
    assert result.returncode == 2
    assert b"characters outside" in result.stderr and result.stdout == b""
    assert bench.calls() == []
    line = bench.last_audit()
    assert line.startswith("client=claude from=192.0.2.10 cmd=[serial-tail SLOT1")
    assert line.endswith("rc=2 err=[refused: characters outside [A-Za-z0-9 _.:=,/#@+-]]")
    assert all(" " <= c <= "~" for c in line)  # a refused input cannot split or forge a log line


def test_forbidden_characters_cover_the_brief(bench):
    for char in ";|&$`'\"<>(){}*?\\\n":
        assert bench.run("help" + char).returncode == 2
    assert bench.calls() == []


def test_length_limit(bench):
    assert bench.run("help" + " " * 396).returncode == 0
    result = bench.run("help" + " " * 397)
    assert result.returncode == 2 and b"too long" in result.stderr
    assert "rc=2 err=[command too long" in bench.last_audit()


def test_unknown_verb_prints_help_on_stderr(bench):
    result = bench.run("shell")
    assert result.returncode == 2 and result.stdout == b""
    assert b"unknown verb: shell" in result.stderr and b"bench-agent verbs" in result.stderr
    assert bench.last_audit() == "client=claude from=192.0.2.10 cmd=[shell] rc=2 err=[unknown verb: shell]"


@pytest.mark.parametrize("command", [
    "serial-tail slot1", "serial-tail SLOT", "pico-info SLOT1x", "reset ../SLOT1", "pico-save 1-1.1",
    "pico-info SLOT9", "flash-pico", "usb-reset SLOT01X", "hold SLOT9 5 test"])
def test_bad_slot_refused(bench, command):
    result = bench.run(command, stdin=UF2)
    assert result.returncode == 2 and bench.calls() == []
    assert b"slot" in result.stderr.lower()


@pytest.mark.parametrize("command", [
    "history 0", "history 201", "history x", "serial-tail SLOT1 1001", "serial-tail SLOT1 -1",
    "serial-wait SLOT1 301 ready", "serial-wait SLOT1 1.5 ready", "serial-wait SLOT1 10",
    "serial-follow SLOT1 601", "serial-follow SLOT1 0", "seq 0 1", "seq 61 1", "seq 2 601", "seq 2",
    "burst 0", "burst 301", "hold SLOT1 0 test", "hold SLOT1 721 test", "hold SLOT1 5",
    "flash-esp SLOT2 write-flash 65536", "flash-esp SLOT2 write-flash", "flash-esp SLOT2 erase-flash",
    "flash-esp SLOT2 chip-id now", "pico-info SLOT1 now", "power-cycle now", "status now",
    "health --quiet", "esp-save SLOT2 ALL more", "release SLOT1 now", "reset SLOT2 now"])
def test_bad_arguments_refused(bench, command):
    result = bench.run(command, stdin=b"\0" * 10)
    assert result.returncode == 2, result.stderr
    assert bench.calls() == [] and result.stdout == b""
    assert bench.last_audit().startswith(f"client=claude from=192.0.2.10 cmd=[{command}] rc=2 err=[")


def test_misconfigured_forced_command(bench):
    assert bench.run("help", client=None).returncode == 2
    result = bench.run("help", client="bad;name")
    assert result.returncode == 2 and b"misconfigured" in result.stderr
    assert bench.last_audit().startswith("client=bad?name from=192.0.2.10 cmd=[help] rc=2 err=[misconfigured")


def test_help_text(bench):
    result = bench.run("help")
    assert result.returncode == 0 and result.stderr == b""
    text = result.stdout.decode()
    for verb in ("status", "health", "history", "serial-tail", "serial-wait", "serial-follow", "serial-write",
                 "reset", "snap", "snap-stats", "seq", "burst", "pico-info", "pico-save", "flash-pico",
                 "flash-esp", "esp-save", "usb-reset", "power-cycle", "hold", "release"):
        assert f"  {verb} " in text
    assert bench.run("").stdout == result.stdout  # empty input means help
    assert bench.last_audit() == "client=claude from=192.0.2.10 cmd=[] rc=0"


# ---- dispatch to the tools ---------------------------------------------------------------------

def test_success_audit_line_format(bench):
    result = bench.run("pico-info SLOT1")
    assert result.returncode == 0
    assert result.stdout == b"Program Information\n name: blink\n"
    assert bench.calls() == [{"tool": "bench-flash-rp2", "argv": ["info", "SLOT1"], "client": "claude"}]
    assert bench.last_audit() == "client=claude from=192.0.2.10 cmd=[pico-info SLOT1] rc=0"
    assert len(bench.audit()) == 1  # one line per call


def test_pico_save_stdout_is_byte_identical_to_the_backup(bench):
    result = bench.run("pico-save SLOT1")
    assert result.returncode == 0
    assert result.stdout == fake.BACKUP  # first-line: went to stderr, not into the backup
    assert b"first-line: hello from rp2" in result.stderr and b"Saving file" in result.stderr
    call = bench.calls()[0]
    assert call["argv"][:2] == ["save", "SLOT1"] and call["argv"][2].endswith("/backup.uf2")
    assert call["client"] == "claude"
    assert not Path(call["argv"][2]).exists()  # the work directory is removed


def test_pico_save_failure_passes_rc_and_no_data(bench):
    result = bench.run("pico-save SLOT1", FAKE_RC_BENCH_FLASH_RP2="67")
    assert result.returncode == 67 and result.stdout == b""
    assert bench.last_audit().endswith("cmd=[pico-save SLOT1] rc=67")


def test_flash_pico_reads_stdin_and_audits_payload(bench):
    import hashlib
    result = bench.run("flash-pico SLOT1", stdin=UF2)
    assert result.returncode == 0 and result.stdout == b""
    assert b"first-line: hello from rp2" in result.stderr
    call = bench.calls()[0]
    assert call["argv"][:2] == ["load", "SLOT1"] and call["client"] == "claude"
    assert bench.last_audit() == (f"client=claude from=192.0.2.10 cmd=[flash-pico SLOT1] rc=0 "
                                  f"payload sha256={hashlib.sha256(UF2).hexdigest()} size={len(UF2)}")


def test_flash_pico_refuses_non_uf2_cap_and_empty(bench):
    result = bench.run("flash-pico SLOT1", stdin=b"\x7fELF" + bytes(100))
    assert result.returncode == 2 and b"not a UF2" in result.stderr
    result = bench.run("flash-pico SLOT1", stdin=UF2 + bytes(4096 - len(UF2) + 1))  # cap is 4096
    assert result.returncode == 2 and b"larger than 4096" in result.stderr
    assert "rc=2 err=[firmware larger than 4096 bytes]" in bench.last_audit()
    assert bench.run("flash-pico SLOT1", stdin=UF2 + bytes(4096 - len(UF2))).returncode == 0
    result = bench.run("flash-pico SLOT1")
    assert result.returncode == 2 and b"no firmware" in result.stderr
    assert [c["argv"][0] for c in bench.calls()] == ["load"]


def test_default_size_cap_is_16_mib(bench, roots):
    (roots["etc"] / "bench.env").write_text("BENCH_CAMERA_DEVICE=/dev/null\n")
    big = UF2 + bytes(16 * 1024 * 1024 - len(UF2) + 1)
    result = bench.run("flash-pico SLOT1", stdin=big)
    assert result.returncode == 2 and b"larger than 16777216" in result.stderr


def test_kind_checks(bench):
    assert bench.run("flash-pico SLOT2", stdin=UF2).returncode == 70
    assert bench.run("flash-esp SLOT1 chip-id").returncode == 70
    assert bench.run("esp-save SLOT1").returncode == 70
    assert bench.run("reset SLOT1").returncode == 70
    assert bench.run("flash-pico SLOT3", stdin=UF2).returncode == 66
    assert bench.run("esp-save SLOT3").returncode == 66
    assert bench.calls() == []


def test_flash_esp_identity_on_stdout(bench):
    for action in ("chip-id", "flash-id"):
        result = bench.run(f"flash-esp SLOT2 {action}")
        assert result.returncode == 0 and result.stdout == b"Chip is ESP32-D0WD-V3\n"
    assert [c["argv"] for c in bench.calls()] == [["SLOT2", "chip-id"], ["SLOT2", "flash-id"]]


def test_flash_esp_write_reads_stdin(bench):
    image = b"\xe9" + bytes(1000)
    result = bench.run("flash-esp SLOT2 write-flash 0x10000", stdin=image)
    assert result.returncode == 0 and result.stdout == b""
    assert b"first-line: hello from esp" in result.stderr
    call = bench.calls()[0]
    assert call["argv"][:3] == ["SLOT2", "write-flash", "0x10000"] and call["client"] == "claude"
    assert f"size={len(image)}" in bench.last_audit()
    result = bench.run("flash-esp SLOT2 write-flash 0x0", stdin=bytes(4097))
    assert result.returncode == 2 and len(bench.calls()) == 1


def test_esp_save_stdout_is_the_image_only(bench):
    result = bench.run("esp-save SLOT2 0x1000")
    assert result.returncode == 0 and result.stdout == fake.ESP_IMAGE
    assert b"read 2048 bytes" in result.stderr
    argv = bench.calls()[0]["argv"]
    assert argv[:2] == ["SLOT2", "read-flash"] and argv[2].endswith("/esp-save.bin") and argv[3] == "0x1000"
    assert bench.run("esp-save SLOT2").returncode == 0
    assert len(bench.calls()[1]["argv"]) == 3  # no SIZE: the tool reads the detected size


def test_usb_reset_and_power_cycle_through_sudo(bench):
    result = bench.run("usb-reset SLOT1")
    assert result.returncode == 0 and b"SLOT1 1-1.1" in result.stdout
    assert bench.run("power-cycle", client="hermes").returncode == 0
    assert bench.calls() == [
        {"tool": "sudo", "argv": ["-n", "/usr/local/sbin/bench-usb", "usb-reset", "SLOT1"], "client": "claude"},
        {"tool": "sudo", "argv": ["-n", "/usr/local/sbin/bench-usb", "power-cycle"], "client": "hermes"}]
    assert bench.run("usb-reset SLOT1", FAKE_RC_SUDO="69").returncode == 69


def test_health_passes_exit_code_through(bench):
    result = bench.run("health")
    assert result.returncode == 0 and result.stdout == b"bench OK; idle=true\n"
    result = bench.run("health --json", FAKE_RC_BENCH_HEALTH="1")
    assert result.returncode == 1 and b"DEGRADED" in result.stdout
    assert bench.calls()[1]["argv"] == ["--json"]
    assert bench.last_audit().endswith("cmd=[health --json] rc=1")


def test_snap_jpeg_on_stdout(bench):
    result = bench.run("snap exposure=40 res=640x480")
    assert result.returncode == 0 and result.stdout == fake.JPEG
    assert b"camera noise" in result.stderr
    argv = bench.calls()[0]["argv"]
    assert argv[0] == "snap" and argv[1].endswith("/snap.jpg") and argv[2:] == ["exposure=40", "res=640x480"]
    result = bench.run("snap", FAKE_RC_BENCH_CAMERA="3")
    assert result.returncode == 3 and result.stdout == b""


def test_snap_stats_json_and_audit(bench):
    result = bench.run("snap-stats exposure=40 threshold=100")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"lit_fraction": 0.5, "dominant": "R"}
    snap, stats = bench.calls()
    assert snap["argv"][2:] == ["exposure=40"] and stats["argv"][0] == "stats"
    assert stats["argv"][2:] == ["threshold=100"] and stats["argv"][1] == snap["argv"][1]
    assert bench.last_audit() == ('client=claude from=192.0.2.10 cmd=[snap-stats exposure=40 threshold=100] '
                                  'rc=0 stats={"lit_fraction": 0.5, "dominant": "R"}')


def members(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


def test_seq_tar_on_stdout(bench):
    result = bench.run("seq 3 0 exposure=40")
    assert result.returncode == 0
    assert members(result.stdout) == {f"./frame-{n:02d}.jpg": fake.JPEG for n in (1, 2, 3)}
    assert [c["argv"][2:] for c in bench.calls()] == [["exposure=40"]] * 3


def test_burst_tar_on_stdout(bench):
    result = bench.run("burst 4 res=320x240")
    assert result.returncode == 0
    assert members(result.stdout) == {f"./frame-{n:03d}.jpg": fake.JPEG for n in (1, 2, 3, 4)}
    argv = bench.calls()[0]["argv"]
    assert argv[0] == "burst" and argv[2:] == ["4", "res=320x240"]


# ---- serial verbs ------------------------------------------------------------------------------

def test_serial_tail(bench):
    stub = bench.stub("SLOT1")
    result = bench.run("serial-tail SLOT1 20")
    assert result.returncode == 0 and result.stdout == b"boot 1\nboot 2\n"
    assert stub.requests == [{"op": "tail", "lines": 20}]
    bench.run("serial-tail SLOT1")
    assert stub.requests[-1] == {"op": "tail", "lines": 50}


def test_serial_wait_match_and_no_match(bench):
    stub = bench.stub("SLOT1")
    result = bench.run("serial-wait SLOT1 5 ready now")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"matched": True, "line": {
        "seq": 7, "ts": "2026-11-20T00:00:00.000Z", "text": "ready now"}}
    assert stub.requests == [{"op": "wait", "pattern": "ready now", "timeout": 5}]
    result = bench.run("serial-wait SLOT1 0 panic")
    assert result.returncode == 1 and json.loads(result.stdout) == {"matched": False}


def test_serial_follow(bench):
    stub = bench.stub("SLOT1")
    result = bench.run("serial-follow SLOT1 600")
    assert result.returncode == 0 and result.stdout == b"boot 1\nboot 2\n"
    assert stub.requests == [{"op": "follow", "seconds": 600}]


def test_serial_write(bench):
    stub = bench.stub("SLOT1")
    result = bench.run("serial-write SLOT1 led on #1")
    assert result.returncode == 0 and b"wrote 11 bytes" in result.stderr and result.stdout == b""
    assert stub.requests == [{"op": "write", "text": "led on #1"}]


def test_serial_write_busy_is_65(bench):
    stub = bench.stub("SLOT1")
    with locks.slot_lock("SLOT1"):
        assert bench.run("serial-write SLOT1 hi").returncode == 65
    assert stub.requests == []


def test_serial_write_holds_the_slot_lock_so_no_hold_lands_mid_write(bench):
    """Codex 2026-11-22: a hold placed between the hold check and the write must not succeed."""
    stub, attempts = bench.stub("SLOT1"), []

    def answer(request):
        if request["op"] == "write":
            try:
                holds.put("SLOT1", "hermes", 5, "grabbed mid-write")
                attempts.append("placed")
            except locks.Busy:
                attempts.append("busy")
        return StubSerial.answer(request)

    stub.answer = answer
    assert bench.run("serial-write SLOT1 hi").returncode == 0
    assert attempts == ["busy"] and holds.get("SLOT1") is None


def test_serial_not_running_is_1(bench):
    result = bench.run("serial-tail SLOT2")
    assert result.returncode == 1 and b"not running" in result.stderr


def test_reset_esp_uart_records_history(bench):
    stub = bench.stub("SLOT2")
    assert bench.run("reset SLOT2").returncode == 0
    assert stub.requests == [{"op": "reset"}]
    record = history.tail(1)[0]
    assert (record["verb"], record["slot"], record["kind"], record["client"], record["rc"]) == (
        "reset", "SLOT2", "esp-uart", "claude", 0)


def test_reset_busy_is_65(bench):
    bench.stub("SLOT2")
    with locks.slot_lock("SLOT2"):
        assert bench.run("reset SLOT2").returncode == 65
    assert bench.serial["SLOT2"].requests == []


# ---- holds -------------------------------------------------------------------------------------

def test_hold_blocks_other_clients_mutations(bench):
    stub1, stub2 = bench.stub("SLOT1"), bench.stub("SLOT2")
    result = bench.run("hold SLOT2 30 soak test, do not touch")
    assert result.returncode == 0
    assert json.loads(result.stdout)["reason"] == "soak test, do not touch"
    assert holds.get("SLOT2")["client"] == "claude"
    result = bench.run("reset SLOT2", client="hermes")
    assert result.returncode == 69 and b"held by claude: soak test" in result.stderr
    assert history.tail(1)[0]["rc"] == 69
    assert bench.run("serial-write SLOT2 hi", client="hermes").returncode == 69
    assert bench.run("hold SLOT2 5 mine", client="hermes").returncode == 69
    assert bench.run("release SLOT2", client="hermes").returncode == 69
    assert stub2.requests == []
    # Read-only verbs still work, and the holder itself is not blocked.
    assert bench.run("serial-tail SLOT2", client="hermes").returncode == 0
    assert bench.run("reset SLOT2").returncode == 0
    status = bench.run("status", client="hermes").stdout.decode()
    assert "hold=claude until=" in status and "reason=[soak test, do not touch]" in status
    assert bench.run("release SLOT2").returncode == 0 and holds.get("SLOT2") is None
    assert bench.run("serial-write SLOT2 hi", client="hermes").returncode == 0
    assert stub1.requests[0]["op"] == "state"


def test_tools_get_the_client_for_their_own_hold_checks(bench):
    holds.put("SLOT1", "claude", 5, "mine")
    bench.run("pico-info SLOT1", client="hermes", FAKE_RC_BENCH_FLASH_RP2="69")
    assert bench.calls()[0]["client"] == "hermes"


# ---- status and history ------------------------------------------------------------------------

def test_status(bench, tmp_path):
    bench.stub("SLOT1")
    (tmp_path / "video9").touch()
    result = bench.run("status")
    assert result.returncode == 0
    lines = result.stdout.decode().splitlines()
    assert lines[0] == "SLOT1   path=1-1.1 kind=rp2 present=yes open=yes paused=no hold=-"
    assert lines[1] == "SLOT2   path=1-1.2 kind=esp-uart present=yes serial=not-running hold=-"
    assert lines[2] == "SLOT3   path=1-1.3 kind=- present=no serial=not-running hold=-"
    assert lines[3] == f"camera  present ({tmp_path}/video9) busy=no"
    assert [line.split(":")[0] for line in lines[4:]] == [
        "tool    picotool", "tool    esptool", "tool    v4l2-ctl", "tool    uhubctl"]


def test_history(bench):
    for n in range(30):
        history.append(client="claude", verb="rp2-load", slot="SLOT1", rc=0, note=str(n))
    result = bench.run("history 3")
    assert result.returncode == 0
    assert [json.loads(line)["note"] for line in result.stdout.splitlines()] == ["27", "28", "29"]
    assert len(bench.run("history").stdout.splitlines()) == 20


def test_wifi_provision_passes_credentials_on_stdin_only(bench):
    creds = b"bench-ssid\nsuper-secret-pass\n"
    result = bench.run("wifi-provision SLOT2", stdin=creds)
    assert result.returncode == 0 and b"state: provisioned" in result.stdout
    call = bench.calls()[0]
    assert call["tool"] == "bench-wifi-provision" and call["argv"] == ["SLOT2"] and call["client"] == "claude"
    assert call["stdin_size"] == len(creds)
    audit = bench.last_audit()
    assert f"credentials size={len(creds)}" in audit
    assert "super-secret-pass" not in audit and "bench-ssid" not in audit
    assert b"super-secret-pass" not in result.stdout + result.stderr


def test_wifi_provision_refusals(bench):
    assert bench.run("wifi-provision SLOT2", stdin=b"").returncode == 2
    assert bench.run("wifi-provision SLOT2", stdin=bytes(513)).returncode == 2
    assert bench.run("wifi-provision SLOT2 extra", stdin=b"a\nb\n").returncode == 2
    assert bench.calls() == []
