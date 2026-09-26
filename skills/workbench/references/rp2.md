# RP2040 / RP2350 boards on the workbench (Pico, Pico W, Pimoroni Galactic Unicorn, ...)

## How `flash-pico` works (so you can read its errors)

1. Takes a portal slot lease (mode `flashing`), so other bench operations on the slot are refused.
2. Stops the slot's serial proxy.
3. Does a **1200-baud touch** on the board's tty. Arduino-pico and pico-sdk firmware with USB
   stdio reboot into the USB boot ROM (BOOTSEL: `2e8a:0003` RP2040, `2e8a:000f` RP2350).
   If that does not work, it falls back to `picotool reboot -f -u`.
4. `picotool load -v -x`: write, verify, run.
5. Waits for the application's tty to come back and makes sure the portal proxy runs again.

Exit codes: `0` ok · `2` bad request · `65` the slot is busy · `66` no device in the slot ·
`67` never reached BOOTSEL · `68` flashed, but the tty or proxy did not come back ·
`69` more than one board is in BOOTSEL · `75` the slot lease is held by someone else.

- **Exit 67:** the firmware on the board has no USB stdio/reset interface (a crashed or bare
  image). Recovery needs the physical BOOTSEL button. Say so to the human; do not retry.
- **Exit 68:** the image was written but did not boot to a USB tty. It may have crashed early.
  Check `status`; flash the backup to recover.

## Serial output from RP2 boards

Arduino-pico and pico-sdk USB stdio **drop all output while DTR is low**. The portal's
proxy keeps DTR low, which is correct for ESP32. So every RP2 slot runs
`wb-rp2-console@SLOTn` on the Pi. It holds the proxy's single RFC2217 client with DTR high, so
`serial-tail` and `serial-wait` see the board, and `serial-write` goes through it.

- If an RP2 slot shows no serial output at all, the holder is not running. That is a bench-owner
  problem; report it.
- Lines printed in the first ~2 s after a reboot can be missed while the proxy reconnects. For
  anything you must catch, print it repeatedly or periodically.

## Backup and restore

```bash
ssh workbench-agent pico-save SLOT1 > backup-$(date +%Y%m%d-%H%M%S).uf2   # whole flash, ~4 MB
ssh workbench-agent flash-pico SLOT1 < backup-....uf2                     # restore app + filesystem
```

The backup includes the filesystem, which holds Wi-Fi credentials. Keep it out of git, out of
chat, and out of public places.

## AWTRIX Galactic Unicorn build (on the build VM)

The awtrix-ng repo ships a CI container. Build inside it on `hermes-playground`:

```bash
cd ~/bench/<your-dir>            # a clone of the fork's galactic-unicorn branch
podman run --rm -v "$PWD":/w:Z -v pio-home:/root/.platformio \
  localhost/awtrix-build tools/container/ci-local.sh build galactic_unicorn
sha256sum .pio/build/galactic_unicorn/firmware.uf2
```

- If `localhost/awtrix-build` is missing, the VM was reset. Rebuild it with
  `podman build -t awtrix-build tools/container`, which takes a few minutes.
- The `pio-home` volume caches toolchains. It is a cache, not garbage.
- From the HOST (Claude Code): `incus exec -T hermes-playground -- su -l hermes -c '...'`.
  The VM user's audited login shell does **not** forward positional arguments (`_ "$x"`), so
  a `$1` arrives empty. Validate simple values (e.g. `[[ $N =~ ^[0-9]{4}$ ]]`) and inline them.
- Extra compile flags for a one-off build: add
  `-e PLATFORMIO_BUILD_FLAGS="-DNAME=value"` to `podman run`.

## Acceptance test: the bench "hello" firmware

Proves the whole loop (build → flash → serial → camera) with a result nobody can fake.

1. Pick a random 4-digit NONCE (e.g. `shuf -i 1000-9999 -n1`).
2. Back up the board: `pico-save SLOT1 > backup.uf2`.
3. In a throwaway clone, apply this patch to `src/platform/rp2040/main_rp2040.cpp`. Insert it
   directly after the line `canvas = new awtrix::Canvas(board->matrixWidth(), board->matrixHeight());`:

   ```cpp
   #ifdef AWTRIX_BENCH_HELLO
     {
       const long nonce = AWTRIX_BENCH_HELLO;
       char msg[16];
       snprintf(msg, sizeof msg, "HI %04ld", nonce);
       const uint32_t colour[] = {0, 0xFF0000u, 0x00FF00u, 0x0000FFu};
       const char* phaseName[] = {"text", "red", "green", "blue"};
       for (;;) {
         for (int phase = 0; phase < 4; ++phase) {
           canvas->clear(colour[phase]);
           if (phase == 0) text::drawText(*canvas, awtrixFont(), 2, 6, msg, 0xFFFFFFu);
           board->show(*canvas);
           for (int t = 0; t < (phase == 0 ? 10 : 4); ++t) {
             Serial.print("BENCH_HELLO ");
             Serial.print(msg + 3);
             Serial.print(" phase=");
             Serial.println(phaseName[phase]);
             delay(1000);
           }
         }
       }
     }
   #endif
   ```

   It loops forever: `HI <NONCE>` for 10 s, then solid red, green and blue for 4 s each.
4. Build with `-e PLATFORMIO_BUILD_FLAGS="-DAWTRIX_BENCH_HELLO=<NONCE>"` and record the sha256.
5. Flash it with `flash-pico SLOT1`. **Pass:** exit 0.
6. `serial-wait SLOT1 30 "BENCH_HELLO <NONCE>"`. **Pass:** exit 0.
7. Photos. `snap exposure=40 > hello.jpg` and look at it. **Pass:** you can read `HI <NONCE>`.
   Repeat `snap-stats exposure=40` every ~2 s for up to 40 s. **Pass:** you see `dominant` R,
   G and B each at least once.
8. Restore: `flash-pico SLOT1 < backup.uf2`. Then `snap`, and confirm the normal clock screen
   is back.
9. Report each step's actual output: sha256, exit codes, the matched serial line, and the
   stats JSON.
