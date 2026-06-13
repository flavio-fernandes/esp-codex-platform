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
- `WORKBENCH_CAMERA_DEVICE` when using `tools/workbench-camera-capture` on a
  host with multiple V4L2 cameras.
