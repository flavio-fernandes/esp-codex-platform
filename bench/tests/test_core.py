import json
import multiprocessing as mp
from pathlib import Path
import pytest
from benchlib import config, usbdev, locks, holds, history

DEVICES = json.loads((Path(__file__).parent / "fixtures/sysfs/devices.json").read_text())


@pytest.mark.parametrize("vid,pid,expected", DEVICES)
def test_kinds(usb, vid, pid, expected):
    usb(vid=vid, pid=pid)
    record = usbdev.resolve("1-1.1")
    assert record == dict(vid=vid, pid=pid, kind=expected, serial="TESTSERIAL01",
                          product="Test board", busnum=1, devnum=7, tty=None, block=None)


@pytest.mark.parametrize("tty,acm", [("ttyACM0", True), ("ttyUSB0", False)])
@pytest.mark.parametrize("nested", [True, False])
def test_ttys(usb, tty, acm, nested):
    usb(tty=tty, acm=acm, nested=nested)
    assert usbdev.resolve("1-1.1")["tty"] == "/dev/" + tty


def test_absent_optional_and_explicit_root(usb, roots, monkeypatch):
    assert usbdev.resolve("1-1.1") is None
    device = usb()
    (device / "serial").unlink()
    monkeypatch.setenv("BENCH_SYSFS", "/nonexistent")
    assert usbdev.resolve("1-1.1", roots["sysfs"])["serial"] is None
    (device / "idProduct").unlink()
    assert usbdev.resolve("1-1.1", roots["sysfs"]) is None


def test_bootsel_exact_path(usb):
    usb(path="1-1.2", pid="0003")
    assert usbdev.find_bootsel_at("1-1.1") is None
    usb(path="1-1.1")
    assert usbdev.find_bootsel_at("1-1.1") is None
    usb(path="1-1.1", pid="000f")
    assert usbdev.find_bootsel_at("1-1.1")["devnum"] == 7
    assert usbdev.find_bootsel_at("1-1.1")["busnum"] == 1


@pytest.mark.parametrize("path,expected", [("1-1.1.4", ("1-1.1", 4)),
                                          ("1-1", ("usb1", 1)), ("2-12", ("usb2", 12))])
def test_split(path, expected):
    assert usbdev.split_port(path) == expected


def test_root_hub():
    assert usbdev.is_root_hub("usb1")
    assert usbdev.is_root_hub("usb12")
    assert not usbdev.is_root_hub("1-1")
    assert not usbdev.is_root_hub("usb1:1.0")


@pytest.mark.parametrize("nested", [True, False])
def test_storage_subtree(usb, nested):
    usb(path="1-1.10", storage=True, nested=nested)
    assert not usbdev.subtree_has_mass_storage("1-1.1")
    usb(path="1-1.1.2.3", storage=True, nested=nested)
    assert usbdev.subtree_has_mass_storage("1-1.1")
    assert usbdev.subtree_has_mass_storage("1-1.1.2.3")
    assert usbdev.resolve("1-1.1.2.3")["block"] == "/dev/sda"


def test_slots_defaults_and_options(roots):
    (roots["etc"] / "slots.conf").write_text(
        "# header\n\nSLOT1 1-1\nSLOT2 1-2 monitor=off baud=9600\n")
    assert config.load_slots() == {"SLOT1": {"path": "1-1", "monitor": "auto", "baud": 115200},
                                   "SLOT2": {"path": "1-2", "monitor": "off", "baud": 9600}}


@pytest.mark.parametrize("bad", ["bad 1-2", "SLOT2 usb1", "SLOT1 1-2", "SLOT2 1-1",
    "SLOT2 1-2 foo=x", "SLOT2", "SLOT2 1-2 monitor=x", "SLOT2 1-2 baud=0",
    "SLOT2 1-2 baud=-1", "SLOT2 1-2 baud=abc", "SLOT2 1-2 monitor=on monitor=off"])
def test_slot_errors_line_number(roots, bad):
    path = roots["etc"] / "slots.conf"
    path.write_text("# header\nSLOT1 1-1\n" + bad + "\n")
    with pytest.raises(ValueError, match="line 3:"):
        config.load_slots(path)


def test_env_literal(roots):
    (roots["etc"] / "bench.env").write_text("# comment\nCAMERA=$(never-run)\nFLAG=0\nTEXT=a=b\n")
    assert config.load_env() == {"CAMERA": "$(never-run)", "FLAG": "0", "TEXT": "a=b"}


@pytest.mark.parametrize("line", ["export FLAG=1", "FLAG", "FLAG=2", "A-B=1"])
def test_env_errors(roots, line):
    path = roots["etc"] / "bench.env"
    path.write_text("FLAG=0\n" + line)
    with pytest.raises(ValueError, match="line 2:"):
        config.load_env(path)


def test_examples():
    base = Path(__file__).parents[1] / "config"
    assert len(config.load_slots(base / "slots.conf.example")) == 6
    assert config.load_env(base / "bench.env.example")["BENCH_POWER_CYCLE"] == "0"


def _lock_probe(queue):
    queue.put(locks.lock_busy("SLOT1"))
    try:
        with locks.slot_lock("SLOT1"):
            queue.put("acquired")
    except locks.Busy:
        queue.put("busy")


def test_lock_two_processes():
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    with locks.slot_lock("SLOT1"):
        child = ctx.Process(target=_lock_probe, args=(queue,))
        child.start()
        child.join(10)
        assert child.exitcode == 0
        assert queue.get(timeout=2) is True
        assert queue.get(timeout=2) == "busy"
    assert not locks.lock_busy("SLOT1")
    with locks.slot_lock("SLOT1"):
        pass
    queue.close()


def test_camera_and_cleanup(roots):
    with locks.camera_lock():
        with pytest.raises(locks.Busy), locks.camera_lock():
            pass
    with pytest.raises(RuntimeError), locks.slot_lock("SLOT1"):
        raise RuntimeError("test")
    assert not locks.lock_busy("SLOT1")
    with pytest.raises(FileNotFoundError), locks.slot_lock("SLOT3"):
        pass
    assert not (roots["run"] / "SLOT3.lock").exists()


def test_holds(roots, monkeypatch):
    monkeypatch.setattr(holds.time, "time", lambda: 1000)
    assert holds.get("SLOT1") is None
    hold = holds.put("SLOT1", "client-a", 1, "testing")
    assert hold == {"client": "client-a", "since": 1000, "until": 1060, "reason": "testing"}
    holds.check("SLOT1", "client-a")
    for op in (lambda: holds.check("SLOT1", "client-b"),
               lambda: holds.put("SLOT1", "client-b", 2, "other"),
               lambda: holds.release("SLOT1", "client-b")):
        with pytest.raises(holds.Held, match="client-a: testing"):
            op()
    assert holds.get("SLOT1") == hold
    holds.put("SLOT1", "client-a", 2, "renewed")
    assert holds.release("SLOT1", "client-a")
    assert holds.get("SLOT1") is None
    assert not holds.release("SLOT1", "client-a")
    holds.put("SLOT1", "client-a", 1, "testing")
    monkeypatch.setattr(holds.time, "time", lambda: 1060)
    assert holds.get("SLOT1") is None
    holds.put("SLOT1", "client-b", 720, "expired takeover")
    assert holds.release("SLOT1", "client-a", force=True)
    assert list((roots["state"] / "holds").iterdir()) == []


@pytest.mark.parametrize("minutes", [0, 721, -1, 1.5, True, "2"])
def test_hold_limits(minutes):
    with pytest.raises(ValueError):
        holds.put("SLOT1", "client-a", minutes, "test")


def test_hold_atomic_failure(monkeypatch):
    original = holds.put("SLOT1", "client-a", 1, "old")
    def fail(*args):
        raise OSError("rename failed")
    monkeypatch.setattr(holds.os, "replace", fail)
    with pytest.raises(OSError):
        holds.put("SLOT1", "client-a", 2, "new")
    assert holds.get("SLOT1") == original


def _history_writer(client, barrier):
    for index in range(20):
        barrier.wait(timeout=10)
        history.append(client=client, verb="test", slot="SLOT1", rc=0,
                       note=f"{index}:" + "x" * 10000 + "\nline")


def test_history_processes(roots):
    assert history.tail(5) == []
    assert history.last_ts() is None
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    children = [ctx.Process(target=_history_writer, args=(client, barrier))
                for client in ("client-a", "client-b")]
    for child in children:
        child.start()
    for child in children:
        child.join(30)
        assert child.exitcode == 0
    records = history.tail(100)
    assert len(records) == 40
    assert all(set(r) == set(history.FIELDS) for r in records)
    assert {r["client"] for r in records} == {"client-a", "client-b"}
    assert len((roots["state"] / "flash-history.jsonl").read_text().splitlines()) == 40
    assert history.tail(2) == records[-2:]
    assert history.last_ts() == records[-1]["ts"]
    assert history.last_ts().endswith("Z")
    assert history.tail(0) == []
    with pytest.raises(ValueError):
        history.tail(-1)
    with pytest.raises(ValueError):
        history.append(typo="bad")


@pytest.mark.parametrize("bad", ["../SLOT1", "SLOT1/extra", "SLOT1\n"])
def test_path_guards(bad):
    with pytest.raises(ValueError):
        holds.get(bad)
    with pytest.raises(ValueError):
        locks.slot_lock(bad)
    with pytest.raises(ValueError):
        usbdev.resolve(bad)