#!/usr/bin/env bash
# Add camera, RP2040/RP2350 flashing and a restricted agent SSH user to a workbench
# Pi that already runs the Embedded-AI-Harness (formerly Universal-Embedded-Workbench)
# rfc2217 portal. Idempotent: safe to re-run after editing anything under workbench/.
#
#   sudo bash workbench/install-agent-extras.sh
#
# It never overwrites /etc/workbench/agent.env or /etc/rfc2217/testbench.json once
# they exist, and never touches authorized_keys (see docs/workbench-build-from-scratch.md).
set -Eeuo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
AGENT_USER="${WB_AGENT_USER:-wbagent}"

echo "== packages"
apt-get update -qq
apt-get install -y picotool v4l-utils python3-pil python3-serial usbutils

echo "== helpers -> /usr/local/bin"
install -o root -g root -m 0755 "$HERE"/bin/wb-agent-shell "$HERE"/bin/wb-camera \
  "$HERE"/bin/wb-pico-flash "$HERE"/bin/wb-rp2-console /usr/local/bin/
install -o root -g root -m 0755 "$REPO/tools/workbench-local-esptool" /usr/local/bin/espwb-local-esptool

echo "== udev"
install -o root -g root -m 0644 "$HERE/udev/60-rp2-picotool.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=2e8a || true

echo "== lock directory /run/workbench"
install -o root -g root -m 0644 "$HERE/tmpfiles/workbench.conf" /etc/tmpfiles.d/workbench.conf
systemd-tmpfiles --create /etc/tmpfiles.d/workbench.conf
rm -f /run/lock/wb-camera.lock /run/lock/wb-pico-SLOT*.lock   # pre-fix locations

echo "== user $AGENT_USER"
if ! id "$AGENT_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --comment "workbench agent (forced command)" "$AGENT_USER"
fi
passwd -l "$AGENT_USER" >/dev/null
usermod -a -G dialout,video,plugdev "$AGENT_USER"
install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 0700 "/home/$AGENT_USER/.ssh"
touch "/home/$AGENT_USER/.ssh/authorized_keys"
chown "$AGENT_USER:$AGENT_USER" "/home/$AGENT_USER/.ssh/authorized_keys"
chmod 0600 "/home/$AGENT_USER/.ssh/authorized_keys"

echo "== sudoers"
tmp="$(mktemp)"
sed "s/^wbagent /${AGENT_USER} /; s/Defaults:wbagent/Defaults:${AGENT_USER}/" "$HERE/sudoers/wbagent" >"$tmp"
visudo -cf "$tmp" >/dev/null
install -o root -g root -m 0440 "$tmp" "/etc/sudoers.d/$AGENT_USER"
rm -f "$tmp"

echo "== config (kept if present)"
install -d -m 0755 /etc/workbench
[[ -e /etc/workbench/agent.env ]] || install -m 0644 "$HERE/config/agent.env.example" /etc/workbench/agent.env
if [[ ! -e /etc/rfc2217/testbench.json && ! -e /etc/rfc2217/workbench.json ]]; then
  install -d -m 0755 /etc/rfc2217
  install -m 0644 "$HERE/config/testbench.json.example" /etc/rfc2217/testbench.json
  echo "   wrote /etc/rfc2217/testbench.json -- check the usb_prefix values, then:"
  echo "   sudo systemctl restart rfc2217-portal"
fi

echo "== RP2 console holders (DTR) for WB_AGENT_PICO_SLOTS"
install -o root -g root -m 0644 "$HERE/systemd/wb-rp2-console@.service" /etc/systemd/system/
systemctl daemon-reload
# shellcheck disable=SC1091
( source /etc/workbench/agent.env
  for s in ${WB_AGENT_PICO_SLOTS:-}; do systemctl enable --now "wb-rp2-console@${s}.service"; done )

echo "== check"
picotool version | head -1
v4l2-ctl --list-devices 2>/dev/null | sed -n '1,3p' || true
grep -q 'REPLACE_WITH_YOUR_CAMERA' /etc/workbench/agent.env \
  && echo "   TODO: set WB_CAMERA_DEVICE in /etc/workbench/agent.env (ls -l /dev/v4l/by-id/)"
echo "done. Add client keys to /home/$AGENT_USER/.ssh/authorized_keys as:"
echo "  restrict,command=\"/usr/local/bin/wb-agent-shell <client-name>\" ssh-ed25519 AAAA... <comment>"
