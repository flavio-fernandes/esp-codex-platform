#!/usr/bin/env bash
# Install the canonical skill; Python stdlib only, no rsync dependency.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/dev/bench_sync_skill.py" "$@"
