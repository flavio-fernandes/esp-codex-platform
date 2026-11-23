"""Fake bench tools for the agent-shell tests: each logs its argv and BENCH_CLIENT, then acts.

Data-producing calls print to stdout and messages to stderr, like the real tools, and the save/load
tools also print `first-line: ..` on stdout, so a test can prove the shell keeps that off its own
stdout. FAKE_RC_<TOOL> (dashes as underscores) sets a tool's exit code.
"""
import json
import os
import sys
from pathlib import Path

BACKUP = b"UF2\n" + bytes(range(256)) * 4 + b"first-line: in the data\n"
ESP_IMAGE = bytes(range(255, -1, -1)) * 8
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


def main(name):
    state = Path(os.environ["FAKE_TOOL_DIR"])
    argv = sys.argv[1:]
    if name == "logger":
        with open(state / "audit.log", "a") as log:
            log.write(json.dumps(argv) + "\n")
        return
    entry = {"tool": name, "argv": argv, "client": os.environ.get("BENCH_CLIENT")}
    if name == "bench-wifi-provision":
        entry["stdin_size"] = len(sys.stdin.buffer.read())  # the size only: never record credentials
    with open(state / "calls.jsonl", "a") as log:
        log.write(json.dumps(entry) + "\n")
    rc = int(os.environ.get("FAKE_RC_" + name.replace("-", "_").upper(), "0"))
    out = sys.stdout
    if name == "bench-flash-rp2":
        if argv[0] == "info":
            out.write("Program Information\n name: blink\n")
        if argv[0] == "save" and rc == 0:
            Path(argv[2]).write_bytes(BACKUP)
        if argv[0] in ("save", "load"):
            sys.stderr.write("picotool: Saving file: [=====] 100%\n")
            out.write("first-line: hello from rp2\n")
    elif name == "bench-wifi-provision":
        out.write("url: http://192.0.2.50\nstate: provisioned\n")
    elif name == "bench-flash-esp":
        if argv[1] in ("chip-id", "flash-id"):
            out.write("Chip is ESP32-D0WD-V3\n")
        if argv[1] == "read-flash" and rc == 0:
            Path(argv[2]).write_bytes(ESP_IMAGE)
            out.write(f"read {len(ESP_IMAGE)} bytes to {argv[2]}\n")
        if argv[1] == "write-flash":
            out.write("first-line: hello from esp\n")
    elif name == "bench-camera":
        if argv[0] == "snap" and rc == 0:
            Path(argv[1]).write_bytes(JPEG)
            out.write("camera noise on stdout\n")
        if argv[0] == "stats":
            out.write('{"lit_fraction": 0.5, "dominant": "R"}\n')
        if argv[0] == "burst" and rc == 0:
            Path(argv[1]).mkdir(parents=True)
            for n in range(1, int(argv[2]) + 1):
                (Path(argv[1]) / f"frame-{n:03d}.jpg").write_bytes(JPEG)
            out.write(argv[2] + "\n")
    elif name == "bench-health":
        out.write("bench DEGRADED; idle=true; camera missing\n" if rc else "bench OK; idle=true\n")
    elif name == "sudo":
        out.write("SLOT1 1-1.1: vid=2e8a pid=000a\n")
    out.flush()
    sys.exit(rc)
