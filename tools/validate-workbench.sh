#!/usr/bin/env bash
printf 'deprecated: use tools/validate-bench.sh\n' >&2; exec "$(dirname -- "$0")/validate-bench.sh" "$@"
