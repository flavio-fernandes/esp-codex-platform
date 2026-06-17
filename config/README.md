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
- `WORKBENCH_CAMERA_DEVICE` when using `tools/workbench-camera-capture` on a
  host with multiple V4L2 cameras.
- `ESPWB_MONITOR_IDLE_TIMEOUT`, default `300`, exits a quiet RFC2217 monitor so
  post-monitor recovery still runs. Set to `0` only for an intentional
  unbounded live monitor.
- `ESPWB_MONITOR_MAX_TIME`, default `0`, optionally caps total monitor runtime.

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
