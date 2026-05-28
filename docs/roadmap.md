# Roadmap

## Completed milestones

- Created reusable ESPHome/devcontainer starter repo.
- Created and pushed private GitHub repo.
- Added reset-aware workbench wrapper pattern.
- Added RFC2217 monitor wrapper pattern with reset-aware recovery.
- Added validation script pattern.
- Added generic ESPHome examples.

## Next project steps

- Copy `config/workbench.env.example` to ignored `config/workbench.env` and
  replace placeholder workbench values for the target environment.
- Validate devcontainer startup.
- Validate workbench API, SSH, and reset-aware esptool access.
- Validate serial monitoring only when intentionally needed; keep RFC2217
  open/close tests opt-in.
- Add one tiny board-specific ESPHome example.
- Commit only after generated files, firmware, artifacts, and secrets are ignored.
- Run `docs/public-release-checklist.md` before making a downstream repo public.

## Extraction rule

Keep this repo generic. Put hardware pinouts, device UI, Home Assistant entities,
and board-specific bring-up notes in the downstream project repo.
