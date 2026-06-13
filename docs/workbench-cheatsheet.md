# Workbench cheat sheet

## Required assumptions

- Docker Engine is installed and `docker ps` works without `sudo` on the Linux
  host.
- The Dev Containers CLI is installed as `devcontainer` on the Linux host, or
  VS Code is opening the repo through its Dev Containers workflow.
- Local workbench settings are in ignored `config/workbench.env`.
- The workbench API is reachable at `${WORKBENCH_URL}`.
- SSH to `${WORKBENCH_USER}@${WORKBENCH_IP}` works.
- `/usr/local/bin/espwb-local-esptool` exists on the workbench.
- `SLOT1` is the safe default slot.
- Optional camera capture requires a local V4L2 camera and `v4l2-ctl` on the
  Linux host.

A Dev Container is the repo-defined Docker development environment in
`.devcontainer/`; it keeps ESPHome and helper tooling consistent. Learn more at
<https://containers.dev/>.

## Commands

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . esphome version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

After changing `.devcontainer/Dockerfile` or `.devcontainer/devcontainer.json`,
rebuild and replace the running container:

```bash
devcontainer up --workspace-folder . --remove-existing-container
devcontainer exec --workspace-folder . rg --version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
```

## ESPHome validation

Identify the board first:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Use the detected chip family and flash size to choose an ESPHome `board` value.
The LED GPIO is not discoverable through `flash-id`; get it from the board or
vendor docs.

Edit the `board` and `status_led_pin` substitutions in
`examples/generic-blink/generic-blink.yaml` for the actual board installed in
the workbench slot before compiling or flashing it.

```bash
devcontainer exec --workspace-folder . esphome config examples/generic-blink/generic-blink.yaml
devcontainer exec --workspace-folder . esphome compile examples/generic-blink/generic-blink.yaml
```

The first compile can take several minutes while PlatformIO fills the
devcontainer cache.

Only flash a factory image immediately after a successful compile of the same
YAML. The ignored `.esphome/` build tree can contain stale binaries from older
experiments, and `tools/espwb-esptool write-flash` will flash whatever file path
you pass it.

## Pre-flash identity check

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Verify the expected slot, chip family, and flash size for the board installed in
the downstream project immediately before flashing.

## Recovery

If the device under test is visually stuck or blank after a bad firmware flash
or after closing an RFC2217 serial monitor, keep using the reset-aware workbench
helper. Do not flash through RFC2217.

First try a reset-aware identity check:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

If `flash-id` fails with a pySerial write timeout and the workbench API reports
the board in application or UF2 USB identity instead of Espressif ROM/download
mode, the board has not entered the ROM bootloader. For boards like the
UnexpectedMaker FeatherS3, the manual recovery sequence is BOOT held while
RESET is pressed and released; persistent automation requires equivalent
BOOT/RESET wiring in the workbench fixture.

If the flashed app is bad, rebuild a known-good tiny example and flash its
fresh factory image.

## Serial monitor

RFC2217 is for monitoring only, not flashing or reset control.

Use the project monitor wrapper from the repo root. It reads
`config/workbench.env`, opens raw DUT serial logs over `${ESP_PORT}`, and runs a
reset-aware `flash-id` after the monitor exits because closing RFC2217 sessions
can perturb some targets:

```bash
devcontainer exec --workspace-folder . tools/espwb-monitor
```

Set `ESPWB_MONITOR_RECOVER=0` only when intentionally debugging the RFC2217
close behavior and you do not want the automatic post-monitor reset check.

If closing a serial monitor perturbs the target device, recover through:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

`tools/validate-workbench.sh` skips its RFC2217 open/close test by default.
Set `RUN_RFC2217_TEST=1` only when intentionally debugging the monitor path.

## Camera capture

The camera helpers run on the Linux host, not through RFC2217 and not through
the workbench flashing path:

```bash
tools/workbench-camera-capture
tools/workbench-camera-sequence 4 3
```

The default `WORKBENCH_CAMERA_DEVICE` is the stable `/dev/v4l/by-id/...` path
for the current workbench camera. Override it in `config/workbench.env` when a
different local camera is attached.
