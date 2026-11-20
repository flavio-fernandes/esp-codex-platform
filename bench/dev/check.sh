#!/usr/bin/env bash
set -uo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd -- "$repo" || exit 1
export PATH="$repo/bench/.venv/bin:$PATH"
export PYTHONPATH="$repo/bench/lib${PYTHONPATH:+:$PYTHONPATH}"
status=0
if "$repo/bench/.venv/bin/python" -m pytest -q bench/tests; then
    printf 'PASS pytest\n'
else
    printf 'FAIL pytest\n'
    status=1
fi
scripts=()
while IFS= read -r -d '' file; do
    scripts+=("$file")
done < <(find bench -type d \( -name .venv -o -name .git \) -prune -o -type f -name '*.sh' -print0)
if [[ -d bench/bin ]]; then
    for file in bench/bin/*; do
        [[ -f "$file" && "$file" != *.sh ]] || continue
        IFS= read -r first < "$file" || true
        if [[ "$first" =~ ^\#!.*(^|[/[:space:]])(ba|da|k|z)?sh([[:space:]]|$) ]]; then
            scripts+=("$file")
        fi
    done
fi
if "$repo/bench/.venv/bin/shellcheck" "${scripts[@]}"; then
    printf 'PASS shellcheck (%s scripts)\n' "${#scripts[@]}"
else
    printf 'FAIL shellcheck\n'
    status=1
fi
if ((status == 0)); then printf 'ALL PASS\n'; fi
exit "$status"