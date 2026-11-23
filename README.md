# ESP Codex Platform

A safety-first ESPHome starter and Raspberry Pi 4 / Debian 13 hardware bench.
The bench owns each serial port once; clients use restricted SSH verbs for
flashing, monitoring, recovery and camera evidence. No client needs an admin
shell on the bench.

## Start here

- [Build the bench](docs/bench-build-from-scratch.md): port map, installation,
  restricted keys, commissioning, recovery and USB-SSD hardening.
- [Command cheatsheet](docs/bench-cheatsheet.md): verbs, holds and exit codes.
- [Design contract](bench/DESIGN.md): authoritative paths and protocols.
- [Client configuration](config/README.md): `BENCH_*` and migration aliases.
- [Historical material](docs/history/README.md): retired setup and dated results.

## Quick start

You need Git, Bash and SSH; configure the `bench-agent` alias with a dedicated
restricted key and a verified host fingerprint. ESPHome builds additionally
need Docker and Dev Containers (or your own isolated toolchain).

```sh
cp config/bench.env.example config/bench.env
# Edit local defaults; never commit this file.
BENCH_STATIC_ONLY=1 tools/validate-bench.sh
tools/validate-bench.sh
tools/wb status
```

The optional `.devcontainer/` supplies ESPHome and developer tools. It uses SSH
agent forwarding provided by Dev Containers; configure the alias/known_hosts
inside the container, or set `BENCH_SSH_CONFIG` and `BENCH_KNOWN_HOSTS` to the
read-only `/host-ssh` mount. Absolute paths in the host SSH config may need a
container-local counterpart. No helper copies private keys or trusts keyscan
output automatically.

```sh
devcontainer up --workspace-folder . --remove-existing-container
devcontainer exec --workspace-folder . esphome version
devcontainer exec --workspace-folder . tools/validate-bench.sh
```

## Build, identify, flash, prove

First choose a supported board and an approved slot. Edit the board, flash size
and pins in an [example](examples/README.md); generic defaults are placeholders,
not a hardware inventory. Compile that exact source before flashing its image.

```sh
tools/wb hold SLOT1 60 blink-proof
tools/espwb-esptool flash-id
devcontainer exec --workspace-folder . esphome config examples/generic-blink/generic-blink.yaml
devcontainer exec --workspace-folder . esphome compile examples/generic-blink/generic-blink.yaml
tools/espwb-esptool write-flash 0x0 examples/generic-blink/.esphome/build/generic-blink/.pioenvs/generic-blink/firmware.factory.bin
tools/espwb-monitor 30
tools/workbench-camera-capture artifacts/after.jpg
tools/wb history 10
tools/wb release SLOT1
```

Stop on any failed step; release holds even after failure. Back up unknown boards
before the first write. Blink requires visual proof; for display/serial work add
a fresh marker to the image and confirm it after flashing. Open the photo before
claiming success. A stale `.esphome/` image or buffered log is not proof.

## Client tools

| Tool | Purpose |
|---|---|
| `tools/wb`, `tools/espwb-ssh` | Restricted `ssh ${BENCH_AGENT:-bench-agent} <verb>` |
| `tools/espwb-status` | Status plus health; distinguishes SSH OFF from DEGRADED |
| `tools/espwb-esptool` | Identity, single merged-image write, zero-offset backup |
| `tools/espwb-monitor [seconds]` | `serial-follow`, bounded to 600 s, no reset on exit |
| `tools/workbench-camera-capture` | Remote snap, atomic local JPEG |
| `tools/workbench-camera-sequence` | Remote timed sequence, atomic tar archive |
| `tools/validate-bench.sh` | Local prerequisites and read-only remote checks |

Existing tool names remain where they still describe the operation. The old
validation filename is a warning/exec shim for one release. Configuration
compatibility and intentionally unsupported legacy operations are documented in
`config/README.md`. Service logic is entirely in `bench/`; historical helpers
must not be installed.

## Develop and publish safely

```sh
bench/dev/setup-venv.sh
bench/dev/check.sh
```

The suite uses fake USB trees, PTYs and fake tools/SSH without physical hardware;
ShellCheck covers both `bench/` and `tools/`. It must finish with `ALL PASS`.
Real hardware commissioning remains a separate gate. Keep firmware, backups,
photos, private identities, SSH keys, `secrets.yaml`, local env files and generated
build trees out of Git. Run the [public-release checklist](docs/public-release-checklist.md)
before publication; GitHub pushes require explicit approval.

This is a reusable starter, not a device abstraction layer. Put detailed pinouts,
Home Assistant entities, UI and private evidence in downstream projects. Add one
subsystem at a time. See [native USB recovery](docs/native-usb-recovery.md) for
boards whose application and bootloader identities differ.
