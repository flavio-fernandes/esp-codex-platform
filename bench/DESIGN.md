# Bench stack: design contract

This is the shared contract for the `bench/` tree. Every component and every card builds to it.
If a card needs to change it, the card changes this file in the same commit and says why in its
handoff. It replaces `workbench/` (the Pi 3 + upstream rfc2217-portal setup), which stays in the
repo as history until the migration is finished.

## Why it exists (the failures it is built to remove)

| Old failure | How this design removes it |
|---|---|
| Slots renumbered by boot timing; the webcam became a phantom slot | Slots are fixed USB **port paths** in `slots.conf`; a port with no entry is ignored |
| Slot role (pico vs esp) edited by hand in `agent.env` | Board **kind is detected** from the device's VID:PID each time |
| Upstream proxy holds DTR low, so RP2 output is silently empty (needed a holder service) | `bench-serial` sets the modem lines **per kind** |
| One RFC2217 client per slot; `stop` stopped every slot; "running" said before the port listened | One owner process per slot, a local socket with many readers; flashing **pauses only its own slot** |
| Boot lines lost after a re-attach (proxy restart churn) | `bench-serial` reopens the tty within ~100 ms of the device appearing |
| Closing an RFC2217 monitor wedged the ESP32-S3 in ROM | No RFC2217. Native-USB ESP slots are not opened unless configured |
| `snap` could hang forever | Every external call has a timeout |
| Logs lost on reboot; HANDOFF.md and the bench disagreed | Persistent journal, per-slot serial log on disk, `flash-history.jsonl` |
| "Bench off" looked like "bench broken" | `bench-health` publishes one verdict; clients report OFF (no SSH) separately from DEGRADED |
| Software-only recovery was impossible (`-71` needed a Pi reboot) | `bench-usb usb-reset` re-enumerates one port; `power-cycle` only where the hub really switches power |
| Two boards in BOOTSEL were ambiguous | The BOOTSEL device is matched **by its port path**, never by "the only one" |

## Hardware model

- Host: Raspberry Pi 4 (xHCI), Debian 13. Boots from a **USB 3 SSD hanging off the root hub**, so
  nothing may ever cut power to, or disable, a root-hub port or any port whose subtree holds a
  mass-storage device.
- Boards sit on a self-powered hub. Slot = one downstream port of that hub (or a spare Pi port).
- Camera: a UVC webcam, deliberately not a slot.

## Files and locations (installed)

| Path | What |
|---|---|
| `/opt/bench/lib/benchlib/` | the Python package (stdlib + pyserial + PIL only) |
| `/opt/bench/venv/` | venv with `--system-site-packages`, holding only `esptool>=5` |
| `/usr/local/bin/bench-serial`, `bench-flash-rp2`, `bench-flash-esp`, `bench-camera`, `bench-health`, `bench-agent-shell` | entry points |
| `/usr/local/sbin/bench-usb` | the one privileged helper (sudo, wbagent may run only this) |
| `/etc/bench/slots.conf` | slot map (below) |
| `/etc/bench/bench.env` | camera device, exposure default, limits, feature flags (`BENCH_POWER_CYCLE=0`) |
| `/run/bench/` (tmpfiles, `root:bench 2775`) | `SLOTn.sock`, `SLOTn.lock`, `camera.lock`, `health.json` |
| `/var/lib/bench/` (`bench:bench 2775`) | `serial/SLOTn.log*`, `flash-history.jsonl`, `holds/SLOTn.json` |

Users: system user **`bench`** (groups `dialout plugdev video`) runs `bench-serial@` and
`bench-health`. The agent user **`wbagent`** is in groups `bench dialout plugdev video` and runs
flash/camera tools directly through `bench-agent-shell`. Sockets are `0660 bench:bench`.
Nothing listens on a network port.

## `slots.conf`

```
# label   usb-port-path   [key=value ...]
SLOT1     1-1.1.1
SLOT2     1-1.1.2
SLOT3     1-1.1.3
SLOT4     1-1.1.4
SLOT5     1-1.2          monitor=auto
SLOT6     1-1.3
```

- `usb-port-path` is the kernel device name under `/sys/bus/usb/devices/` (e.g. `1-1.1.4`).
  Labels match `^SLOT[0-9]+$`; paths match `^[0-9]+-[0-9]+(\.[0-9]+)*$`; both unique.
- Options: `monitor=auto|on|off` (default `auto`: open for rp2 and esp-uart, not for esp-usb),
  `baud=N` (default 115200).
- Parse errors are fatal with the line number. Unknown keys are errors.

## Device resolution and kind

`benchlib.usbdev.resolve(path, sysfs_root="/sys")` returns `None` (absent) or:
`{vid, pid, kind, serial, product, busnum, devnum, tty, block}`

- Read `idVendor`, `idProduct`, `serial`, `product`, `busnum`, `devnum` from `<sysfs>/bus/usb/devices/<path>/`.
- `tty`: the `/dev/` name found under the device's interfaces (`<path>:1.*/tty/tty*` for ACM,
  `<path>:1.*/tty*` for usb-serial).
- Kind table:
  - `2e8a:0003` / `2e8a:000f` → `rp2-bootsel` (RP2040 / RP2350 boot ROM)
  - any other `2e8a:*` → `rp2`
  - `10c4:ea60`, `1a86:7523`, `1a86:55d4`, `0403:6001`, `0403:6015` → `esp-uart`
  - `303a:*` → `esp-usb`
  - anything else → `unknown` (never flashed, never opened)
- All sysfs access goes through a root parameter, so tests use fake trees under `tests/fixtures/`.

## `bench-serial@SLOTn` (one process per slot)

- Owns the slot's tty while the device is present and the kind's monitor policy says open.
- Modem lines by kind:
  - `rp2`: DTR **high**, RTS high (arduino-pico and pico-sdk drop output while DTR is low).
  - `esp-uart`: DTR and RTS **deasserted**, set on the `serial.Serial()` object **before**
    `open()`. Linux may still pulse the lines on open; that is why the port is opened once per
    attach and held, never reopened per client.
  - `esp-usb`: not opened unless `monitor=on`.
- Hot-plug: poll the port every 100 ms (sysfs only, no udev dependency). Reopen as soon as a tty
  appears. Record `attach` and `detach` events in the log.
- Every received line gets `seq` (monotonic per process), `ts` (UTC ISO8601, ms), and `text`
  (decoded utf-8, `errors=replace`, CR stripped). Partial lines are flushed after 500 ms idle.
- Ring buffer of the last 5000 lines in memory. Persistent log at `/var/lib/bench/serial/SLOTn.log`,
  one `ts text` line each, rotated at 5 MiB, keeping 5.
- Socket `/run/bench/SLOTn.sock`: unix stream, one JSON request line, then JSON response lines.
  Many clients at once.

| op | request | response |
|---|---|---|
| `state` | `{"op":"state"}` | `{"ok":true,"present":..,"kind":..,"tty":..,"open":..,"paused":..,"paused_by":..,"seq":N}` |
| `tail` | `{"op":"tail","lines":N}` (≤1000) | one `{"seq","ts","text"}` per line, then `{"ok":true,"end":true}` |
| `wait` | `{"op":"wait","pattern":"..","timeout":S,"since_seq":N?}` | `{"ok":true,"matched":true,"line":{..}}` or `{"ok":true,"matched":false}`; it searches buffered lines with `seq > since_seq` first, then new ones; `pattern` is a plain substring |
| `follow` | `{"op":"follow","seconds":S}` (≤600) | line objects as they arrive, then `{"ok":true,"end":true}` |
| `write` | `{"op":"write","text":".."}` | `{"ok":true,"bytes":N}`; sends text + `\r\n`; refused while paused |
| `pause` | `{"op":"pause","by":"<who>"}` | `{"ok":true,"paused":true}` once the tty is **closed**. The pause lasts while this connection stays open; closing it (or the client dying) resumes automatically |
| `reset` | `{"op":"reset"}` | esp-uart only: the standard EN pulse (RTS low→high with DTR high), then `{"ok":true}` |

  Errors: `{"ok":false,"error":"<code>","detail":".."}` with codes `bad-request`, `absent`,
  `paused`, `not-open`, `timeout`, `unsupported`.
- Readiness contract: `state.open==true` means the tty is open **and** the reader thread runs.
- Reset's low/high names refer to electrical levels: on active-low USB UART controls,
  pyserial `dtr=False` holds DTR high; `rts=True`, wait 100 ms, `rts=False` pulses EN low→high.
  Both controls finish deasserted. Reset is unsupported for native-USB ESP, even with monitoring on.
- `wait` without `since_seq` searches only lines arriving after the request; with it, buffered
  lines are searched first, including for a zero timeout. Tail accepts 0–1000 lines; durations
  are finite nonnegative seconds (follow at most 600). Invalid types/ranges are `bad-request`.

## Locks, holds, history (`benchlib`)

- **Lock** (`benchlib.locks.slot_lock(slot)`): `flock` on `/run/bench/SLOTn.lock`, which is opened
  read-only (pre-created by tmpfiles; never created in a sticky dir). Every flash, backup, info,
  usb-reset or reset of a slot holds it. Non-blocking: busy → exit code 65.
- **Hold** (`benchlib.holds`): `/var/lib/bench/holds/SLOTn.json` = `{client, reason, since, until}`.
  `hold SLOTn MINUTES REASON` (1–720 min) by client A makes every mutating verb from any other
  client fail with code 69 and the hold's text. Read-only verbs still work. Expired holds are ignored.
- **History** (`benchlib.history.append(...)`): one JSON line per mutating verb in
  `/var/lib/bench/flash-history.jsonl`, written with `O_APPEND` and an fsync:
  `{ts, client, verb, slot, kind, usb_serial, sha256, size, rc, seconds, note}`.

## Flashing

**`bench-flash-rp2 info|save|load SLOTn [file]`**
1. Take the lock. Check the hold. Pause `bench-serial` (keep the pause connection open throughout).
2. If the slot is already `rp2-bootsel`, skip to 4.
3. 1200-baud touch on the tty. Then wait ≤10 s for an `rp2-bootsel` device **on the same port
   path**. Fallback: `picotool reboot -f -u --bus B --address A`.
4. `picotool <info -a | save -a FILE | load -v -x FILE> --bus B --address A` (bus and address
   of the BOOTSEL device at that path).
   - `load` of a filesystem-only UF2 exits 254 after `OK`: treat that as success with a warning.
5. For `load`, and for `info`/`save` (which reboot the board): wait ≤30 s for the application tty
   at the same port path. Close the pause connection. Then wait ≤15 s for `bench-serial`
   `state.open`, and report the first line seen (or "no output yet").
6. Append history. Exit codes:

| code | meaning |
|---|---|
| 0 | OK |
| 2 | usage |
| 65 | busy (lock) |
| 66 | absent |
| 67 | never reached BOOTSEL |
| 68 | picotool failed |
| 69 | held by another client |
| 70 | wrong kind |
| 71 | application did not come back |

Details fixed by the implementation (`benchlib.rp2flash`):
- Usage (2) is checked before the lock and writes no history: `info` takes no file, `save`/`load`
  need one, the slot must be in `slots.conf`, and a `load` file must be readable, non-empty and
  start with the UF2 magic `UF2\n`. Every later outcome, success or failure, appends history with
  `verb` `rp2-info|rp2-save|rp2-load`, the slot's kind and USB serial as found before the
  operation, and sha256/size of the loaded file.
- The client is `BENCH_CLIENT` (set by `bench-agent-shell`), else the login name. The pause is
  taken as `bench-flash-rp2 <client>`. A pause refused because another client holds it is busy
  (65). If `bench-serial` is not running for the slot, nothing owns the tty: the operation runs
  without a pause and says so.
- `info` output is data on stdout; `save`/`load` picotool output goes to stderr, without progress
  bars. The result line `first-line: <text>` (or `first-line: (no output yet)`) is on stdout.
- After `info`, `save`, or a failed picotool call, a board still in BOOTSEL at the path gets
  `picotool reboot --bus B --address A` (as `wb-pico-flash` did). 68 wins over 71.
- Timeouts in seconds, overridable from the environment: `BENCH_RP2_BOOTSEL_WAIT` (10, each
  poll), `BENCH_RP2_APP_WAIT` (30), `BENCH_RP2_OPEN_WAIT` (15), `BENCH_RP2_LINE_WAIT` (5, first
  line after the reopen), `BENCH_RP2_PICOTOOL_TIMEOUT` (300), `BENCH_RP2_REBOOT_TIMEOUT` (20).

**`bench-flash-esp SLOTn chip-id|flash-id|read-flash OUT [SIZE]|write-flash 0xOFF FILE`**
- Uses `/opt/bench/venv/bin/esptool` (esptool ≥5) on the slot's tty, with the slot paused.
- Identity runs first on every write.
- `--after hard-reset`.
- Kinds `esp-uart` and `esp-usb`. For `esp-usb`, port the reset-aware sequence and the S3 ROM
  recovery from `tools/workbench-local-esptool`.
- Same lock, hold, history and exit codes. 68 means esptool failed.

## `bench-usb` (root via sudo; the only privileged code)

- `usb-reset SLOTn`: write `1`, then `0` (after 1 s), to
  `/sys/bus/usb/devices/<parent>:1.0/<parent>-port<N>/disable`. `<parent>` and `<N>` are split
  from the slot's port path.
- `power-cycle`: `uhubctl -l <hub-location> -a cycle -d 3`. Only when `BENCH_POWER_CYCLE=1`.
- **Guards** (unit-tested, refuse with exit 2):
  - the target must be a configured slot's port path, or the configured hub location;
  - the parent must not be a root hub (`usbN`);
  - no mass-storage device (`bInterfaceClass 08`) may sit anywhere in the affected subtree.

## `bench-camera snap|stats|burst`

- Port of `workbench/bin/wb-camera`: MJPG, manual exposure via v4l2-ctl. The default `exposure`
  comes from `bench.env`.
- Every v4l2-ctl call runs under `timeout`. Total `snap` ≤ 20 s.
- `camera.lock` is held for the whole capture.
- A JPEG missing its EOI marker is retried once.
- `stats`: the PIL mean colour of lit pixels, as in the old tool.

## `bench-health [--json] [--quiet]`

Writes `/run/bench/health.json` (the timer runs it every 5 minutes) and prints a summary. It
reports:
- `ok`, `ts`, `uptime_s`, `throttled` (vcgencmd), `temp_c`, `disk_free_pct`
- `hub_present`
- per slot: `{present, kind, tty, serial_service, open, paused}`
- `kernel`: counts since boot of `error -71`, `error -110`, `USB disconnect`, `over-current`
- `camera_present`
- `holds`
- `locks_busy`
- `idle`: no holds, no busy locks, and no history entry in the last 10 minutes. The weekly
  upgrade job gates on this.

Exit codes: 0 ok, 1 degraded (it says what), 2 cannot run.

Health JSON uses `slots` and active `holds` objects keyed by slot label; `locks_busy`
is a list of busy slot labels, plus `camera` when the camera lock is held. Kernel keys
are the literal strings `error -71`, `error -110`, `USB disconnect`, `over-current`.
`throttled` is a hexadecimal string (or null if unavailable). Missing optional probes
produce null and a `notes` entry, not a crash; missing serial state reports open/paused
false with a note. Invalid configuration, unreadable lock/hold/history state, or failed
publication is exit 2, never a trustworthy idle verdict.

`BENCH_HUB_PATHS` in bench.env is a space-separated list of USB port paths; every path
must exist. An empty list means hub missing. `BENCH_CAMERA_DEVICE` must exist.
`ok` is false for nonzero throttled flags, temperature >=80 C, disk free <10%, a missing
hub/camera, or a present board permitted by monitor/kind policy whose service is not
active or whose serial state is not open (including paused). `problems` lists the reasons.
Growth in either kernel error counter also degrades health: compare with
`/run/bench/health.prev.json`, then advance that baseline after publishing. First run
establishes a baseline; unchanged/decreased counts do not degrade. Unavailable kernel
logs leave the previous baseline intact. Disconnect/over-current counts are diagnostic.

The command atomically replaces health.json and health.prev.json individually. `collect()`
only reads the baseline; the CLI advances it. `--quiet` suppresses output only when OK
(including with --json). Degraded results log each problem via `logger -t bench-health`.
Command probes time out after 3 seconds; serial state uses its 5-second client timeout.
The oneshot service runs as bench with journal read access; exit 1 is a successful
collection of a degraded result, while exit 2 is a service failure. The timer starts
2 minutes after boot and repeats every 5 minutes. This snapshot is an advisory idle
signal, not a replacement for taking operation locks.

## `bench-agent-shell CLIENT` (forced command for `wbagent`)

- Same input rules as the old `wb-agent-shell`:
  - `SSH_ORIGINAL_COMMAND` ≤ 400 chars, alphabet `[A-Za-z0-9 _.:=,/#@+-]`;
  - split on whitespace;
  - first word = verb;
  - firmware in on stdin, size-capped, UF2 magic checked for RP2;
  - data out on stdout, messages on stderr.
- Every call is logged with `logger -t bench-agent "client=.. from=.. cmd=[..] rc=.."`, plus the
  payload sha256 for writes.
- Verbs:
  - status: `status`, `health`, `history [N]`
  - serial: `serial-tail SLOTn [lines]`, `serial-wait SLOTn SECONDS PATTERN`,
    `serial-follow SLOTn SECONDS`, `serial-write SLOTn TEXT`, `reset SLOTn`
  - camera: `snap [exposure=auto|N] [res=WxH]`, `snap-stats [..] [threshold=N]`,
    `seq COUNT INTERVAL [..]`, `burst COUNT [..]`
  - RP2: `pico-info SLOTn`, `pico-save SLOTn`, `flash-pico SLOTn`
  - ESP: `flash-esp SLOTn flash-id|chip-id|write-flash 0xOFF`, `esp-save SLOTn [SIZE]`
  - recovery: `usb-reset SLOTn`, `power-cycle`
  - holds: `hold SLOTn MINUTES REASON`, `release SLOTn`
  - `help`
- Kind checks replace the old slot allow-lists: `flash-pico` needs `rp2` or `rp2-bootsel`;
  `flash-esp`/`esp-save`/`reset` need `esp-*`.

## Installer `bench/install.sh [--check]`

- Idempotent. It installs packages, the lib, bins, units, udev rules, tmpfiles, sudoers
  (`visudo -c` checked), users and groups. It never overwrites an existing `/etc/bench/*.conf`.
- udev:
  - `2e8a` → group plugdev;
  - `power/control=on` for the bench hub (05e3:0610/0626) and any device on a slot port.
- Never touches: hostname, wlan0/NetworkManager, docker, ufw, sshd except one drop-in
  (`Match User wbagent`: no forwarding, no PTY), or authorized_keys.
- `--check` changes nothing and reports drift as a list.

## Tests

- `bench/tests/` with pytest. Fake sysfs trees for resolution, kinds and guards. A pty pair
  (`os.openpty`) stands in for a tty in `bench-serial` tests. picotool, esptool, uhubctl and
  v4l2-ctl are faked by scripts on `PATH`.
- `bench/dev/setup-venv.sh` makes `.venv` with `pytest pyserial pillow shellcheck-py`.
  `bench/dev/check.sh` runs pytest and shellcheck on every shell script; it must print `ALL PASS`.
