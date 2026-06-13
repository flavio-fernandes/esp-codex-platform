# Examples

These are tiny ESPHome smoke tests for proving the devcontainer, workbench, and
flashing path.

- `generic-blink/` toggles one GPIO output.
- `generic-heartbeat/` logs a heartbeat and toggles one GPIO output.

Before compiling or flashing either example, edit the `board` and
`status_led_pin` substitutions at the top of the YAML so they match the board
installed in your workbench slot. Always confirm the physical board identity
first with:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

`flash-id` confirms the ESP chip family and flash size. It does not know which
GPIO drives a visible LED, so use the board's docs or downstream hardware notes
for `status_led_pin`.
