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

- `239a:80e5` -- MagTag application USB identity (Adafruit/TinyUSB firmware).
- `303a:0002` -- Espressif ESP32-S2 ROM bootloader, and also the USB identity
  used by ESPHome Arduino firmware on the ESP32-S2. With ESPHome Arduino the
  device stays at `303a:0002` in app mode; use serial read to confirm the app
  is running rather than relying on the USB identity changing.

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

After compiling `examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml`, put the
MagTag in ESP32-S2 ROM bootloader mode and find the local USB port:

```bash
find /dev/serial/by-id -maxdepth 1 -type l -print
find /dev -maxdepth 1 \( -name 'ttyACM*' -o -name 'ttyUSB*' \) -print
```

Set the port to the path discovered above. Prefer the by-id path when present;
otherwise use the active `/dev/ttyACM*` or `/dev/ttyUSB*` node. Do not assume a
fixed ACM number.

```bash
export MAGTAG_PORT=/dev/serial/by-id/usb-Espressif_ESP32-S2_7c:df:a1:01:25:f2-if00
```

Flash the combined factory image at `0x0`. This is the preferred recovery and
bring-up command because it keeps bootloader, partition table, and application
image together:

```bash
.venv-esptool/bin/python -m esptool \
  --chip esp32s2 \
  --port "$MAGTAG_PORT" \
  write-flash 0x0 \
  examples/magtag-lvgl-shapes/.esphome/build/magtag-lvgl-shapes/.pioenvs/magtag-lvgl-shapes/firmware.factory.bin
```

After flashing, esptool usually hard-resets the MagTag into the application. The
USB identity may appear as a raw `/dev/ttyACM*` node instead of the Adafruit
application ID, so prove the app with a short serial sample and, for display
work, a camera frame. Press RESET only if the app does not start or no serial
node appears.

### Serial Console Monitoring

To view the MagTag application logs after flashing, use one of these methods:

#### Option 1: Using picocom

```bash
sudo apt install -y picocom
timeout 30s picocom --baud 115200 /dev/ttyACM0
```

The `timeout` ensures the session exits automatically after 30 seconds. Remove it for continuous monitoring.

Press RESET on the MagTag if needed to restart the application and see fresh logs.

#### Option 2: Using a simple script

For USB CDC logger proof, read a short sample from the running app:

```bash
.venv-esptool/bin/python -c "import os,serial,time,sys; p=os.environ.get('MAGTAG_PORT','/dev/ttyACM1'); s=serial.Serial(p,115200,timeout=0.2); end=time.time()+14; data=bytearray();
while time.time()<end:
    data.extend(s.read(4096))
s.close(); sys.stdout.buffer.write(data)"
```

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
