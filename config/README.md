# Local environment settings

This directory is the obvious home for setup-specific values such as workbench
IP addresses, SSH users, RFC2217 monitor ports, and optional local camera
settings.

Committed files:

- `workbench.env.example`: safe placeholder values and documentation.

Ignored local files:

- `workbench.env`: real values for this machine/network.

To configure a checkout:

```bash
cp config/workbench.env.example config/workbench.env
```

Then edit `config/workbench.env`.

The project tools automatically source `config/workbench.env` when it exists.
Keep local overrides in that file, or point `ESPWB_CONFIG_FILE` at another env
file for a one-off setup.

Useful values to set locally:

- `WORKBENCH_IP`, `WORKBENCH_USER`, `WORKBENCH_URL`, and `ESP_PORT` for the
  workbench connection.
- `ESPWB_SLOT`, which should stay `SLOT1` unless a downstream project clearly
  documents a different safe slot.
- `ESPWB_SSH_KEY` when the default `/host-ssh` mount is not enough.
- `ESPWB_KNOWN_HOSTS` when the workbench is reachable by SSH but `ssh-keyscan`
  is unreliable during recovery. The helper still uses strict host-key
  checking against the supplied known_hosts file.
- `ESPWB_SSH_CONNECT_TIMEOUT`, `ESPWB_SSH_SERVER_ALIVE_INTERVAL`, and
  `ESPWB_SSH_SERVER_ALIVE_COUNT_MAX` when SSH needs longer or shorter failure
  bounds.
- `ESPWB_API_CONNECT_TIMEOUT`, `ESPWB_API_MAX_TIME`, and
  `ESPWB_RECOVER_API_MAX_TIME` when workbench API requests need different
  bounds.
- `ESPWB_UF2_VOLUME_LABEL` and `ESPWB_UF2_MIN_ADDRESS` only in a downstream
  project that actually uses app-only UF2 iteration. The generic starter leaves
  board-specific values such as `MAGTAGBOOT` and `0x10000` as commented
  examples.
- `ESPWB_UF2_VERIFY_TIMEOUT` and `ESPWB_UF2_VERIFY_INTERVAL` to tune how long
  app-only UF2 verification waits for the bootloader volume to reappear.
- `ESPWB_LOCAL_PORTAL_SERVICE` when the workbench service has a non-default
  name and `tools/espwb-uf2-write` needs to stop/restart it remotely.
- `WORKBENCH_CAMERA_DEVICE` when using `tools/workbench-camera-capture` on a
  host with multiple V4L2 cameras.
- `ESPWB_MONITOR_IDLE_TIMEOUT`, default `300`, exits a quiet RFC2217 monitor so
  post-monitor recovery still runs. Set to `0` only for an intentional
  unbounded live monitor.
- `ESPWB_MONITOR_MAX_TIME`, default `0`, optionally caps total monitor runtime.
- `STATIC_ONLY=1` for validation runs that should check local tooling without
  requiring a reachable workbench or physical board.

Values for the workbench-installed helper:

The following variables are read by `/usr/local/bin/espwb-local-esptool` on the
Raspberry Pi workbench, not by the project checkout unless you are running that
helper directly on the Pi:

- `ESPWB_LOCAL_API_URL`
- `ESPWB_LOCAL_REENUMERATE_WAIT`
- `ESPWB_LOCAL_REENUMERATE_INTERVAL`
- `ESPWB_LOCAL_POST_ESPTOOL_SETTLE`
- `ESPWB_LOCAL_ESPTOOL_ATTEMPTS`
- `ESPWB_LOCAL_PORTAL_START_ATTEMPTS`
- `ESPWB_LOCAL_PORTAL_START_INTERVAL`
- `ESPWB_LOCAL_PORTAL_PORT`
- `ESPWB_LOCAL_GPIO_RESET`
- `ESPWB_LOCAL_GPIO_BOOT`
- `ESPWB_LOCAL_GPIO_EN`

Keep GPIO reset support opt-in. The generic platform does not assume every
fixture has BOOT/EN wired, nor that every board uses the same GPIO numbers.

Debug-only toggles:

- `ESPWB_POST_OPERATION_RECOVER=0` skips the esptool wrapper's post-operation
  workbench recovery request.
- `ESPWB_VERIFY_APP_BOOT=0` skips the optional OpenOCD ROM/download-mode check.
- `ESPWB_MONITOR_RECOVER=0` skips post-monitor recovery only when paired with
  `ESPWB_MONITOR_ALLOW_UNRECOVERED_EXIT=1`.
- `RUN_RFC2217_TEST=1` enables the validation script's intentional RFC2217
  open/close test.

Do not set these in normal use; they are for isolating workbench reset,
recovery, or monitor behavior.
