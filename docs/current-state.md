# Current State

Last updated: 2026-06-19, after stepping back from the MagTag LVGL/display
path to a basic ESP32-S2 blink baseline.

## Repository

- Repo: `/home/ff/src/esp-codex-platform`
- Do not push to GitHub without explicit approval.
- Do not flash slots other than `SLOT1`.
- Do not use RFC2217 reset control for flashing.
- `workbench.env` at the repo root is untracked local config and should be left
  alone unless the user asks.

## Main Goal

Provide a happy-path MagTag LVGL shapes example that can be built with ESPHome
and flashed from the Linux host over direct local USB.

The current tactical goal is smaller: first prove the MagTag can boot a minimal
ESPHome Arduino app from flash, then return to the e-paper/LVGL stack.

The example draws:

- `MagTag LVGL` text
- a circle
- a triangle
- a rectangle

## MagTag Example

Files:

- `examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml`
- `examples/magtag-lvgl-shapes/partitions.csv`

The YAML targets:

- `esp32.board: adafruit_magtag29_esp32s2`
- `flash_size: 4MB`
- Arduino framework
- Waveshare e-paper `model: gdew029t5`
- SPI pins `GPIO36` clock and `GPIO35` MOSI
- Display pins `GPIO8` CS, `GPIO7` DC, `GPIO5` busy, `GPIO6` reset
- LVGL rotation `270`
- LVGL canvas size `296x128`

**This repo intentionally uses a much simpler MagTag partition table than the
Adafruit_Wippersnapper_Arduino MagTag work. That project has its own constraints
and should not be disturbed. This ESPHome example uses a direct-USB,
factory/no-OTA layout so the flash recipe stays boring: bootloader, partition
table, and application image only.**

Current partition table:

```csv
app0,     app,  factory, 0x10000,  0x200000,
nvs,      data, nvs,     0x210000, 0x100000,
spiffs,   data, spiffs,  0x310000, 0x0E0000,
coredump, data, coredump,0x3F0000, 0x010000,
```

## Build Status

The user rebuilt successfully on 2026-06-19:

```bash
devcontainer exec --workspace-folder . esphome compile examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml
```

Compile completed successfully.

## Latest Failure

After the successful LVGL rebuild, the host erased the MagTag flash and wrote
the split ESPHome artifacts:

- `bootloader.bin` at `0x1000`
- `partitions.bin` at `0x8000`
- `firmware.bin` at `0x10000`

The esptool write and verification completed successfully. After hard reset,
watchdog reset, and a manual reset button press, the board still enumerated as
the ESP32-S2 ROM bootloader:

```text
303a:0002  ESP32-S2
```

That means the transport and flash write path appear to work, but the board did
not reach the expected MagTag application USB identity:

```text
239a:80e5  Adafruit EPD MagTag 2.9" ESP32-S2
```

Do not assume the LVGL/display code is good until a basic app boots correctly.

## Basic Blink Baseline

A deliberately tiny MagTag baseline now exists:

- `examples/magtag-basic-blink/magtag-basic-blink.yaml`
- `examples/magtag-basic-blink/partitions.csv`

The Arduino MagTag variant defines:

- `LED_BUILTIN` as GPIO13
- USB CDC logging as the logger transport

The basic blink app only:

- targets `adafruit_magtag29_esp32s2`
- uses `flash_size: 4MB`
- uses the same direct-USB factory partition table
- toggles GPIO13 every 500 ms
- logs `magtag basic blink on/off`

Its config has been validated:

```bash
devcontainer exec --workspace-folder . esphome config examples/magtag-basic-blink/magtag-basic-blink.yaml
```

Next compile step:

```bash
devcontainer exec --workspace-folder . esphome compile examples/magtag-basic-blink/magtag-basic-blink.yaml
```

## Direct USB Flash Recipe

Use the local direct USB ESP32-S2 ROM bootloader port, not the workbench path.
Expected ROM identity:

```text
303a:0002  ESP32-S2 ROM bootloader
```

Flash the rebuilt ESPHome artifacts:

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

For the basic blink baseline, use the same command shape but replace
`magtag-lvgl-shapes` paths with `magtag-basic-blink` paths.

After a full erase, plain application `firmware.bin` alone is not enough. The
ESP32-S2 ROM needs a bootloader, a partition table, and an application image at
the offsets above. A single-file flash is only valid when the file is a merged
image that already contains those pieces and is written at `0x0`. For the
current debugging pass, split-image flashing is preferred because every offset
is explicit.

After flashing, reset or unplug/replug the MagTag without holding any buttons.
Expected application identity:

```text
239a:80e5  Adafruit EPD MagTag 2.9" ESP32-S2
```

## Camera Proof

Use the local V4L2 camera directly:

```bash
v4l2-ctl \
  --device /dev/video0 \
  --set-fmt-video=width=1280,height=720,pixelformat=MJPG \
  --stream-mmap \
  --stream-skip=10 \
  --stream-count=1 \
  --stream-to=artifacts/magtag-lvgl-shapes-final.jpg
```

## Lessons Preserved

- Direct local USB is the reliable path for this MagTag walkthrough.
- The workbench RFC2217 path is for monitoring, not flashing.
- Do not infer that `serial: reachable` means the application is running.
- If a verified flash leaves the board in ROM bootloader, first suspect reset
  and boot-button state before changing firmware again.
- First prove a basic GPIO13 blink app boots before returning to e-paper,
  LVGL, NeoPixels, Wi-Fi, or other higher-level behavior.
