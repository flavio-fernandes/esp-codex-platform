"""Installer implementation. Only --check and --probe are read-only entry points."""
import grp
import importlib.metadata
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import sysconfig
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'lib'))
from benchlib.config import load_env, load_slots, validate_port

PACKAGES = 'python3 python3-serial python3-pil python3-venv picotool v4l-utils usbutils uhubctl sudo openssh-server udev systemd'.split()
GROUPS = ('bench', 'dialout', 'plugdev', 'video')


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=120)


def probe():
    import serial
    from PIL import Image
    assert serial and Image
    print(json.dumps({'site': sysconfig.get_path('purelib'),
                      'esptool': importlib.metadata.version('esptool')}))


def venv_info():
    return json.loads(run('/opt/bench/venv/bin/python', '-B', str(Path(__file__).resolve()),
                          '--probe').stdout)


def manifest(site, env, slots):
    files = {}
    for path in sorted((SOURCE / 'lib/benchlib').glob('*.py')):
        files['/opt/bench/lib/benchlib/' + path.name] = (path.read_bytes(), 0o644)
    for folder, target in [('bin', '/usr/local/bin'), ('sbin', '/usr/local/sbin')]:
        for path in sorted((SOURCE / folder).iterdir()):
            if path.is_file():
                text = path.read_text().splitlines(keepends=True)
                text[0] = '#!/opt/bench/venv/bin/python -I\n'
                files[target + '/' + path.name] = (''.join(text).encode(), 0o755)
    files[site + '/bench.pth'] = (b'/opt/bench/lib\n', 0o644)
    for folder, target in [('systemd', '/etc/systemd/system'), ('udev', '/etc/udev/rules.d'),
                           ('tmpfiles', '/etc/tmpfiles.d'), ('sudoers', '/etc/sudoers.d'),
                           ('sshd', '/etc/ssh/sshd_config.d'),
                           ('journald', '/etc/systemd/journald.conf.d')]:
        for path in sorted((SOURCE / folder).iterdir()):
            files[target + '/' + path.name] = (path.read_bytes(), 0o440 if folder == 'sudoers' else 0o644)
    rules = ['# Generated from BENCH_HUB_PATHS; rerun install after configuration changes.\n']
    for hub in env.get('BENCH_HUB_PATHS', '').split():
        validate_port(hub)
        for match in (f'KERNEL=="{hub}"', f'KERNELS=="{hub}"'):
            rules.append(f'ACTION=="add|change", SUBSYSTEM=="usb", {match}, TEST=="power/control", ATTR{{power/control}}="on"\n')
    files['/etc/udev/rules.d/61-bench-paths.rules'] = (''.join(rules).encode(), 0o644)
    extra = ''.join(f'f /run/bench/{slot}.lock 0664 root bench -\n' for slot in slots
                    if slot not in {f'SLOT{i}' for i in range(1, 10)})
    files['/etc/tmpfiles.d/bench-slots.conf'] = (extra.encode(), 0o644)
    return files


def put(path, data, mode):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement; never follow a destination symlink as root.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(data)
        os.fchmod(file.fileno(), mode)
        os.fchown(file.fileno(), 0, 0)
    temporary.replace(path)


def enabled_serial():
    result = run('systemctl', 'list-unit-files', 'bench-serial@*.service', '--no-legend', '--no-pager')
    return {line.split()[0] for line in result.stdout.splitlines()
            if len(line.split()) > 1 and line.split()[1].startswith('enabled')}


def check_install():
    drift = []
    def bad(message):
        drift.append(message)
    for package in PACKAGES:
        result = run('dpkg-query', '-W', '-f=${Status}', package, check=False)
        if result.returncode or result.stdout != 'install ok installed':
            bad('package ' + package)
    for group in GROUPS:
        try:
            grp.getgrnam(group)
        except KeyError:
            bad('group ' + group)
    for user, groups in [('bench', GROUPS), ('wbagent', GROUPS)]:
        try:
            account = pwd.getpwnam(user)
            actual = {grp.getgrgid(gid).gr_name for gid in os.getgrouplist(user, account.pw_gid)}
            if not set(groups) <= actual:
                bad('groups for ' + user)
            if user == 'bench' and account.pw_shell != '/usr/sbin/nologin':
                bad('bench shell')
            if user == 'wbagent':
                status = run('passwd', '-S', user, check=False)
                if status.returncode or status.stdout.split()[1] != 'L':
                    bad('wbagent password lock')
                key = Path(account.pw_dir) / '.ssh/authorized_keys'
                if not key.is_file() or key.is_symlink() or key.stat().st_mode & 0o777 != 0o600 or key.stat().st_uid != account.pw_uid:
                    bad('authorized_keys existence/permissions (contents unmanaged)')
        except KeyError:
            bad('user ' + user)
    info = None
    try:
        info = venv_info()
        if info['esptool'].split('.')[0] != '5':
            bad('venv esptool major')
        if 'include-system-site-packages = true' not in Path('/opt/bench/venv/pyvenv.cfg').read_text():
            bad('venv system-site-packages')
    except (OSError, subprocess.SubprocessError, ValueError):
        bad('venv esptool/dependencies')
    slots, env = {}, {}
    for example in (SOURCE / 'config').glob('*.example'):
        if not Path('/etc/bench', example.name.removesuffix('.example')).is_file():
            bad('config ' + example.name.removesuffix('.example'))
    try:
        slots = load_slots('/etc/bench/slots.conf')
        env = load_env('/etc/bench/bench.env')
        files = manifest(info['site'] if info else '/opt/bench/venv/missing', env, slots)
        for name, (data, mode) in files.items():
            path = Path(name)
            try:
                st = path.lstat()
                if path.is_symlink() or path.read_bytes() != data or st.st_mode & 0o7777 != mode or (st.st_uid, st.st_gid) != (0, 0):
                    bad('file ' + name)
            except OSError:
                bad('file ' + name)
    except (OSError, ValueError) as exc:
        bad('configuration: ' + str(exc))
    try:
        bench = pwd.getpwnam('bench')
        gid = grp.getgrnam('bench').gr_gid
        paths = [('/run/bench', 0o2775, 0), ('/var/lib/bench', 0o2775, bench.pw_uid),
                 ('/var/lib/bench/serial', 0o2775, bench.pw_uid), ('/var/lib/bench/holds', 0o2775, bench.pw_uid)]
        paths += [(f'/run/bench/{name}.lock', 0o664, 0) for name in set(slots) | {f'SLOT{i}' for i in range(1, 10)} | {'camera'}]
        for name, mode, uid in paths:
            try:
                st = Path(name).lstat()
                if Path(name).is_symlink() or st.st_mode & 0o7777 != mode or (st.st_uid, st.st_gid) != (uid, gid):
                    bad('permissions ' + name)
            except OSError:
                bad('missing ' + name)
    except KeyError:
        pass
    if os.environ.get('BENCH_NO_SYSTEMD') != '1':
        desired = {f'bench-serial@{slot}.service' for slot in slots}
        for unit in sorted(desired | {'bench-health.timer'}):
            if run('systemctl', 'is-enabled', unit, check=False).returncode:
                bad('disabled unit ' + unit)
        for unit in sorted(enabled_serial() - desired):
            bad('obsolete enabled unit ' + unit)
    for message in drift:
        print('DRIFT ' + message)
    if not drift:
        print('NO DRIFT')
    return bool(drift)


def install():
    for name in GROUPS:
        try:
            grp.getgrnam(name)
        except KeyError:
            run('groupadd', '--system', name)
    for user in ('bench', 'wbagent'):
        try:
            pwd.getpwnam(user)
        except KeyError:
            if user == 'bench':
                run('useradd', '--system', '--gid', 'bench', '--home-dir', '/var/lib/bench', '--shell', '/usr/sbin/nologin', user)
            else:
                run('useradd', '--create-home', '--shell', '/bin/bash', user)
        run('usermod', '--append', '--groups', ','.join(GROUPS), user)
    run('usermod', '--shell', '/usr/sbin/nologin', 'bench')
    run('usermod', '--lock', 'wbagent')
    agent = pwd.getpwnam('wbagent')
    ssh = Path(agent.pw_dir) / '.ssh'
    if ssh.is_symlink():
        raise ValueError('refusing symlink .ssh')
    ssh.mkdir(mode=0o700, exist_ok=True)
    ssh.chmod(0o700)
    os.chown(ssh, agent.pw_uid, agent.pw_gid)
    key = ssh / 'authorized_keys'
    if key.is_symlink():
        raise ValueError('refusing symlink authorized_keys')
    if not key.exists():
        key.touch(mode=0o600)
    key.chmod(0o600)
    os.chown(key, agent.pw_uid, agent.pw_gid)
    for example in (SOURCE / 'config').glob('*.example'):
        target = Path('/etc/bench') / example.name.removesuffix('.example')
        if not target.exists():
            put(target, example.read_bytes(), 0o644)
    slots = load_slots('/etc/bench/slots.conf')
    env = load_env('/etc/bench/bench.env')
    files = manifest(venv_info()['site'], env, slots)
    run('visudo', '-cf', str(SOURCE / 'sudoers/bench'))
    # Validate the proposed drop-in in the context of the existing daemon config
    # before publishing it. Match all prevents leaking Match into subsequent files.
    Path('/run/sshd').mkdir(mode=0o755, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', prefix='bench-sshd-') as candidate:
        candidate.write(f'Include {SOURCE}/sshd/50-bench-agent.conf\nInclude /etc/ssh/sshd_config\n')
        candidate.flush()
        run('/usr/sbin/sshd', '-t', '-f', candidate.name)
    changed = set()
    for name, (data, mode) in files.items():
        try:
            same = Path(name).read_bytes() == data
        except OSError:
            same = False
        if not same:
            changed.add(name)
        put(name, data, mode)
    run('/usr/sbin/sshd', '-t')
    run('systemd-tmpfiles', '--create', '/etc/tmpfiles.d/bench.conf', '/etc/tmpfiles.d/bench-slots.conf')
    desired = {f'bench-serial@{slot}.service' for slot in slots}
    if os.environ.get('BENCH_NO_SYSTEMD') == '1':
        for unit in sorted(desired | {'bench-health.timer'}):
            print('WOULD ENABLE/START ' + unit)
        print('BENCH_NO_SYSTEMD=1: skipped daemon reloads and obsolete-instance reconciliation')
    else:
        run('udevadm', 'control', '--reload-rules')
        # Re-trigger only the bench hubs' subtrees, never every USB device: the root disk is USB too.
        for hub in env.get('BENCH_HUB_PATHS', '').split():
            if Path('/sys/bus/usb/devices', hub).exists():
                run('udevadm', 'trigger', '--action=change', '--subsystem-match=usb',
                    '--parent-match=' + str(Path('/sys/bus/usb/devices', hub).resolve()))
        # Restart journald only when its drop-in changed (a restart flushes to disk under load).
        if any(name.startswith('/etc/systemd/journald.conf.d/') for name in changed):
            run('systemctl', 'restart', 'systemd-journald')
            run('journalctl', '--flush')
        run('systemctl', 'daemon-reload')
        for unit in sorted(enabled_serial() - desired):
            run('systemctl', 'disable', '--now', unit)
        for unit in sorted(desired | {'bench-health.timer'}):
            run('systemctl', 'enable', '--now', unit)
        run('systemctl', 'reload', 'ssh')
    print('INSTALL OK')
    return 0


def main():
    try:
        if sys.argv[1:] == ['--probe']:
            probe()
            return 0
        if sys.argv[1:] == ['--check']:
            return int(check_install())
        if sys.argv[1:] or os.geteuid() != 0:
            raise ValueError('install requires root and no arguments')
        return install()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'ERROR {exc}', file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
