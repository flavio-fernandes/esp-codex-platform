# Workbench cheat sheet

## Required assumptions

- Docker works without `sudo` on the Linux host.
- The devcontainer CLI works on the Linux host.
- The workbench API is reachable from the devcontainer.
- SSH to the workbench works.
- `/usr/local/bin/espwb-local-esptool` exists on the workbench.
- `SLOT1` is the safe default slot.

## Commands

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . esphome version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```

## Serial monitor note

RFC2217 is for monitoring only, not flashing or reset control.

If closing a serial monitor perturbs the target device, recover through:

```bash
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
```
