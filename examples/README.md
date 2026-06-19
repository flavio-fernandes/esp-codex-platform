# Examples

These are tiny ESPHome smoke tests for proving the devcontainer, workbench, and
flashing path.

- `generic-blink/` toggles one GPIO output.
- `generic-heartbeat/` logs a heartbeat and toggles one GPIO output.
- `feathers3-rgb-blink/` drives the Unexpected Maker FeatherS3 RGB LED
  through its LDO2 power pin.
- `magtag-basic-blink/` toggles the Adafruit MagTag red D13 LED on GPIO13.
- `magtag-lvgl-shapes/` targets the Adafruit MagTag 2.9 ESP32-S2 e-paper
  display with a small LVGL canvas.
- `esp32-devkit-gpio2-blink/` targets common CP2102 ESP32 DevKit-style boards
  with an ESP32-D0WD chip, 4MB flash, and a visible blue LED on GPIO2.

Before compiling or flashing the generic examples, edit the `board`, `flash_size`,
and `status_led_pin` substitutions at the top of the YAML so they match the
board installed in your workbench slot. Always confirm the physical board
identity first with:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

`flash-id` confirms the ESP chip family and flash size. It does not know which
GPIO drives a visible LED, so use the board's docs or downstream hardware notes
for `status_led_pin`.

For Unexpected Maker FeatherS3 boards, `GPIO13` is the blue user LED, while the
onboard RGB LED is a WS2812 on `GPIO40` powered by `GPIO39`/LDO2. Use
`feathers3-rgb-blink/` when you want a board-specific blink that pulses that
RGB LED power rail.
