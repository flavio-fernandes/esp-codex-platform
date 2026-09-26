# ESP32 / ESP8266 boards on the workbench

ESP flashing for agents is **off until a slot is enabled** on the Pi: `WB_AGENT_ESP_SLOTS` in
`/etc/workbench/agent.env`. `status` prints which slots are enabled. A refusal is the bench
owner's decision; report it and do not look for another way in.

```bash
ssh workbench-agent flash-esp SLOT2 flash-id              # ALWAYS first: chip, flash size, MAC
ssh workbench-agent flash-esp SLOT2 write-flash 0x0 < firmware.factory.bin
ssh workbench-agent serial-wait SLOT2 30 "some boot marker"
```

`flash-esp` runs the Pi-side reset-aware helper (`espwb-local-esptool`). The helper:
- stops the portal,
- re-finds the board by USB topology,
- runs esptool with retries,
- restarts the portal.

The whole portal restarts, so **every slot's serial proxy blips, the RP2 slots included.**

## Rules (learned the hard way; see `pitfalls.md` for sources)

- **RFC2217 is for monitoring only.** Never flash or reset through `rfc2217://...:400N`.
- **Never open the board's `/dev/ttyACM*` directly.** Opening the port toggles DTR/RTS, and a
  native-USB ESP32-S3 can drop into ROM download mode or freeze.
- **Identity before flashing.** If `flash-id` doesn't show the chip and flash size you expect,
  stop.
- **Flash only a fresh build.** `.esphome/` and `.pio/` keep stale images. After an erase, flash
  `firmware.factory.bin` at `0x0`; the app `firmware.bin` alone won't boot. Never copy offsets
  from a different board.
- **"serial: reachable" only means the portal socket answers**, not that the app is alive.
  Prove it with a serial marker or a photo.
- **The S3 can stay in ROM after a verified flash** if BOOT is held. If a manual reset boots the
  new app, the image was fine and the reset path is the problem.
- **A pySerial write timeout while the board shows its app USB identity** means it never entered
  ROM. Boards like the FeatherS3 need BOOT/EN wiring on the Pi GPIOs (BOOT=18, EN=17).
- **Closing an RFC2217 monitor can wedge an S3.** `flash-id` through the helper recovers it.
  `logger.deassert_rts_dtr` does not.
- **One writer per slot.** The helper takes a lock; exit 65 means someone else is flashing.
