# Build the bench from scratch

The supported host is a Raspberry Pi 4 (or a suitable xHCI Pi), Debian 13,
booting from SSD. [The design contract](../bench/DESIGN.md) fixes the protocol,
paths and guards. This is an **administrator** runbook, not a set of commands
available through the restricted agent login. Replace all angle-bracket
placeholders locally; never commit installation identities or credentials.

## Hardware and OS

Use a stable power supply, a self-powered board hub, short USB cables, and a
UVC camera on its own Pi port. One slot is **one physical downstream USB port**,
not a board serial number or a tty number. Label the ports. Keep the camera out
of the slot map. Keep the SSD outside the board hub's subtree. Never reset or
power-cycle the root hub: the root filesystem depends on it.

Install Debian 13 with SSH and SSD boot using the board's supported imaging
and bootloader procedure. Confirm root with `findmnt /` and topology with
`lsusb -t` before enabling automated recovery. Keep an independent recovery
boot medium and a tested backup. Do not migrate an unstable disk unattended.

## Map ports, then install

On the host, compare the device tree before and after plugging one board into
each labelled port:

```sh
ls /sys/bus/usb/devices
lsusb -t
udevadm info --query=path --name=/dev/ttyACM0
udevadm info --attribute-walk --name=/dev/ttyACM0
```

Use the USB **device** path (for example `1-1.1.1`), not an interface suffix
such as `:1.0`, nor `/dev/ttyACM0`. The examples below are schematic; discover
your own topology. Map ports even when their boards are absent.

Prepare `/etc/bench/slots.conf` as root:

```text
# label usb-port-path [options]
SLOT1 1-1.1.1
SLOT2 1-1.1.2 baud=115200
SLOT3 1-1.1.3 monitor=off
SLOT4 1-1.1.4 monitor=auto
```

Labels and paths must be unique. Options are only `monitor=auto|on|off` and
`baud=N`. Auto monitors RP2 and ESP UART bridges, not native-USB ESP boards.
Enable `monitor=on` for native USB only after proving that opening its CDC
port does not upset the application. Kind is detected each time from VID:PID.

Copy `bench/config/bench.env.example` to `/etc/bench/bench.env`, then edit it.
It is **literal KEY=VALUE, not shell syntax**: do not add quotes to a space-separated
`BENCH_HUB_PATHS=1-1.1 2-1`. Discover every actual hub path; remove examples that
are not present. Set `BENCH_CAMERA_DEVICE` to the camera's stable by-id device
and choose `BENCH_CAMERA_EXPOSURE` for the scene. Keep `BENCH_POWER_CYCLE=0`.

From a reviewed checkout, as administrator:

```sh
sudo bash bench/install.sh
sudo bash bench/install.sh --check
```

Expected: `INSTALL OK`, then `NO DRIFT` and exit 0. The installer preserves
existing config and authorized keys. It installs the Python package, isolated
venv with esptool 5, serial units, health timer, udev/tmpfiles, narrowly scoped
sudoers, SSH restrictions and persistent journald settings. It does not configure
networking, the hostname, firewall, or client keys. Rerun it after changing slots,
hub paths or code; it regenerates port rules and reconciles/restarts serial units.
`--check` is read-only and exits 1 with `DRIFT ...` for differences. Never use
`BENCH_NO_SYSTEMD=1` on a real host; that is for disposable install tests only.

## Restricted agent keys

The service user is `bench`; clients log in as password-locked `wbagent`.
Give every client a dedicated key and distinct client label. An administrator
adds a line to `wbagent`'s `authorized_keys` (0600; directory 0700):

```text
restrict,from="<client-ip-or-cidr>",command="/usr/local/bin/bench-agent-shell <client>" ssh-ed25519 <public-key> <label>
```

Use a client label matching `[A-Za-z0-9_.@-]{1,64}`. The source restriction must
match the address the host actually sees. Private keys stay on the client.
Verify the host fingerprint out of band, then configure the client SSH alias:

```sshconfig
Host bench-agent
    HostName <bench-ip>
    User wbagent
    IdentityFile ~/.ssh/<dedicated-key>
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    RequestTTY no
```

No admin shell, PTY, forwarding, or arbitrary shell syntax is exposed. Only
`/usr/local/sbin/bench-usb` is permitted through sudo. Never broaden this to
make a failing operation work. Clients use `ssh bench-agent <verb>` or `tools/wb`.

## Commissioning: H1 smoke list

An administrator verifies before handing over hardware:

1. `sudo bash bench/install.sh --check` prints `NO DRIFT`.
2. `systemctl --failed` has no failed units; each configured
   `bench-serial@SLOTn.service` and `bench-health.timer` is active. Inspect tty
   access and state with a present, monitor-enabled board.
3. From **each** permitted client, run these separately:
   `ssh bench-agent status`, `ssh bench-agent health`, `ssh bench-agent help`.
4. `ssh bench-agent 'status; id'` must be refused (2), run nothing, and appear
   in the audit journal. `ssh -tt bench-agent status` must not allocate a PTY.
5. Verify the worker cannot log in through an administrative account or access
   services outside its allowed isolation boundary. Run the deployment's own
   isolation probe and retain its result privately; do not invent a universal
   firewall command for an unknown network.
6. Check the camera with `ssh bench-agent snap > proof.jpg`, **open the image**,
   and verify framing/exposure. Delete failed/partial captures.
7. Hold the selected slot and perform the board-specific backup/flash/marker
   acceptance in the [cheatsheet](bench-cheatsheet.md), then release it. Repeat
   for each supported kind. Software tests alone are not hardware commissioning.

## Recovery and health

Start with `ssh bench-agent health --json` and `ssh bench-agent status`.
SSH exit 255 is **OFF/unreachable or authentication failure**, not evidence of a
bad board. Health exit 1 is DEGRADED with `problems`; 2 means collection failed
and must never authorize an upgrade. Reasons include throttling, temperature
at least 80 C, disk free below 10%, missing hub/camera, unopened monitored ports,
and growth in kernel `error -71` / `error -110` counters. Missing optional probes
produce `null` plus `notes`, not healthy zeroes. A deliberate flash pause is not
an unopened-port fault. Health's idle snapshot is advisory, not an operation lock.

For an approved held slot, `ssh bench-agent usb-reset SLOT1` re-enumerates that
port only; check a fresh application marker afterward. Never manipulate sysfs
from a client. The guard refuses root-hub operations and mass-storage subtrees
(except RP2's BOOTSEL interface). A timeout or absent device is not permission
to reset a wider hub.

`power-cycle` affects the whole configured hub. Enable it only after physically
proving that the hub **really switches VBUS**, with no disk in its subtree, and
coordinating every affected slot. Many hubs report switching while leaving power
on; a USB detach is not proof. Never use the root hub as a fallback. Battery-backed
boards may remain powered even when VBUS is cut.

If an unstable USB root causes a host reboot, wait for SSH/health to return,
recheck slot state and the application marker, and retry the interrupted step
once under the project's recovery policy. Do not count an old buffered line as
a new boot or repeatedly flash into a failing host.

## Persistent evidence, backups and upgrades

The installer enables persistent journald with a 500 MiB cap. As administrator:

```sh
journalctl -t bench-agent
journalctl -u bench-serial@SLOT1.service
journalctl -u bench-health.service
journalctl -k -b
journalctl --list-boots
```

Serial logs live in `/var/lib/bench/serial/SLOTn.log` (5 MiB, five backups; mode 0640 `bench:bench`, so an admin reads them with `sudo`).
`/var/lib/bench/flash-history.jsonl` records mutations, hashes, rc and timing;
`ssh bench-agent history 20` reads recent entries. Holds live under
`/var/lib/bench/holds/`. `/run/bench/health.json` is a volatile health snapshot.
Audit/history may contain private device identities: redact before publication.

Pull backups with a **read-only rrsync key**, restricted to approved backup paths
and source addresses. Keep host configuration, persistent logs/history and
recovery material in private, versioned snapshots on another host; test a restore.
Use a separate backup key, never the agent's key, and never expose writable rsync
or administrative shell access to workers. Firmware backups can contain secrets.

Upgrade only when a fresh successful `bench-health --json` says **idle**: no
active holds, no busy slot/camera locks and no history mutation within ten minutes.
Coordinate the maintenance window to prevent new work racing the advisory check.
Abort on stale/missing/failed health. Back up first, update reviewed code and OS
packages, rerun the installer and `--check`, then repeat the smoke list after
any reboot. Keep rollback material; never auto-upgrade during a held job.

## Host hardening: USB-SSD root on a Pi 4

A disappearing USB root disk can leave ping working while every executable and
local log is unreadable. These are defensive measures, **not proof of a repaired
bridge**. A prior incident persisted even with UAS and link-power quirks; replace
the bridge/SSD or change root media if resets recur. Keep recovery access before
changing boot configuration. All steps here are administrator work.

- Find the root disk with `findmnt /`, trace its USB parent with `lsblk`,
  `lsusb -t` and `udevadm info --attribute-walk --name=<root-block-device>`.
  Read that bridge's `idVendor` and `idProduct` (or `lsusb`), not the hub's ID.
- Back up the actual bootloader command-line file (commonly
  `/boot/firmware/cmdline.txt`). Preserve its single line and root parameters;
  append `usb-storage.quirks=<vid>:<pid>:u` to disable UAS for **that bridge**.
  Reboot during maintenance and verify `lsusb -t` shows `Driver=usb-storage`
  rather than `uas`. Do not copy another installation's IDs.
- If USB 3 link-power transitions are implicated, the additional kernel quirk
  `usbcore.quirks=<vid>:<pid>:k` disables LPM for that device. Verify the running
  kernel's support and observed behavior. Neither quirk guarantees stability.
- For an **ext4** root, configure `errors=panic` in its mount options (or set the
  filesystem default with `tune2fs -e panic <root-ext4-device>`). Confirm the
  correct filesystem/device first. Set `kernel.panic=10` in an administrator-owned
  `/etc/sysctl.d/50-bench-recovery.conf`; optionally set
  `kernel.hung_task_timeout_secs=30` for early diagnostics. Load it with
  `sysctl --system`. This changes a disk-error hang into an automatic reboot;
  it does not prevent lost work or replace backups.
- Enable the board's hardware watchdog using its supported boot configuration.
  In a systemd manager drop-in under `/etc/systemd/system.conf.d/`, set
  `[Manager]`, `RuntimeWatchdogSec=60s`, and `RebootWatchdogSec=2min`.
  Verify `/dev/watchdog0` and systemd's watchdog startup log after a planned reboot.
  Overly short deadlines can reboot a healthy host during heavy I/O.
- Send kernel logs to an independent log host via **netconsole**, so root-disk
  failure cannot erase the evidence. Arrange a persistent UDP listener and a
  source-restricted firewall rule on a trusted network. A schematic module
  parameter is `netconsole=6665@<bench-ip>/<interface>,6666@<log-host-ip>/<next-hop-mac>`.
  Discover the actual interface and destination/next-hop MAC; do not publish them.
  Load/configure the module using the distribution's supported method, after
  networking is ready, and test receipt with a harmless `/dev/kmsg` test message.
  UDP is unencrypted and lossy: keep it off untrusted networks and protect logs.
- Keep the bootloader and VL805 EEPROM current using the distribution-supported
  tooling (`rpi-eeprom-update`, where packaged; `-a` stages updates). Back up
  boot configuration, review release notes, use stable power, reboot in a
  maintenance window and verify installed versions afterward.
- Before trusting the host, stress **a disposable file, never the raw root disk**:
  sustained multi-GiB direct writes, many-file copies and package installations
  into disposable environments, then bursty small fsynced writes with idle gaps.
  Leave ample free space. A continuous test can pass while idle transitions fail.
  Record duration, throughput, boot identity, throttling and USB/disk error deltas
  in both journal and netconsole. Require no resets, I/O errors or unexpected
  reboots; test realistic backup/flash concurrency too. Clean only test-owned files.

Do not deliberately panic a working production bench just to test recovery without
an approved maintenance plan and physical recovery access.
