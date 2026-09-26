# Workbench camera: getting photos you can trust

The bench camera is a fixed-focus UVC webcam (tested: 1280x720 MJPG at up to 30 fps). It has
no focus control. Captures are serialised on the Pi, so a second caller waits up to 30 s.

```bash
ssh workbench-agent snap > a.jpg                        # auto exposure (room / board overview)
ssh workbench-agent snap exposure=40 > b.jpg            # lit LED matrix: short manual exposure
ssh workbench-agent snap res=640x480 > small.jpg
ssh workbench-agent snap-stats exposure=40              # {"dominant":"R","lit_fraction":...}
ssh workbench-agent seq 6 2 exposure=40 | tar -xf - -C out/    # 6 frames, 2 s apart
ssh workbench-agent burst 30 exposure=40 | tar -xf - -C out/   # ~2 s of consecutive frames
```

## Reading a display reliably

- **Exposure.** Auto exposure turns a lit LED matrix into white blobs. Use `exposure=N`:
  lower N means darker, crisper pixels. Tune once, then reuse the value. Controls persist on
  the camera, so every call sets exposure explicitly.
- **Deterministic checks beat eyeballing.** `snap-stats` reports the mean colour of the pixels
  above `threshold` (default 128). Show solid colours and check `dominant`. `lit_fraction` near
  0 means the display is off, or the exposure is far too short.
- **Timing.** Displays refresh and cycle. If what you expect is time-dependent, use `seq` or
  `burst` rather than a single snap. Don't set the camera interval equal to the firmware's
  cycle period, or you'll photograph the same phase every time.
- **Change one visible thing at a time** between photos when debugging, so a difference in the
  picture has one cause. Keep backlights at full brightness for photos.
- **Look at the image before you describe it.** Say what you see, including uncertainty
  ("the rightmost digit is blurred; it reads 6 or 8").
- **Privacy.** The frame may catch the room. Photos are working evidence: keep them in scratch
  or artifacts directories, don't commit them, and don't post them publicly.
