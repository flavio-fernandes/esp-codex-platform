# ESP Codex platform

Reusable starter repo for ESPHome projects developed through this workflow:

Mac -> VS Code SSH -> Linux host -> Docker/devcontainer -> ESP workbench -> USB ESP board

The goal is not to hide the workflow behind a clever generator. The goal is to
start every future project with the same small, validated pieces:

- ESPHome devcontainer
- reset-aware workbench wrappers
- safe flashing defaults
- docs and handoff templates
- a tiny known-good ESPHome example

## Configure

Set these values for your environment before using the workbench tools:

```bash
export WORKBENCH_IP=192.0.2.10
export WORKBENCH_USER=pi
export ESPWB_SLOT=SLOT1
```

`SLOT1` is the safe default. Use other slots only after explicit approval in the
device-specific project.

## Useful commands

```bash
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . esphome version
devcontainer exec --workspace-folder . tools/validate-workbench.sh
devcontainer exec --workspace-folder . tools/espwb-esptool flash-id
devcontainer exec --workspace-folder . esphome config examples/generic-blink/generic-blink.yaml
devcontainer exec --workspace-folder . esphome compile examples/generic-blink/generic-blink.yaml
```

## Safety

- Use `tools/espwb-esptool` for flashing and esptool operations.
- Use RFC2217 only for serial monitoring.
- Do not use RFC2217 reset control for flashing.
- Keep `secrets.yaml`, private keys, tokens, `.env` files, generated firmware,
  and `artifacts/` out of git.
