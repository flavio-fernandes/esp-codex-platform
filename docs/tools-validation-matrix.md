# Tools Validation Matrix

## Hardware Inventory

Identified by `esptool flash-id` on 2026-06-21.

| Location | Board | Chip | Flash | Interface | USB identity |
|---|---|---|---|---|---|
| MagTag (argon direct USB) | Adafruit MagTag 2.9 | ESP32-S2 rev0.0, 40 MHz | 4 MB | USB-OTG | 239a:80e5 (app) / 303a:0002 (ROM boot) |
| SLOT1 (workbench Pi) | UM FeatherS3 | ESP32-S3 (QFN56) rev0.1, 40 MHz | 16 MB (Winbond) | USB-Serial/JTAG | 303a:1001 |
| SLOT2 (workbench Pi) | Witty Cloud | ESP8266EX, 26 MHz | 4 MB | CH340G UART adapter | 1a86:7523 |

> MACs: MagTag `7c:df:a1:01:25:f2` · SLOT1 `70:04:1d:ad:cc:48` · SLOT2 `5c:cf:7f:16:e4:76`

---

## Matrix

Legend: **PASS** = tested and works | **REJECT** = tool actively rejects this path | **N/A** = tool is not applicable for this target (different layer) | **TBD** = applicable but not yet exercised

All PASS results below were obtained on 2026-06-21/22 from inside the devcontainer
(`devcontainer exec --workspace-folder .`), with `WORKBENCH_IP`, `WORKBENCH_URL`,
and `ESP_PORT` supplied explicitly because those vars were removed from `containerEnv`
but the container has not been rebuilt yet.
After `devcontainer up --remove-existing-container` the explicit overrides are no
longer needed; `config/workbench.env` takes effect automatically.

| Tool | MagTag (local USB) | SLOT1 (UM FeatherS3) | SLOT2 (Witty Cloud) |
|---|:---:|:---:|:---:|
| `espwb-esptool` | REJECT | **PASS** | **PASS** |
| `espwb-monitor` | REJECT | **PASS** | **PASS** |
| `espwb-ssh` | N/A | **PASS** | **PASS** |
| `espwb-status` | N/A | **PASS** | **PASS** |
| `workbench-local-esptool` | N/A | **PASS** (via slot flash-id) | **PASS** (via slot flash-id) |
| `validate-workbench.sh` | **PASS** (`STATIC_ONLY=1`) | **PASS** | **PASS** |
| `workbench-camera-capture` | **PASS** | **PASS** | **PASS** |
| `workbench-camera-sequence` | **PASS** | **PASS** | **PASS** |

---

## Tool Behaviour by Flow

### `espwb-esptool`

- **MagTag → REJECT**: Script always SSHes to the workbench Pi. MagTag is a direct local USB board; use `.venv-esptool/bin/python -m esptool` instead.
- **SLOT1 → PASS** (default `ESPWB_SLOT=SLOT1`).
- **SLOT2 → PASS** (`ESPWB_SLOT=SLOT2`; `ALLOW_NON_SLOT1=1` already set in `config/workbench.env`).

```bash
# SLOT1
tools/espwb-esptool flash-id

# SLOT2
ESPWB_SLOT=SLOT2 tools/espwb-esptool flash-id
```

### `espwb-monitor`

- **MagTag → REJECT**: Opens `rfc2217://WORKBENCH_IP:PORT` — targets the workbench portal, not a local USB device. Use `picocom` or the Python serial snippet from `docs/native-usb-recovery.md`.
- **SLOT1 → PASS**: Default `ESP_PORT` port 4001. Live serial output received (`feathers3 blink on/off` messages confirmed).
- **SLOT2 → PASS**: Needs `ESP_PORT` overridden to port 4002. Monitor connected; SLOT2 has no ESPHome firmware flashed yet so no serial output, but RFC2217 open/close and post-monitor recovery succeeded.

```bash
# SLOT1 (default)
ESPWB_MONITOR_MAX_TIME=30 tools/espwb-monitor

# SLOT2
ESPWB_SLOT=SLOT2 ESP_PORT="rfc2217://${WORKBENCH_IP}:4002?ign_set_control" \
  ESPWB_MONITOR_MAX_TIME=30 tools/espwb-monitor
```

### `espwb-ssh`

Slot-agnostic. SSHes to `WORKBENCH_USER@WORKBENCH_IP`. Does not target MagTag.
Both `hostname` and `test -x /usr/local/bin/espwb-local-esptool` passed.

```bash
tools/espwb-ssh hostname
tools/espwb-ssh test -x /usr/local/bin/espwb-local-esptool
```

### `espwb-status`

- **MagTag → N/A**: MagTag is not in a workbench slot. Check identity with `lsusb` and `find /dev/serial/by-id`.
- **SLOT1 → PASS**: Reports `present=True running=True state=idle tcp_port=4001 devnode=/dev/ttyACM0`.
- **SLOT2 → PASS**: Reports `present=True running=True state=idle tcp_port=4002 devnode=/dev/ttyUSB0 USB2.0-Serial (1a86:7523)`.

```bash
# SLOT1
tools/espwb-status

# SLOT2
ESPWB_SLOT=SLOT2 ESP_PORT="rfc2217://${WORKBENCH_IP}:4002?ign_set_control" tools/espwb-status
```

### `workbench-local-esptool`

Runs **on the workbench Pi**. Invoked by `espwb-esptool` over SSH. Not called directly from the host. Tested implicitly by every `espwb-esptool` invocation; confirmed for both SLOT1 and SLOT2.

### `validate-workbench.sh`

- **MagTag → PASS** (`STATIC_ONLY=1`): 18/18 checks passed. Host tooling and project helpers all present.
- **SLOT1 → PASS**: 23/23 checks passed, including workbench API, SSH, `espwb-status`, and flash-id.
- **SLOT2 → PASS** (`ESPWB_SLOT=SLOT2`): 23/23 checks passed.

```bash
# Static only (safe for any flow)
STATIC_ONLY=1 tools/validate-workbench.sh

# SLOT1 full
tools/validate-workbench.sh

# SLOT2 full
ESPWB_SLOT=SLOT2 ESP_PORT="rfc2217://${WORKBENCH_IP}:4002?ign_set_control" \
  tools/validate-workbench.sh
```

### `workbench-camera-capture` / `workbench-camera-sequence`

Host-side V4L2 only. No slot dependency. Camera confirmed present at
`/dev/v4l/by-id/usb-Creative_Technology_Ltd._Live__Cam_Chat_HD_VF0790_2015032504121-video-index0`.
Both tools captured frames successfully.

```bash
tools/workbench-camera-capture
tools/workbench-camera-sequence 4 3
```

---

## ESPHome Build Targets

| Board | Example YAML | `esphome config` | `esphome compile` | Flash tested |
|---|---|:---:|:---:|:---:|
| MagTag (ESP32-S2, 4 MB) | `examples/magtag-basic-blink/magtag-basic-blink.yaml` | **PASS** | **PASS** | **PASS** |
| MagTag + LVGL (ESP32-S2, 4 MB) | `examples/magtag-lvgl-shapes/magtag-lvgl-shapes.yaml` | TBD | **PASS** | **PASS** |
| SLOT1 — UM FeatherS3 (ESP32-S3, 16 MB) | `examples/feathers3-rgb-blink/feathers3-rgb-blink.yaml` | **PASS** | **PASS** | **PASS** |
| SLOT2 — Witty Cloud (ESP8266EX, 4 MB) | `examples/esp8266-blink/esp8266-blink.yaml` | **PASS** | **PASS** | **PASS** |
| Generic ESP32 (4 MB) | `examples/esp32-devkit-gpio2-blink/esp32-devkit-gpio2-blink.yaml` | TBD | TBD | TBD |

> `magtag-lvgl-shapes` compile and flash were completed during the MagTag bring-up work
> (see git log and `docs/native-usb-recovery.md`). All other compile/flash cells pending.

---

## Known Issues Fixed

### `devcontainer.json` containerEnv override (fixed 2026-06-22)

`devcontainer.json` previously set `WORKBENCH_IP`, `WORKBENCH_URL`, and `ESP_PORT`
as `containerEnv` entries with placeholder value `192.0.2.10`. Because
`load_workbench_env` preserves already-exported env vars so that command-line
overrides win over `config/workbench.env`, those container-level placeholders were
inadvertently protected from being overridden by the per-checkout config file.

Tools that include `WORKBENCH_IP` / `WORKBENCH_URL` / `ESP_PORT` in their
`load_workbench_env` argument list (`espwb-status`, `espwb-monitor`,
`validate-workbench.sh`) all connected to `192.0.2.10` instead of the real
workbench IP. Tools that omit those vars from the list (`espwb-esptool`,
`espwb-ssh`) were unaffected.

**Fix**: removed `WORKBENCH_IP`, `WORKBENCH_URL`, and `ESP_PORT` from `containerEnv`
in `.devcontainer/devcontainer.json`. The scripts' own `${WORKBENCH_IP:-192.0.2.10}`
fallbacks remain for environments with no config file.

**Action required**: run `devcontainer up --workspace-folder . --remove-existing-container`
once to pick up the change. Until then, pass the real IP explicitly:

```bash
WORKBENCH_IP=192.168.1.235 WORKBENCH_URL="http://192.168.1.235:8080" \
  ESP_PORT="rfc2217://192.168.1.235:4001?ign_set_control" tools/espwb-status
```
