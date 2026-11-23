# Current state

The supported bench is the Pi 4/xHCI Debian 13 stack in `bench/`. See the
[design contract](../bench/DESIGN.md), [runbook](bench-build-from-scratch.md) and
[cheatsheet](bench-cheatsheet.md). Client tools use restricted SSH verbs;
`config/bench.env` holds ignored local settings. No hardware is implied by a
successful client unit test.

## Preserved findings

The 2026-06-20 MagTag blink, e-paper and LVGL investigation is preserved in
[the dated report](history/current-state-2026-06.md). Those direct-USB results
are historical, not a statement about the board currently occupying any slot.
The LVGL refresh analysis remains in `magtag-lvgl-refresh-analysis.md`.

Lessons still applicable: use a complete freshly built image; add one subsystem
at a time; verify application output, not only USB identity; inspect a camera
frame before asserting display success. A battery-backed board may not power
cycle when USB is unplugged.

## Current verification model

`bench/dev/check.sh` exercises fake USB, serial sockets, tool processes and client
SSH dispatch, plus ShellCheck. Real-host commissioning and board acceptance are
separate: use a hold, private backup, fresh marker, serial evidence and photo.
Keep hardware-specific reports and identities in private project notes.
