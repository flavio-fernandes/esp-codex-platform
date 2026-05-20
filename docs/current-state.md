# Current state

## Repository

- Repo: private GitHub repo configured as `origin`.
- Branch: `main`
- Roadmap: `docs/roadmap.md`

## Architecture

Mac -> VS Code SSH -> Linux host -> devcontainer -> workbench -> ESP board

## Known-good workflow

1. Create or copy a tiny ESPHome YAML.
2. Run `esphome config`.
3. Run `esphome compile`.
4. Confirm board identity with `tools/espwb-esptool flash-id`.
5. Flash the generated factory image through `tools/espwb-esptool write-flash`.
6. Validate the real hardware behavior.

## Safety rules

- RFC2217 is monitor-only.
- Flashing goes through `tools/espwb-esptool`.
- Use `SLOT1` only unless explicitly approved.
- No secrets in git.
- No `sudo` without explicit approval.
- No GitHub push unless explicitly approved.
