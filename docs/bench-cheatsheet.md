# Bench cheatsheet

Use `ssh bench-agent <verb>`; `tools/wb <verb>` is its configured client wrapper.
Setup: [runbook](bench-build-from-scratch.md). Contract: [DESIGN](../bench/DESIGN.md).
No remote shell or network serial listener is needed. Data is stdout; diagnostics
are stderr. Never merge stderr into firmware/photo downloads.

## Verbs

| Verb | Result / bounds |
|---|---|
| `help` | Supported commands |
| `status` | Slots, kind, presence, serial state, holds, camera, versions |
| `health [--json]` | Fresh health and idle verdict |
| `history [N]` | Recent mutations; 1–200, default 20 |
| `serial-tail SLOTn [lines]` | Buffered JSON lines, 0–1000, default 50 |
| `serial-wait SLOTn SECONDS PATTERN` | Future substring match; 0–300 s, JSON result |
| `serial-follow SLOTn SECONDS` | New JSON lines, 1–600 s; closing never resets the board |
| `serial-write SLOTn TEXT` | Send text plus CRLF; respects holds |
| `reset SLOTn` | ESP UART EN pulse only; not native-USB ESP or RP2 |
| `snap [exposure=auto\|N] [res=WxH] [skip=N]` | JPEG bytes |
| `snap-stats [camera options] [threshold=N]` | Lit-pixel colour JSON |
| `seq COUNT INTERVAL [camera options]` | Tar of timed JPEGs; count 1–60, interval 0–600 s |
| `burst COUNT [camera options]` | Tar of consecutive frames; count 1–300 |
| `pico-info SLOTn` | RP2040/RP2350 info (may reboot a running board) |
| `pico-save SLOTn` | Full-flash UF2 bytes; keep private |
| `flash-pico SLOTn` | UF2 from stdin, touch/BOOTSEL/load/verify/run |
| `flash-esp SLOTn chip-id\|flash-id` | ESP identity, reset-aware |
| `flash-esp SLOTn write-flash 0xOFF` | Single merged image from stdin; identity first |
| `esp-save SLOTn [SIZE]` | Binary backup starting at zero; SIZE decimal/hex/ALL |
| `usb-reset SLOTn` | Guarded re-enumeration of one USB port |
| `power-cycle` | Guarded, opt-in whole-hub power switch; see runbook |
| `hold SLOTn MINUTES REASON` | Reserve against other clients' mutations; 1–720 minutes |
| `release SLOTn` | Release your hold |

Input is at most 400 characters with alphabet `[A-Za-z0-9 _.:=,/#@+-]`.
Spaces in patterns/reasons are accepted; quotes are local shell quoting only,
not part of the transmitted command. Shell operators, newlines and substitutions
are refused. Firmware defaults to a 16 MiB cap; UF2 magic is checked. Unknown
kinds are never flashed. A slot names a physical port, not a permanent board.

## Standard loop

Build a new image with a new random four-digit marker on the display **and in
repeated application logs**. Record the image hash and verify board identity.
Hold every affected slot for the entire job (including waits), and release on
success or failure. Holds are keyed by the forced-command client label, not a
process ID; jobs sharing a key must coordinate with each other.

```sh
ssh bench-agent hold SLOT1 60 display-proof
ssh bench-agent status
# First encounter: backup before writing. Backups may contain secrets.
umask 077
ssh bench-agent pico-save SLOT1 > before.uf2
# Continue only after successful backup; discard partial output on failure.
ssh bench-agent flash-pico SLOT1 < firmware.uf2
ssh bench-agent serial-wait SLOT1 30 'HELLO 1234'
ssh bench-agent snap > after.jpg
# Open after.jpg and describe the visible marker/content before claiming success.
ssh bench-agent history 10
ssh bench-agent release SLOT1
```

`1234` is an example, not a reusable proof marker. `serial-wait` searches only
future lines, so repeat the marker in test firmware or arm the wait before a
one-shot boot event. `serial-tail` can show buffered boot lines but those alone
are not freshness proof. Sequence numbers restart with the serial owner.

For an ESP, select the approved slot and use the corresponding loop:

```sh
ssh bench-agent hold SLOT4 60 esp-proof
ssh bench-agent flash-esp SLOT4 flash-id
ssh bench-agent esp-save SLOT4 > before.bin
ssh bench-agent flash-esp SLOT4 write-flash 0x0 < firmware.factory.bin
ssh bench-agent serial-wait SLOT4 30 'HELLO 5678'
ssh bench-agent snap > after.jpg
ssh bench-agent release SLOT4
```

These snippets show command order, not unattended error handling: stop on failure,
retain diagnostics, and always release holds in cleanup. The `tools/espwb-esptool`
wrapper supports identity, a single-image write and zero-offset backup; it keeps
an existing backup intact if SSH fails. It intentionally rejects arbitrary esptool
flags, nonzero backup offsets and split-image writes. Use a merged image. Default
slot is SLOT1; other slots need `BENCH_ALLOW_NON_SLOT1=1` in that wrapper.

Camera defaults live on the server. Override exposure only after measuring the
scene; LEDs can saturate auto exposure. The camera helpers always use the bench:
`tools/workbench-camera-capture [photo.jpg]` and
`tools/workbench-camera-sequence [count] [interval] [frames.tar]`. Sequence now
returns a tar archive, not a locally captured directory. Inspect before unpacking.

## Exit codes

Interpret codes in the context of the verb; the client preserves server codes.

| Code | Meaning |
|---|---|
| 0 | Success / serial match / healthy |
| 1 | General failure; serial wait unmatched; health degraded; camera tool failure |
| 2 | Usage/input/guard refusal; health cannot run; camera absent/bad options |
| 3 | Camera timeout |
| 4 | Truncated JPEG or burst without a complete frame |
| 65 | Slot or camera busy |
| 66 | Board absent / did not return after USB reset |
| 67 | RP2 never reached BOOTSEL |
| 68 | picotool/esptool/hub-switch tool failure |
| 69 | Held by another client |
| 70 | Wrong board kind |
| 71 | Application did not come back after flashing |
| 255 | SSH transport/authentication failure: client reports OFF |

A successful flash verifies programming, not display behavior. Check fresh logs
and view the image. Native-USB ESP monitoring is off by default; do not mistake
that policy for a fault. A board found in BOOTSEL stays there after info/save;
loading firmware is what starts its application.

## Evidence and maintenance

`history` records timestamps, client, verb, slot, kind, device identity, hash,
size, rc, elapsed time and notes. Keep private reports and redact identities
before publishing. Persistent serial and audit locations are in the runbook.
Holds make health non-idle; maintenance must check a fresh idle verdict and
coordinate against new jobs. Never power-cycle a root hub or a disk subtree.
