"""bench-wifi-provision SLOTn: give a board its Wi-Fi credentials over serial (Improv), from stdin.

Why: firmware is built on an internet-connected build VM that must never see credentials. So
ESPHome images are built WITHOUT Wi-Fi secrets (`wifi: ap:` + `improv_serial:`), and this tool
provisions them afterwards through the slot's serial port.

stdin: two lines, SSID then password (at most 512 bytes). They are never logged, printed, stored or
put on a command line; history records only that a provisioning happened. The slot is locked, its
hold checked and its serial owner paused for the exchange, as for a flash.

Exit codes as the flashers: 0 ok, 2 usage, 65 busy, 66 absent, 68 provisioning failed (device error,
no improv answer or timeout), 69 held by another client, 70 wrong kind.
"""
from contextlib import ExitStack
import getpass
import os
import sys
import time

import serial
from . import config, history, holds, improv, locks, serialclient, serialowner, usbdev
from .rp2flash import BUSY, HELD, OK, Failed

USAGE = 2
ABSENT = 66
TOOL_FAILED = 68
WRONG_KIND = 70
KINDS = {"esp-uart", "esp-usb", "rp2"}
MAX_STDIN = 512


def log(message):
    print(f"bench-wifi-provision: {message}", file=sys.stderr, flush=True)


def read_credentials(stream):
    raw = stream.buffer.read(MAX_STDIN + 1) if hasattr(stream, "buffer") else stream.read(MAX_STDIN + 1)
    if isinstance(raw, str):
        raw = raw.encode()
    if len(raw) > MAX_STDIN:
        raise Failed(USAGE, "stdin too large: expected two lines, SSID then password")
    lines = raw.decode("utf-8", errors="strict").split("\n")
    if len(lines) < 2 or not lines[0]:
        raise Failed(USAGE, "stdin must be two lines: SSID, then password")
    ssid, password = lines[0].rstrip("\r"), lines[1].rstrip("\r")
    improv.wifi_payload(ssid, password)  # validates lengths
    return ssid, password


def run(slot, client, ssid, password, open_port=None, record=None):
    record = {} if record is None else record
    open_port = open_port or serialowner.open_serial
    slots = config.load_slots()
    if slot not in slots:
        raise Failed(USAGE, f"{slot} is not in slots.conf")
    path, baud = slots[slot]["path"], slots[slot]["baud"]
    try:
        with locks.slot_lock(slot):
            try:
                holds.check(slot, client)
            except holds.Held as exc:
                raise Failed(HELD, f"{slot} {exc}") from None
            device = usbdev.resolve(path)
            if device is None or not device["tty"]:
                raise Failed(ABSENT, f"{slot} ({path}) has no serial device")
            record.update(kind=device["kind"], usb_serial=device["serial"])
            if device["kind"] not in KINDS:
                raise Failed(WRONG_KIND, f"{slot} holds a {device['kind']} board; improv needs an ESP or RP2 app")
            with ExitStack() as stack:
                try:
                    stack.enter_context(serialclient.paused(slot, f"bench-wifi-provision {client}"))
                except serialclient.NotRunning:
                    log(f"bench-serial is not running for {slot}; continuing without a pause")
                except (serialclient.ProtocolError, OSError) as exc:
                    raise Failed(BUSY, f"bench-serial refused the pause: {exc}") from None
                try:
                    port = open_port(device, baud)
                    stack.callback(port.close)
                    port.timeout = 0.2
                    result = improv.provision(port, ssid, password, log=log)
                except improv.ImprovError as exc:
                    raise Failed(TOOL_FAILED, str(exc)) from None
                except (OSError, serial.SerialException) as exc:   # busy port, board unplugged
                    raise Failed(TOOL_FAILED, f"serial port error on {device['tty']}: {exc}") from None
    except locks.Busy:
        raise Failed(BUSY, f"{slot} is busy (another operation holds its lock)") from None
    for url in result["urls"]:
        print(f"url: {url}", flush=True)
    print(f"state: {result['state'] or 'provisioned'}", flush=True)
    if not result["urls"]:
        # ESPHome sends a URL only with web_server:, so the address comes from the board's own
        # "[C] IP Address:" log line, which a logger level less verbose than CONFIG (e.g. INFO) hides.
        log("joined, but no address seen: keep the ESPHome logger at its default level "
            "(DEBUG) or add web_server:, then rebuild and reflash")
    record["note"] = "wifi provisioned" + (f"; {result['urls'][0]}" if result["urls"] else "")
    return OK


def main(argv=None, stdin=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not config.SLOT_RE.fullmatch(argv[0]):
        log("usage: bench-wifi-provision SLOTn   (stdin: SSID line, password line)")
        return USAGE
    slot, client = argv[0], os.environ.get("BENCH_CLIENT") or getpass.getuser()
    record, started, rc = {}, time.monotonic(), 1
    try:
        ssid, password = read_credentials(stdin or sys.stdin)
        rc = run(slot, client, ssid, password, record=record)
    except Failed as exc:
        rc = exc.code
        record["note"] = (record.get("note") + "; " if record.get("note") else "") + str(exc)
        log(str(exc))
    except ValueError as exc:
        rc = USAGE
        log(str(exc))
    finally:
        history.append(client=client, verb="wifi-provision", slot=slot, rc=rc,
                       seconds=round(time.monotonic() - started, 3),
                       kind=record.get("kind"), usb_serial=record.get("usb_serial"), note=record.get("note"))
    log(f"wifi-provision {slot}: rc={rc}")
    return rc
