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


def test_enabled_instances(monkeypatch):
    def run(*args):
        assert args[:2] == ('systemctl', 'list-unit-files')
        return SimpleNamespace(stdout='bench-serial@SLOT1.service enabled enabled\n'
                               'bench-serial@SLOT2.service disabled enabled\n'
                               'bench-serial@SLOT3.service enabled-runtime enabled\n'
                               'bench-serial@.service disabled enabled\n')
    monkeypatch.setattr(installer, 'run', run)
    assert installer.enabled_serial() == {'bench-serial@SLOT1.service', 'bench-serial@SLOT3.service'}


def test_serial_unit_contract():
    unit = (installer.SOURCE / 'systemd/bench-serial@.service').read_text()
    for line in ('User=bench', 'Group=bench', 'SupplementaryGroups=dialout plugdev',
                 'ExecStart=/usr/local/bin/bench-serial %i', 'Restart=always', 'RestartSec=2',
                 'NoNewPrivileges=yes', 'ProtectSystem=strict',
                 'ReadWritePaths=/run/bench /var/lib/bench', 'PrivateTmp=yes'):
        assert line in unit.splitlines()
    assert 'RuntimeDirectory=' not in unit
