# Native USB and TinyUF2 Recovery

Native USB boards can change USB identities and devnodes during reset, ROM-loader entry, application boot, deep sleep, and TinyUF2 boot. RFC2217 is useful for monitoring, but repeated esptool writes through a Raspberry Pi and USB hub can be fragile for ESP32-S2 boards.

Use three distinct paths:

1. Reset-aware esptool when the fixture has a known-good ROM-loader path.
2. App-only UF2 for normal iteration when a trusted UF2 volume is present.
3. Direct USB or another known-good ESP ROM-loader path for full recovery.

TinyUF2 is executing from flash. Do not use it to rewrite its own bootloader or other bootloader-region images.

## Status and runtime states

Run `tools/espwb-status` first. `serial: reachable` means only that the RFC2217 TCP socket accepted a connection. It does not prove that the application is alive.

For MagTag examples:

- `239a:00e5` -- TinyUF2 bootloader.
- `239a:80e5` -- MagTag application USB identity.
- `303a:0002` -- Espressif ESP32-S2 ROM bootloader.

| State | Typical observation | Meaning | Recovery |
| --- | --- | --- | --- |
| Application running | Application USB identity and an application-specific probe responds | Firmware is executing | Continue normal testing. |
| TinyUF2 bootloader | UF2 volume present; MagTag shows `239a:00e5`; app commands do not respond | Bootloader is running | Write and verify an app-only UF2. If the app still does not boot, use ESP ROM-loader recovery. |
| Deep sleep | USB serial disappears or portal state becomes stale | Application intentionally powered down native USB | Wait for wake, use a configured wake source, or reset. |
| Portal or recovery issue | API reachable but serial refused, `running` false, or `last_error` set | Portal ownership and USB state disagree | Run reset-aware recovery and inspect status again. |

## Workbench-side helper

Install the tracked reference implementation on the workbench:

```bash
sudo install -m 0755 tools/workbench-local-esptool /usr/local/bin/espwb-local-esptool
sudo systemctl restart rfc2217-portal
```

The helper resolves the slot through the API and captures USB topology from the resolved devnode before stopping the portal. It does not derive topology from fixed keys such as `_fixed_SLOT1`.

An EXIT trap is installed before the portal is stopped. Cleanup restarts the portal on success, failure, interruption, and esptool errors. Portal startup is retried when systemd reports an active process but the expected TCP port is not listening yet.

Strict SSH host-key checking is preserved. When `ssh-keyscan` is unreliable, use an already trusted file:

```bash
ESPWB_KNOWN_HOSTS=/host-ssh/known_hosts tools/espwb-esptool flash-id
ESPWB_KNOWN_HOSTS=/host-ssh/known_hosts tools/espwb-ssh hostname
```

## Recovery decision tree

```text
Does the application boot?
|
+-- Yes -- use normal application or OTA iteration.
|
+-- No, but a trusted UF2 volume exists
|   +-- write app-only UF2
|   +-- verify requested blocks against CURRENT.UF2
|   +-- reset and verify application identity plus an app-specific probe
|
+-- No after verified app-only UF2, or no UF2 volume
    +-- enter the ESP ROM bootloader
    +-- use direct USB or another known-good ROM-loader transport
    +-- perform the downstream project's documented full flash
```

## App-only TinyUF2

The generic helper writes an already generated app-only UF2. The downstream project owns UF2 generation, application offsets, and image composition.

MagTag example:

```bash
tools/espwb-uf2-write \
  --app-only \
  --label MAGTAGBOOT \
  --min-address 0x10000 \
  path/to/magtag-app.uf2
```

The helper validates UF2 framing, refuses blocks below the minimum address, mounts only `/dev/disk/by-label/<label>`, writes the image, waits for the volume to reappear, and compares requested payloads with `CURRENT.UF2`. Verification compares only the requested byte count, so a final partial block is handled correctly. `--full` is always refused.

## Direct USB full recovery

Install esptool locally when it is missing:

```bash
python3 -m venv --copies .venv-esptool
. ./.venv-esptool/bin/activate
python -m pip install --upgrade pip esptool
python -m esptool version
```

Then connect the board directly, enter its ESP ROM bootloader, resolve the stable bootloader device under `/dev/serial/by-id/`, and run the downstream board's full flash command with `--before no-reset` and `--after hard-reset`.

ESP32-S2 command shape:

```bash
python -m esptool \
  --chip esp32s2 \
  --port /dev/serial/by-id/<rom-loader-device> \
  --before no-reset \
  --after hard-reset \
  write-flash <offset-1> <image-1> <offset-2> <image-2>
```

Do not copy offsets from another board.

## Post-flash state check

1. Run `tools/espwb-status` and record the USB identities.
2. Treat RFC2217 reachability only as a portal test.
3. Run an application-specific probe.
4. If TinyUF2 remains after a verified app-only write, move to ESP ROM-loader recovery instead of attempting a full TinyUF2 write.
5. Distinguish deliberate deep sleep from portal failure before changing firmware.

MagTag commands such as `WSLP STATUS`, `WSLP AWAKE <seconds>`, and `WSLP SLEEP <seconds>` remain downstream firmware-specific and are not built into this generic platform.

## Lessons from issue #1

The MagTag investigation showed that healthy host, SSH, API, and slot checks do not prove a native USB flash path is reliable. Portal startup can precede TCP readiness, fixed slot keys do not provide USB topology, and both stub and no-stub writes can fail during repeated USB identity transitions. Direct workstation USB was the reliable full-recovery fallback. TinyUF2 became suitable for normal iteration only with app-only scope and `CURRENT.UF2` verification, including partial final blocks.
