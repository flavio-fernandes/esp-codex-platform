# Native USB recovery

Use the current [design](../bench/DESIGN.md) and
[bench runbook](bench-build-from-scratch.md). The prior MagTag direct-USB
investigation and commands are retained as
[historical findings](history/native-usb-recovery-before-migration.md), not
instructions to install retired host helpers.

Native USB can change identities and tty nodes during bootloader entry, app boot
and deep sleep. A USB identity or reachable SSH host does not prove the app is
running. Start with `tools/espwb-status`, then an application-specific marker.
The bench resolves the configured physical port again on re-enumeration.

## On the bench

Hold the approved slot, then identify before writing:

```sh
tools/wb hold SLOT1 60 native-usb-recovery
tools/espwb-esptool flash-id
# Back up first; this is private binary data.
tools/espwb-esptool read-flash 0x0 ALL artifacts/before.bin
# Use a freshly compiled merged image and correct offset for the board.
tools/espwb-esptool write-flash 0x0 path/to/firmware.factory.bin
tools/wb status
tools/wb release SLOT1
```

Stop on failure, retain stderr and always release the hold. The server pauses
only the affected serial owner, identifies before writing, applies native-USB
retry/re-enumeration handling and reports failure if the app does not return.
Do not install another serial owner, open its tty directly or request an admin
shell from an agent. There is no automatic GPIO BOOT/EN fixture wiring.

Native-USB monitoring is off under `monitor=auto`; an administrator may choose
`monitor=on` only after proving safe behavior. `reset` supports ESP UART bridges,
not native USB. `usb-reset` re-enumerates a guarded slot; it is not a guaranteed
power cycle and cannot manufacture a missing ROM-loader path. If BOOT/RESET
requires a physical action, stop for an operator rather than repeatedly flashing.

## Direct local recovery

For a board physically removed from the bench, an operator may use direct local
USB with its vendor's documented ROM-loader sequence and esptool. Discover the
actual device; never assume a fixed tty number or offsets from another board.
Use the generated full factory image when appropriate. Restore normal BOOT state
and prove application logs plus visible behavior afterward.

The historical MagTag lesson remains: ESP32-S2 firmware and ROM may share a USB
identity, and a battery can keep the application powered across USB unplugging.
Do not infer a fresh boot from either event alone.
