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

## Quick Start

Clone the starter and enter the repo:

```bash
git clone https://github.com/flavio-fernandes/esp-codex-platform.git
cd esp-codex-platform
```

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

Validate a tiny ESPHome example:

```bash
devcontainer exec --workspace-folder . esphome config examples/generic-blink/generic-blink.yaml
devcontainer exec --workspace-folder . esphome compile examples/generic-blink/generic-blink.yaml
```

Identify the board before flashing:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

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

`tools/espwb-monitor` opens raw DUT serial logs through RFC2217:

```bash
devcontainer exec --workspace-folder . tools/espwb-monitor
```

By default it runs `tools/espwb-esptool flash-id` when the monitor exits. This
gives the reset-aware helper a chance to recover targets that are perturbed by
closing an RFC2217 session. Set `ESPWB_MONITOR_RECOVER=0` only when you are
intentionally debugging monitor close behavior.

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
