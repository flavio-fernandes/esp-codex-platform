# Native USB Recovery

Native USB boards can change USB identities and devnodes during reset,
ROM-loader entry, application boot, and deep sleep. RFC2217 is useful for
monitoring, but flashing native USB boards through a Raspberry Pi and USB hub
can be fragile.

Use two distinct paths:

1. Reset-aware esptool when the fixture has a known-good ROM-loader path.
2. Direct local USB or another known-good ESP ROM-loader path for full recovery.

## Status

For boards that remain in a workbench slot, run `tools/espwb-status` first.
`serial: reachable` means only that the RFC2217 TCP socket accepted a
connection. It does not prove that application firmware is alive.

For the MagTag walkthrough, use direct USB status:

```bash
lsusb | grep -E '239a:80e5|303a:0002|MagTag|Espressif' || true
find /dev/serial/by-id -maxdepth 1 -type l -print 2>/dev/null
```

MagTag identities used by this repo:

- `239a:80e5` -- MagTag application USB identity.
- `303a:0002` -- Espressif ESP32-S2 ROM bootloader.

## Workbench-side helper

Install the tracked reference implementation on the workbench:

```bash
sudo install -m 0755 tools/workbench-local-esptool /usr/local/bin/espwb-local-esptool
sudo systemctl restart rfc2217-portal
```

The helper resolves the slot through the API and captures USB topology from
the resolved devnode before stopping the portal. It does not derive topology
from fixed keys such as `_fixed_SLOT1`.

An EXIT trap is installed before the portal is stopped. Cleanup restarts the
portal on success, failure, interruption, and esptool errors. Portal startup is
retried when systemd reports an active process but the expected TCP port is not
listening yet.

Strict SSH host-key checking is preserved. When `ssh-keyscan` is unreliable,
use an already trusted file:

```bash
ESPWB_KNOWN_HOSTS=/host-ssh/known_hosts tools/espwb-esptool flash-id
ESPWB_KNOWN_HOSTS=/host-ssh/known_hosts tools/espwb-ssh hostname
```

## Direct USB Full Recovery

Install esptool locally when it is missing:

```bash
python3 -m venv --copies .venv-esptool
. ./.venv-esptool/bin/activate
python -m pip install --upgrade pip esptool
python -m esptool version
```

Then connect the board directly, enter its ESP ROM bootloader, resolve the
stable bootloader device under `/dev/serial/by-id/`, and run the board's full
flash command with explicit offsets.

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

## MagTag LVGL Shapes

**This repo intentionally uses a much simpler MagTag partition table than the
Adafruit_Wippersnapper_Arduino MagTag work. That project has its own constraints
and should not be disturbed. This ESPHome example uses a direct-USB,
factory/no-OTA layout so the flash recipe stays boring: bootloader, partition
table, and application image only.**

After compiling `examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml`, flash:

```bash
python -m esptool \
  --chip esp32s2 \
  --port /dev/serial/by-id/<esp32-s2-rom-loader> \
  --before no-reset \
  --after hard-reset \
  write-flash \
  --flash-mode dio \
  --flash-freq 80m \
  --flash-size 4MB \
  0x1000 examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/bootloader.bin \
  0x8000 examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/partitions.bin \
  0x10000 examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/firmware.bin
```

After flashing, reset or unplug/replug the MagTag without holding buttons and
verify `239a:80e5`.

## Post-flash State Check

1. For MagTag, record direct USB identity with `lsusb`.
2. For workbench-slot boards, run `tools/espwb-status` and treat RFC2217
   reachability only as a portal test.
3. Run an application-specific probe.
4. Distinguish deliberate deep sleep from portal failure before changing
   firmware.

## Lessons

The MagTag investigation showed that healthy host, SSH, API, and slot checks do
not prove a native USB flash path is reliable. Portal startup can precede TCP
readiness, fixed slot keys do not provide USB topology, and repeated USB
identity transitions can leave a board in ROM bootloader until a plain reset.
Direct workstation USB is the reliable firmware upload path for the MagTag
walkthrough in this repo.
