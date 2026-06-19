#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/lib/workbench-env
source "${SCRIPT_DIR}/lib/workbench-env"
load_workbench_env \
  ESPWB_SLOT ALLOW_NON_SLOT1 ESP_PORT RUN_RFC2217_TEST ESPWB_SSH_KEY \
  ESPWB_KNOWN_HOSTS ESPWB_SSH_CONNECT_TIMEOUT \
  ESPWB_SSH_SERVER_ALIVE_INTERVAL ESPWB_SSH_SERVER_ALIVE_COUNT_MAX \
  STATIC_ONLY

WORKBENCH_IP="${WORKBENCH_IP:-192.0.2.10}"
WORKBENCH_URL="${WORKBENCH_URL:-http://${WORKBENCH_IP}:8080}"
ESPWB_SLOT="${ESPWB_SLOT:-SLOT1}"
ALLOW_NON_SLOT1="${ALLOW_NON_SLOT1:-0}"
ESP_PORT="${ESP_PORT:-rfc2217://${WORKBENCH_IP}:4001?ign_set_control}"
RUN_RFC2217_TEST="${RUN_RFC2217_TEST:-0}"
STATIC_ONLY="${STATIC_ONLY:-0}"

if [[ "$ESPWB_SLOT" != "SLOT1" && "$ALLOW_NON_SLOT1" != "1" ]]; then
  echo "Refusing to validate ESPWB_SLOT=$ESPWB_SLOT; only SLOT1 is allowed by default." >&2
  echo "Set ALLOW_NON_SLOT1=1 only after explicit approval." >&2
  exit 2
fi

PASS=()
FAIL=()

pass() {
  PASS+=("$1")
  printf '[PASS] %s\n' "$1"
}

fail() {
  FAIL+=("$1")
  printf '[FAIL] %s\n' "$1"
}

check_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "command exists: $1"
  else
    fail "missing command: $1"
  fi
}

check_cmd bash
check_cmd curl
check_cmd file
check_cmd git
check_cmd jq
check_cmd rg
check_cmd ssh
check_cmd scp
check_cmd shellcheck
check_cmd tree
check_cmd unzip
check_cmd python3
check_cmd esphome

check_executable() {
  if [[ -x "$1" ]]; then
    pass "project helper is executable: $1"
  else
    fail "project helper is missing or not executable: $1"
  fi
}

check_executable tools/espwb-esptool
check_executable tools/espwb-monitor
check_executable tools/espwb-ssh
check_executable tools/espwb-status
check_executable tools/workbench-local-esptool

if [[ "$STATIC_ONLY" == "1" ]]; then
  printf '[SKIP] Workbench network and board checks skipped because STATIC_ONLY=1.\n'
  printf '\nSummary:\n'
  printf '  Pass: %s\n' "${#PASS[@]}"
  printf '  Fail: %s\n' "${#FAIL[@]}"

  if [[ "${#FAIL[@]}" -gt 0 ]]; then
    printf '\nFailures:\n'
    printf '  - %s\n' "${FAIL[@]}"
    exit 1
  fi

  printf '\nvalidate-workbench.sh static checks PASSED\n'
  exit 0
fi

if python3 -m esptool version >/tmp/esptool-version.txt 2>&1; then
  pass "python3 -m esptool works"
  cat /tmp/esptool-version.txt
else
  fail "python3 -m esptool failed"
  cat /tmp/esptool-version.txt || true
fi

if curl -fsS --connect-timeout 5 --max-time 10 "$WORKBENCH_URL/api/info" >/tmp/workbench-api.json; then
  pass "workbench API reachable: $WORKBENCH_URL/api/info"
  python3 - <<'PY' || true
import json
with open("/tmp/workbench-api.json", "r", encoding="utf-8") as f:
    data = json.load(f)
print({
    "slots_configured": data.get("slots_configured"),
    "slots_running": data.get("slots_running"),
})
PY
else
  fail "workbench API is not reachable: $WORKBENCH_URL/api/info"
fi

if tools/espwb-ssh test -x /usr/local/bin/espwb-local-esptool >/tmp/workbench-helper-check.txt 2>&1; then
  pass "SSH to workbench works and helper is executable"
else
  fail "SSH to workbench/helper check failed"
  cat /tmp/workbench-helper-check.txt || true
fi

if tools/espwb-status >/tmp/espwb-status.txt 2>&1; then
  pass "non-destructive workbench status helper ran"
  sed -n '1,120p' /tmp/espwb-status.txt
else
  fail "non-destructive workbench status helper failed"
  sed -n '1,160p' /tmp/espwb-status.txt || true
fi

if tools/espwb-esptool flash-id >/tmp/espwb-flash-id.txt 2>&1; then
  pass "reset-aware flash-id through tools/espwb-esptool works"
  sed -n '1,100p' /tmp/espwb-flash-id.txt
else
  fail "reset-aware flash-id through tools/espwb-esptool failed"
  sed -n '1,140p' /tmp/espwb-flash-id.txt || true
  printf '[INFO] SLOT state after flash-id failure:\n'
  if curl -fsS --connect-timeout 5 --max-time 10 "$WORKBENCH_URL/api/devices" >/tmp/workbench-devices.json; then
    python3 - "$ESPWB_SLOT" /tmp/workbench-devices.json <<'PY' || true
import json
import sys

slot_name = sys.argv[1].upper()
with open(sys.argv[2], "r", encoding="utf-8") as f:
    data = json.load(f)

for item in data.get("slots", []):
    if str(item.get("label", "")).upper() != slot_name:
        continue
    summary = {
        "label": item.get("label"),
        "state": item.get("state"),
        "present": item.get("present"),
        "running": item.get("running"),
        "devnode": item.get("devnode"),
        "gpio_boot": item.get("gpio_boot"),
        "gpio_en": item.get("gpio_en"),
        "usb_devices": item.get("usb_devices"),
    }
    print(summary)
    products = item.get("usb_devices") or []
    if products:
        print(
            "[HINT] If this is not an Espressif ROM/download-mode USB identity, "
            "check that the workbench BOOT/EN GPIO lines are wired to this slot "
            "and have the expected polarity."
        )
    break
else:
    print({"error": f"{slot_name} not found in workbench API"})
PY
  else
    printf '[INFO] Could not read %s/api/devices\n' "$WORKBENCH_URL"
  fi
fi

if [[ "$RUN_RFC2217_TEST" == "1" ]]; then
  printf '[INFO] RFC2217 open/close test may perturb ESP32-S3 USB-Serial/JTAG devices.\n'
  if python3 - "$ESP_PORT" <<'PY' >/tmp/rfc2217-open-close.txt 2>&1
import sys
import serial

port = sys.argv[1]
ser = serial.serial_for_url(port, baudrate=115200, timeout=2)
print(f"opened {port}")
ser.close()
print("closed")
PY
  then
    pass "RFC2217 serial open/close works for monitor path"
    cat /tmp/rfc2217-open-close.txt
  else
    fail "RFC2217 serial open/close failed"
    cat /tmp/rfc2217-open-close.txt || true
  fi
else
  printf '[SKIP] RFC2217 open/close test skipped; set RUN_RFC2217_TEST=1 to run it intentionally.\n'
fi

printf '\nSummary:\n'
printf '  Pass: %s\n' "${#PASS[@]}"
printf '  Fail: %s\n' "${#FAIL[@]}"

if [[ "${#FAIL[@]}" -gt 0 ]]; then
  printf '\nFailures:\n'
  printf '  - %s\n' "${FAIL[@]}"
  exit 1
fi

printf '\nvalidate-workbench.sh PASSED\n'
