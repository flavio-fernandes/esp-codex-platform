"""bench-camera: evidence photos from the bench UVC camera (legacy source in docs/history).

  bench-camera snap  OUT.jpg [exposure=auto|N] [res=WxH] [skip=N]
  bench-camera burst OUTDIR COUNT [exposure=auto|N] [res=WxH] [skip=N]
  bench-camera stats IN.jpg [threshold=N]

Settings come from bench.env (BENCH_CAMERA_DEVICE, _EXPOSURE, _WIDTH, _HEIGHT, _SKIP). Exposure is
set on every capture: the camera keeps its controls between opens, so a previous manual value would
otherwise leak into an "auto" shot. Every v4l2-ctl call is bounded by a timeout and camera.lock is
held for the whole capture. Exit codes: 0 ok, 1 v4l2-ctl failed, 2 usage or no camera,
3 camera timeout, 4 truncated JPEG (no FF D9 end marker, after one retry), 65 camera busy.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config, locks

OK, FAILED, USAGE, TIMEOUT, TRUNCATED, BUSY = 0, 1, 2, 3, 4, 65
DEFAULTS = {"BENCH_CAMERA_DEVICE": "/dev/video0", "BENCH_CAMERA_EXPOSURE": "auto",
            "BENCH_CAMERA_WIDTH": "1280", "BENCH_CAMERA_HEIGHT": "720", "BENCH_CAMERA_SKIP": "10"}
MAX_BURST = 300
SOI, EOI = b"\xff\xd8", b"\xff\xd9"
USAGE_TEXT = __doc__.split("\n\n")[1]


class Failed(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def log(message):
    print(f"bench-camera: {message}", file=sys.stderr, flush=True)


def seconds_env(name, default):
    """Timeouts are overridable from the environment, as for the flash tools."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        raise Failed(USAGE, f"{name} must be a number of seconds") from None


def settings():
    try:
        env = config.load_env()
    except FileNotFoundError:
        env = {}
    except (OSError, ValueError) as exc:
        raise Failed(USAGE, f"bench.env: {exc}") from None
    values = {key: env.get(key) or default for key, default in DEFAULTS.items()}
    return {"device": values["BENCH_CAMERA_DEVICE"], "exposure": values["BENCH_CAMERA_EXPOSURE"],
            "width": values["BENCH_CAMERA_WIDTH"], "height": values["BENCH_CAMERA_HEIGHT"],
            "skip": values["BENCH_CAMERA_SKIP"], "threshold": "128"}


def parse_opts(opts, allowed, conf):
    for opt in opts:
        key, _, value = opt.partition("=")
        if key not in allowed or not value:
            raise Failed(USAGE, f"bad option: {opt}")
        if key == "res":
            match = re.fullmatch(r"([0-9]+)x([0-9]+)", value)
            if not match:
                raise Failed(USAGE, f"bad res: {value}")
            conf["width"], conf["height"] = match.groups()
        else:
            conf[key] = value
    if not (conf["exposure"] == "auto" or conf["exposure"].isdigit()):
        raise Failed(USAGE, f"bad exposure: {conf['exposure']}")
    for key in ("width", "height", "skip", "threshold"):
        if not (conf[key].isascii() and conf[key].isdigit()):
            raise Failed(USAGE, f"bad {key}: {conf[key]}")
    return conf


class Deadline:
    def __init__(self, seconds):
        self.total, self.end = seconds, time.monotonic() + seconds

    def left(self):
        left = self.end - time.monotonic()
        if left <= 0:
            raise Failed(TIMEOUT, f"camera timeout ({self.total:g}s)")
        return left


def v4l2(args, deadline):
    """One v4l2-ctl call under the remaining time; the whole process group dies on timeout."""
    left = deadline.left()
    try:
        proc = subprocess.Popen(["v4l2-ctl", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise Failed(FAILED, f"cannot run v4l2-ctl: {exc}") from None
    try:
        _, err = proc.communicate(timeout=left)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise Failed(TIMEOUT, f"camera timeout ({deadline.total:g}s): v4l2-ctl {args[0]} did not finish") from None
    if proc.returncode != 0:
        # v4l2-ctl prints a '<' per frame on stderr; keep only the message.
        sys.stderr.write(err.decode("utf-8", errors="replace").replace("<", ""))
        raise Failed(FAILED, f"v4l2-ctl failed (rc={proc.returncode})")


def capture(conf, count, out, deadline):
    device = conf["device"]
    if conf["exposure"] == "auto":
        v4l2(["-d", device, "-c", "auto_exposure=3"], deadline)
    else:
        v4l2(["-d", device, "-c", "auto_exposure=1"], deadline)
        v4l2(["-d", device, "-c", f"exposure_time_absolute={conf['exposure']}"], deadline)
    v4l2([f"--device={device}",
          f"--set-fmt-video=width={conf['width']},height={conf['height']},pixelformat=MJPG",
          "--stream-mmap", f"--stream-skip={conf['skip']}", f"--stream-count={count}",
          f"--stream-to={out}"], deadline)


def lock_camera(deadline, wait):
    """Enter camera.lock, retrying for up to `wait` seconds of the deadline; return the context."""
    give_up = time.monotonic() + wait
    while True:
        lock = locks.camera_lock()
        try:
            lock.__enter__()
            return lock
        except locks.Busy:
            if time.monotonic() >= give_up:
                raise Failed(BUSY, "camera busy (camera.lock is held)") from None
        deadline.left()
        time.sleep(0.1)


def complete_jpeg(data):
    """Return the JPEG without trailing padding, or None if it lacks SOI or the EOI marker."""
    data = data.rstrip(b"\0")
    return data if data.startswith(SOI) and data.endswith(EOI) else None


def split_frames(data):
    """Split an MJPG stream on SOI/EOI markers, preserving the legacy behavior."""
    frames, pos = [], 0
    while (start := data.find(SOI, pos)) >= 0:
        end = data.find(EOI, start + 2)
        if end < 0:
            break
        frames.append(data[start:end + 2])
        pos = end + 2
    return frames


def check_device(conf):
    if not os.path.exists(conf["device"]):
        raise Failed(USAGE, f"camera device not found: {conf['device']}")


def snap(out, opts):
    conf = parse_opts(opts, {"exposure", "res", "skip"}, settings())
    out = Path(out)
    if not out.parent.is_dir():
        raise Failed(USAGE, f"no directory for {out}")
    check_device(conf)
    deadline = Deadline(seconds_env("BENCH_CAMERA_SNAP_TIMEOUT", 20))
    lock = lock_camera(deadline, seconds_env("BENCH_CAMERA_LOCK_WAIT", 5))
    fd, tmp = tempfile.mkstemp(prefix=".snap.", suffix=".jpg", dir=out.parent)
    os.close(fd)
    try:
        for attempt in (1, 2):
            capture(conf, 1, tmp, deadline)
            image = complete_jpeg(Path(tmp).read_bytes())
            if image:
                Path(tmp).write_bytes(image)
                os.replace(tmp, out)
                log(f"{out}: {len(image)} bytes")
                return OK
            log(f"capture {attempt} is not a complete JPEG (no FF D9 end marker)"
                + ("; retrying once" if attempt == 1 else ""))
        raise Failed(TRUNCATED, "truncated JPEG twice")
    finally:
        lock.__exit__(None, None, None)
        if os.path.exists(tmp):
            os.unlink(tmp)


def burst(outdir, count, opts):
    if not (count.isascii() and count.isdigit() and 1 <= int(count) <= MAX_BURST):
        raise Failed(USAGE, f"usage: burst OUTDIR COUNT(1-{MAX_BURST}) [opts]")
    count = int(count)
    conf = parse_opts(opts, {"exposure", "res", "skip"}, settings())
    check_device(conf)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # Same budget as a snap, plus half a second per frame (low light drops the frame rate).
    deadline = Deadline(seconds_env("BENCH_CAMERA_SNAP_TIMEOUT", 20) + count * 0.5)
    lock = lock_camera(deadline, seconds_env("BENCH_CAMERA_LOCK_WAIT", 5))
    stream = outdir / "stream.mjpg"
    try:
        capture(conf, count, stream, deadline)
        frames = split_frames(stream.read_bytes())
    finally:
        lock.__exit__(None, None, None)
        if stream.exists():
            stream.unlink()
    for number, frame in enumerate(frames, 1):
        (outdir / f"frame-{number:03d}.jpg").write_bytes(frame)
    if not frames:
        raise Failed(TRUNCATED, "no complete JPEG frame in the stream")
    print(len(frames), flush=True)
    return OK


def stats(path, threshold):
    """Colour of the LIT pixels only (the room would dominate the mean). Deterministic."""
    from PIL import Image
    img = Image.open(path).convert("RGB")
    img.thumbnail((320, 320))
    data = img.tobytes()
    lit = [p for p in zip(data[0::3], data[1::3], data[2::3]) if max(p) >= threshold]
    total = img.width * img.height
    res = {"lit_fraction": round(len(lit) / total, 4), "lit_pixels": len(lit)}
    if lit:
        mean = [round(sum(p[i] for p in lit) / len(lit), 1) for i in range(3)]
        res["mean_rgb"] = mean
        res["dominant"] = "RGB"[mean.index(max(mean))]
        res["purity"] = round(max(mean) / (sum(mean) or 1), 3)
    else:
        res["dominant"] = "none"
    return res


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "snap" and args:
            return snap(args[0], args[1:])
        if cmd == "burst" and len(args) >= 2:
            return burst(args[0], args[1], args[2:])
        if cmd == "stats" and args:
            conf = parse_opts(args[1:], {"threshold"}, settings())
            if not os.access(args[0], os.R_OK):
                raise Failed(USAGE, f"cannot read {args[0]}")
            try:
                print(json.dumps(stats(args[0], int(conf["threshold"]))), flush=True)
            except OSError as exc:
                raise Failed(FAILED, f"cannot decode {args[0]}: {exc}") from None
            return OK
        if cmd in ("help", "-h", "--help"):
            print(USAGE_TEXT)
            return OK
        raise Failed(USAGE, "usage:\n" + USAGE_TEXT)
    except Failed as exc:
        log(str(exc))
        return exc.code
