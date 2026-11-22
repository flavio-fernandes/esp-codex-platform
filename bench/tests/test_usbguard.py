"""bench-usb guards and actions on a fake sysfs, with a fake uhubctl on PATH. No hardware.

Topology: root hub usb1 -> hub 1-1 (the bench hub) -> SLOT1 at 1-1.1; SLOT2 at 1-2 sits on a
root-hub port; SLOT3 at 1-1.3 is a board with a mass-storage interface."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from benchlib import history, holds, locks, usbguard

TESTS = Path(__file__).resolve().parent


@pytest.fixture
def hub(roots, usb, tmp_path, monkeypatch):
    devices = roots["sysfs"] / "bus/usb/devices"
    usb(path="1-1", vid="05e3", pid="0610")
    usb(path="1-1.1", vid="10c4", pid="ea60", tty="ttyUSB0", acm=False)
    usb(path="1-2", vid="303a", pid="1001", tty="ttyACM0")
    for port in (1, 2, 3, 4):
        control = devices / "1-1:1.0" / f"1-1-port{port}"
        control.mkdir(parents=True)
        (control / "disable").write_text("0\n")
    root_port = devices / "usb1:1.0/usb1-port2"
    root_port.mkdir(parents=True)
    (root_port / "disable").write_text("0\n")
    (roots["etc"] / "slots.conf").write_text("SLOT1 1-1.1\nSLOT2 1-2\nSLOT3 1-1.3\n")
    (roots["etc"] / "bench.env").write_text(
        "BENCH_POWER_CYCLE=1\nBENCH_HUB_LOCATION=1-1\nBENCH_HUB_PATHS=1-1\n")
    (roots["run"] / "SLOT3.lock").touch(mode=0o444)
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    uhubctl = bin_dir / "uhubctl"
    uhubctl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{tmp_path}/uhubctl.log"\n'
                       'echo "Current status for hub 1-1 [05e3:0610, USB 2.10, 4 ports, ppps]"\n'
                       'exit "${FAKE_UHUBCTL_RC:-0}"\n')
    uhubctl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("BENCH_CLIENT", "client-a")
    # As root, main() drops the BENCH_* overrides and would act on the real /sys: always run as
    # a non-root user here, so the fake tree is used even when pytest itself runs as root.
    monkeypatch.setattr(usbguard.os, "geteuid", lambda: 1000)
    pauses = []
    monkeypatch.setattr(usbguard, "sleep", lambda seconds: pauses.append(
        (seconds, (devices / "1-1:1.0/1-1-port1/disable").read_text())))
    monkeypatch.setattr(usbguard, "REAPPEAR_WAIT", 0.2)

    class Hub:
        log = tmp_path / "uhubctl.log"
        disable = devices / "1-1:1.0/1-1-port1/disable"
        env = roots["etc"] / "bench.env"

        @staticmethod
        def uhubctl_calls():
            return Hub.log.read_text().splitlines() if Hub.log.exists() else []

        @staticmethod
        def untouched():
            return (all((devices / f"1-1:1.0/1-1-port{n}/disable").read_text() == "0\n" for n in (1, 2, 3, 4))
                    and (root_port / "disable").read_text() == "0\n" and not Hub.log.exists())

    Hub.pauses, Hub.devices, Hub.usb = pauses, devices, usb
    return Hub


def test_usb_reset_writes_1_then_0_and_reports(hub, capsys):
    assert usbguard.main(["usb-reset", "SLOT1"]) == 0
    assert hub.pauses[0] == (1, "1")  # disabled (1) during the 1 s pause
    assert hub.disable.read_text() == "0"  # then re-enabled (0)
    assert "SLOT1 1-1.1: vid=10c4 pid=ea60 kind=esp-uart tty=/dev/ttyUSB0" in capsys.readouterr().out
    record = history.tail(1)[0]
    assert (record["verb"], record["slot"], record["rc"], record["kind"]) == ("usb-reset", "SLOT1", 0, "esp-uart")


def test_usb_reset_nothing_came_back_66(hub, capsys):
    (hub.devices / "1-1.1/idVendor").unlink()
    assert usbguard.main(["usb-reset", "SLOT1"]) == 66
    assert hub.disable.read_text() == "0"
    assert "nothing came back" in capsys.readouterr().out
    assert history.tail(1)[0]["rc"] == 66


def test_guard_slot_not_configured(hub, capsys):
    assert usbguard.main(["usb-reset", "SLOT7"]) == 2
    assert "not a configured slot" in capsys.readouterr().err
    assert hub.untouched() and history.tail(1) == []


def test_guard_parent_is_root_hub(hub, capsys):
    assert usbguard.main(["usb-reset", "SLOT2"]) == 2
    assert "root hub usb1" in capsys.readouterr().err
    assert hub.untouched() and history.tail(1) == []


def test_guard_mass_storage_in_slot_subtree(hub, capsys):
    hub.usb(path="1-1.3", vid="0781", pid="5567", storage=True)
    assert usbguard.main(["usb-reset", "SLOT3"]) == 2
    assert "mass-storage" in capsys.readouterr().err
    assert hub.untouched() and history.tail(1) == []


def test_guard_mass_storage_in_hub_subtree_power_cycle(hub, capsys):
    hub.usb(path="1-1.4.2", vid="0781", pid="5567", storage=True)  # behind a second-level hub
    assert usbguard.main(["power-cycle"]) == 2
    assert "mass-storage" in capsys.readouterr().err
    assert hub.untouched() and history.tail(1) == []


def test_guard_hub_location_not_in_hub_paths(hub, capsys):
    hub.env.write_text("BENCH_POWER_CYCLE=1\nBENCH_HUB_LOCATION=1-1\nBENCH_HUB_PATHS=2-1\n")
    assert usbguard.main(["power-cycle"]) == 2
    assert "not in BENCH_HUB_PATHS" in capsys.readouterr().err
    assert hub.untouched() and history.tail(1) == []


@pytest.mark.parametrize("argv", [
    [], ["usb-reset"], ["usb-reset", "SLOT1", "now"], ["power-cycle", "1-1"], ["reboot"],
    ["usb-reset", "../SLOT1"], ["usb-reset", "1-1.1"], ["--help"], ["power-cycle", "-l", "1-1"]])
def test_guard_argv(hub, capsys, argv):
    assert usbguard.main(argv) == 2
    assert hub.untouched() and history.tail(1) == []


def test_power_cycle_disabled_by_default(hub, capsys):
    hub.env.write_text("BENCH_POWER_CYCLE=0\nBENCH_HUB_LOCATION=1-1\nBENCH_HUB_PATHS=1-1\n")
    assert usbguard.main(["power-cycle"]) == 2
    assert "disabled" in capsys.readouterr().err and hub.untouched()


def test_power_cycle_bad_location_refused(hub):
    hub.env.write_text("BENCH_POWER_CYCLE=1\nBENCH_HUB_LOCATION=1\nBENCH_HUB_PATHS=1\n")
    assert usbguard.main(["power-cycle"]) == 2 and hub.untouched()


def test_power_cycle_runs_uhubctl(hub, capsys):
    assert usbguard.main(["power-cycle"]) == 0
    assert hub.uhubctl_calls() == ["-l 1-1 -a cycle -d 3"]
    assert "power-cycled hub 1-1" in capsys.readouterr().out
    record = history.tail(1)[0]
    assert (record["verb"], record["slot"], record["rc"], record["note"]) == ("power-cycle", None, 0, "hub 1-1")


def test_power_cycle_uhubctl_fails_68(hub, monkeypatch):
    monkeypatch.setenv("FAKE_UHUBCTL_RC", "1")
    assert usbguard.main(["power-cycle"]) == 68
    assert history.tail(1)[0]["rc"] == 68


def test_usb_reset_busy_65(hub):
    with locks.slot_lock("SLOT1"):
        assert usbguard.main(["usb-reset", "SLOT1"]) == 65
    assert hub.untouched() and history.tail(1)[0]["rc"] == 65


def test_usb_reset_held_69(hub, capsys):
    holds.put("SLOT1", "client-b", 5, "soak test")
    assert usbguard.main(["usb-reset", "SLOT1"]) == 69
    assert "client-b: soak test" in capsys.readouterr().err
    assert hub.untouched() and history.tail(1)[0]["rc"] == 69


def test_entry_point_refuses_other_verbs():
    script = TESTS.parent / "sbin/bench-usb"
    result = subprocess.run([sys.executable, str(script), "unbind", "1-1.1"], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 2 and "usage" in result.stderr


def test_guard_lets_rp2_bootsel_storage_through_usb_reset(hub, capsys):
    hub.usb(path="1-1.1", vid="2e8a", pid="0003", storage=True)  # RP2040 boot ROM: class 08
    assert usbguard.main(["usb-reset", "SLOT1"]) == 0
    assert "kind=rp2-bootsel" in capsys.readouterr().out
    assert history.tail(1)[0]["rc"] == 0


def test_guard_lets_rp2350_bootsel_storage_through_power_cycle(hub):
    hub.usb(path="1-1.4", vid="2e8a", pid="000f", storage=True)  # RP2350 boot ROM
    assert usbguard.main(["power-cycle"]) == 0
    assert hub.uhubctl_calls() == ["-l 1-1 -a cycle -d 3"]


@pytest.mark.parametrize("vid,pid", [("0781", "5567"), ("2e8a", "000a"), ("2e8a", "1000")])
def test_guard_other_storage_still_refused_beside_bootsel(hub, capsys, vid, pid):
    hub.usb(path="1-1.4", vid="2e8a", pid="0003", storage=True)
    hub.usb(path="1-1.4.1", vid=vid, pid=pid, storage=True)  # a real disk (or non-BOOTSEL storage)
    assert usbguard.main(["power-cycle"]) == 2
    assert "mass-storage" in capsys.readouterr().err
    assert hub.untouched() and history.tail(1) == []


# --- PR #4 review (2026-11-22): power-cycle honours holds and locks; root-opened files are not redirectable


def test_power_cycle_refused_while_another_client_holds_a_slot_under_the_hub(hub, capsys):
    holds.put("SLOT1", "client-b", 30, "flashing something")
    assert usbguard.main(["power-cycle"]) == 69
    assert hub.uhubctl_calls() == []
    assert "SLOT1" in capsys.readouterr().err


def test_power_cycle_refused_while_a_slot_under_the_hub_is_busy(hub):
    with locks.slot_lock("SLOT3"):
        assert usbguard.main(["power-cycle"]) == 65
    assert hub.uhubctl_calls() == []


def test_power_cycle_keeps_every_affected_lock_during_the_cycle(hub, monkeypatch):
    seen = {}
    real = usbguard._run_uhubctl

    def spy(tool, location):
        seen.update({slot: locks.lock_busy(slot) for slot in ("SLOT1", "SLOT2", "SLOT3")})
        return real(tool, location)
    monkeypatch.setattr(usbguard, "_run_uhubctl", spy)
    assert usbguard.main(["power-cycle"]) == 0
    # SLOT1 (1-1.1) and SLOT3 (1-1.3) are under hub 1-1; SLOT2 (1-2) is not.
    assert seen == {"SLOT1": True, "SLOT2": False, "SLOT3": True}


def test_power_cycle_own_hold_does_not_block(hub):
    holds.put("SLOT1", "client-a", 30, "my own reservation")
    assert usbguard.main(["power-cycle"]) == 0


def test_planted_fifo_hold_refuses_instead_of_hanging_the_root_helper(hub, roots, capsys):
    path = roots["state"] / "holds" / "SLOT1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    assert usbguard.main(["usb-reset", "SLOT1"]) == 69
    assert "unreadable hold file" in capsys.readouterr().err
    assert hub.untouched()
    assert usbguard.main(["power-cycle"]) == 69 and hub.untouched()


def test_root_ignores_bench_path_overrides(monkeypatch):
    monkeypatch.setattr(usbguard.os, "geteuid", lambda: 0)
    monkeypatch.setenv("BENCH_STATE", "/tmp/attacker-chosen")
    assert usbguard.main(["no-such-verb"]) == 2
    assert "BENCH_STATE" not in os.environ


def test_history_refuses_a_planted_symlink(roots, tmp_path):
    victim = tmp_path / "root-owned-file"
    victim.write_text("precious\n")
    (roots["state"] / "flash-history.jsonl").symlink_to(victim)
    with pytest.raises(OSError):
        history.append(client="x", verb="usb-reset", slot="SLOT1", rc=0)
    assert victim.read_text() == "precious\n"


def test_history_refuses_a_hard_link(roots, tmp_path):
    victim = tmp_path / "other-file"
    victim.write_text("precious\n")
    os.link(victim, roots["state"] / "flash-history.jsonl")
    with pytest.raises(OSError):
        history.append(client="x", verb="usb-reset", slot="SLOT1", rc=0)
    assert victim.read_text() == "precious\n"


def test_lock_refuses_a_symlink_or_fifo(roots, tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("")
    (roots["run"] / "SLOT1.lock").unlink()
    (roots["run"] / "SLOT1.lock").symlink_to(target)
    with pytest.raises(OSError):
        with locks.slot_lock("SLOT1"):
            pass
    (roots["run"] / "SLOT2.lock").unlink()
    os.mkfifo(roots["run"] / "SLOT2.lock")
    with pytest.raises(OSError):
        with locks.slot_lock("SLOT2"):
            pass
