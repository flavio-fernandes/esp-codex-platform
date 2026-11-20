"""Small fake-sysfs builder; no real USB or serial access."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    result = {}
    for name in ("etc", "run", "state", "sysfs"):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setenv("BENCH_" + name.upper(), str(path))
        result[name] = path
    (result["sysfs"] / "bus/usb/devices").mkdir(parents=True)
    for name in ("SLOT1.lock", "SLOT2.lock", "camera.lock"):
        (result["run"] / name).touch(mode=0o444)
    return result


@pytest.fixture
def usb(roots):
    def make(path="1-1.1", vid="2e8a", pid="000a", tty=None, acm=True,
             storage=False, nested=False):
        base = roots["sysfs"] / "bus/usb/devices"
        device = base / path
        device.mkdir(parents=True, exist_ok=True)
        for key, value in {"idVendor": vid, "idProduct": pid, "serial": "TESTSERIAL01",
                           "product": "Test board", "busnum": "1", "devnum": "7"}.items():
            (device / key).write_text(value + "\n")
        interface = (device if nested else base) / (path + ":1.0")
        interface.mkdir(exist_ok=True)
        if nested:
            (base / interface.name).symlink_to(interface, target_is_directory=True)
        (interface / "bInterfaceClass").write_text("08\n" if storage else "02\n")
        if tty:
            (interface / ("tty/" if acm else "") / tty).mkdir(parents=True)
        if storage:
            (interface / "host0/target0:0:0/0:0:0:0/block/sda").mkdir(parents=True)
        return device
    return make