# Bench pitfalls

- **Stale images lie.** Build, record the exact output's SHA256, flash that path.
  Put a new nonce on the display and in repeated serial logs for every build.
- **Backups contain credentials.** RP2 UF2 and ESP binary dumps are private, not
  source artifacts. Back up first and check success before destroying unknown state.
- **Freshness matters.** Tail can be stale; wait searches new lines only. A nonce
  plus increasing counter proves more than an old boot line. History records what
  was attempted/written; failed operations may leave partial firmware.
- **Kind is detected, not assigned.** A slot is a fixed port, not a board. Verify
  it every job. Two BOOTSEL devices are now handled by port-path selection.
- **RP2 software recovery needs working USB.** Touch/fallback cannot rescue a
  crashed image without USB. Ask for physical BOOTSEL, not endless retries.
- **DTR differs by kind.** RP2 needs it high; UART ESP needs deasserted controls.
  The per-slot owner handles this, not a client opening the tty.
- **Native USB is opt-in for monitoring.** Do not confuse deliberately closed
  monitoring with a dead application or force it on from a code task.
- **Battery boards stay powered.** Unplugging USB may not power-cycle them. A
  hub reporting switching does not prove VBUS actually switched.
- **Protect the host disk.** Never reset a root hub or disk subtree. Use only
  guarded, approved slot recovery, and coordinate all projects before hub actions.
- **Hold for the whole job.** A hold prevents other client labels' mutations and
  marks maintenance non-idle; it does not isolate jobs sharing the same SSH key.
- **On failure:** run health/status/tail, preserve exact errors, stop. Apply only
  the task's bounded reboot recovery policy; recheck nonce after any host restart.
- **Evidence:** view the photo, quote actual serial/stats/exit codes, redact
  identities when publishing. Camera exposure/phase can conceal a working board.
- **Deployment is separate.** Editing a checkout does not update installed bench
  services or agent skills. Operators deploy reviewed changes and check drift.

## History (not current instructions)

- The retired Pi 3 proxy scanned ports at startup; fixed port paths now prevent renumbering.
- The retired network portal lacked authentication; restricted SSH is the sole client entry.
- The retired global proxy stop interrupted every board; pauses now affect only one slot.
- Pi 3 shared USB/Ethernet hangs and packet-flow workarounds are archived, not Pi 4 recovery recipes.
- The old RP2 console holder and repeated proxy restarts are replaced by one per-slot serial owner.
- The old upstream installer renamed hosts; current installation preserves host identity.
- The 1.2.1 hello patch is archived with the retired skill, not current AWTRIX guidance.