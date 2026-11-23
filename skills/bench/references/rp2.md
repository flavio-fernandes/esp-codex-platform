# RP2040 / RP2350

Hold the approved slot and check status before using `pico-info`, `pico-save`,
or `flash-pico`. Kind comes from VID:PID; slot identity comes from the USB port
path. Multiple BOOTSEL boards are safe: the helper selects the bus/address at
**that port**, never whichever board is the only one visible.

The helper locks and pauses only this slot. A running board receives a
**1200-baud touch**, then a bounded wait for BOOTSEL at the same path, with a
targeted `picotool reboot -f -u` fallback. It checks UF2 magic and uses picotool
load/verify/execute. Do not manually open ttys or run picotool through an admin
login. Serial ownership resumes automatically; RP2 DTR and RTS are high.

`pico-save SLOTn > backup.uf2` exports whole flash, including credentials. Use
umask 077 and a temporary file renamed only on success. Keep it outside repos.
Restore with `flash-pico SLOTn < backup.uf2`. Info/save of a running board
reboot it back; a board already in BOOTSEL stays there. Do not repeatedly run
info as a liveness probe. After load, require fresh serial and visual evidence.

picotool rc **254 after OK** on a filesystem-only UF2 is treated by the helper
as success with a warning; it is not a generic permission to ignore tool errors.
A crashed image without a working USB stack cannot accept the touch or software
reboot. A human must use the physical BOOTSEL button. Stop and report rc 67/71;
do not keep reflashing or assume a USB port reset supplies physical BOOTSEL.

## Generic PlatformIO Pico hello (W8 approach)

Build in the approved sandbox, not on the bench. Start with a minimal Pico
Arduino serial/LED program before adding a panel library. Choose the environment
for the **actual board**; this example is for a plain RP2040 Pico, not a Pico W,
RP2350, or a custom display board. Record the resolved platform/toolchain version
and hash of `.pio/build/pico/firmware.uf2` from the successful build.

`platformio.ini`:

```ini
[env:pico]
platform = https://github.com/maxgerhardt/platform-raspberrypi.git
board = rpipico
framework = arduino
board_build.core = earlephilhower
monitor_speed = 115200
```

`src/main.cpp`:

```cpp
#include <Arduino.h>
// Replace for EVERY build; 1234 is an example, not a proof nonce.
static const char *nonce = "1234";
static unsigned long counter = 0;
void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}
void loop() {
  digitalWrite(LED_BUILTIN, counter % 2);
  Serial.print("HELLO ");
  Serial.print(nonce);
  Serial.print(" counter=");
  Serial.println(counter++);
  delay(1000);
}
```

Run `pio run -e pico`, then flash the emitted UF2 through the standard loop.
Do not wait forever for Serial in setup: an unattached monitor must not prevent
boot. Repeated nonce/counter lines make future-only `serial-wait` useful. This
plain Pico hello proves serial/LED operation only; for a display board add the
same nonce to the display and inspect a photo before claiming display proof.

AWTRIX hello (Galactic Unicorn): `references/awtrix-hello-main-picow.patch`
applies to awtrix-ng `main-picow` (verified at a0d1df1, 2026-11-22, on a
Pico 2 W Unicorn). Build with `-DAWTRIX_BENCH_HELLO=<NONCE>`. It shows `HI <NONCE>`
for 10 s, then solid red, green and blue for 4 s each, printing
`BENCH_HELLO <NONCE> phase=<name>` every second. Build the repo's container from the
repo root (`podman build --file tools/container/Containerfile .`). Do not apply the
archived 1.2.1-tree hello patch to a newer tree.

Colour proof with `snap-stats`: count a colour when `lit_fraction >= 0.15` and
`purity >= 0.40`. With this camera green reads about 0.44; the lit floor alone keeps
the dim white text phase (lit ~0.02) from counting as blue.

Whole-flash backups (`pico-save`) carry Wi-Fi credentials. On shared benches the
operator, not an automated worker, takes and restores them.
## Wi-Fi for ESPHome images (Pico W / Pico 2 W)

ESPHome on RP2 (`rp2:` with `board: rpipicow`; the older `rp2040:` key is deprecated) is built
without credentials: `wifi: ap: {}` + `improv_serial:`, no `!secret`. After `flash-pico` and a
fresh serial line, pipe the SSID and password lines to `ssh bench-agent wifi-provision SLOTn`.
The CDC port is opened with DTR high, as `bench-serial` does, so the logger keeps talking.
