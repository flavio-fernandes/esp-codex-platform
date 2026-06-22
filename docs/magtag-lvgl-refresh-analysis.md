# MagTag LVGL Refresh Analysis

This note records why the MagTag LVGL example can produce more than one
`on_draw_end` event during boot even though the screen is static.

The short version: extra `on_draw_end` events are LVGL boot/layout settling, not
a feedback loop from `component.update: magtag_epaper`.

## Proven Event Chain

The MagTag LVGL example has four separate events that are easy to conflate:

1. The Waveshare e-paper component resets and initializes the physical panel.
2. LVGL renders widgets into its draw buffer.
3. LVGL flushes rendered pixels into ESPHome's Waveshare framebuffer.
4. `component.update: magtag_epaper` sends that framebuffer to the panel.

Visible panel motion can happen during step 1, before any LVGL draw-end callback
or display update automation has run. A visible e-paper flash at boot is
therefore not proof that `component.update: magtag_epaper` ran.

## What `on_draw_end` Really Means

In the generated ESPHome build for ESPHome 2026.6, LVGL `on_draw_end` is wired
to LVGL's `LV_EVENT_REFR_READY` event:

- `src/esphome/components/lvgl/lvgl_esphome.cpp`
- `managed_components/lvgl__lvgl/src/core/lv_refr.c`

LVGL sends this event only after a render pass that processed invalidated
areas. If there are no invalidated areas, LVGL does not emit another draw-end
event. That matches the observed steady-state serial captures: after startup
settles, the logs go quiet and the refresh counter stays at `1`.

## Root Cause Of Multiple Startup Events

During early startup, LVGL layout is still settling. The static screen contains
content-sized labels and several widgets, so the first few LVGL ticks may:

- update layout,
- compute final label/widget geometry,
- invalidate adjusted areas,
- render again,
- emit another `on_draw_end`.

That is normal LVGL boot settling. It is not caused by the e-paper push.

## Why The E-Paper Update Is Not The Cause

The Waveshare display has:

```yaml
auto_clear_enabled: false
update_interval: never
```

The MagTag LVGL example does not install a display writer/lambda on
`magtag_epaper`. With no auto-clear and no writer, `component.update:
magtag_epaper` does not call back into LVGL or invalidate the LVGL screen. It
pushes the existing Waveshare framebuffer to the physical panel.

That means the old theory was wrong:

> `component.update: magtag_epaper` causes another LVGL draw-end event.

The actual explanation is:

> LVGL may emit multiple draw-end events while its static boot layout settles.

The one-shot guard still matters because the e-paper panel should only be pushed
once for this static example.

## Known-Good Static Pattern

The proven static e-paper pattern is:

```yaml
display:
  - platform: waveshare_epaper
    auto_clear_enabled: false
    update_interval: never
    full_update_every: 30

lvgl:
  update_interval: never
  update_when_display_idle: false
  buffer_size: 100%
  on_draw_end:
    - if:
        condition:
          lambda: return !id(magtag_epaper_refreshed);
        then:
          - lambda: id(magtag_epaper_refreshed) = true;
          - lambda: id(magtag_epaper_refresh_count) += 1;
          - component.update: magtag_epaper
```

Set the guard before calling `component.update` as a conservative safety rule,
but the root cause of multiple startup draw-end events is LVGL layout settling,
not re-entrant e-paper updating.

`lvgl.pause` after the one-shot update is optional. It can be used to freeze a
static screen after the first panel push, but it is not required to prevent an
e-paper feedback loop. Leaving the draw-start/draw-end logs enabled is useful
while this example remains a diagnostic reference.

## How To Verify

Capture serial from reset:

```bash
.venv-esptool/bin/python -c "import os,serial,time,sys; p=os.environ['MAGTAG_PORT']; s=serial.Serial(p,115200,timeout=0.2); end=time.time()+20; d=bytearray()
while time.time()<end: d.extend(s.read(4096))
s.close(); sys.stdout.buffer.write(d)"
```

Expected healthy pattern:

- a short startup burst of `LVGL draw start` / `LVGL draw end` logs,
- one `requesting one-shot e-paper refresh` log,
- later heartbeat lines with `epaper_refreshed=true epaper_refresh_count=1`,
- no later draw-end burst unless something invalidates LVGL again.

Interpretation:

| Observation | Meaning |
| --- | --- |
| Visible panel flash with no LVGL logs | Panel reset/power sequencing, not an LVGL update. |
| Draw start/end burst, count stays `1` | LVGL boot/layout settling; one physical update requested. |
| Heartbeat count rises above `1` | Another path requested a physical e-paper update. |
| No draw-start log | LVGL did not begin rendering, or serial capture missed startup. |
| Draw start without draw end | LVGL render did not complete. |

