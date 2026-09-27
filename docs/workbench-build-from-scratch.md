# Workbench: build from scratch (portal, camera, RP2 flashing, agent access)

This is the runbook for rebuilding the **workbench**, the Raspberry Pi that holds the boards,
from a blank SD card. After following it the Pi will do four things:

- run the upstream **Embedded-AI-Harness** rfc2217 portal (the project formerly called
  Universal-Embedded-Workbench; upstream now says "testbench") for ESP flashing and serial;
- flash **RP2040/RP2350** boards (Pico, Pico W, Pimoroni Galactic/Cosmic Unicorn), which
  upstream does not support;
- take evidence photos with a **USB webcam** pointed at the boards;
- expose all of that to **AI agents** (Claude Code, Hermes, Codex) through one restricted,
  audited SSH user.

Upstream: <https://github.com/SensorsIot/Embedded-AI-Harness>. The old URL,
<https://github.com/SensorsIot/Universal-Embedded-Workbench>, redirects there. Its User
Manual §1–2 and FSD Appendix D (API) remain the reference for the portal itself. This page
covers only what this bench adds or does differently.

Values in angle brackets (`<bench-ip>`, `<camera-by-id>`) are yours to fill in. Keep them in
ignored local files, never in this repo.

---

## 1. Hardware

| Part | This bench | Notes |
|---|---|---|
| Pi | Raspberry Pi 3 Model B | Pi 3B has no phantom-port table upstream; Pi 3B+ does (`0:1.4`) |
| OS | Raspberry Pi OS Lite 64-bit (Debian 13 trixie) | user `pi`, SSH enabled |
| Network | **eth0** (wired, DHCP reservation) | wlan0 is handed to the portal for Wi-Fi tests |
| Hub | Pi's onboard SMSC9514 plus a 4-port USB 2.0 hub on onboard port 2 | |
| Camera | Creative Live! Cam Chat HD (UVC, 041e:4097) | any UVC camera that does MJPG works |

### USB port map (Pi 3B plus external 4-port hub)

`usb_prefix` is the part of the udev `ID_PATH` after `usb-`, as the portal uses it.

| Physical port | usb_prefix | Slot | Use |
|---|---|---|---|
| external hub port 1 | `0:1.2.1` | SLOT1 | RP2 board (Galactic Unicorn) |
| external hub port 2 | `0:1.2.2` | SLOT2 | free |
| external hub port 3 | `0:1.2.3` | SLOT3 | free |
| external hub port 4 | `0:1.2.4` | SLOT7 | free |
| Pi port (2nd) | `0:1.3` | SLOT4 | free |
| Pi port (3rd) | `0:1.4` | **none** | **camera: deliberately not a slot** |
| Pi port (4th) | `0:1.5` | SLOT6 | free |

Onboard port `0:1.1` is the Ethernet adapter, and `0:1.2` is the external hub itself. To
confirm the prefix of a port, plug something in and run
`udevadm info -q property -n /dev/ttyACM0 | grep ID_PATH=` or `v4l2-ctl --list-devices`.

---

## 2. Base OS

1. Flash Raspberry Pi OS Lite 64-bit with Raspberry Pi Imager: hostname `workbench`, user `pi`,
   SSH on with your public key.
2. Boot, then `sudo apt update && sudo apt full-upgrade -y && sudo reboot`.
3. Give eth0 a DHCP reservation on your router, and add an SSH alias on each client.
   **Use the IP**, because mDNS does not resolve inside containers. List both names so old and
   new docs work:

   ```sshconfig
   Host workbench testbench
     Hostname <bench-ip>
     User pi
   ```

4. **Pi 3 / Pi Zero only: turn off the USB controller's FIQ FSM.** These Pis use the `dwc_otg`
   USB host controller, and on a Pi 3 the Ethernet adapter hangs off it too. With the default
   FIQ FSM, every USB detach, BOOTSEL cycle or serial-port close on a slot board can leak one of
   the controller's host channels, until the external hub and the Ethernet stall (see
   [USB host-controller wedge](#usb-host-controller-wedge-pi-3)). Append the setting to the
   single line of `cmdline.txt`, keep a backup, and reboot:

   ```bash
   sudo cp -n /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak
   sudo sed -i '1 s/$/ dwc_otg.fiq_fsm_enable=0/' /boot/firmware/cmdline.txt
   sudo reboot
   cat /sys/module/dwc_otg/parameters/fiq_fsm_enable    # after the reboot: N
   ```

   Pi 4 and Pi 5 use an xHCI controller and need nothing here.

Optional hardening for small Pis is in upstream User Manual §2.2: journald size, swap, and
disabling ModemManager. On this bench the journal is **volatile** (lost on reboot), so copy
`journalctl -k -b` off the Pi before rebooting it to clear a fault.

---

## 3. Upstream portal (pinned to a release tag)

Pin a **release tag**, never `main`. At the time of writing the latest release is **v1.0.1**.

```bash
git clone --branch v1.0.1 --depth 1 https://github.com/SensorsIot/Embedded-AI-Harness.git ~/Embedded-AI-Harness
```

**Before** running the installer, write the slot config (§4). The installer's restart then
picks it up, and slot numbers never depend on boot timing.

```bash
cd ~/Embedded-AI-Harness/pi && sudo bash install.sh
```

What `install.sh` changes, and what to do about it:

- **Packages.** It installs apt packages (hostapd, dnsmasq-base, mosquitto, bluez,
  rtl-sdr, ...), `esptool>=5` via pip, and openocd-esp32. It masks or disables hostapd,
  dnsmasq, mosquitto and wpa_supplicant; the portal starts them on demand.
- **wlan0.** It marks wlan0 unmanaged in NetworkManager and stops dhcpcd taking DNS from
  wlan0. This is safe **only because management runs over eth0**. Don't run it on a
  Wi-Fi-only bench over SSH.
- **Hostname.** It **renames the host to `testbench-<last 4 of wlan0 MAC>`**. Put the name back
  right after:

  ```bash
  sudo hostnamectl set-hostname workbench
  sudo sed -i 's/^127\.0\.1\.1.*/127.0.1.1\tworkbench/' /etc/hosts
  ```

  sudo prints one "unable to resolve host" warning between those two commands; that is
  expected. Never use `_` in a hostname: systemd silently strips it and sudo then stalls.
- **Service.** It installs `rfc2217-portal.service`, which runs as root from
  `/usr/local/bin/rfc2217-portal`, **not** from the checkout. After editing the checkout,
  run `sudo bash install.sh --update`.

To move to a newer upstream release later:
1. Snapshot first (§9).
2. Clone the new tag next to the old checkout.
3. Read its `install.sh` diff and release notes.
4. Install.
5. Restore the hostname.
6. Re-run §8.

History: this bench previously ran an unpinned May 2026 checkout carrying two local patches, a
proxy start retry and an optional `cw_beacon.py`. v1.0.1 needs neither. Proxies came up on the
first attempt across repeated restarts, and `cw_beacon.py` no longer exists upstream.

---

## 4. Pin the slots and keep the camera out

Without `/etc/rfc2217/testbench.json` (formerly `workbench.json`; the installer renames an old
file), the portal **auto-detects** hub ports once at startup. It skips a port only if a
non-serial driver is already bound at that instant. A webcam therefore becomes a phantom slot
or not, depending on boot timing. When it doesn't, every later slot is renumbered. That
happened here: the camera's port became SLOT5, and moving the camera would have shifted
SLOT4–SLOT6 on the next restart.

The fix is an explicit slot list with no entry for the camera's port. The portal only manages
ttys, and the camera has none, so it is then ignored completely. The file used here is
[`workbench/config/testbench.json.example`](../workbench/config/testbench.json.example):

```bash
sudo install -m 0644 workbench/config/testbench.json.example /etc/rfc2217/testbench.json
sudo systemctl restart rfc2217-portal
curl -s localhost:8080/api/devices | python3 -m json.tool | grep -E '"label"|slot_key|running'
```

Details:
- Labels are kept stable. Leaving out SLOT5 is deliberate: the old SLOT6 keeps its number and
  TCP port 4006.
- Matching is by substring, longest prefix first.
- `sudo rfc2217-learn-slots` suggests a file from whatever ttys are plugged in at the moment.
- `gpio_boot`/`gpio_en` (BCM 18/17) are the defaults. Only boards with BOOT/EN wired to those
  pins benefit; `POST /api/serial/gpio-test` measures whether they are actually wired.

---

## 5. Camera, RP2 flashing and the agent user

Everything in this section is installed by one idempotent script from this repo:

```bash
git clone https://github.com/flavio-fernandes/esp-codex-platform.git ~/esp-codex-platform
sudo bash ~/esp-codex-platform/workbench/install-agent-extras.sh
```

| Installs | Why |
|---|---|
| apt `picotool v4l-utils python3-pil python3-serial usbutils` | RP2 flashing, camera capture, colour stats, 1200-baud touch |
| `/usr/local/bin/wb-agent-shell` | the agent user's only entry point (verb table, audit log) |
| `/usr/local/bin/wb-camera` | `snap`, `burst`, `stats` (v4l2-ctl, MJPG) |
| `/usr/local/bin/wb-pico-flash` | RP2040/RP2350 `info`, `save`, `load` |
| `/usr/local/bin/wb-rp2-console` + `wb-rp2-console@SLOTn.service` | holds DTR on RP2 slots so their serial output is not dropped (§5.1) |
| `/usr/local/bin/espwb-local-esptool` | reset-aware ESP helper, from `tools/workbench-local-esptool` |
| `/etc/tmpfiles.d/workbench.conf` → `/run/workbench` | lock dir shared by pi, root and wbagent (group plugdev, not sticky) |
| `/etc/udev/rules.d/60-rp2-picotool.rules` | vendor `2e8a` → group `plugdev`, so picotool needs no sudo |
| user `wbagent` | groups dialout, video, plugdev; password locked |
| `/etc/sudoers.d/wbagent` | NOPASSWD for **only** `espwb-local-esptool`; checked with `visudo -c` |
| `/etc/workbench/agent.env` | camera device, exposure, which slots agents may touch (kept if present) |
| `/etc/rfc2217/testbench.json` | only if no slot config exists yet |

Then edit `/etc/workbench/agent.env`:

```bash
ls -l /dev/v4l/by-id/          # pick the *-video-index0 entry of your camera
sudoedit /etc/workbench/agent.env
#   WB_CAMERA_DEVICE=/dev/v4l/by-id/<camera-by-id>-video-index0
#   WB_CAMERA_EXPOSURE=auto     (or a number, see §6)
#   WB_AGENT_PICO_SLOTS="SLOT1"
#   WB_AGENT_ESP_SLOTS=""       (add a slot only when an ESP board is there)
```

### 5.1 RP2 serial needs DTR (`wb-rp2-console`)

The portal's proxy opens every tty with DTR and RTS **low**. That is correct for ESP32 native
USB, where DTR/RTS mean reset and download mode. But **arduino-pico and pico-sdk USB stdio drop
all output while DTR is low**. So without help, `/api/serial/output`, `/api/serial/monitor` and
the web UI show nothing from an RP2 board that is happily printing.

Found the hard way: a freshly flashed board visibly ran on camera, but serial stayed empty
until an RFC2217 client held DTR high.

Only the proxy's single RFC2217 client may change control lines, and the proxy drops DTR again
when that client leaves. So `wb-rp2-console@SLOTn`:
- stays connected as that client with DTR high, and discards what it reads (the portal's
  recorder gets the same bytes through the proxy's read-only fan-out);
- reconnects by itself after every flash or portal restart;
- accepts writes on `127.0.0.1:600n`, because it holds the client slot that
  `/api/serial/write` would otherwise use. `wb-agent-shell serial-write` routes RP2 slots
  there.

The installer enables it for each slot in `WB_AGENT_PICO_SLOTS`. **Never enable it on an ESP
slot**: DTR high holds an ESP32-S3/C3 in reset or download mode.

A human wanting an interactive console on an RP2 slot can stop the holder first
(`sudo systemctl stop wb-rp2-console@SLOT1`), or read the fan-out port `tcp_port+1000`, which
is read-only.

### 5.2 How RP2 flashing works (`wb-pico-flash`)
1. Take a portal lease on the slot: `POST /api/slot/acquire`, mode `flashing`.
2. Stop the slot's proxy: `POST /api/stop`.
3. **1200-baud touch** on the tty. This reboots arduino-pico and pico-sdk USB-stdio firmware into
   the USB boot ROM (`2e8a:0003` RP2040, `2e8a:000f` RP2350). If it fails, fall back to
   `picotool reboot -f -u`.
4. `picotool load -v -x`, or `save -a` / `info -a`.
5. Wait for the application tty to come back. Hotplug restarts the proxy; `POST /api/start` is
   the fallback.
6. Release the lease.

Refusals: more than one board in BOOTSEL, a non-`2e8a` device in the slot, or a busy slot.

A crashed image with no USB stack can't be reached this way; it needs the physical BOOTSEL
button.

---

## 6. Camera notes

- The Live! Cam Chat HD does MJPG 1280x720 at up to 30 fps and YUYV 1280x720 at only 10 fps,
  so the scripts use MJPG. It is fixed-focus, with no focus controls.
- The `video` group is enough to capture; no sudo needed. Only one capture runs at a time
  (flock).
- **Lit LED matrices blow out under auto exposure.** Use a short manual exposure (`exposure=N`,
  i.e. `auto_exposure=1` plus `exposure_time_absolute=N`). The scripts set exposure on every
  call because the camera remembers the last value.
- `wb-camera stats` reports the mean colour of the pixels above a brightness threshold. That
  gives agents a deterministic check (show solid red/green/blue, test `dominant`) instead of a
  judgement call.
- Aim the camera and leave it. Move it only between tests, then re-tune exposure.
- These scripts replace `tools/workbench-camera-capture` and `-sequence`, which ran on a
  separate Linux host when the camera was plugged in there. Those tools now fetch from the
  bench over SSH when there is no local camera (§8).

---

## 7. Agent access (restricted SSH)

Agents never log in as `pi`, which has full sudo. Each agent gets its own key in
`/home/wbagent/.ssh/authorized_keys`. The word after the path names the client in the audit
log:

```text
restrict,command="/usr/local/bin/wb-agent-shell claude" ssh-ed25519 AAAA... claude@host
restrict,command="/usr/local/bin/wb-agent-shell hermes" ssh-ed25519 AAAA... hermes@container
```

- `restrict` turns off PTY, port, agent and X11 forwarding. The forced command ignores what
  the client asked to run, except as data: it reads `SSH_ORIGINAL_COMMAND`.
- The command must match `[A-Za-z0-9 _.:=,/#@+-]` (no quotes, `$`, `;`, `|`, backticks or
  newlines). It is split without globbing, and its first word must be a known verb.
- Firmware comes in on stdin: size-capped, and UF2 magic checked for RP2. Photos and backups
  go out on stdout.
- Every call is logged: `journalctl -t wb-agent`. Grade agent reports against that log, not
  against what the agent says it did.

Client-side alias, both names:

```sshconfig
Host workbench-agent testbench-agent
  Hostname <bench-ip>
  User wbagent
  IdentityFile ~/.ssh/<agent-key>
  IdentitiesOnly yes
  BatchMode yes
```

The verb list is `ssh workbench-agent help`. The agent-facing guide is the skill in
[`skills/workbench/`](../skills/workbench/SKILL.md).

- **Revoke an agent instantly:** delete its line from `authorized_keys`.
- **Revoke all agents:** `sudo truncate -s0 /home/wbagent/.ssh/authorized_keys`.

---

## 8. Verify

Measured on this bench (Pi 3B, v1.0.1, 2026-09-26):
- a whole-flash `pico-save` of a Pico W (4 MB) took ~52 s;
- `flash-pico` of a 470 KB UF2 took ~22 s, and of the 4 MB backup ~60 s;
- the 1200-baud touch entered BOOTSEL on the first try every time;
- the slot map was identical across two portal restarts.


On a client with the aliases:

```bash
ssh workbench-agent status                                   # slots + camera + picotool
ssh workbench-agent snap > /tmp/s.jpg && file /tmp/s.jpg     # JPEG image data
ssh workbench-agent 'status; id'                             # MUST be refused (and logged)
ssh -t workbench-agent status                                # no PTY is granted
STATIC_ONLY=1 tools/validate-workbench.sh                    # repo-side static checks
```

RP2 round trip with zero firmware risk, when an RP2 board is in SLOT1:

```bash
ssh workbench-agent pico-save SLOT1 > backup.uf2     # whole flash; holds Wi-Fi creds: keep private
ssh workbench-agent flash-pico SLOT1 < backup.uf2    # write the same image back
ssh workbench-agent serial-tail SLOT1 20             # it booted (needs §5.1)
```

End-to-end acceptance (build → flash → serial → camera): see the "hello" procedure in
[`skills/workbench/references/rp2.md`](../skills/workbench/references/rp2.md).

USB controller health (Pi 3 / Zero), also checked by `tools/validate-workbench.sh`:

```bash
ssh workbench 'cat /sys/module/dwc_otg/parameters/fiq_fsm_enable'        # N (§2 step 4)
ssh workbench 'journalctl -k -b | grep -c "FSM NP"'                       # 0, or a handful
ssh workbench 'journalctl -k -b | grep -c "hub_ext_port_status failed"'  # must be 0
```

Measured on this bench (2026-09-26), 20 `pico-info` BOOTSEL cycles of a Pico W in SLOT1: with the
default FIQ FSM, 285 `dwc_otg ... FSM NP` warnings (249 of them in one burst when the portal first
closed the board's serial port); with `dwc_otg.fiq_fsm_enable=0`, none. Earlier the same day,
after about ten flash and BOOTSEL cycles on the default setting, the controller wedged (below).

---

## USB host-controller wedge (Pi 3)

What it looks like from a client:

- `ping` answers, but each reply arrives one interval late: at `-i 1` the RTT is ~1000 ms, at
  `-i 0.5` ~500 ms, at `-i 0.05` ~50 ms. The Pi only processes a received packet when the next one
  arrives. On a Pi 3 the Ethernet is a USB device on the same controller.
- `ssh` fails with `Connection timed out during banner exchange`.
- The Pi itself is fine (load, temperature, `vcgencmd get_throttled` = `0x0`).

On the Pi, the kernel log shows a growing number of
`WARN::dwc_otg_hcd_urb_dequeue:639: Timed out waiting for FSM NP transfer to complete on N`
(N = 0-7, the controller's host channels), then `usb 1-1.2.1: USB disconnect`,
`hub 1-1.2:1.0: hub_ext_port_status failed (err = -110)` and `connect-debounce failed`. The slot
board disappears from `lsusb -t`, although it keeps running.

**Getting in without a power cycle:** keep packets flowing, and SSH works again:

```bash
ping -i 0.02 -q -w 90 <bench-ip> >/dev/null &
ssh workbench 'journalctl -k -b --no-pager' > wedge-kernel.log    # save the evidence first
ssh workbench 'sudo systemctl reboot'
```

A reboot clears it; USB and Ethernet come back with the slots. Prevention is §2 step 4. Also keep
BOOTSEL cycles (flash, `pico-save`, `pico-info`) per Pi boot to what you need, and change a
networked board's settings over its own API rather than by reflashing.

---

## 9. Snapshot and rollback

Before any upgrade, pull a tarball of everything the installers touch to another machine:

```bash
ssh workbench 'sudo tar czf - /usr/local/bin /etc/rfc2217 /etc/workbench \
  /etc/systemd/system/rfc2217-portal.service /etc/udev/rules.d /etc/sudoers.d \
  /etc/NetworkManager /etc/dhcpcd.conf /etc/hostname /etc/hosts /home/wbagent/.ssh' \
  > workbench-snapshot-$(date +%Y%m%d-%H%M%S).tgz
```

To roll back: extract the tarball at `/`, then run
`sudo systemctl daemon-reload && sudo udevadm control --reload-rules && sudo systemctl restart rfc2217-portal`.
Keep the snapshot private, since it contains keys and host details.

---

## 10. Pitfalls this setup exists to avoid

| Pitfall | Where handled |
|---|---|
| A webcam or other non-serial device becomes a slot, or renumbers slots, depending on boot timing | §4 pinned `testbench.json` |
| Upstream installer renames the host | §3 restore |
| Portal runs from `/usr/local/bin`, so edits to the checkout do nothing | §3 `--update` |
| RFC2217 used for flashing or reset | ESP flashing only via `espwb-local-esptool` (`tools/espwb-esptool`, `flash-esp`) |
| Plain open/close of an ESP32-S3 tty or RFC2217 monitor wedges it in ROM | recover with `flash-id` through the helper; `validate-workbench.sh` keeps the RFC2217 test opt-in |
| `serial: reachable` read as "app alive" | require a serial marker or photo |
| Stale images in `.esphome/`/`.pio/`; wrong offsets after erase | flash fresh builds only; `factory.bin` at 0x0 |
| Board kept old firmware and "passed" | unique marker on display and serial |
| Agent claims a visual result it never saw | `snap-stats` deterministic check; the audit log |
| uhubctl power-cycling kills the bench's own Ethernet (Pi 3) | not automated; human only |
| Pi 3 `dwc_otg` FIQ FSM leaks host channels on every USB detach/BOOTSEL/tty close until the hub and the USB Ethernet stall (ping RTT = ping interval, SSH banner timeouts) | §2 step 4 `dwc_otg.fiq_fsm_enable=0`; [USB host-controller wedge](#usb-host-controller-wedge-pi-3); `validate-workbench.sh` checks it |
| Portal API has no auth | agents use the restricted SSH verbs; keep the bench on a trusted LAN |
| devcontainer `containerEnv` placeholders override `config/workbench.env` | see `docs/tools-validation-matrix.md` |
| Two RP2 boards in BOOTSEL at once | `wb-pico-flash` refuses |
| A lock file in sticky `/run/lock` created by one user locks out every other, root included (`fs.protected_regular=2`) | locks live in `/run/workbench` and are opened read-only |
| RP2 serial silently empty: the proxy keeps DTR low, and arduino-pico/pico-sdk drop output | §5.1 `wb-rp2-console` holds DTR on RP2 slots only |
| Portal says `running` before its monitor port listens, so a `serial-wait` straight after a flash fails | `wb-pico-flash` waits for the monitor port too |
| Playground audit shell drops `su -c` positional args, so a build flag arrives empty | validate simple values and inline them (skill `rp2.md`) |
| RP2 backups contain Wi-Fi credentials | never commit; keep private |

More: [`skills/workbench/references/pitfalls.md`](../skills/workbench/references/pitfalls.md),
[`docs/native-usb-recovery.md`](native-usb-recovery.md), and upstream's
`docs/Harness-User-Manual.md`.
