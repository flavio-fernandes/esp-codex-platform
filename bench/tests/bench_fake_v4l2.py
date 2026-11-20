"""Fake v4l2-ctl: logs argv, then acts on FAKE_V4L2_MODE for --stream-to captures.

good: a complete JPEG; truncated: no FF D9 marker; truncated-once: truncated on the first capture
only; hang: never finishes (a child process sleeps too, to prove the whole group dies); fail: rc 1;
burst: --stream-count frames concatenated, padded like a real MJPG stream.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

state = Path(os.environ["FAKE_V4L2_DIR"])
with open(state / "calls.jsonl", "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_V4L2_MODE", "good")
args = dict(arg[2:].split("=", 1) for arg in sys.argv[1:] if arg.startswith("--") and "=" in arg)
if mode == "hang":
    subprocess.Popen(["sleep", "300"])
    sys.stderr.write("<<<")
    sys.stderr.flush()
    import time
    time.sleep(300)
if mode == "fail":
    sys.stderr.write("<<VIDIOC_STREAMON: failed: No such device\n")
    sys.exit(1)
if "stream-to" in args:
    image = (state / "frame.jpg").read_bytes()
    captures = state / "captures"
    number = int(captures.read_text()) + 1 if captures.exists() else 1
    captures.write_text(str(number))
    if mode == "truncated" or (mode == "truncated-once" and number == 1):
        data = image[:-200]
    elif mode == "burst":
        data = b"".join(image + b"\0" * 7 for _ in range(int(args["stream-count"])))
    else:
        data = image + b"\0\0\0"
    Path(args["stream-to"]).write_bytes(data)
