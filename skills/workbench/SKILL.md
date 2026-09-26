---
name: workbench
description: "Flash ESP32/RP2040 + webcam photos on the workbench Pi. Use when firmware must be flashed onto real hardware on the bench, a board's serial output read, or a photo taken to prove what a display/LED matrix shows (e.g. Galactic Unicorn, ESP32 panels). Not needed for compile-only work."
version: "1.0.0"
author: "Gute + assistant (explicitly authored, not auto-learned)"
license: MIT
tags: [workbench, testbench, flash, firmware, esp32, rp2040, pico, uf2, camera, serial, hardware]
---

# Workbench: flash real boards, read serial, photograph the result

The workbench is a Raspberry Pi on the LAN with microcontroller boards on a USB hub
(numbered **slots**) and a USB webcam pointed at them. It runs the upstream
Embedded-AI-Harness rfc2217 portal plus a small restricted agent interface.

**You reach it only through one SSH alias, `workbench-agent`** (`testbench-agent` is the
same host). There is no shell on the other side: every call is one verb from the table below,
firmware goes IN on stdin, photos and backups come OUT on stdout, messages go to stderr.
Every call is audited on the Pi (`journalctl -t wb-agent`), so report what you actually ran.

```bash
ssh workbench-agent status                      # what is plugged in where
ssh workbench-agent help                        # the verb list
```

## Verbs

| Verb | What it does |
|---|---|
| `status` | slots, what is in each, camera present, allowed slots |
| `serial-tail SLOTn [lines]` | last lines of the slot's serial buffer (passive, safe) |
| `serial-wait SLOTn SECONDS PATTERN` | wait for PATTERN (spaces allowed) on serial; exit 0 if matched |
| `serial-write SLOTn TEXT` | send TEXT + CRLF |
| `snap [exposure=auto\|N] [res=WxH]` | one JPEG on stdout |
| `snap-stats [exposure=N] [threshold=N]` | JSON colour of the lit pixels: `dominant` R/G/B, `lit_fraction` |
| `seq COUNT INTERVAL [opts]` | tar of COUNT JPEGs on stdout |
| `burst COUNT [opts]` | tar of ~COUNT consecutive frames (~14 fps) |
| `flash-pico SLOTn < fw.uf2` | RP2040/RP2350: BOOTSEL, write, verify, run |
| `pico-save SLOTn > backup.uf2` | whole-flash backup (contains Wi-Fi credentials: keep private, never commit) |
| `pico-info SLOTn` | picotool info |
| `flash-esp SLOTn flash-id\|chip-id` | ESP identity check through the reset-aware helper |
| `flash-esp SLOTn write-flash 0xOFFSET < image.bin` | ESP flash |
| `reset SLOTn` | ESP reset (not for RP2 boards) |

Only slots enabled on the Pi are accepted (`status` prints them); anything else is refused.
Arguments may only use `A-Z a-z 0-9 space _ . : = , / # @ + -`.

## The standard loop: flash, prove it on serial, prove it on camera

```bash
ssh workbench-agent flash-pico SLOT1 < firmware.uf2           # exits 0 only if it ran again
ssh workbench-agent serial-wait SLOT1 20 "some boot marker"   # proves the NEW image booted
ssh workbench-agent snap exposure=40 > after.jpg              # look at it before claiming anything
```

Rules that matter (the reasons are in `references/pitfalls.md`):

1. **Never claim a visual result you did not look at.** Take the photo, open it, describe it.
   Where a check can be numeric, use `snap-stats`, which cannot be misread.
2. **Put a unique marker in test firmware** (a random number shown on the display AND printed on
   serial). A stale screen or old log then cannot pass for the new build.
3. **Back up before the first flash of an unknown board:** `pico-save SLOTn > backup.uf2`.
   Restore with `flash-pico SLOTn < backup.uf2`.
4. **Flash only what you just built.** Build directories keep old images; flash the path your
   build printed, and record its sha256.
5. **ESP: identity first** (`flash-esp SLOTn flash-id`), never flash through RFC2217, and never
   open the board's tty yourself. RFC2217 is for monitoring only.
6. **The LED matrix blows out under auto exposure.** Use `exposure=N` (start around 40, lower if
   the pixels bloom, raise if the frame is black).
7. On failure, run `status` and `serial-tail`, and report the exact stderr. Do not retry the same
   flash in a loop. A board stuck in BOOTSEL shows as absent; `flash-pico` again recovers it.

## Where firmware gets built

The workbench does not compile. Builds happen on the build VM (`hermes-playground`), and the
image is **streamed** to the bench. It is never copied into a git repo, and never committed.

- The build VM cannot reach the LAN. Relay it through the machine you run on:
  `ssh hermes-playground cat PATH/firmware.uf2 | ssh workbench-agent flash-pico SLOT1`
- Claude Code on the host uses `incus exec -T hermes-playground -- cat PATH | ssh workbench-agent ...`.
- Recipe for the AWTRIX Galactic Unicorn build, including the bench "hello" acceptance firmware:
  `references/rp2.md`.

## References (load ONE when needed)

| File | Load when |
|---|---|
| `references/rp2.md` | flashing a Pico/RP2040/RP2350, BOOTSEL trouble, AWTRIX Galactic Unicorn build, hello-world acceptance test |
| `references/esp.md` | flashing or monitoring any ESP32/ESP8266 |
| `references/camera.md` | framing, exposure, colour checks, bursts, reading a photo reliably |
| `references/pitfalls.md` | something failed, or before doing anything unusual on the bench |

Administration, meaning rebuilding the Pi, adding slots, and managing keys, is out of scope for
agents. It lives in `docs/workbench-build-from-scratch.md` of the esp-codex-platform repo.
