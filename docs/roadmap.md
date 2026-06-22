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

- Built `docs/tools-validation-matrix.md`: all 8 tools tested against MagTag
  local-USB, SLOT1 (UM FeatherS3, ESP32-S3 16 MB), and SLOT2 (Witty Cloud,
  ESP8266EX 4 MB). Every cell is PASS, REJECT, or N/A. Added
  `examples/esp8266-blink/esp8266-blink.yaml` for SLOT2. Fixed
  `devcontainer.json` `containerEnv` placeholder IPs that prevented
  `config/workbench.env` from being loaded by `espwb-status`, `espwb-monitor`,
  and `validate-workbench.sh`.
- Validate devcontainer startup after any `.devcontainer/` change.
- Validate workbench API, SSH, and reset-aware esptool access.
- Validate serial monitoring only when intentionally needed; keep RFC2217
  open/close tests opt-in.
- Commit only after generated files, firmware, artifacts, and secrets are ignored.
- Run `docs/public-release-checklist.md` before making a downstream repo public.

## Extraction rule

Keep this repo generic. Put hardware pinouts, device UI, Home Assistant entities,
and board-specific bring-up notes in the downstream project repo.
