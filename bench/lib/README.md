# benchlib API (W1)

Import with `/opt/bench/lib` on `PYTHONPATH` (development: `bench/lib`). All path
overrides are read at call time: `BENCH_ETC`, `BENCH_RUN`, `BENCH_STATE`,
`BENCH_SYSFS`. No function opens serial ports or touches hardware controls.

## Configuration

- `config.root(name)` → `Path`; name is `etc`, `run`, `state`, or `sysfs`.
- `config.validate_slot(slot)` → validated string, or `ValueError`.
- `config.validate_port(path)` → validated string, or `ValueError`.
- `config.load_slots(path=None)` → `{label: {path, monitor, baud}}`; baud is int.
- `config.load_env(path=None)` → `{KEY: literal_value}`; no evaluation, quote
  removal, interpolation, or inline comment stripping. Full comment lines and
  blank lines are ignored. Duplicate keys and malformed records are errors.

The readers default to files under `BENCH_ETC`. Parse failures are `ValueError`
with a line number; filesystem errors propagate. `monitor` defaults to `auto`,
`baud` to 115200. Duplicate options and nonpositive baud rates are rejected.

## USB

- `usbdev.kind(vid, pid)` → kind string; hex strings are case insensitive.
- `usbdev.resolve(path, sysfs_root=None)` → device dict or `None`.
- `usbdev.split_port(path)` → `(parent, portnum)`; portnum is int.
- `usbdev.is_root_hub(name)` → bool.
- `usbdev.subtree_has_mass_storage(path, sysfs_root=None)` → bool.
- `usbdev.find_bootsel_at(path, sysfs_root=None)` → device dict or `None`.

`None` for the root means `BENCH_SYSFS`, default `/sys`; an explicit root wins.
Device fields: `vid`, `pid` (lowercase hex strings), `kind`, `serial`, `product`,
`busnum`, `devnum` (ints), `tty`, `block` (`/dev/` paths). Missing optional values
are `None`. No VID/PID means absent; multiple tty/block candidates choose the
lexically first. `KIND_TABLE` contains exact matches; vendor fallback rules are
implemented by `kind()`. BOOTSEL never falls back to a different port.

## Locks and holds

- `locks.slot_lock(slot)` → context manager; `Busy` means CLI exit 65.
- `locks.camera_lock()` → context manager with the same semantics.
- `locks.lock_busy(slot)` → bool; probes and immediately releases an acquired lock.
- `holds.get(slot)` → `{client, reason, since, until}` or `None`.
- `holds.put(slot, client, minutes, reason)` → stored hold; integer minutes 1–720.
- `holds.release(slot, client, force=False)` → bool indicating a removed file.
- `holds.check(slot, client)` → `None`, or raises `Held` (CLI exit 69).

Lock files must already exist; missing files are installation errors, not busy
locks. All are opened read-only. `put` and `release` take the slot operation
lock internally, so **do not nest them inside `slot_lock`**. Mutating device
operations must take `slot_lock`, then call `check`, and retain the lock through
the operation. This also serializes hold updates with active operations.

Hold timestamps are Unix seconds. An expired hold is ignored without deleting
it during a read. The holder may renew; another client cannot replace or remove
an active hold without explicit forced release. `Held.hold` exposes the record;
the exception text includes holder, reason and expiration. Malformed hold JSON
fails loudly rather than permitting a mutation. Writes use a same-directory
temporary file, fsync and rename. Files are group-readable/writable; installed
state directories must use the configured shared group and setgid permissions.

## History

- `history.append(**fields)` → appended record with every DESIGN field present.
- `history.tail(n)` → list of up to n records, oldest first; zero returns `[]`.
- `history.last_ts()` → latest record's timestamp, or `None` for no history.

Unspecified fields are null; unspecified `ts` is UTC ISO8601 with milliseconds
and `Z`. Unknown fields are rejected. Append uses `O_APPEND`, an advisory lock,
short-write handling and fsync; readers share that lock to avoid partial lines.
Malformed history fails loudly. `last_ts` returns a string, not Unix seconds.

## Developer checks

Run `bench/dev/setup-venv.sh`, then `bench/dev/check.sh`. The latter checks
`bench/tests`, every `bench/**/*.sh` (excluding the venv), and shell-shebang
entrypoints in `bench/bin/`; legacy `workbench/` and `tools/` are outside this
new stack's check scope. It runs both steps even if one fails and prints
`ALL PASS` only if both succeed.