# Agent instructions for this repository

Read `bench/DESIGN.md` before changing bench code. Use the current
`docs/bench-build-from-scratch.md` and `docs/bench-cheatsheet.md`; material in
`docs/history/` is not an installation recipe.

Allowed within an assigned task: edit workspace files, add small examples and
project-local tools, run `bench/dev/setup-venv.sh` and `bench/dev/check.sh`.
Never install system-wide packages or use sudo without explicit approval.
Never modify SSH keys, networking, system services or files outside the approved
workspace. Never push to GitHub without explicit approval.

Hardware requires explicit task approval. Use only `ssh bench-agent <verb>`
(or `tools/wb`) through the restricted client. No admin login, direct tty opens,
or ad hoc sysfs recovery. SLOT1 is the wrapper default; other slots need explicit
approval. Hold every slot for the entire job and release it in cleanup. Do not
flash from a code-only task.

Use `tools/espwb-esptool` for supported ESP identity/flash/backup operations;
`tools/espwb-monitor` uses serial-follow and never resets on exit. RP2 flashing
uses `tools/wb flash-pico SLOTn` with UF2 on stdin. Verify board identity and
back up unknown boards before writing. Compile the exact image you flash,
record its hash, prove a fresh application marker and inspect any display photo.
Do not infer application health from SSH connectivity or a successful write.

Never print, copy into the repository, commit or upload private keys, tokens,
passwords, `secrets.yaml`, firmware backups or private device identities.
Keep local configuration in ignored `config/bench.env`. Keep generated artifacts
and photos untracked. Do not build a large hardware-specific example in one step.
