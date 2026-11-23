# Tools validation matrix

Current contract: [bench/DESIGN.md](../bench/DESIGN.md). Current commissioning:
[runbook](bench-build-from-scratch.md). The dated 2026-06-21/22 hardware matrix
and environment-override findings are preserved in
[history](history/tools-validation-matrix-2026-06.md); do not transfer its PASS
labels to this deployment or assume its old slot inventory still applies.

| Current path | Automated check | Hardware acceptance |
|---|---|---|
| `wb`, `espwb-ssh` | Fake SSH argv, alphabet/length rejection, legacy aliases | Restricted keys and H1 smoke list |
| `espwb-status` | Status/health dispatch, OFF vs DEGRADED, rc propagation | Read fresh health on deployment |
| `espwb-esptool` | Identity, stdin bytes, atomic backup, invalid-input rejection | Identify, private backup, flash, marker |
| `espwb-monitor` | Bounded serial-follow; no reset command | Fresh application lines |
| Camera helpers | Remote snap/seq options, byte-preserving downloads | Open photo and inspect marker/framing |
| `validate-bench.sh` | Static mode has no network; live checks are read-only | Help/status/health from each client |

Run `bench/dev/setup-venv.sh`, then `bench/dev/check.sh`; require `ALL PASS`.
Tests use fake SSH, never the real bench. ShellCheck includes all shell clients
and their shared adapter. Follow the cheatsheet for board-specific acceptance;
unit tests do not establish physical reset, camera or flash behavior.
