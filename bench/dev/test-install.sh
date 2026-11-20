#!/usr/bin/env bash
# Run only inside a disposable Debian trixie container as root.
set -euo pipefail
cd /w
export BENCH_NO_SYSTEMD=1
bash bench/install.sh
printf '# preserved config\n' >> /etc/bench/bench.env
printf '# preserved keys\n' > /home/wbagent/.ssh/authorized_keys
before="$(sha256sum /etc/bench/bench.env /home/wbagent/.ssh/authorized_keys)"
bash bench/install.sh
[[ "$before" == "$(sha256sum /etc/bench/bench.env /home/wbagent/.ssh/authorized_keys)" ]]
bash bench/install.sh --check
/usr/local/bin/bench-agent-shell test-client
/opt/bench/venv/bin/esptool version
visudo -cf /etc/sudoers.d/bench
/usr/sbin/sshd -t
printf '# drift\n' >> /usr/local/bin/bench-camera
if bash bench/install.sh --check > /tmp/bench-drift.log; then
    printf 'FAIL expected drift\n' >&2
    exit 1
fi
grep -Fx 'DRIFT file /usr/local/bin/bench-camera' /tmp/bench-drift.log
printf 'INSTALL TEST PASS: two installs, preserved config/keys, NO DRIFT, deliberate DRIFT\n'
