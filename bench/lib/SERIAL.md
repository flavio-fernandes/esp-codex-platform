# Serial owner and client

`bench/bin/bench-serial SLOTn` loads `slots.conf`, resolves its configured physical
port, and serves one Unix socket. It uses W1's BENCH_ETC/RUN/STATE/SYSFS overrides.
SIGTERM closes the tty, joins reader/client threads, and removes its own socket.
Startup refuses an existing socket rather than removing a possible live owner's
socket. After an unclean kill, the supervisor/operator must remove a stale socket
before restarting (never remove one belonging to a live owner).

## Client API (`from benchlib import serialclient`)

- `state(slot)` → complete state dict, including `ok`, `present`, `kind`, `tty`, `open`, `paused`, `paused_by`, `seq`.
- `tail(slot, n)` → list of up to n line dicts, oldest first; 0–1000.
- `wait(slot, pattern, timeout, since_seq=None)` → matching line dict or `None`; substring, not regex. Without a cursor only future lines match. With a cursor buffered lines with greater seq are searched first, even at timeout 0.
- `follow(slot, seconds)` → generator of new line dicts for up to 600 seconds. Explicitly close an abandoned generator to close its connection.
- `write(slot, text)` → number of UTF-8 bytes written, including appended CRLF.
- `reset(slot)` → `{"ok": true}` after the esp-uart EN pulse.
- `paused(slot, by)` → context manager yielding the acknowledgement after tty closure. Keep the context open throughout direct tty operations; normal exit, exceptions, or process death release it.

Each line is `{seq, ts, text}`. Sequence numbers are per owner process, not persisted
across restarts. Events (`attach`, `detach`) go to the rotated log, not the line ring.
All socket calls have timeouts: 5 seconds normally; wait/follow use duration + 5
seconds (at least 5). A pause has no lease expiry while its connection remains open.
`NotRunning` (a `ConnectionError`) means missing socket or refused connection.
`ProtocolError.code` and `.detail` expose server error responses; socket timeouts
raise `TimeoutError`. Do not treat these as an absent device: use `state.present`.

Mutating command wrappers remain responsible for slot locks, holds and history as
specified in DESIGN. In particular, take the slot lock and check holds before a
reset or entering a flash pause. This transport does not duplicate those guards.

## Engine/testing seams

`SerialOwner(slot, poll_interval=0.1, log_bytes=5242880,
serial_factory=serial.Serial, resolver=usbdev.resolve)` exposes `serve_forever()`,
`stop()`, `ready` (socket ready, not necessarily tty ready), and `state()`.
The reader and socket handlers serialize tty access through a condition lock.
Pause acknowledgement is sent only after close, and a competing pause is refused.

Tests use real pyserial and `os.openpty()`, plus a fake sysfs tty symlink. A test-only
Serial subclass translates the resolved `/dev/ttyACM0` to that symlink's PTY target;
production `usbdev.resolve()` remains unchanged. Modem policy/order is separately
checked with a recording Serial fake because PTYs do not implement modem lines.
Socket tests use a relative BENCH_RUN within the isolated test directory to stay
under the Unix socket pathname limit even with long pytest temp paths.
