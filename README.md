# ESP Codex Platform

A small, safety-first starter repo for ESPHome projects that are developed from
a workstation through a Linux dev host and an ESP workbench.

It is meant to give each new project the boring pieces up front: a repeatable
devcontainer, reset-aware flashing wrappers, serial-monitor guardrails, generic
ESPHome smoke examples, and docs that make the workflow easy to hand off.

```text
Mac/workstation -> VS Code SSH -> Linux host -> Docker/devcontainer -> ESP workbench -> ESP board
```

## What You Get

- ESPHome devcontainer based on the stable ESPHome image.
- Project-local workbench wrappers in `tools/`.
- Reset-aware `chip-id`, `flash-id`, `read-flash`, and `write-flash` through
  `tools/espwb-esptool`.
- RFC2217 serial monitoring through `tools/espwb-monitor`.
- Workbench validation through `tools/validate-workbench.sh`.
- Safe defaults for `SLOT1`, ignored local config, ignored secrets, ignored
  build output, and ignored local artifacts.
- Tiny generic ESPHome blink and heartbeat examples.
- Docs templates for current state, roadmap, GitHub setup, references, public
  hygiene, and workbench commands.

## What This Is Not

This is not a generator, package manager, or hardware abstraction layer. It is a
plain repo you can copy, fork, or use as a starting point.

This repo intentionally does not include board pinouts, private network values,
Home Assistant entity IDs, device photos, firmware backups, generated binaries,
or real secrets. Those belong in the downstream device project, and private
values should stay in ignored local files.

## Prerequisites

Install these on the Linux host before using the quick start:

- Docker Engine, with your user able to run `docker ps` without `sudo`.
- Dev Containers CLI, available as `devcontainer` on `PATH`. VS Code with the
  Dev Containers extension can also build/open the container, but the examples
  below use the CLI.
- Git, Bash, and an editor.
- SSH access from the Linux host or devcontainer to the ESP workbench.
- Optional: `v4l2-ctl` from `v4l-utils` when using the local camera helpers.

This repo uses a Dev Container: a Docker-based development environment defined
in `.devcontainer/` so ESPHome, esptool, and supporting CLI tools are consistent
across checkouts. Curious readers can learn more at
[containers.dev](https://containers.dev/).

This repo's devcontainer supplies ESPHome, esptool, Python serial tooling,
`curl`, `jq`, `rg`, `shellcheck`, and other project tools inside the container.
It does not install Docker, the `devcontainer` CLI, SSH keys, or workbench
system services on the host.

The ESP workbench must already provide:

- A reachable workbench API at `${WORKBENCH_URL}`.
- SSH for `${WORKBENCH_USER}@${WORKBENCH_IP}`.
- The reset-aware helper `/usr/local/bin/espwb-local-esptool`.
- RFC2217 serial service for monitoring at `${ESP_PORT}`.
- A board installed in `SLOT1`, unless a downstream project explicitly
  documents and approves another slot.

## Quick Start

Clone the starter into any directory name and enter it:

```bash
git clone https://github.com/flavio-fernandes/esp-codex-platform.git my-esp-project
cd my-esp-project
```

The devcontainer uses the local folder basename, so commands work the same
whether the checkout is named `my-esp-project`, `esp-codex-playground`, or
something else.

Create local-only workbench settings:

```bash
cp config/workbench.env.example config/workbench.env
$EDITOR config/workbench.env
```

Keep `config/workbench.env` private. The committed example uses documentation
placeholders such as `192.0.2.10`.

Build or open the devcontainer:

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . esphome version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
```

If `devcontainer` is not found, install the Dev Containers CLI on the Linux
host or open the repository through VS Code's Dev Containers workflow. If
`docker ps` requires `sudo`, fix the host Docker setup before continuing.

Identify the board before changing or flashing examples:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Record the detected chip family and flash size. They must match the ESPHome
`board` value you use later. The probe cannot discover the visible LED pin, so
get that pin from the board silkscreen, vendor docs, or the downstream
project's hardware notes.

Check the blink example before building it. The committed defaults are a
placeholder `featheresp32` board with `4MB` flash and `GPIO13` as the LED pin.
In a fork, edit the `board`, `flash_size`, and `status_led_pin` substitutions in
`examples/generic-blink/generic-blink.yaml` so they match the board installed
in your workbench slot.

Validate a tiny ESPHome example:

```bash
devcontainer exec --workspace-folder . esphome config examples/generic-blink/generic-blink.yaml
devcontainer exec --workspace-folder . esphome compile examples/generic-blink/generic-blink.yaml
```

The first compile can take several minutes while PlatformIO downloads toolchains
into the devcontainer cache. Later compiles are much faster.

Check the board identity again immediately before flashing:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Confirm the detected chip family and flash size still match the `board` and
`flash_size` values you put in the blink YAML. Stop here if they do not match.

Flash only after a successful compile of that same YAML:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool write-flash 0x0 examples/generic-blink/.esphome/build/generic-blink/.pioenvs/generic-blink/firmware.factory.bin
```

## Workbench Tools

`tools/espwb-esptool` SSHs to the workbench and calls the reset-aware helper
there. Use it for esptool operations and flashing:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
devcontainer exec --workspace-folder . tools/espwb-esptool chip-id
devcontainer exec --workspace-folder . tools/espwb-esptool read-flash 0x0 0x1000 artifacts/readback.bin
devcontainer exec --workspace-folder . tools/espwb-esptool write-flash 0x0 path/to/firmware.factory.bin
```

If `flash-id` fails with a pySerial write timeout and the workbench API reports
the board in application or UF2 USB identity instead of Espressif ROM/download
mode, the board has not entered the ROM bootloader. For boards like the
UnexpectedMaker FeatherS3, the manual recovery sequence is BOOT held while
RESET is pressed and released; unattended recovery needs equivalent BOOT/RESET
wiring in the workbench fixture.

`tools/espwb-monitor` opens raw DUT serial logs through RFC2217:

```bash
devcontainer exec --workspace-folder . tools/espwb-monitor
```

By default it runs `tools/espwb-esptool flash-id` when the monitor exits. This
gives the reset-aware helper a chance to recover targets that are perturbed by
closing an RFC2217 session. Set `ESPWB_MONITOR_RECOVER=0` only when you are
intentionally debugging monitor close behavior.

`tools/workbench-camera-capture` captures one JPEG from a local V4L2 camera on
the Linux host, and `tools/workbench-camera-sequence` captures a timed sequence:

```bash
tools/workbench-camera-capture
tools/workbench-camera-sequence 4 3
```

By default it uses the current workbench camera's stable `/dev/v4l/by-id/...`
path. Override `WORKBENCH_CAMERA_DEVICE` in `config/workbench.env` when a
different local camera is attached.

## Safety Rules

- Keep `SLOT1` as the safe default unless a downstream project explicitly
  approves another slot.
- Use `tools/espwb-esptool` for flashing and esptool operations.
- Use RFC2217 only for serial monitoring through `tools/espwb-monitor`.
- Do not use RFC2217 reset control for flashing.
- Only flash a `firmware.factory.bin` immediately after compiling the matching
  YAML; ignored `.esphome/` build trees can contain stale binaries.
- Keep `config/workbench.env`, `secrets.yaml`, `.env` files, private keys,
  tokens, generated firmware, `.esphome/`, and `artifacts/` out of git.
- Run `docs/public-release-checklist.md` before making any repo public.

## Repository Map

- `.devcontainer/` - ESPHome devcontainer definition.
- `config/workbench.env.example` - safe placeholder workbench settings.
- `examples/generic-blink/` - tiny GPIO blink smoke test.
- `examples/generic-heartbeat/` - tiny heartbeat/logging smoke test.
- `tools/espwb-ssh` - project-local SSH wrapper.
- `tools/espwb-esptool` - reset-aware esptool/flashing wrapper.
- `tools/espwb-monitor` - RFC2217 serial monitor wrapper.
- `tools/workbench-camera-capture` - optional local V4L2 camera snapshot.
- `tools/workbench-camera-sequence` - optional timed camera snapshots.
- `tools/validate-workbench.sh` - local toolchain and workbench validation.
- `docs/workbench-cheatsheet.md` - command reference.
- `docs/public-release-checklist.md` - public hygiene checks.

## Starting a Device Project

For a real device repo, keep the first firmware intentionally tiny:

1. Copy this starter or use it as the base repo.
2. Set local workbench values in ignored `config/workbench.env`.
3. Validate the devcontainer and workbench path.
4. Add one minimal board-specific ESPHome YAML.
5. Run `esphome config` and `esphome compile`.
6. Confirm board identity with `tools/espwb-esptool flash-id`.
7. Flash through `tools/espwb-esptool`.
8. Record the validated facts in `docs/current-state.md`.

Add display, touch, sensors, Home Assistant, OTA, and UI behavior one step at a
time. Keep hardware-specific notes, photos, generated firmware, and private
values in the downstream project, not in this generic starter.
