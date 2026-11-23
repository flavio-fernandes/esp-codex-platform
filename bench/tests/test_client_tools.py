"""Client migration tests: fake SSH only; no network or physical devices."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
while args and args[0] in ('-o', '-i', '-F'):
    args = args[2:]
assert args.pop(0) == '--'
with open(os.environ['CALLS'], 'a') as out:
    out.write(json.dumps(args) + '\\n')
if len(args) > 1 and args[1] == 'flash-esp' and 'write-flash' in args:
    Path(os.environ['PAYLOAD']).write_bytes(sys.stdin.buffer.read())
sys.stdout.buffer.write(bytes.fromhex(os.environ.get('OUTPUT_HEX', '')))
print('fake diagnostic', file=sys.stderr)
code = os.environ.get('HEALTH_RC') if 'health' in args else None
sys.exit(int(code or os.environ.get('SSH_RC', '0')))
""")
    ssh.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BENCH_", "WORKBENCH_", "ESPWB_"))
           and k not in ("STATIC_ONLY", "ALLOW_NON_SLOT1", "SSH_CONFIG")}
    env.update(PATH=f"{bindir}:{env['PATH']}", CALLS=str(tmp_path / "calls"),
               PAYLOAD=str(tmp_path / "payload"), BENCH_CONFIG_FILE=str(tmp_path / "missing"))

    def run(tool, *args, settings=None, data=None):
        return subprocess.run([str(ROOT / "tools" / tool), *map(str, args)],
                              cwd=tmp_path, env=env | (settings or {}), input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    def calls():
        path = tmp_path / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    return run, calls, tmp_path


@pytest.mark.parametrize("tool,args,expected", [
    ("wb", ["status"], ["status"]),
    ("espwb-ssh", ["history", "10"], ["history", "10"]),
    ("espwb-monitor", ["30"], ["serial-follow", "SLOT1", "30"]),
    ("espwb-esptool", ["flash-id"], ["flash-esp", "SLOT1", "flash-id"]),
    ("espwb-esptool", ["chip-id"], ["flash-esp", "SLOT1", "chip-id"]),
])
def test_dispatch(client, tool, args, expected):
    run, calls, _ = client
    result = run(tool, *args)
    assert result.returncode == 0, result.stderr
    assert calls() == [["bench-agent", *expected]]


def test_stream_and_atomic_backup(client):
    run, calls, tmp = client
    payload = b'\x00\xfffirmware\n'
    image = tmp / "image with spaces.bin"
    image.write_bytes(payload)
    result = run("espwb-esptool", "write-flash", "0x0", image)
    assert result.returncode == 0, result.stderr
    assert (tmp / "payload").read_bytes() == payload
    assert calls()[-1] == ["bench-agent", "flash-esp", "SLOT1", "write-flash", "0x0"]
    output = tmp / "backup.bin"
    output.write_bytes(b"previous")
    result = run("espwb-esptool", "read-flash", "0x0", "ALL", output,
                 settings={"SSH_RC": "68", "OUTPUT_HEX": payload.hex()})
    assert result.returncode == 68
    assert output.read_bytes() == b"previous"
    assert not list(tmp.glob("*.part.*"))
    result = run("espwb-esptool", "read-flash", "0", "4096", output,
                 settings={"OUTPUT_HEX": payload.hex()})
    assert result.returncode == 0
    assert output.read_bytes() == payload
    assert output.stat().st_mode & 0o777 == 0o600
    assert calls()[-1] == ["bench-agent", "esp-save", "SLOT1", "4096"]


@pytest.mark.parametrize("args", [[], ["erase-flash"], ["read-flash", "0x1000", "4096", "out"],
    ["write-flash", "0", "missing"], ["write-flash", "0x0", "missing"], ["flash-id", "extra"]])
def test_invalid_flash_never_connects(client, args):
    run, calls, _ = client
    assert run("espwb-esptool", *args).returncode == 2
    assert calls() == []


@pytest.mark.parametrize("bad", ["status; id", "status\nhelp", "$(id)", "'status'", "x" * 401, "-F/config"])
def test_input_refusal(client, bad):
    run, calls, _ = client
    assert run("wb", bad).returncode == 2
    assert calls() == []


@pytest.mark.parametrize("seconds", ["-1", "601", "999999999999", "1.5", "abc"])
def test_monitor_bounds(client, seconds):
    run, calls, _ = client
    assert run("espwb-monitor", seconds).returncode == 2
    assert calls() == []


def test_slot_approval_and_legacy_monitor(client):
    run, calls, _ = client
    assert run("espwb-monitor", settings={"ESPWB_SLOT": "SLOT2"}).returncode == 2
    assert calls() == []
    result = run("espwb-monitor", settings={"ESPWB_SLOT": "SLOT2", "ALLOW_NON_SLOT1": "1",
                                          "ESPWB_MONITOR_MAX_TIME": "0"})
    assert result.returncode == 0
    assert calls() == [["bench-agent", "serial-follow", "SLOT2", "600"]]
    assert b"deprecated: ESPWB_SLOT" in result.stderr


@pytest.mark.parametrize("settings,host", [
    ({"WORKBENCH_AGENT_HOST": "old-alias"}, "old-alias"),
    ({"WORKBENCH_AGENT_HOST": "old-alias", "BENCH_AGENT": "new-alias"}, "new-alias"),
    ({"WORKBENCH_IP": "example.invalid", "WORKBENCH_USER": "forbidden"}, "wbagent@example.invalid"),
])
def test_deprecated_connection(client, settings, host):
    run, calls, _ = client
    result = run("wb", "help", settings=settings)
    assert result.returncode == 0, result.stderr
    assert calls() == [[host, "help"]]
    assert b"deprecated:" in result.stderr
    assert result.stdout == b""


def test_config_precedence(client):
    run, calls, tmp = client
    config = tmp / "defaults.env"
    config.write_text('BENCH_AGENT=file-alias\nBENCH_SLOT=SLOT2\nBENCH_ALLOW_NON_SLOT1=1\n')
    result = run("espwb-esptool", "flash-id", settings={"BENCH_CONFIG_FILE": str(config),
                 "WORKBENCH_AGENT_HOST": "explicit-old", "BENCH_SLOT": "SLOT1"})
    assert result.returncode == 0, result.stderr
    assert calls()[-1] == ["explicit-old", "flash-esp", "SLOT1", "flash-id"]
    result = run("wb", "status", settings={"BENCH_CONFIG_FILE": str(config),
                 "WORKBENCH_AGENT_HOST": "explicit-old", "BENCH_AGENT": "explicit-new"})
    assert result.returncode == 0
    assert calls()[-1] == ["explicit-new", "status"]


def test_camera(client):
    run, calls, tmp = client
    photo = tmp / "photo.jpg"
    payload = b'\xff\xd8test\xff\xd9'
    result = run("workbench-camera-capture", photo, settings={
        "WORKBENCH_CAMERA_WIDTH": "320", "BENCH_CAMERA_HEIGHT": "240",
        "BENCH_CAMERA_EXPOSURE": "40", "OUTPUT_HEX": payload.hex()})
    assert result.returncode == 0, result.stderr
    assert calls()[-1] == ["bench-agent", "snap", "exposure=40", "res=320x240"]
    assert photo.read_bytes() == payload
    archive = tmp / "frames.tar"
    result = run("workbench-camera-sequence", "4", "3", archive,
                 settings={"OUTPUT_HEX": b"fake archive".hex()})
    assert result.returncode == 0
    assert calls()[-1] == ["bench-agent", "seq", "4", "3"]
    assert archive.read_bytes() == b"fake archive"


@pytest.mark.parametrize("tool", ["validate-bench.sh", "validate-workbench.sh"])
def test_static_validation(client, tool):
    run, calls, _ = client
    result = run(tool, settings={"STATIC_ONLY": "1"})
    assert result.returncode == 0, result.stderr
    assert calls() == []
    assert b"deprecated" in result.stderr


@pytest.mark.parametrize("rc,label", [(1, b"DEGRADED"), (2, b"fake diagnostic"), (255, b"OFF")])
def test_health_failure_propagation(client, rc, label):
    run, calls, _ = client
    result = run("espwb-status", settings={"HEALTH_RC": str(rc)})
    assert result.returncode == rc
    assert label in result.stderr
    assert calls() == [["bench-agent", "status"], ["bench-agent", "health", "--json"]]


def test_validation_readonly(client):
    run, calls, _ = client
    result = run("validate-bench.sh")
    assert result.returncode == 0
    assert calls() == [["bench-agent", "help"], ["bench-agent", "status"],
                       ["bench-agent", "health", "--json"]]
