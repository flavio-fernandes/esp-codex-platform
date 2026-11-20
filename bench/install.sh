#!/usr/bin/env bash
# Root only on the target; --check is read-only. Test containers set BENCH_NO_SYSTEMD=1.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --check ) ]]; then
    printf 'usage: %s [--check]\n' "$0" >&2
    exit 2
fi
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} != --check ]]; then
    [[ $EUID -eq 0 ]] || { printf 'install requires root\n' >&2; exit 2; }
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-serial python3-pil python3-venv picotool v4l-utils usbutils uhubctl sudo openssh-server udev systemd
    python3 -m venv --system-site-packages /opt/bench/venv
    /opt/bench/venv/bin/pip install 'esptool>=5,<6'
fi
exec python3 -I -B "$here/dev/bench_install.py" "$@"
