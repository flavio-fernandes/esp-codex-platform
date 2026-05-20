# Codex instructions for this workspace

Scope: this repository only.

Safe things to do:

- Edit files in this workspace.
- Run commands inside this repo.
- Add project-local scripts, docs, ESPHome YAML, and devcontainer files.
- Build or rebuild the devcontainer.
- Run validation scripts in `tools/`.
- Use `tools/espwb-esptool` for chip-id, flash-id, read-flash, and write-flash on `SLOT1`.
- Install small missing developer tools only after asking, limited to packages like `ripgrep`, `jq`, `tree`, `shellcheck`, `git`, `curl`, and `unzip`.

Do not do these without explicit user approval:

- Do not run `sudo`.
- Do not run `sudo` for Docker, SSH keys, systemd, Tailscale, firewall, `/usr/local/bin`, or workbench service changes.
- Do not modify files outside this workspace.
- Do not modify SSH key directories.
- Do not print, copy, commit, or upload private keys, tokens, passwords, or `secrets.yaml`.
- Do not push to GitHub.
- Do not flash slots other than `SLOT1`.
- Do not use RFC2217 reset control for flashing.
- Do not create a large hardware-specific ESPHome YAML in one jump.

Important workflow rule:

- RFC2217 is for serial monitor only.
- Flashing must go through `tools/espwb-esptool`, which calls the reset-aware helper on the workbench.
