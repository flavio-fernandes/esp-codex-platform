# Workbench cheat sheet

## Required assumptions

- Docker Engine is installed and `docker ps` works without `sudo` on the Linux
  host.
- The Dev Containers CLI is installed as `devcontainer` on the Linux host, or
  VS Code is opening the repo through its Dev Containers workflow.
- Local workbench settings are in ignored `config/workbench.env`.
- The workbench API is reachable at `${WORKBENCH_URL}`.
- SSH to `${WORKBENCH_USER}@${WORKBENCH_IP}` works.
- `/usr/local/bin/espwb-local-esptool` exists on the workbench.
- `SLOT1` is the safe default slot.
- Optional camera capture requires a local V4L2 camera and `v4l2-ctl` on the
  Linux host.

A Dev Container is the repo-defined Docker development environment in
`.devcontainer/`; it keeps ESPHome and helper tooling consistent. Learn more at
<https://containers.dev/>.

## Commands

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . esphome version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
devcontainer exec --workspace-folder . tools/espwb-status
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

After changing `.devcontainer/Dockerfile` or `.devcontainer/devcontainer.json`,
rebuild and replace the running container:

```bash
devcontainer up --workspace-folder . --remove-existing-container
devcontainer exec --workspace-folder . rg --version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
```

The committed devcontainer creates the `vscode` user itself and keeps
`updateRemoteUserUID` disabled. If that setting is removed, Dev Containers may
build an extra UID-adjustment image and Docker may warn about an empty generated
`BASE_IMAGE`.

## Using SLOT2

`SLOT1` is the default and safest target. To use `SLOT2`, pass both
`ESPWB_SLOT=SLOT2` and `ALLOW_NON_SLOT1=1` so the command makes the non-default
slot choice explicit.

```bash
devcontainer exec --workspace-folder . env ESPWB_SLOT=SLOT2 ALLOW_NON_SLOT1=1 tools/espwb-esptool flash-id
devcontainer exec --workspace-folder . env ESPWB_SLOT=SLOT2 ALLOW_NON_SLOT1=1 tools/espwb-esptool chip-id
devcontainer exec --workspace-folder . env ESPWB_SLOT=SLOT2 ALLOW_NON_SLOT1=1 tools/espwb-esptool write-flash 0x0 path/to/firmware.factory.bin
```

For an RFC2217 serial monitor on `SLOT2`, use port `4002` and keep the same
post-monitor recovery guardrails:

```bash
devcontainer exec --workspace-folder . bash -lc 'set -a; source config/workbench.env; set +a; ESPWB_SLOT=SLOT2 ALLOW_NON_SLOT1=1 ESP_PORT="rfc2217://${WORKBENCH_IP}:4002?ign_set_control" tools/espwb-monitor'
```

The workbench helper can discover the SLOT2 serial device from the workbench
API. Do not substitute `/dev/ttyUSB*` or `/dev/ttyACM*` paths by hand unless
you are intentionally debugging the workbench itself.

## ESPHome validation

Identify the board first:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Use the detected chip family and flash size to choose ESPHome `board` and
`flash_size` values. The LED GPIO is not discoverable through `flash-id`; get it
from the board or vendor docs.

Edit the `board`, `flash_size`, and `status_led_pin` substitutions in
`examples/generic-blink/generic-blink.yaml` for the actual board installed in
the workbench slot before compiling or flashing it.

```bash
devcontainer exec --workspace-folder . esphome config examples/generic-blink/generic-blink.yaml
devcontainer exec --workspace-folder . esphome compile examples/generic-blink/generic-blink.yaml
```

The first compile can take several minutes while PlatformIO fills the
devcontainer cache.

Only flash a factory image immediately after a successful compile of the same
YAML. The ignored `.esphome/` build tree can contain stale binaries from older
experiments, and `tools/espwb-esptool write-flash` will flash whatever file path
you pass it.

## Pre-flash identity check

```bash
devcontainer exec --workspace-folder . tools/espwb-status
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Verify the expected slot, chip family, and flash size for the board installed in
the downstream project immediately before flashing. In the status output,
`serial: reachable` only means the RFC2217 portal socket accepted a connection;
it does not prove application liveness.

## Native USB states

Native USB boards can change USB identity as they move between application
firmware, ESP ROM bootloader, UF2 bootloader, and deep sleep. Start with:

```bash
devcontainer exec --workspace-folder . tools/espwb-status
```

| State | What status usually shows | Meaning | Next step |
| --- | --- | --- | --- |
| Application running | Application USB identity, portal reachable, and an app-specific probe responds | Firmware is executing | Continue normal testing. |
| TinyUF2 bootloader | UF2 volume present; for MagTag, `239a:00e5` | Bootloader is running, not the app | Use app-only UF2 if the downstream project supports it. |
| Deep sleep | USB serial disappears or portal state becomes stale until wake | Firmware intentionally powered down native USB | Wait for wake, use a configured wake source, or reset. |
| Portal/recovery issue | API reachable but serial refused, `running=false`, or `last_error` set | Workbench ownership and USB state disagree | Run reset-aware recovery, then re-check status. |

MagTag examples are useful concrete identities, not generic assumptions:

```text
239a:00e5  Adafruit MagTag TinyUF2 bootloader
239a:80e5  Adafruit MagTag application USB identity
303a:0002  Espressif ESP32-S2 ROM bootloader
```

## Recovery

If the device under test is visually stuck or blank after a bad firmware flash
or after closing an RFC2217 serial monitor, keep using the reset-aware workbench
helper. Do not flash through RFC2217.

First try a reset-aware identity check:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

`tools/espwb-esptool` also asks the workbench API to recover/release the slot
after the RFC2217 portal has restarted. It first trusts the API's `running`
status. If the slot is present and recovery has ended, but the API state is
stale, it also checks whether the slot's RFC2217 TCP port is reachable. This
catches ESP32-S3 USB-Serial/JTAG cases where the portal reopening `/dev/ttyACM*`
would otherwise leave the board visually wedged again.

Healthy recovery output looks like one of these:

```text
== workbench reports SLOT1 recovered and portal running ==
== workbench reports SLOT1 recovered; RFC2217 TCP port is reachable ==
```

If recovery still warns, read the printed slot status. `present=true` with a TCP
connection error points at the portal path; `recovering=true` or a ROM/download
mode app-boot error points back at the reset/release path.

If the SSH host-key pre-scan times out during recovery but the workbench key is
already trusted locally, point the helper at that file while keeping strict
host-key checking:

```bash
devcontainer exec --workspace-folder . env ESPWB_KNOWN_HOSTS=/host-ssh/known_hosts tools/espwb-esptool flash-id
```

If OpenOCD is available, `tools/espwb-esptool` also checks whether the CPU is
still sitting in ESP32-S3 ROM/download code after recovery. If it prints
`still appears to be in ESP32-S3 ROM/download code`, the flash can be valid and
verified while the app is still not running. Release BOOT and reset the board,
or fix the workbench BOOT/EN fixture wiring/state so the recovery path can do
that unattended. Use `ESPWB_VERIFY_APP_BOOT=0` only while debugging that check.

If pressing the physical RESET button makes the freshly flashed app run, keep
the firmware and focus on the reset path. That result means the board was left
in ROM/download mode after flashing, but the image itself is good. The automated
path needs to reproduce a plain reset with BOOT released.

If `flash-id` fails with a pySerial write timeout and the workbench API reports
the board in application or UF2 USB identity instead of Espressif ROM/download
mode, the board has not entered the ROM bootloader. For boards like the
UnexpectedMaker FeatherS3, the manual recovery sequence is BOOT held while
RESET is pressed and released; persistent automation requires equivalent
BOOT/RESET wiring in the workbench fixture.

If the flashed app is bad, rebuild a known-good tiny example and flash its
fresh factory image.

For native USB boards, use this recovery decision tree:

```text
Does the application boot?
|
+-- Yes -- continue normal testing.
|
+-- No, but a trusted UF2 volume exists
|   +-- write app-only UF2
|   +-- verify requested blocks against CURRENT.UF2
|   +-- reset and verify application identity plus an app-specific probe
|
+-- No after verified app-only UF2, or no UF2 volume
    +-- enter the ESP ROM bootloader
    +-- use direct USB or another known-good ROM-loader transport
    +-- run the downstream project's documented full flash
```

App-only UF2 command shape:

```bash
devcontainer exec --workspace-folder . tools/espwb-uf2-write \
  --app-only \
  --label VOLUME \
  --min-address APP_OFFSET \
  path/to/app-only.uf2
```

The UF2 label and application offset are board-specific. For example, a MagTag
downstream project may use `--label MAGTAGBOOT --min-address 0x10000`, but this
generic repo does not make those defaults. `tools/espwb-uf2-write --full` is
always refused; TinyUF2 is not a full bootloader-region recovery path.

For full native USB recovery, connect the board directly to the Linux host or
another known-good ESP ROM-loader path, enter ROM bootloader mode, and run the
downstream board's full flash command with explicit offsets. Generic ESP32-S2
command shape:

```bash
python -m esptool \
  --chip esp32s2 \
  --port /dev/serial/by-id/<rom-loader-device> \
  --before no-reset \
  --after hard-reset \
  write-flash <offset-1> <image-1> <offset-2> <image-2>
```

Do not copy offsets from another board. More detail is in
`docs/native-usb-recovery.md`.

After any flash, ask "what state am I in?" before changing firmware again:

```bash
devcontainer exec --workspace-folder . tools/espwb-status
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

Then run a downstream application-specific probe. If the board remains in UF2
after a verified app-only write, switch to ROM-loader full recovery. If the app
is intentionally asleep, validate wake/reset behavior separately from portal
failure.

## Serial monitor

RFC2217 is for monitoring only, not flashing or reset control.

Use the project monitor wrapper from the repo root. It reads
`config/workbench.env`, opens raw DUT serial logs over `${ESP_PORT}`, and runs a
reset-aware `flash-id` after the monitor exits because closing RFC2217 sessions
can perturb some targets:

```bash
devcontainer exec --workspace-folder . tools/espwb-monitor
```

By default the wrapper exits after `ESPWB_MONITOR_IDLE_TIMEOUT` seconds without
serial bytes, default `300`, so a quiet or wedged RFC2217 session still reaches
the automatic recovery check. Set `ESPWB_MONITOR_IDLE_TIMEOUT=0` only for an
intentional unbounded live monitor. Set `ESPWB_MONITOR_MAX_TIME` to a non-zero
number of seconds when a test should have a hard total runtime cap.

Set `ESPWB_MONITOR_RECOVER=0` only when intentionally debugging the RFC2217
close behavior and you do not want the automatic post-monitor reset check.
The wrapper also requires `ESPWB_MONITOR_ALLOW_UNRECOVERED_EXIT=1` before it
will skip recovery. Do not use either setting for normal monitoring.

Avoid opening the workbench `/dev/ttyACM*` device directly with ad hoc serial
tools. On ESP32-S3 USB-Serial/JTAG boards, control-line changes from a plain
serial open can leave the device in ROM download mode or otherwise visually
wedged. Use `tools/espwb-monitor`, then let its automatic recovery check run.

If closing a serial monitor perturbs the target device, recover through:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

That command is intentionally used as a recovery reset check: it enters the ROM
bootloader through the workbench helper, reads the flash identity, and hard
resets the board back toward normal app boot.

`tools/validate-workbench.sh` skips its RFC2217 open/close test by default.
Set `RUN_RFC2217_TEST=1` only when intentionally debugging the monitor path.

## Camera capture

The camera helpers run on the Linux host, not through RFC2217 and not through
the workbench flashing path:

```bash
tools/workbench-camera-capture
tools/workbench-camera-sequence 4 3
```

The default `WORKBENCH_CAMERA_DEVICE` is the stable `/dev/v4l/by-id/...` path
for the current workbench camera. Override it in `config/workbench.env` when a
different local camera is attached.
