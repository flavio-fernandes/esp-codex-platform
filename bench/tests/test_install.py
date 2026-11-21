"""Pure manifest and reconciliation tests; real root install is test-install.sh."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('bench_install', Path(__file__).parents[1] / 'dev/bench_install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_manifest_complete():
    files = installer.manifest('/opt/bench/venv/lib/python3/site-packages',
                               {'BENCH_HUB_PATHS': '1-1.1 2-1'}, {'SLOT1': {}, 'SLOT12': {}})
    for source in (installer.SOURCE / 'lib/benchlib').glob('*.py'):
        assert files['/opt/bench/lib/benchlib/' + source.name] == (source.read_bytes(), 0o644)
    for folder in ('bin', 'sbin'):
        for source in (installer.SOURCE / folder).iterdir():
            data, mode = files['/usr/local/' + folder + '/' + source.name]
            assert data.startswith(b'#!/opt/bench/venv/bin/python -I\n')
            assert mode == 0o755
    assert files['/etc/sudoers.d/bench'][1] == 0o440
    assert files['/etc/tmpfiles.d/bench-slots.conf'][0] == b'f /run/bench/SLOT12.lock 0664 root bench -\n'
    rules = files['/etc/udev/rules.d/61-bench-paths.rules'][0].decode()
    assert 'KERNELS=="1-1.1"' in rules and 'KERNEL=="2-1"' in rules
    assert '/etc/systemd/system/bench-health.timer' in files
    assert not any('authorized_keys' in path for path in files)
    assert not any(path.endswith('/bench.env') for path in files)


@pytest.mark.parametrize('hub', ['usb1', '1-1"', '1-1;bad', '/etc/passwd'])
def test_generated_udev_rejects_injection(hub):
    with pytest.raises(ValueError):
        installer.manifest('/site', {'BENCH_HUB_PATHS': hub}, {})


def test_enabled_instances(monkeypatch, tmp_path):
    """Instances come from .wants links and loaded units; list-unit-files shows only the template
    (Argon 2026-11-21: a removed slot's unit was never disabled)."""
    wants = tmp_path / "etc/systemd/system/multi-user.target.wants"
    wants.mkdir(parents=True)
    (wants / "bench-serial@SLOT1.service").symlink_to("/dev/null")
    (wants / "bench-serial@SLOT6.service").symlink_to("/dev/null")

    def run(*args, check=True):
        assert args[:3] == ('systemctl', 'list-units', '--all')
        return SimpleNamespace(stdout='bench-serial@SLOT1.service loaded active running x\n'
                               'bench-serial@SLOT3.service loaded active running x\n')
    real_path = installer.Path
    monkeypatch.setattr(installer, 'Path', lambda p, *a: real_path(str(tmp_path) + p) if str(p) == '/etc/systemd/system' else real_path(p, *a))
    monkeypatch.setattr(installer, 'run', run)
    assert installer.enabled_serial() == {'bench-serial@SLOT1.service', 'bench-serial@SLOT3.service',
                                          'bench-serial@SLOT6.service'}


def test_serial_unit_contract():
    unit = (installer.SOURCE / 'systemd/bench-serial@.service').read_text()
    for line in ('User=bench', 'Group=bench', 'SupplementaryGroups=dialout plugdev',
                 'ExecStart=/usr/local/bin/bench-serial %i', 'Restart=always', 'RestartSec=2',
                 'NoNewPrivileges=yes', 'ProtectSystem=strict',
                 'ReadWritePaths=/run/bench /var/lib/bench', 'PrivateTmp=yes'):
        assert line in unit.splitlines()
    assert 'RuntimeDirectory=' not in unit
