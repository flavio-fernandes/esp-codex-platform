# Client configuration

Copy `config/bench.env.example` to ignored `config/bench.env`. These are trusted
local **shell** settings, unlike the host's literal `/etc/bench/bench.env`.
Tools locate defaults relative to the repository, not your working directory.
`BENCH_CONFIG_FILE` selects another trusted file. Explicit environment values
win over file defaults. Never put keys, passwords or private identities in examples.

| Variable | Meaning / default |
|---|---|
| `BENCH_AGENT` | SSH destination alias, `bench-agent` |
| `BENCH_SLOT` | Wrapper slot, `SLOT1` |
| `BENCH_ALLOW_NON_SLOT1` | Explicit approval for another slot, `0` |
| `BENCH_SSH_KEY` | Optional key path; normally SSH config/agent handles it |
| `BENCH_SSH_CONFIG` | Optional SSH config path; normally standard SSH config |
| `BENCH_KNOWN_HOSTS` | Optional trusted host-key file; checking always strict |
| `BENCH_SSH_CONNECT_TIMEOUT` | Seconds, `10` |
| `BENCH_SSH_SERVER_ALIVE_INTERVAL` / `BENCH_SSH_SERVER_ALIVE_COUNT_MAX` | `5` / `2` |
| `BENCH_MONITOR_MAX_TIME` | Follow duration, `300`, maximum 600 seconds |
| `BENCH_CAMERA_EXPOSURE` / `WIDTH` / `HEIGHT` / `SKIP` | Optional overrides of server camera defaults |
| `BENCH_STATIC_ONLY` | `1` skips every remote validation call |

The thin `wb` wrapper accepts explicit slots as part of a verb; its server enforces
configured slots, kind, locks and holds. The default-slot approval flag is for
`espwb-esptool` and `espwb-monitor`, not a substitute for server access control.

## One-release compatibility

`tools/lib/workbench-env` is the compatibility adapter. It accepts old
`WORKBENCH_*` and `ESPWB_*` names for the supported settings above, mapping the
suffix to `BENCH_*`, with a deprecation warning on **stderr**. `*_AGENT_HOST`
maps to `BENCH_AGENT`. `WORKBENCH_IP` maps to `BENCH_IP`, a fallback destination
`wbagent@<value>` only when no alias is set; it never selects an administrative
user. Explicit new names win over explicit old names; explicit old names win over
new file defaults. Do not set conflicting old prefixes for the same setting.
`ESPWB_CONFIG_FILE`, `STATIC_ONLY`, `ALLOW_NON_SLOT1` and `SSH_CONFIG` also warn
and map to their new names. An existing `config/workbench.env` is loaded with a
warning only when no new default file or explicit config selector exists.

Retired API URL/timeouts, local service/GPIO controls, SSH user selection,
post-operation recovery, monitor-close recovery and local camera device settings
have no equivalent: they warn and are ignored, never resurrecting the old
transport or granting shell access. `ESP_PORT` is no longer a transport selector;
set `BENCH_SLOT` instead. Legacy monitor max-time zero is bounded to 600 seconds;
idle timeout/recovery flags do not affect serial-follow. The server handles
kind-aware reopening and reset sequences.

`tools/validate-workbench.sh` is a two-line warning/exec shim. Existing camera
filenames remain, but always capture on the bench; sequence output is now a tar
archive. ESP reads must start at zero and writes use one merged image. Arbitrary
esptool options, nonzero-offset reads, admin shell commands and local camera
capture are intentionally not emulated by the restricted protocol.

Use verified SSH host keys; there is no automatic keyscan trust or private-key
copy. Dev Containers can forward an SSH agent; the read-only `/host-ssh` mount
can supply configuration/known_hosts explicitly. Do not embed machine-specific
paths in `.devcontainer/devcontainer.json`.
