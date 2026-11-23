#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/lib/workbench-env
source "$SCRIPT_DIR/lib/workbench-env"
load_bench_env
for command in bash ssh; do command -v "$command" >/dev/null; done
for tool in wb espwb-ssh espwb-esptool espwb-monitor espwb-status workbench-camera-capture workbench-camera-sequence; do
  [[ -x "$SCRIPT_DIR/$tool" ]] || { printf 'missing executable: %s\n' "$tool" >&2; exit 1; }
done
printf 'PASS client prerequisites\n'
if [[ "${BENCH_STATIC_ONLY:-0}" == 1 ]]; then
  printf 'PASS static checks (no network or hardware)\n'
  exit 0
fi
# Read-only checks: validation never resets or flashes a board.
bench_ssh help
bench_ssh status
bench_ssh health --json
printf 'PASS bench validation\n'
