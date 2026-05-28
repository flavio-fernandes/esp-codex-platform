# Public hygiene checklist

Before making this repo or a downstream project public, run these checks from
the repo root:

```bash
git status --short
git status --ignored --short
git ls-files
git diff --check
git grep -n -E '<private-lan-regex>|<secret-regex>'
rg -n '<private-lan-regex>|<secret-regex>'
```

Confirm that tracked files do not include:

- `config/workbench.env`
- `secrets.yaml`
- `.env` or `.env.*`
- private keys, tokens, passwords, or certificates
- generated firmware such as `*.bin`, `*.elf`, `*.factory.bin`, or `*.ota.bin`
- generated ESPHome or PlatformIO build directories
- local captures under `artifacts/`
- private LAN IPs, device MACs, usernames, hostnames, or Tailscale names
- project-specific hardware notes, photos, screenshots, or Home Assistant
  entities that do not belong in a generic public starter

Local setup values belong in ignored files such as `config/workbench.env`.
Committed docs should use placeholders like `192.0.2.10`.
