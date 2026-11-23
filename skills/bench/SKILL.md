---
name: bench
description: "Flash ESP32/RP2040 boards, read serial and photograph displays on the hardware bench."
tags: [bench, hardware, esp32, rp2040, rp2350, serial, camera, firmware]
---

# Hardware bench

The bench is a Raspberry Pi on the LAN with a self-powered USB hub and a UVC
webcam. Slots name fixed hub port paths, not permanent boards or tty numbers.
Use **only `ssh bench-agent <verb>`**. There is no shell, PTY or network serial
listener. Every call is audited. Firmware goes in on stdin; photos, backups and
JSON come out on stdout; diagnostics go to stderr. Never combine the streams
when downloading binary data. Builds happen on the approved build machine,
not on the bench. No hardware access is permitted on code-only tasks.

Machine-local notes: see LOCAL.md if present

## Verbs

The following is W6's `bench-agent-shell help` text (from
`bench/lib/benchlib/agentshell.py`), not a guessed shell interface:

```text
bench-agent verbs (ssh bench-agent <verb> ...):
  help                                   this text
  status                                 slots (kind present open paused hold), camera, tool versions
  health [--json]                        bench-health verdict; its exit code is passed through
  history [N]                            last N (1-200, default 20) flash-history records, JSON lines
  serial-tail SLOTn [lines]              last lines (0-1000, default 50) of the slot's serial buffer
  serial-wait SLOTn SECONDS PATTERN      wait <=300 s for PATTERN (may contain spaces) in new lines;
                                         JSON result; exit 0 on a match, 1 otherwise
  serial-follow SLOTn SECONDS            stream new serial lines for <=600 s
  serial-write SLOTn TEXT                send TEXT + CRLF
  reset SLOTn                            EN reset (esp-uart only)
  snap [exposure=auto|N] [res=WxH]       one JPEG on stdout
  snap-stats [..] [threshold=N]          JSON colour of lit pixels (deterministic)
  seq COUNT INTERVAL [..]                tar of COUNT (1-60) JPEGs, INTERVAL (0-600) s apart, on stdout
  burst COUNT [..]                       tar of COUNT (1-300) consecutive frames on stdout
  pico-info SLOTn                        picotool info (the board reboots afterwards)
  pico-save SLOTn                        whole-flash UF2 backup on stdout (holds secrets!)
  flash-pico SLOTn < fw.uf2              write, verify, run (rp2 or rp2-bootsel)
  flash-esp SLOTn flash-id|chip-id       ESP identity on stdout
  flash-esp SLOTn write-flash 0xOFF < image.bin
  esp-save SLOTn [SIZE]                  flash backup on stdout (SIZE: ALL, decimal or 0x-hex)
  wifi-provision SLOTn < creds           give the board Wi-Fi over serial (Improv): stdin = SSID line,
                                         password line; never logged or stored
  usb-reset SLOTn                        re-enumerate the slot's USB port
  power-cycle                            power-cycle the bench hub (only if enabled)
  hold SLOTn MINUTES REASON...           reserve a slot (1-720 min): other clients' mutations get 69
  release SLOTn                          drop your hold
Exit codes: 0 ok, 1 failed, 2 refused/usage, 3 camera timeout, 4 truncated JPEG, 65 busy,
66 absent, 67 never reached BOOTSEL, 68 tool failed, 69 held by another client, 70 wrong kind,
71 application did not come back.
```

Exception to the terse help: an RP2 **found in BOOTSEL stays there after
info/save**. Loading starts its application. Camera options also accept `skip=N`.
The input alphabet is `[A-Za-z0-9 _.:=,/#@+-]`, with a 400-character maximum.
Local quoting keeps spaces together; quotes and shell operators must not be
transmitted. Firmware has a configured size cap (default 16 MiB); RP2 needs UF2
magic. Unknown board kinds are refused.

## Standard loop

1. Hold every approved slot for the entire job, including build/wait time. Check
   `status`, `health` and `history` before changing a board.
2. Back up an unknown board **before the first write**. Keep backups private:
   they can contain credentials. Stop on backup failure; do not use partial output.
3. Build a new image with a **new random four-digit nonce** on the display and
   in repeated serial logs. Record its SHA256; flash only that exact build output.
4. Flash, wait for a fresh nonce, photograph, **open the image** and describe what
   is actually visible. Successful programming is not proof of application health.
5. Check history and release holds on success **or failure**.

Example for an approved RP2 slot, in a private artifacts directory (replace
`1234` with the nonce compiled into this build):

```sh
set -eu
umask 077
ssh bench-agent hold SLOT1 60 display-proof
trap 'ssh bench-agent release SLOT1' EXIT
ssh bench-agent status
ssh bench-agent health
ssh bench-agent history 10
ssh bench-agent pico-save SLOT1 > before.uf2.part
mv before.uf2.part before.uf2
sha256sum firmware.uf2
ssh bench-agent flash-pico SLOT1 < firmware.uf2
ssh bench-agent serial-wait SLOT1 30 'HELLO 1234'
ssh bench-agent snap > after.jpg.part
mv after.jpg.part after.jpg
ssh bench-agent history 10
```

The human/agent must now view `after.jpg`; the script cannot judge it. Use a
measured manual exposure for LED matrices (see camera reference). `serial-wait`
matches **new** lines only: repeat the nonce, or arm the wait before a one-shot
boot event. Buffered tail output alone is not freshness proof. Serial sequence
numbers restart when the owner restarts.

For ESP, perform `flash-esp SLOTn flash-id` first, back up with `esp-save`, then
`flash-esp SLOTn write-flash 0x0 < firmware.factory.bin`. Use a merged image,
not an application-only binary at zero. See [ESP](references/esp.md).

## Wi-Fi without secrets in the build

Build machines never see Wi-Fi credentials. Build the image **without** them and
provision it on the bench afterwards, over the slot's serial port:

- ESPHome: `wifi:` with `ap: {}` and **no** `ssid`/`password`, plus `improv_serial:`
  (and keep `logger:` on its default UART/USB port and its default level). No `!secret`
  anywhere. ESPHome sends a `url:` only with `web_server:`; otherwise the bench reads the
  address from the board's `[C] IP Address:` log line, which `level: INFO` (or anything less
  verbose than CONFIG) hides; then there is no `url:` line, only a hint on stderr.
- After the flash and the first fresh serial line:
  `printf '%s\n%s\n' "$SSID" "$PASSWORD" | ssh bench-agent wifi-provision SLOTn`.
  Prints `url: http://<ip>` and `state: provisioned`. The credentials go only on
  stdin: never on a command line, in a file on a build host, or in a log.
- Provision after **every** flash: it is idempotent, and proves that the new image
  answers Improv. Check `state: provisioned` before any network test.
- A re-flash does **not** erase the credentials. ESPHome keeps them in NVS: on an
  ESP32 with esp-idf that is the `nvs` partition (e.g. 0x390000 on 4 MB), outside
  the factory image; on a Pico W it is the last flash sector, outside the UF2. To
  retire a board, write an image padded with 0xFF to the end of flash, then check
  a backup for the old SSID.
- Exit 68 = device error, no improv answer (no `improv_serial:`, or the logger
  not on that port) or no join within 45 s. `history` records only that a
  provisioning happened.

## Sharing, history and failures

Holds replace ad hoc HOLD files and notices.log. `hold SLOTn MINUTES REASON`
(1–720 minutes) rejects other clients' mutations with 69; read-only operations
still work. Holds are keyed by the SSH forced-command client label, **not the
job/process**: jobs sharing a key must coordinate. Renew before expiry for long
work. Holds make the bench non-idle so maintenance must not reboot it. A hold is
not the operation lock; busy operations return 65. Release only your own holds.

`history [N]` is the source of truth for recorded board mutations and the last
successfully written image hash, rather than a hand-written handoff. Read rc,
slot, kind, timestamp, hash and size together; a failed write can leave a partial
image. History cannot prove the current application is running: check the fresh
nonce. Redact device identities before publishing history or logs.

On failure run `health`, `status`, and `serial-tail SLOTn`, retain the exact
diagnostics/exit codes, and report them verbatim privately. No retry loops. An
approved recovery uses `usb-reset SLOTn`, never direct tty/sysfs access. Never
reset a root hub or disk subtree; whole-hub `power-cycle` is opt-in only after
physical VBUS switching is proven and all affected projects agree. If a host
reboot interrupted work, follow the task's bounded wait/retry policy, then
recheck the nonce; do not trust pre-reboot board state.

## Exit codes

Interpret codes per verb; SSH preserves server codes.

| Code | Meaning |
|---|---|
| 0 | Success, serial match, or healthy |
| 1 | Failure, serial unmatched, health degraded, or camera tool failure |
| 2 | Usage/input/guard refusal, health cannot run, or camera absent/bad options |
| 3 / 4 | Camera timeout / truncated JPEG or no complete burst frame |
| 65 / 66 | Busy / absent (including no return after USB reset) |
| 67 / 68 | Never reached BOOTSEL / external tool failed |
| 69 / 70 | Held by another client / wrong board kind |
| 71 | Application did not come back after flashing |
| 255 | SSH transport/authentication failure: OFF, not proof of a broken board |

## References and canonical installation

- [RP2](references/rp2.md): BOOTSEL, backup, generic Pico hello and AWTRIX pointer.
- [ESP](references/esp.md): identity, merged images, modem lines, native USB.
- [Camera](references/camera.md): exposure, colour checks, timed captures.
- [Pitfalls](references/pitfalls.md): failure lessons and retired history.

This repository's `skills/bench/` is canonical. From a reviewed checkout:

```sh
bench/sync-skill.sh ~/.claude/skills/bench ~/.hermes/skills/bench
bench/sync-skill.sh --check ~/.claude/skills/bench ~/.hermes/skills/bench
```

Use **dedicated skill directories**: sync removes extra files and directories
except each destination's root `LOCAL.md`, which is never read or changed.
Check is read-only, lists missing/extra/content/type/mode differences, and exits
1 on drift (0 clean, 2 error/unsafe target). It rejects symlinks except LOCAL.md,
special files, repository overlap and overlapping destinations. Stop concurrent
edits during sync. Machine-specific instructions belong only in LOCAL.md;
do not invent another client's local setup. Installing on real clients is a
separate operator step, not part of code-only verification.