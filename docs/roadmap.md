# Roadmap

Current design: [bench/DESIGN.md](../bench/DESIGN.md).
The earlier milestones and dated validation findings are preserved in
[the historical roadmap](history/roadmap-before-migration.md).

## Current platform

- Pi 4/xHCI Debian 13 host, fixed USB port paths and kind detection.
- Per-slot serial owner, multi-reader local sockets, slot-local flash pauses.
- RP2/ESP flashing, guarded USB recovery, bounded camera captures.
- Restricted SSH verbs, holds, persistent audit/history and health/idle status.
- Idempotent installer and read-only drift checks.
- Client tools and runbooks using the new bench protocol.

## Remaining release gates

- Update agent skills separately; do not use archived skill instructions as
  current host administration guidance.
- Migrate downstream device repositories and re-run their physical acceptance.
- Validate devcontainer startup after configuration changes; fake client tests
  do not prove Docker startup or SSH mounts/agent forwarding.
- Review hardware commissioning and USB-root reliability evidence privately.
- Run `public-release-checklist.md` before any approved GitHub publication.

Keep the platform generic. Device pinouts, UI, Home Assistant entities and
private hardware reports belong in downstream projects.
