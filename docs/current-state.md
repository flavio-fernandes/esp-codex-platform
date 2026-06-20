# Current State

Last updated: 2026-06-20, after the MagTag LVGL image flashed over local USB,
booted, and produced USB CDC heartbeat logs, but still rendered a blank e-paper
panel.

## Repository

- Repo: `/home/ff/src/esp-codex-platform`
- Do not push to GitHub without explicit approval.
- Do not flash slots other than `SLOT1`.
- Do not use RFC2217 reset control for flashing.
- `config/workbench.env` is the canonical ignored local config file.

## Current Objective

Prove the MagTag direct-USB firmware path one layer at a time, keeping each step
observable on the physical board before adding the next subsystem.

Current status: the minimal GPIO13 blink application boots after a direct USB
factory-image flash, BOOT0 release, and physical reset; the e-paper-only smoke
image produced a camera-visible geometry pattern; and the LVGL image boots far
enough to print a 10-second USB CDC heartbeat. The LVGL panel output is still
blank, so the remaining problem is LVGL rendering/refresh behavior rather than
flashing, boot, USB logging, SPI wiring, or basic e-paper refresh. The board is
battery-backed, so unplugging USB is not a true power cycle for this test setup.

Now proven:

- direct USB factory-image flash at `0x0`
- GPIO13 red D13 blink heartbeat
- e-paper without LVGL, using the MagTag SPI/display pins
- camera-visible display refresh
- USB CDC logging in the running app

Not yet proven:

- LVGL drawing visible content on e-paper

## Proven Blink And E-Paper Baselines

Files:

- `examples/magtag-basic-blink/magtag-basic-blink.yaml`
- `examples/magtag-basic-blink/.gitignore`

The blink YAML is intentionally minimal:

```yaml
esphome:
  name: magtag-basic-blink

esp32:
  board: adafruit_magtag29_esp32s2
  framework:
    type: arduino

output:
  - platform: gpio
    id: red_led
    pin: GPIO13

interval:
  - interval: 1s
    then:
      - output.turn_on: red_led
      - delay: 500ms
      - output.turn_off: red_led
```

Deliberately absent from the baseline:

- logger
- Wi-Fi
- API
- OTA
- display
- LVGL
- SPI
- custom PlatformIO options
- custom partition table

The installed Arduino MagTag variant was checked locally at
`/cache/platformio/packages/framework-arduinoespressif32/variants/adafruit_magtag29_esp32s2/pins_arduino.h`
and defines:

```c
#define LED_BUILTIN 13
```

## Blink Build And Flash Results

Configuration validation passed:

```bash
devcontainer exec --workspace-folder . esphome config examples/magtag-basic-blink/magtag-basic-blink.yaml
```

Compilation was run by the user and completed successfully.

Generated artifacts included:

```text
bootloader.bin          21760 bytes
firmware.bin           183504 bytes
firmware.factory.bin   249040 bytes
firmware.ota.bin       183504 bytes
partitions.bin         3072 bytes
flash_args
flash_args.in
```

The generated `flash_args` used the normal board defaults:

```text
--flash_mode dio --flash_freq 80m --flash_size 4MB
0x1000 bootloader/bootloader.bin
0x10000 magtag-basic-blink.bin
0x8000 partition_table/partition-table.bin
0x9000 ota_data_initial.bin
```

For the diagnostic flash, the complete `firmware.factory.bin` was written at
`0x0` instead of manually supplying split images:

```text
.venv-esptool/bin/python -m esptool \
  --chip esp32s2 \
  --port /dev/serial/by-id/usb-Espressif_ESP32-S2_0-if00 \
  --before no-reset \
  --after no-reset \
  write-flash 0x0 \
  examples/magtag-basic-blink/.esphome/build/magtag-basic-blink/.pioenvs/magtag-basic-blink/firmware.factory.bin
```

Write result:

```text
Wrote 249040 bytes (120538 compressed) at 0x00000000.
Hash of data verified.
```

Explicit verify result:

```text
Verification successful (digest matched).
```

After BOOT0 was released and RESET was pressed once, the user observed the
physical red D13 LED blinking.

No camera proof was used for the blink-only baseline. No ROM UART boot log was
captured because the physical blink passed.

## Direct USB Flash Rule

Use the local direct USB ESP32-S2 ROM bootloader port, not the workbench path.
Expected ROM identity:

```text
303a:0002  ESP32-S2 ROM bootloader
```

Prefer the generated `firmware.factory.bin` at `0x0` for complete-image tests.
Plain application `firmware.bin` alone is not enough after an erase.

Actual local-USB workflow used during this bring-up:

```bash
# The user runs long compiles outside Codex when usage is constrained.
devcontainer exec --workspace-folder . esphome compile examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml
```

```bash
# Find the local USB device after putting the MagTag in ROM bootloader mode.
find /dev/serial/by-id -maxdepth 1 -type l -print
find /dev -maxdepth 1 \( -name 'ttyACM*' -o -name 'ttyUSB*' \) -print
```

```bash
# Flash the combined factory image. Use the discovered port, often /dev/ttyACM0
# or /dev/serial/by-id/usb-Espressif_ESP32-S2_...-if00.
.venv-esptool/bin/python -m esptool \
  --chip esp32s2 \
  --port /dev/ttyACM0 \
  write-flash 0x0 \
  examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/firmware.factory.bin
```

```bash
# Read a short USB CDC log sample from the running app.
.venv-esptool/bin/python -c "import serial,time,sys; p='/dev/ttyACM0'; s=serial.Serial(p,115200,timeout=0.2); end=time.time()+14; data=bytearray();
while time.time()<end:
    data.extend(s.read(4096))
s.close(); sys.stdout.buffer.write(data)"
```

Expected healthy-app serial evidence:

```text
[I][app:060]: Running through setup()
[D][main:333]: ping output
```

```bash
# Capture one host-camera proof frame.
tools/workbench-camera-capture /tmp/magtag-lvgl-widgets.jpg
```

## Next E-Paper Smoke Step

The temporary e-paper-only smoke example intentionally kept the proven GPIO13
blink heartbeat and added only the MagTag e-paper wiring:

```yaml
spi:
  clk_pin: GPIO36
  mosi_pin: GPIO35

display:
  - platform: waveshare_epaper
    id: magtag_epaper
    cs_pin: GPIO8
    dc_pin: GPIO7
    busy_pin: GPIO5
    reset_pin: GPIO6
    model: gdew029t5
    update_interval: never
```

The display lambda clears the panel and draws a border, bar, circle, triangle,
and rectangle. It has no Wi-Fi, API, OTA, LVGL, custom PlatformIO options, or
custom partition table. It now also has an explicit USB CDC logger setting for
the next flash:

```yaml
logger:
  hardware_uart: USB_CDC
```

Observed result:

- The image flashed and verified as a complete `firmware.factory.bin` at `0x0`.
- The camera captured the panel with the simple geometry pattern visible.
- After that proof, the temporary e-paper-only example was removed so the repo
  keeps the LVGL example as the single MagTag display target.

## Next LVGL Step

The LVGL example now carries forward only the proven pieces:

- `adafruit_magtag29_esp32s2`
- explicit `logger: hardware_uart: USB_CDC`
- GPIO13 blink heartbeat
- SPI pins `GPIO36`/`GPIO35`
- e-paper pins `GPIO8`/`GPIO7`/`GPIO5`/`GPIO6`
- `gdew029t5`
- default generated partition table and `firmware.factory.bin` flashing at
  `0x0`
- USB CDC logger with a 10-second `ping output` heartbeat
- 10-second e-paper display refresh so LVGL has repeated opportunities to
  flush before the panel update

It deliberately does not use custom PlatformIO partition options.

Still deferred:

- Reintroduce remote monitoring only after direct USB is boringly reliable.
- If the widget version remains blank, remove LVGL software rotation next and
  test an unrotated minimal label.

## Target LVGL Example

The target LVGL example is:

- `examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml`

The intended final example draws:

- `MagTag LVGL` text
- a circle
- a rotated square/diamond while the triangle path is isolated
- a rectangle

**This repo intentionally uses a much simpler MagTag partitioning approach than
the Adafruit_Wippersnapper_Arduino MagTag work. That project has its own
constraints and should not be disturbed.**

## Lessons Preserved

- Direct local USB is the reliable path for this MagTag walkthrough.
- The workbench RFC2217 path is for monitoring, not flashing.
- Do not infer that `serial: reachable` means the application is running.
- A complete generated `firmware.factory.bin` at `0x0` reduced variables and
  booted the minimal app successfully.
- Physical LED observation is the pass criterion for blink, not USB identity.
- Keep adding one subsystem at a time: blink, then e-paper, then LVGL, then any
  optional connectivity.
