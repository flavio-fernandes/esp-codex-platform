"""bench-flash-rp2: info, save or load an RP2040/RP2350 board found by USB port path.

Port of the archived RP2 flash helper onto benchlib. The BOOTSEL device is matched by the
slot's port path, never by being the only one. Data goes to stdout (info) or the file
(save); messages and picotool progress go to stderr.
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

import serial
from . import config, history, holds, locks, serialclient, usbdev

OK, USAGE, BUSY, ABSENT, NO_BOOTSEL, PICOTOOL_FAILED, HELD, WRONG_KIND, NO_APP = (
    0, 2, 65, 66, 67, 68, 69, 70, 71)
UF2_MAGIC = b"UF2\n"
RP2_KINDS = {"rp2", "rp2-bootsel"}
# Seconds; each is overridable through the environment.
WAITS = {"BENCH_RP2_BOOTSEL_WAIT": 10, "BENCH_RP2_APP_WAIT": 30, "BENCH_RP2_OPEN_WAIT": 15,
         "BENCH_RP2_LINE_WAIT": 5, "BENCH_RP2_PICOTOOL_TIMEOUT": 300,
         "BENCH_RP2_REBOOT_TIMEOUT": 20}
ACTIONS = {"info": ["info", "-a"], "save": ["save", "-a"], "load": ["load", "-v", "-x"]}
PROGRESS = re.compile(r".*\[[= ]*\]\s+[0-9]+%")
USAGE_TEXT = "usage: bench-flash-rp2 info|save|load SLOTn [file]"
serial_factory = serial.Serial  # tests replace this; the touch never reaches a real port


class Failed(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def log(message):
    print(f"bench-flash-rp2: {message}", file=sys.stderr, flush=True)


def waits():
    result = {}
    for name, default in WAITS.items():
        raw = os.environ.get(name)
        try:
            value = default if raw is None else float(raw)
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or value < 0:
            raise Failed(USAGE, f"{name} must be a finite nonnegative number of seconds")
        result[name] = value
    raw = os.environ.get("BENCH_RP2_FLASH_BYTES")
    if raw is not None and not (raw.isascii() and raw.isdigit() and 0 < int(raw) <= 1 << 28):
        raise Failed(USAGE, "BENCH_RP2_FLASH_BYTES must be a whole number of bytes (at most 256 MiB)")
    result["BENCH_RP2_FLASH_BYTES"] = int(raw) if raw is not None else None
    return result


def poll(predicate, seconds, interval=0.1):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def location(device):
    return ["--bus", str(device["busnum"]), "--address", str(device["devnum"])]


def touch_1200(tty):
    """Open at 1200 baud and close: the RP2 USB stack reboots into BOOTSEL."""
    port = serial_factory()
    try:
        port.port, port.baudrate = tty, 1200
        port.open()
        port.dtr = False
    finally:
        port.close()


def picotool(args, timeout, stream):
    """Run picotool with a timeout; echo its output without progress bars; return (rc, text)."""
    try:
        proc = subprocess.run(["picotool", *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout, check=False)
        rc, raw = proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as exc:
        rc, raw = 124, (exc.stdout or b"") + f"\npicotool timed out after {timeout:g}s".encode()
    except OSError as exc:
        rc, raw = 127, str(exc).encode()
    lines = [line for line in raw.decode("utf-8", errors="replace").replace("\r", "\n").split("\n")
             if line.strip() and not PROGRESS.fullmatch(line.strip())]
    for line in lines:
        print(line, file=stream, flush=True)
    return rc, "\n".join(lines)


def app_at(path):
    device = usbdev.resolve(path)
    return device if device and device["kind"] != "rp2-bootsel" and device["tty"] else None


def check_usage(argv):
    if len(argv) not in (2, 3) or argv[0] not in ACTIONS:
        raise Failed(USAGE, USAGE_TEXT)
    action, slot = argv[0], argv[1]
    file = argv[2] if len(argv) == 3 else None
    if (action == "info") != (file is None):
        raise Failed(USAGE, "info takes no file; save and load need one")
    try:
        path = config.load_slots()[config.validate_slot(slot)]["path"]
    except KeyError:
        raise Failed(USAGE, f"{slot} is not a configured slot") from None
    except (ValueError, OSError) as exc:
        raise Failed(USAGE, str(exc)) from None
    image = None
    if action == "load":
        try:
            with open(file, "rb") as stream:
                data = stream.read()
        except OSError as exc:
            raise Failed(USAGE, f"cannot read firmware {file}: {exc.strerror}") from None
        if not data:
            raise Failed(USAGE, f"firmware is empty: {file}")
        if not data.startswith(UF2_MAGIC):
            raise Failed(USAGE, f"not a UF2 file: {file}")
        image = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return action, slot, path, file, image


class Operation:
    def __init__(self, action, slot, path, file, client, limits):
        self.action, self.slot, self.path, self.file = action, slot, path, file
        self.client, self.limits = client, limits
        self.record = {"kind": None, "usb_serial": None}
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
        if device["kind"] not in RP2_KINDS:
            raise Failed(WRONG_KIND, f"{self.slot} holds {device['vid']}:{device['pid']} "
                                     f"({device['kind']}), not an RP2 board")
        # A board found in BOOTSEL (blank, or parked there on purpose) is left in BOOTSEL by info/save:
        # there may be no application to return to. Only a load must bring an application up.
        self.started_bootsel = device["kind"] == "rp2-bootsel"
        leave_in_bootsel = self.started_bootsel and self.action != "load"
        with ExitStack() as pause:
            owner = self._pause(pause)
            seq = self._seq() if owner else None
            rc = self._flash(device)
            if leave_in_bootsel:
                log(f"{self.slot} was in BOOTSEL before {self.action}; left in BOOTSEL (no application wait)")
            elif not app_at(self.path):
                log(f"waiting up to {self.limits['BENCH_RP2_APP_WAIT']:g}s for the application tty")
                if not poll(lambda: app_at(self.path), self.limits["BENCH_RP2_APP_WAIT"]):
                    message = f"{self.slot} application tty did not come back"
                    if rc == OK:
                        raise Failed(NO_APP, message)
                    self.note(message)
        # The pause connection is closed here; bench-serial may reopen the tty.
        if leave_in_bootsel:
            print("first-line: (board in BOOTSEL)", flush=True)
        else:
            self._first_line(owner, seq)
        return rc

    def _pause(self, stack):
        try:
            stack.enter_context(serialclient.paused(self.slot, f"bench-flash-rp2 {self.client}"))
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

    def _bootsel(self, device):
        if device["kind"] == "rp2-bootsel":
            log(f"{self.slot} is already in BOOTSEL")
            return device
        wait = self.limits["BENCH_RP2_BOOTSEL_WAIT"]
        if device["tty"]:
            log(f"{self.slot}: 1200-baud touch on {device['tty']}")
            try:
                touch_1200(device["tty"])
            except (OSError, ValueError, serial.SerialException) as exc:
                log(f"1200-baud touch failed: {exc}")
            boot = poll(lambda: usbdev.find_bootsel_at(self.path), wait)
            if boot:
                return boot
        if device["busnum"] is None or device["devnum"] is None:
            raise Failed(NO_BOOTSEL, f"{self.slot} never reached BOOTSEL (no bus/address to reboot)")
        self.note("no BOOTSEL after the 1200-baud touch; fallback: picotool reboot -f -u")
        picotool(["reboot", "-f", "-u", *location(device)],
                 self.limits["BENCH_RP2_REBOOT_TIMEOUT"], sys.stderr)
        boot = poll(lambda: usbdev.find_bootsel_at(self.path), wait)
        if not boot:
            raise Failed(NO_BOOTSEL, f"{self.slot} never reached BOOTSEL at {self.path}")
        return boot

    def _flash(self, device):
        boot = self._bootsel(device)
        args = ACTIONS[self.action] + ([self.file] if self.file else []) + location(boot)
        rc, output = picotool(args, self.limits["BENCH_RP2_PICOTOOL_TIMEOUT"],
                              sys.stdout if self.action == "info" else sys.stderr)
        if self.action == "save" and rc != 0 and "Cannot determine the flash size" in output:
            # A blank board has no binary info, so picotool cannot size the flash. Save the chip's
            # standard flash range instead (override with BENCH_RP2_FLASH_BYTES) and say so.
            size = self.limits["BENCH_RP2_FLASH_BYTES"] or (4 << 20 if boot["pid"] == "000f" else 2 << 20)
            self.note(f"flash size unknown (no binary info): saving the standard {size >> 20} MiB range")
            args = ["save", "-r", "0x10000000", hex(0x10000000 + size), self.file] + location(boot)
            rc, output = picotool(args, self.limits["BENCH_RP2_PICOTOOL_TIMEOUT"], sys.stderr)
        result = OK
        if rc == 0:
            pass
        elif self.action == "load" and rc == 254 and re.search(r"\bOK\b", output):
            self.note("WARNING: picotool load exited 254 after OK (filesystem-only UF2); treated as success")
        else:
            self.note(f"picotool {self.action} failed (rc={rc})")
            result = PICOTOOL_FAILED
        # load -x starts the application itself; info and save (and failures) leave BOOTSEL.
        if getattr(self, "started_bootsel", False) and self.action != "load":
            return result
        if (self.action != "load" or result != OK) and usbdev.find_bootsel_at(self.path):
            picotool(["reboot", *location(boot)], self.limits["BENCH_RP2_REBOOT_TIMEOUT"], sys.stderr)
        return result

    def _first_line(self, owner, seq):
        text = None
        if owner:
            def is_open():
                try:
                    return serialclient.state(self.slot)["open"]
                except (OSError, ValueError, serialclient.ProtocolError):
                    return False
            if poll(is_open, self.limits["BENCH_RP2_OPEN_WAIT"]):
                try:
                    line = serialclient.wait(self.slot, "", self.limits["BENCH_RP2_LINE_WAIT"], seq)
                except (OSError, ValueError, serialclient.ProtocolError):
                    line = None
                text = line["text"] if line else None
            else:
                self.note(f"bench-serial did not reopen {self.slot} within "
                          f"{self.limits['BENCH_RP2_OPEN_WAIT']:g}s")
        print(f"first-line: {text if text is not None else '(no output yet)'}", flush=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        action, slot, path, file, image = check_usage(argv)
        limits = waits()
    except Failed as exc:
        log(str(exc))
        return exc.code
    client = os.environ.get("BENCH_CLIENT") or getpass.getuser()
    operation = Operation(action, slot, path, file, client, limits)
    started, rc = time.monotonic(), 1
    try:
        rc = operation.run()
    except Failed as exc:
        rc = exc.code
        operation.note(str(exc))
    finally:
        history.append(client=client, verb="rp2-" + action, slot=slot, rc=rc,
                       seconds=round(time.monotonic() - started, 3),
                       note="; ".join(operation.notes) or None,
                       **operation.record, **(image or {}))
    log(f"{action} {slot}: rc={rc}")
    return rc
