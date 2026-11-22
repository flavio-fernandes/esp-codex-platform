# Camera: photos you can trust

The UVC camera is not a slot. Use its installed defaults from bench.env unless
the scene needs an override; `snap` without options does **not** imply auto
exposure. Capture is locked (default lock wait 5 seconds), and a snap has a total
20-second deadline. A truncated JPEG is retried once by the server, then fails.

```sh
ssh bench-agent snap > overview.jpg
ssh bench-agent snap exposure=auto res=640x480 > small.jpg
# EXPOSURE is a measured value from local notes, not a universal constant.
ssh bench-agent snap exposure="$EXPOSURE" > display.jpg
ssh bench-agent snap-stats exposure="$EXPOSURE" threshold=128
ssh bench-agent seq 6 2 exposure="$EXPOSURE" > frames.tar
ssh bench-agent burst 30 exposure="$EXPOSURE" > burst.tar
```

Check each exit status before opening results. For automation write to a `.part`
file and rename only on success. Never combine stderr with JPEG/tar stdout.
Inspect archive entries before extracting into a fresh private directory. `seq`
is timed (1–60 images, interval 0–600 seconds); `burst` is consecutive frames
(1–300), not a promise of an exact frame rate. Resolution is `res=WxH`; `skip=N`
discards warm-up frames. Do not assume a camera supports focus controls.

- **Manual exposure for LEDs.** Auto exposure can turn pixels into white blobs.
  Lower N gives darker, crisper pixels. Tune once for the scene, then reuse it
  from machine-local notes. Every capture sets exposure explicitly on the server.
- **Numeric checks.** `snap-stats` reports mean colour of pixels above threshold
  (default 128), dominant colour and lit fraction. Near-zero lit fraction means
  off **or underexposed**, not proof the firmware failed. It captures a new image;
  it is not statistics for the preceding snap.
- **Timing.** Use seq/burst for changing displays; avoid sampling at the exact
  firmware cycle period or you may repeatedly catch only one phase.
- **Debugging.** Change one visible thing at a time; keep backlight consistent.
- **Inspect.** Open every cited photo, read the fresh nonce and describe uncertainty
  honestly (for example, a blurred digit). Stats alone cannot prove rendered text.
- **Privacy.** Frames can show the room. Keep photos private and untracked; never
  publish automatically. No focus or exposure adjustment repairs bad framing.