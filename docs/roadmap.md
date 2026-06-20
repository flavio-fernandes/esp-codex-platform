# Roadmap

## Completed milestones

- Created reusable ESPHome/devcontainer starter repo.
- Created and pushed private GitHub repo.
- Added reset-aware workbench wrapper pattern.
- Added RFC2217 monitor wrapper pattern with reset-aware recovery.
- Added post-operation recovery status fallback for stale workbench API
  `running` state when the RFC2217 TCP port is reachable.
- Added validation script pattern.
- Added optional generic workbench camera capture helpers.
- Documented host, devcontainer, and workbench prerequisites.
- Added generic ESPHome examples.

## Next project steps

- TODO: build a tools validation matrix and test every script under `tools/`
  one by one. Each tool should either explicitly support or explicitly reject
  the MagTag local-USB flow, the ESP32 boards used in this repo, and workbench
  slots other than `SLOT1`.
- Copy `config/workbench.env.example` to ignored `config/workbench.env` and
  replace placeholder workbench values for the target environment.
- Validate devcontainer startup after any `.devcontainer/` change.
- Validate workbench API, SSH, and reset-aware esptool access.
- Validate serial monitoring only when intentionally needed; keep RFC2217
  open/close tests opt-in.
- Add one tiny board-specific ESPHome example.
- Commit only after generated files, firmware, artifacts, and secrets are ignored.
- Run `docs/public-release-checklist.md` before making a downstream repo public.

## Extraction rule

Keep this repo generic. Put hardware pinouts, device UI, Home Assistant entities,
and board-specific bring-up notes in the downstream project repo.
