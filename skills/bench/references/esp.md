# ESP identity, backups and flashing

Hold the approved slot first. Identify **before writing**:

```sh
ssh bench-agent flash-esp SLOT4 flash-id
ssh bench-agent flash-esp SLOT4 chip-id
```

SLOT4 is an example, not blanket authorization. Check the chip and flash capacity
against the build target. The helper also runs identity before every read/write
and refuses images exceeding capacity. Back up an unknown board privately:

```sh
umask 077
ssh bench-agent esp-save SLOT4 > before.bin.part && mv before.bin.part before.bin
```

Omitting SIZE reads detected flash size (or ALL if unavailable); explicit SIZE
accepts decimal, hex or ALL. Backup begins at zero and may contain credentials.
Stop on failure; a partial file is not a backup. Never publish it or place it in
the repository. Use a single **merged image at 0x0** containing bootloader,
partition table and application:

```sh
sha256sum firmware.factory.bin
ssh bench-agent flash-esp SLOT4 write-flash 0x0 < firmware.factory.bin
ssh bench-agent serial-wait SLOT4 30 'HELLO 5678'
```

Replace the nonce for every build. Do not flash an app-only `firmware.bin` at
zero; do not invent an erase or arbitrary esptool verb. The flash helper pauses
only this slot, takes its lock, uses reset-aware esptool and resumes the owner.
Inspect fresh logs and a display photo; programming success is not boot proof.

## DTR/RTS and native USB

For ESP UART bridges the serial owner sets DTR/RTS **deasserted before open**,
then holds the tty open once per attach. It does not intentionally reset on open;
Linux can still pulse the lines. Connecting/disconnecting serial clients does
not reopen or reset the board. `reset SLOTn` is an explicit UART EN pulse and is
unsupported for native USB. RP2 has the opposite DTR requirement; never apply
ESP modem-line policy to it.

Native-USB ESP (including S3) can change tty during flashing or remain in ROM.
Monitoring defaults off (`monitor=auto`); only an administrator who has verified
safe behavior should configure `monitor=on`. Missing serial output under this
policy is not itself a failure. The helper re-resolves the same USB port path,
uses bounded reset-aware retries and settling time, and reports an application
that never returns as 71 after a write. No client-side retry loops or manual
GPIO BOOT/EN forcing. For failures capture health/status/tail; request physical
recovery if required. Never bypass the restricted interface.