# Workbench Cheat Sheet

## Required Assumptions

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

## Devcontainer

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . esphome version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
devcontainer exec --workspace-folder . tools/espwb-status
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

After changing `.devcontainer/Dockerfile` or `.devcontainer/devcontainer.json`,
rebuild and replace the running container:

```bash
devcontainer up --workspace-folder . --remove-existing-container
devcontainer exec --workspace-folder . rg --version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
```

## ESPHome Validation

Identify the board first:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Use the detected chip family and flash size to choose ESPHome `board` and
`flash_size` values. The LED GPIO is not discoverable through `flash-id`; get it
from the board or vendor docs.

```bash
devcontainer exec --workspace-folder . esphome config examples/generic-blink/generic-blink.yaml
devcontainer exec --workspace-folder . esphome compile examples/generic-blink/generic-blink.yaml
```

Only flash a factory image immediately after a successful compile of the same
YAML. Ignored `.esphome/` build trees can contain stale binaries.

## Workbench Flashing

RFC2217 is for monitoring only, not flashing or reset control.

Use `tools/espwb-esptool` for workbench-slot flashing:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
devcontainer exec --workspace-folder . tools/espwb-esptool chip-id
devcontainer exec --workspace-folder . tools/espwb-esptool write-flash 0x0 path/to/firmware.factory.bin
```

The wrapper asks the workbench API to recover/release the slot after the
operation. Treat `serial: reachable` as a portal-socket check only; it does not
prove that application firmware is awake or responding.

## Native USB States

Native USB boards can change USB identity as they move between application
firmware, ESP ROM bootloader, and deep sleep. Start with:

```bash
devcontainer exec --workspace-folder . tools/espwb-status
```

| State | What status usually shows | Meaning | Next step |
| --- | --- | --- | --- |
| Application running | Application USB identity, portal reachable, and an app-specific probe responds | Firmware is executing | Continue normal testing. |
| ROM bootloader | Espressif ROM USB identity | Board is waiting for esptool | Flash or reset with BOOT released. |
| Deep sleep | USB serial disappears or portal state becomes stale until wake | Firmware intentionally powered down native USB | Wait for wake, use a configured wake source, or reset. |
| Portal/recovery issue | API reachable but serial refused, `running=false`, or `last_error` set | Workbench ownership and USB state disagree | Run reset-aware recovery, then re-check status. |

MagTag identities used in this repo:

```text
239a:80e5  Adafruit MagTag application USB identity
303a:0002  Espressif ESP32-S2 ROM bootloader
```

## MagTag LVGL Shapes Example

`examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml` targets the Adafruit
MagTag 2.9 ESP32-S2. It uses the `gdew029t5` e-paper panel with LVGL canvas
drawing actions to draw text, a circle, a triangle, and a rectangle.

**This repo intentionally uses a much simpler MagTag partition table than the
Adafruit_Wippersnapper_Arduino MagTag work. That project has its own constraints
and should not be disturbed. This ESPHome example uses a direct-USB,
factory/no-OTA layout so the flash recipe stays boring: bootloader, partition
table, and application image only.**

Validate and build:

```bash
devcontainer exec --workspace-folder . esphome config examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml
devcontainer exec --workspace-folder . esphome compile examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml
```

With the MagTag connected directly to the Linux host, check identity:

```bash
lsusb | grep -E '239a:80e5|303a:0002|MagTag|Espressif' || true
find /dev/serial/by-id -maxdepth 1 -type l -print 2>/dev/null
```

Enter ESP32-S2 ROM bootloader mode, then flash the rebuilt ESPHome artifacts:

```bash
.venv-esptool/bin/python -m esptool \
  --chip esp32s2 \
  --port /dev/serial/by-id/<esp32-s2-rom-loader> \
  --before no-reset \
  --after hard-reset \
  write-flash \
  --flash-mode dio \
  --flash-freq 80m \
  --flash-size 4MB \
  0x1000 examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/bootloader.bin \
  0x8000 examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/partitions.bin \
  0x10000 examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/firmware.bin
```

After flashing, reset or unplug/replug the directly connected MagTag without
holding any buttons, then verify:

```bash
lsusb | grep -E '239a:80e5|MagTag' || true
```

Use the local V4L2 camera directly for proof:

```bash
v4l2-ctl \
  --device /dev/video0 \
  --set-fmt-video=width=1280,height=720,pixelformat=MJPG \
  --stream-mmap \
  --stream-skip=10 \
  --stream-count=1 \
  --stream-to=artifacts/magtag-lvgl-shapes-final.jpg
```

Do not use the workbench path for this MagTag walkthrough. Use direct USB for
status, flashing, and camera proof.

## Serial Monitor

Use the project monitor wrapper from the repo root. It reads
`config/workbench.env`, opens raw DUT serial logs over `${ESP_PORT}`, and runs a
reset-aware `flash-id` after the monitor exits because closing RFC2217 sessions
can perturb some targets:

```bash
devcontainer exec --workspace-folder . tools/espwb-monitor
```

Set `ESPWB_MONITOR_IDLE_TIMEOUT=0` only for an intentional unbounded live
monitor. Set `ESPWB_MONITOR_RECOVER=0` only when intentionally debugging the
RFC2217 close behavior and only with
`ESPWB_MONITOR_ALLOW_UNRECOVERED_EXIT=1`.

## Camera Capture

The camera helpers run on the Linux host:

```bash
tools/workbench-camera-capture
tools/workbench-camera-sequence 4 3
```

Override `WORKBENCH_CAMERA_DEVICE` in `config/workbench.env` when a different
local camera is attached.
