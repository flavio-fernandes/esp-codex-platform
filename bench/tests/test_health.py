"""Health probes against fake sysfs, PATH tools and serial state, never hardware."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from benchlib import health, history, holds, locks


@pytest.fixture
def good(roots, usb, monkeypatch):
    monkeypatch.chdir(roots["run"].parent)
    monkeypatch.setenv("BENCH_RUN", "run")
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-1.2\n")
    usb(tty="ttyACM0")
    (roots["sysfs"] / "bus/usb/devices/2-1").mkdir()
    camera = Path("camera-test")
    camera.touch()
    (roots["etc"] / "bench.env").write_text(
        "BENCH_HUB_PATHS=1-1.1 2-1\nBENCH_CAMERA_DEVICE=camera-test\n")
    temp = roots["sysfs"] / "class/thermal/thermal_zone0/temp"
    temp.parent.mkdir(parents=True)
    temp.write_text("42000\n")
    tools = Path("tools")
    tools.mkdir()
    monkeypatch.setenv("PATH", str(tools.resolve()))
    def tool(name, body):
        path = tools / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path
    tool("vcgencmd", "printf 'throttled=0x0\\n'")
    tool("systemctl", "printf 'active\\n'")
    tool("journalctl", "printf 'USB disconnect\\n'")
    tool("dmesg", "printf 'over-current\\n'")
    tool("logger", "printf '%s\\n' \"$*\" >> logger-output")
    monkeypatch.setattr(health.serialclient, "state", lambda slot: {"open": slot == "SLOT1", "paused": False})
    monkeypatch.setattr(health, "probe_disk", lambda: 50.0)
    monkeypatch.setattr(health, "probe_uptime", lambda: 1234.0)
    return tool


def test_all_good(good):
    result = health.collect(now=1700000000)
    assert result == {"ok": True, "ts": "2023-11-14T22:13:20.000Z", "uptime_s": 1234.0,
        "throttled": "0x0", "temp_c": 42.0, "disk_free_pct": 50.0, "hub_present": True,
        "camera_present": True, "slots": {
            "SLOT1": {"present": True, "kind": "rp2", "tty": "/dev/ttyACM0",
                      "serial_service": "active", "open": True, "paused": False},
            "SLOT2": {"present": False, "kind": None, "tty": None,
                      "serial_service": "active", "open": False, "paused": False}},
        "kernel": {"error -71": 0, "error -110": 0, "USB disconnect": 1, "over-current": 0},
        "holds": {}, "locks_busy": [], "idle": True, "problems": [], "notes": []}


@pytest.mark.parametrize("probe,value,reason", [
    ("throttled", "0x10000", "throttled is nonzero"),
    ("temp_c", 80, "temperature >= 80 C"),
    ("disk_free_pct", 9.99, "disk free < 10%"),
    ("hub_present", False, "hub missing"),
    ("camera_present", False, "camera missing"),
    ("serial_service", "inactive", "SLOT1: serial monitor should be open"),
    ("state", {"open": False, "paused": False}, "SLOT1: serial monitor should be open"),
    ("state", {"open": False, "paused": True}, "SLOT1: serial monitor should be open")])
def test_problem(good, probe, value, reason):
    result = health.collect(probes={probe: lambda *args: value})
    assert not result["ok"] and result["problems"] == [reason]


@pytest.mark.parametrize("vid,pid,monitor,problem", [
    ("303a", "0001", "auto", False), ("303a", "0001", "on", True),
    ("ffff", "ffff", "on", False), ("2e8a", "0003", "on", False),
    ("10c4", "ea60", "auto", True), ("2e8a", "000a", "off", False)])
def test_kind_policy(good, roots, vid, pid, monitor, problem):
    device = roots["sysfs"] / "bus/usb/devices/1-1.1"
    (device / "idVendor").write_text(vid)
    (device / "idProduct").write_text(pid)
    (roots["etc"] / "slots.conf").write_text(f"SLOT1 1-1.1 monitor={monitor}\n")
    result = health.collect(probes={"state": lambda _: {"open": False}})
    assert result["ok"] is not problem


def test_kernel_baseline_growth_and_reset(good, roots, capsys):
    good("journalctl", "printf 'error -71\\nerror -710\\nerror -110\\n'")
    assert health.main(["--quiet"]) == 0  # first observation is the baseline
    assert capsys.readouterr().out == ""
    assert health.collect()["ok"]
    good("journalctl", "printf 'error -71\\nerror -71\\nerror -110\\nerror -110\\n'")
    assert health.main(["--quiet"]) == 1
    result = json.loads((roots["run"] / "health.json").read_text())
    assert result["problems"] == ["kernel error -71 count grew", "kernel error -110 count grew"]
    assert Path("logger-output").read_text().count("-t bench-health") == 2
    assert health.main(["--quiet"]) == 0
    good("journalctl", "printf ''")
    assert health.main(["--quiet"]) == 0  # counter decrease/reboot
    good("journalctl", "printf 'error -71\\n'")
    assert health.main(["--quiet"]) == 1


@pytest.mark.parametrize("age,idle", [(599, False), (600, False), (601, True)])
def test_history_idle(good, age, idle):
    now = 1700000000
    history.append(ts=datetime.fromtimestamp(now - age, timezone.utc).isoformat())
    assert health.collect(now=now)["idle"] is idle


def test_holds_idle(good, roots):
    holds.put("SLOT1", "test-client", 10, "testing")
    assert not health.collect()["idle"]
    path = roots["state"] / "holds/SLOT1.json"
    value = json.loads(path.read_text())
    value["until"] = 1
    path.write_text(json.dumps(value))
    assert health.collect()["idle"] and health.collect()["holds"] == {}


@pytest.mark.parametrize("name", ["SLOT1", "camera"])
def test_locks_idle(good, name):
    lock = locks.camera_lock() if name == "camera" else locks.slot_lock(name)
    with lock:
        result = health.collect()
        assert not result["idle"] and result["locks_busy"] == [name]
    assert health.collect()["idle"]


def test_missing_tools_and_socket(good, monkeypatch):
    Path("tools/vcgencmd").unlink()
    Path("tools/journalctl").unlink()
    result = health.collect()
    assert result["throttled"] is None and result["notes"]
    assert result["kernel"]["over-current"] == 1  # dmesg fallback
    Path("tools/dmesg").unlink()
    Path("tools/systemctl").unlink()
    def missing(_):
        raise health.serialclient.NotRunning("missing socket")
    monkeypatch.setattr(health.serialclient, "state", missing)
    result = health.collect()
    assert result["kernel"] is None and not result["ok"]
    assert result["slots"]["SLOT1"]["open"] is False
    assert any("missing socket" in note for note in result["notes"])
    assert len(result["notes"]) == 6


def test_command_timeout(good, monkeypatch):
    monkeypatch.setattr(health, "PROBE_TIMEOUT", 0.05)
    good("vcgencmd", "exec /bin/sleep 2")
    result = health.collect()
    assert result["throttled"] is None
    assert any("timed out" in note for note in result["notes"])


def test_hub_all_paths_and_camera(good, roots):
    (roots["sysfs"] / "bus/usb/devices/2-1").rmdir()
    Path("camera-test").unlink()
    result = health.collect()
    assert result["problems"] == ["hub missing", "camera missing"]


def test_threshold_boundaries(good):
    assert health.collect(probes={"temp_c": lambda: 79.99, "disk_free_pct": lambda: 10})["ok"]


def test_bad_previous_and_failed_probe_preserve_baseline(good, roots):
    previous = roots["run"] / "health.prev.json"
    previous.write_text("[]")
    assert any("previous kernel" in n for n in health.collect()["notes"])
    good("journalctl", "exit 1")
    good("dmesg", "exit 1")
    assert health.main(["--quiet"]) == 0
    assert previous.read_text() == "[]"


def test_cli_outputs_and_errors(good, roots, capsys, monkeypatch):
    assert health.main(["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads((roots["run"] / "health.json").read_text())
    assert (roots["run"] / "health.json").stat().st_mode & 0o777 == 0o664
    assert health.main(["--quiet", "--json"]) == 0
    assert capsys.readouterr().out == ""
    good("vcgencmd", "printf 'throttled=0x1\\n'")
    assert health.main([]) == 1
    assert "DEGRADED" in capsys.readouterr().out
    Path("tools/logger").unlink()
    assert health.main([]) == 1
    assert "logger unavailable" in capsys.readouterr().err
    (roots["etc"] / "slots.conf").write_text("invalid")
    assert health.main([]) == 2
    assert "cannot run" in capsys.readouterr().err


def test_atomic_failure_preserves_snapshot(good, roots, monkeypatch):
    path = roots["run"] / "health.json"
    path.write_text("old snapshot")
    def fail(*args):
        raise OSError("injected rename failure")
    monkeypatch.setattr(os, "replace", fail)
    assert health.main([]) == 2
    assert path.read_text() == "old snapshot"
    assert list(roots["run"].glob("health.json.*")) == []


def test_real_entrypoint(good, roots, capsys):
    # Native USB with auto monitoring legitimately has no open serial socket.
    device = roots["sysfs"] / "bus/usb/devices/1-1.1"
    (device / "idVendor").write_text("303a")
    entrypoint = Path(__file__).resolve().parents[1] / "bin/bench-health"
    result = subprocess.run([sys.executable, str(entrypoint), "--json"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] and not data["slots"]["SLOT1"]["open"]
    with capsys.disabled():
        print("FIXTURE --json: " + result.stdout.strip())
    Path("camera-test").unlink()
    assert subprocess.run([sys.executable, str(entrypoint), "--quiet"],
                          capture_output=True, timeout=10).returncode == 1
    (roots["etc"] / "slots.conf").unlink()
    assert subprocess.run([sys.executable, str(entrypoint)],
                          capture_output=True, timeout=10).returncode == 2


def test_timer_and_service_contract():
    base = Path(__file__).resolve().parents[1] / "systemd"
    timer = (base / "bench-health.timer").read_text()
    service = (base / "bench-health.service").read_text()
    assert "OnBootSec=2min" in timer and "OnUnitActiveSec=5min" in timer
    assert "Type=oneshot" in service and "User=bench" in service
    assert "ExecStart=/usr/local/bin/bench-health --quiet" in service
