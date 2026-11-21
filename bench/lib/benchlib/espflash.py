"""bench-flash-esp: chip-id, flash-id, read-flash or write-flash on an ESP board by slot.

Runs esptool >= 5 on the slot's tty while bench-serial is paused. Port of the reset-aware
sequence in tools/workbench-local-esptool for native-USB boards (esp-usb): the tty is
re-resolved at the slot's port path before every esptool run, because a reset makes the
board re-enumerate (possibly under a new ttyACM name); a failed run waits for the board to
come back and retries with --before no-reset (3 attempts), the last one without the stub.
RFC2217 is never used: esptool talks to the local tty only.
"""
from contextlib import ExitStack
import getpass
import hashlib
import math
import os
import re
import subprocess
import sys
import time

from . import config, history, holds, locks, serialclient, usbdev
from .rp2flash import Failed, poll

OK, USAGE, BUSY, ABSENT, TOOL_FAILED, HELD, WRONG_KIND, NO_APP = 0, 2, 65, 66, 68, 69, 70, 71
ESP_KINDS = {"esp-uart", "esp-usb"}
ROM_IDS = {("303a", "0002")}  # ESP32-S2 ROM download mode (CDC); not the application
# Seconds (attempts: a count); each is overridable through the environment.
WAITS = {"BENCH_ESP_REENUM_WAIT": 12, "BENCH_ESP_USB_SETTLE": 8, "BENCH_ESP_APP_WAIT": 20,
         "BENCH_ESP_OPEN_WAIT": 15, "BENCH_ESP_LINE_WAIT": 5, "BENCH_ESP_TOOL_TIMEOUT": 900,
         "BENCH_ESP_ATTEMPTS": 3}
ACTIONS = ("chip-id", "flash-id", "read-flash", "write-flash")
OFFSET_RE = re.compile(r"0x[0-9a-fA-F]+\Z")
SIZE_RE = re.compile(r"(?:0x[0-9a-fA-F]+|[0-9]+|ALL)\Z")
PROGRESS = re.compile(r".*(?:\[[=>. ]*\]\s*[0-9.]+\s*%|\(\s*[0-9]+\s*%\s*\)|^(?:Reading from|Writing at|Erasing at|Checking|Compressed) .*[0-9.]+%.*).*")
TRANSFER_ERROR = re.compile(r"Packet content transfer stopped|Timed out waiting for packet|Invalid head of packet|Serial data stream stopped|Failed to (?:read|write)")
MAC = re.compile(r"\bMAC\b|BASE MAC", re.IGNORECASE)
CHIP = re.compile(r"(?:Chip is|Chip type:)\s*(.+)")
FLASH_SIZE = re.compile(r"Detected flash size:\s*([0-9]+)\s*(KB|MB)")
USAGE_TEXT = ("usage: bench-flash-esp SLOTn chip-id|flash-id|read-flash OUT [SIZE]"
              "|write-flash 0xOFF FILE")
sleep = time.sleep  # tests skip the settle and retry pauses


def log(message):
    print(f"bench-flash-esp: {message}", file=sys.stderr, flush=True)


def esptool_path():
    return os.environ.get("BENCH_ESPTOOL") or "/opt/bench/venv/bin/esptool"


def waits():
    result = {}
    for name, default in WAITS.items():
        raw = os.environ.get(name)
        try:
            value = default if raw is None else float(raw)
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or value < 0 or (name == "BENCH_ESP_ATTEMPTS" and
                                                     (value < 1 or value != int(value))):
            raise Failed(USAGE, f"{name} must be a finite nonnegative number"
                         + (" (a whole count >= 1)" if name == "BENCH_ESP_ATTEMPTS" else ""))
        result[name] = value
    result["BENCH_ESP_ATTEMPTS"] = int(result["BENCH_ESP_ATTEMPTS"])
    return result


def parse_identity(text):
    """Return (chip, flash size in bytes or None) from esptool flash-id/chip-id output."""
    chip = CHIP.search(text)
    size = FLASH_SIZE.search(text)
    nbytes = int(size.group(1)) * (1024 * 1024 if size.group(2) == "MB" else 1024) if size else None
    return (chip.group(1).strip() if chip else None), nbytes


def esptool(port, args, timeout, stream, before="default-reset", stub=True, hide_mac=False, baud=None):
    """Run esptool on port; echo output without progress (or MAC) lines; return (rc, text)."""
    argv = [esptool_path(), "--port", port, "--chip", "auto", "--before", before,
            "--after", "hard-reset", *([] if stub else ["--no-stub"]),
            *(["--baud", str(baud)] if baud else []), *args]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout, check=False)
        rc, raw = proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as exc:
        rc, raw = 124, (exc.stdout or b"") + f"\nesptool timed out after {timeout:g}s".encode()
    except OSError as exc:
        rc, raw = 127, str(exc).encode()
    lines = [line for line in raw.decode("utf-8", errors="replace").replace("\r", "\n").split("\n")
             if line.strip() and not PROGRESS.fullmatch(line.strip())]
    for line in lines:
        if not (hide_mac and MAC.search(line)):
            print(line, file=stream, flush=True)
    return rc, "\n".join(lines)


def tty_at(path, app_only=False):
    device = usbdev.resolve(path)
    if not device or not device["tty"]:
        return None
    if app_only and (device["vid"], device["pid"]) in ROM_IDS:
        return None
    return device["tty"]


def check_usage(argv):
    if len(argv) < 2 or argv[1] not in ACTIONS:
        raise Failed(USAGE, USAGE_TEXT)
    slot, action, rest = argv[0], argv[1], argv[2:]
    if not ((action in ("chip-id", "flash-id") and not rest) or
            (action == "read-flash" and len(rest) in (1, 2)) or
            (action == "write-flash" and len(rest) == 2)):
        raise Failed(USAGE, USAGE_TEXT)
    try:
        path = config.load_slots()[config.validate_slot(slot)]["path"]
    except KeyError:
        raise Failed(USAGE, f"{slot} is not a configured slot") from None
    except (ValueError, OSError) as exc:
        raise Failed(USAGE, str(exc)) from None
    request = {"action": action, "slot": slot, "path": path, "image": None}
    if action == "read-flash":
        out = rest[0]
        size = rest[1] if len(rest) == 2 else None
        if not out or not os.path.isdir(os.path.dirname(os.path.abspath(out))):
            raise Failed(USAGE, f"no directory for the output file: {out}")
        if size is not None and (not SIZE_RE.fullmatch(size) or
                                 (size != "ALL" and int(size, 0) == 0)):
            raise Failed(USAGE, f"SIZE must be ALL, decimal or 0x-hex bytes above 0: {size}")
        request.update(file=out, size=size)
    elif action == "write-flash":
        offset, file = rest
        if not OFFSET_RE.fullmatch(offset):
            raise Failed(USAGE, f"offset must look like 0x10000: {offset}")
        try:
            with open(file, "rb") as stream:
                data = stream.read()
        except OSError as exc:
            raise Failed(USAGE, f"cannot read firmware {file}: {exc.strerror}") from None
        if not data:
            raise Failed(USAGE, f"firmware is empty: {file}")
        request.update(file=file, offset=offset,
                       image={"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return request


class Operation:
    def __init__(self, request, client, limits):
        self.action, self.slot, self.path = request["action"], request["slot"], request["path"]
        self.request, self.client, self.limits = request, client, limits
        self.record = {"kind": None, "usb_serial": None}
        self.result = None
        self.notes = []

    def note(self, text):
        log(text)
        self.notes.append(text)

    def run(self):
        try:
            with locks.slot_lock(self.slot):
                return self._locked()
        except locks.Busy:
            raise Failed(BUSY, f"{self.slot} is busy (another operation holds its lock)") from None

    def _locked(self):
        try:
            holds.check(self.slot, self.client)
        except holds.Held as exc:
            raise Failed(HELD, f"{self.slot} {exc}") from None
        device = usbdev.resolve(self.path)
        if device is None:
            raise Failed(ABSENT, f"{self.slot} ({self.path}) has no device")
        self.record.update(kind=device["kind"], usb_serial=device["serial"])
        if device["kind"] not in ESP_KINDS:
            raise Failed(WRONG_KIND, f"{self.slot} holds {device['vid']}:{device['pid']} "
                                     f"({device['kind']}), not an ESP board")
        self.native = device["kind"] == "esp-usb"
        with ExitStack() as pause:
            owner = self._pause(pause)
            seq = self._seq() if owner else None
            rc = self._operate()
            if self.native and self.limits["BENCH_ESP_USB_SETTLE"]:
                # The old tty can linger briefly after the hard reset before it re-enumerates.
                log(f"waiting {self.limits['BENCH_ESP_USB_SETTLE']:g}s for native USB to settle")
                sleep(self.limits["BENCH_ESP_USB_SETTLE"])
            if not tty_at(self.path, app_only=True):
                log(f"waiting up to {self.limits['BENCH_ESP_APP_WAIT']:g}s for the application tty")
                if not poll(lambda: tty_at(self.path, app_only=True), self.limits["BENCH_ESP_APP_WAIT"]):
                    message = f"{self.slot} application tty did not come back"
                    if tty_at(self.path):
                        message += " (the board is still in ROM download mode)"
                    if rc == OK and self.action == "write-flash":
                        raise Failed(NO_APP, message)
                    self.note(message)
        # The pause connection is closed here; bench-serial may reopen the tty.
        if self.action == "write-flash":
            self._first_line(owner, seq)
        return rc

    def _pause(self, stack):
        try:
            stack.enter_context(serialclient.paused(self.slot, f"bench-flash-esp {self.client}"))
        except serialclient.NotRunning:
            self.note(f"bench-serial is not running for {self.slot}; continuing without a pause")
            return False
        except serialclient.ProtocolError as exc:
            raise Failed(BUSY, f"bench-serial refused the pause: {exc}") from None
        except OSError as exc:
            raise Failed(BUSY, f"bench-serial pause failed: {exc}") from None
        return True

    def _seq(self):
        try:
            return serialclient.state(self.slot)["seq"]
        except (OSError, ValueError, serialclient.ProtocolError):
            return None

    def _port(self):
        """The slot's tty now; a native-USB board may still be re-enumerating."""
        tty = tty_at(self.path)
        if tty is None and self.native:
            log(f"waiting up to {self.limits['BENCH_ESP_REENUM_WAIT']:g}s for {self.slot} "
                f"to re-enumerate at {self.path}")
            tty = poll(lambda: tty_at(self.path), self.limits["BENCH_ESP_REENUM_WAIT"])
        if tty is None:
            raise Failed(ABSENT, f"{self.slot} ({self.path}) has no tty")
        return tty

    def _esptool(self, args, stream, hide_mac=False):
        """One esptool step. esp-usb: bounded retries across re-enumeration, as the old tool."""
        attempts = self.limits["BENCH_ESP_ATTEMPTS"] if self.native else 1
        before = "default-reset"
        for attempt in range(1, attempts + 1):
            port = self._port()
            stub = not (self.native and attempt > 1 and attempt == attempts)
            if attempt > 1:
                log(f"retrying esptool on {port} (attempt {attempt}/{attempts}, before={before}"
                    f"{'' if stub else ', no stub'})")
            # USB-UART bridges (CP210x, CH340, FTDI) at the 115200 default need ~6 min for a 4 MB
            # read (Argon W9, 2026-11-21). Bulk transfers run at BENCH_ESP_BAUD (default 921600);
            # native USB ignores baud.
            baud = None
            if not self.native and args[0] in ("read-flash", "write-flash"):
                baud = int(os.environ.get("BENCH_ESP_BAUD") or 921600)
            rc, text = esptool(port, args, self.limits["BENCH_ESP_TOOL_TIMEOUT"], stream, before, stub,
                               hide_mac, baud)
            if rc != 0 and baud and TRANSFER_ERROR.search(text):
                # Some USB-UART bridges (a clone CP2102 on Argon, 2026-11-21) cannot sustain the fast
                # rate: every read failed at 230400-921600 and passed at 115200. Fall back once.
                self.note(f"esptool {args[0]} failed at {baud} baud; retried at the 115200 default")
                rc, text = esptool(port, args, self.limits["BENCH_ESP_TOOL_TIMEOUT"], stream, before, stub,
                                   hide_mac, None)
            if rc == 0:
                return rc, text
            self.note(f"esptool {args[0]} failed (rc={rc}, attempt {attempt}/{attempts})")
            if attempt < attempts:
                # The board may have reset into ROM (or back) and re-enumerated; wait for it.
                if poll(lambda: tty_at(self.path), self.limits["BENCH_ESP_REENUM_WAIT"]) is None:
                    self.note(f"{self.slot} did not reappear for an esptool retry")
                    return rc, text
                before = "no-reset"
                sleep(1)
        return rc, text

    def _identity(self):
        rc, text = self._esptool(["flash-id"], sys.stderr, hide_mac=True)
        if rc != 0:
            raise Failed(TOOL_FAILED, f"identity (flash-id) failed on {self.slot}")
        chip, size = parse_identity(text)
        log(f"identity: chip={chip or 'unknown'} flash={size if size is not None else 'unknown'} bytes")
        return size

    def _operate(self):
        if self.action in ("chip-id", "flash-id"):
            rc, _ = self._esptool([self.action], sys.stdout)
            if rc != 0:
                raise Failed(TOOL_FAILED, f"esptool {self.action} failed on {self.slot}")
            return OK
        flash = self._identity()
        if self.action == "read-flash":
            size = self.request["size"]
            if size is None:
                size = hex(flash) if flash is not None else "ALL"
            elif size != "ALL" and flash is not None and int(size, 0) > flash:
                raise Failed(USAGE, f"SIZE {size} is over the flash size ({flash} bytes)")
            args = ["read-flash", "0", size, self.request["file"]]
        else:
            image, offset = self.request["image"], int(self.request["offset"], 16)
            if flash is None:
                self.note("WARNING: flash size not detected; size check skipped")
            elif image["size"] > flash or offset + image["size"] > flash:
                raise Failed(USAGE, f"{image['size']} bytes at {self.request['offset']} do not fit "
                                    f"the {flash}-byte flash")
            args = ["write-flash", self.request["offset"], self.request["file"]]
        rc, _ = self._esptool(args, sys.stderr)
        if rc != 0:
            raise Failed(TOOL_FAILED, f"esptool {self.action} failed on {self.slot}")
        if self.action == "read-flash":
            with open(self.request["file"], "rb") as stream:
                data = stream.read()
            self.result = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            print(f"read {len(data)} bytes to {self.request['file']}", flush=True)
        return OK

    def _first_line(self, owner, seq):
        text = None
        if owner:
            def is_open():
                try:
                    return serialclient.state(self.slot)["open"]
                except (OSError, ValueError, serialclient.ProtocolError):
                    return False
            if poll(is_open, self.limits["BENCH_ESP_OPEN_WAIT"]):
                try:
                    line = serialclient.wait(self.slot, "", self.limits["BENCH_ESP_LINE_WAIT"], seq)
                except (OSError, ValueError, serialclient.ProtocolError):
                    line = None
                text = line["text"] if line else None
            else:
                self.note(f"bench-serial did not reopen {self.slot} within "
                          f"{self.limits['BENCH_ESP_OPEN_WAIT']:g}s")
        print(f"first-line: {text if text is not None else '(no output yet)'}", flush=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        request = check_usage(argv)
        limits = waits()
    except Failed as exc:
        log(str(exc))
        return exc.code
    client = os.environ.get("BENCH_CLIENT") or getpass.getuser()
    operation = Operation(request, client, limits)
    started, rc = time.monotonic(), 1
    try:
        rc = operation.run()
    except Failed as exc:
        rc = exc.code
        operation.note(str(exc))
    finally:
        history.append(client=client, verb="esp-" + request["action"], slot=request["slot"], rc=rc,
                       seconds=round(time.monotonic() - started, 3),
                       note="; ".join(operation.notes) or None,
                       **operation.record, **(request["image"] or operation.result or {}))
    log(f"{request['action']} {request['slot']}: rc={rc}")
    return rc
