#!/usr/bin/env bash
set -euo pipefail
bench_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$bench_dir/.venv/bin/python" ]]; then
    python3 -m venv "$bench_dir/.venv"
fi
"$bench_dir/.venv/bin/python" -m pip install pytest pyserial pillow shellcheck-py