# Workbench pitfalls (why the rules exist)

Sources: `tools/validate-workbench.sh`, the esp-codex-platform and crowpanel-esphome docs,
the upstream Embedded-AI-Harness skills, and the camera/RP2 bring-up on this bench.

## Bench and portal
1. **Slot numbers come from USB hub ports, and auto-detection is decided at boot.** With no
   `/etc/rfc2217/testbench.json`, the portal scans the hub once at startup. It skips a port only
   if a non-serial driver is already bound, so a webcam can become a "slot" or silently renumber
   the others, depending on boot timing. That is why this bench pins its slots and leaves the
   camera port out.
2. **The portal has no authentication.** Anything on the LAN can call `:8080`. Agents use the
   restricted SSH verbs instead: those are the audited path.
3. **`sudo systemctl stop rfc2217-portal` stops every slot.** The ESP helper does this, so an
   ESP flash interrupts serial on every board for a few seconds.
4. **The service runs from `/usr/local/bin`, not from the git checkout.** Editing the checkout
   changes nothing until `install.sh --update` runs.
5. **The upstream installer renames the host to `testbench-<mac>`.** This bench is renamed
   back to `workbench` after every full install.
6. **Never cut USB power to "reset" a board with uhubctl.** On a Pi 3 the Ethernet adapter
   shares the hub, so the bench takes itself offline.
7. **If the bench drops off the network** (ARP incomplete), a reboot fixes it. Capture
   `uptime`, `vcgencmd get_throttled` and `journalctl --list-boots` first.
7a. **If the bench answers ping slowly and SSH times out in "banner exchange"**, the Pi 3's USB
   host controller (`dwc_otg`) is wedged, and the Ethernet, which hangs off it, only delivers a
   packet when the next one arrives: ping RTT equals the ping interval. Confirm with
   `ping -i 0.5` (~500 ms) vs `ping -i 0.05` (~50 ms). To get in, keep packets flowing:
   `ping -i 0.02 -q -w 90 <bench-ip> &`, then ssh. The kernel log shows many
   `dwc_otg ... Timed out waiting for FSM NP transfer` warnings and
   `hub_ext_port_status failed (err = -110)`, and the slot board is gone from `lsusb -t`. Save
   `journalctl -k -b` first (the journal does not survive a reboot), then **ask the human** before
   rebooting the Pi. Prevention: `dwc_otg.fiq_fsm_enable=0` on the kernel command line
   (`docs/workbench-build-from-scratch.md` §2); `tools/validate-workbench.sh` checks it. If it is
   missing, tell the human rather than editing `cmdline.txt` yourself.

## Flashing
8. **Flash only what you just built, and record the sha256.** Old images linger in build dirs.
9. **Test firmware must carry a unique marker**, on the display and on serial. A board that
   kept running old firmware can otherwise pass a check.
10. **ESP: RFC2217 is for monitoring only; identity first; `factory.bin` at 0x0 after an erase.**
    See `esp.md`.
11. **The native-USB ESP32-S3 is fragile.** A plain open or close of its tty, or of an RFC2217
    monitor, can wedge it in ROM. Recovery is `flash-esp SLOTn flash-id`.
11a. **Every RP2 BOOTSEL cycle is a USB detach and re-attach**, and after each one the portal
    restarts the slot's serial proxy several times (more tty closes). On a Pi 3 these are what wear
    the USB controller down (7a). Budget them: back up once, don't `pico-info` in loops, and when
    the board is on the LAN change its settings over its own HTTP API instead of reflashing.
12. **RP2: a crashed image with no USB interface can't be reached by software.** The 1200-baud
    touch and `picotool reboot` both need the firmware's USB stack. The last resort is the
    physical BOOTSEL button, which needs a human.
13. **RP2 whole-flash backups hold Wi-Fi credentials.** Treat them as secrets.
14. **Two boards in BOOTSEL at once is ambiguous.** `flash-pico` refuses rather than guess.
15. **A battery-backed board is not power-cycled by unplugging USB.**
16. **An RP2 board prints nothing unless DTR is high.** The bench's `wb-rp2-console` holder
    takes care of it for RP2 slots. An empty `serial-tail` on an RP2 slot usually means the
    holder is down, not that the firmware is silent. Check a photo before concluding the
    board is dead.

## Evidence
17. **Say only what you observed.** Report exit codes, matched serial lines and stats JSON
    verbatim. The audit log on the Pi is what your report is graded against.
18. **Camera:** use manual exposure for LEDs, and never sync the camera interval to the
    firmware's cycle. Look at every photo you cite (`camera.md`).
