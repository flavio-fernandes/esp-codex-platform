"""bench-agent-shell CLIENT: the forced command for the restricted `wbagent` SSH user.

authorized_keys line (one per client; the word after the path names the client):
  restrict,command="/usr/local/bin/bench-agent-shell claude" ssh-ed25519 AAAA... comment

There is no shell. SSH_ORIGINAL_COMMAND (at most 400 characters of [A-Za-z0-9 _.:=,/#@+-]) is split
on whitespace; the first word is the verb. Every verb calls a bench tool or benchlib client.
Firmware comes in on stdin; data (photos, backups, JSON) goes out on stdout; every message goes to
stderr. Each call writes one audit line: journalctl -t bench-agent
"""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from . import config, history, holds, locks, serialclient, usbdev

OK, FAILED, REFUSED, BUSY, ABSENT, HELD, WRONG_KIND = 0, 1, 2, 65, 66, 69, 70
MAX_INPUT = 400
ALPHABET = re.compile(r"[A-Za-z0-9 _.:=,/#@+-]*")
SLOT = re.compile(r"SLOT[0-9]+")
CLIENT_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")
OFFSET = re.compile(r"0x[0-9a-fA-F]+")
UF2_MAGIC = b"UF2\n"
DEFAULT_MAX_FW_BYTES = 16 * 1024 * 1024
WIFI_MAX = 512
BENCH_USB = "/usr/local/sbin/bench-usb"
TOOL_PROBE_TIMEOUT = 5

HELP = """\
bench-agent verbs (ssh bench-agent <verb> ...):
  help                                   this text
  status                                 slots (kind present open paused hold), camera, tool versions
  health [--json]                        bench-health verdict; its exit code is passed through
  history [N]                            last N (1-200, default 20) flash-history records, JSON lines
  serial-tail SLOTn [lines]              last lines (0-1000, default 50) of the slot's serial buffer
  serial-wait SLOTn SECONDS PATTERN      wait <=300 s for PATTERN (may contain spaces) in new lines;
                                         JSON result; exit 0 on a match, 1 otherwise
  serial-follow SLOTn SECONDS            stream new serial lines for <=600 s
  serial-write SLOTn TEXT                send TEXT + CRLF
  reset SLOTn                            EN reset (esp-uart only)
  snap [exposure=auto|N] [res=WxH]       one JPEG on stdout
  snap-stats [..] [threshold=N]          JSON colour of lit pixels (deterministic)
  seq COUNT INTERVAL [..]                tar of COUNT (1-60) JPEGs, INTERVAL (0-600) s apart, on stdout
  burst COUNT [..]                       tar of COUNT (1-300) consecutive frames on stdout
  pico-info SLOTn                        picotool info (the board reboots afterwards)
  pico-save SLOTn                        whole-flash UF2 backup on stdout (holds secrets!)
  flash-pico SLOTn < fw.uf2              write, verify, run (rp2 or rp2-bootsel)
  flash-esp SLOTn flash-id|chip-id       ESP identity on stdout
  flash-esp SLOTn write-flash 0xOFF < image.bin
  esp-save SLOTn [SIZE]                  flash backup on stdout (SIZE: ALL, decimal or 0x-hex)
  wifi-provision SLOTn < creds           give the board Wi-Fi over serial (Improv): stdin = SSID line,
                                         password line; never logged or stored
  usb-reset SLOTn                        re-enumerate the slot's USB port
  power-cycle                            power-cycle the bench hub (only if enabled)
  hold SLOTn MINUTES REASON...           reserve a slot (1-720 min): other clients' mutations get 69
  release SLOTn                          drop your hold
Exit codes: 0 ok, 1 failed, 2 refused/usage, 3 camera timeout, 4 truncated JPEG, 65 busy,
66 absent, 67 never reached BOOTSEL, 68 tool failed, 69 held by another client, 70 wrong kind,
71 application did not come back."""


class Refuse(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class Interrupted(BaseException):
    def __init__(self, code):
        self.code = code
        super().__init__(f"interrupted (rc={code})")


def say(message):
    print(f"bench-agent: {message}", file=sys.stderr, flush=True)


def audit_text(text, limit=MAX_INPUT + 20):
    """Printable ASCII only, so a refused input can never forge or split a log line."""
    clean = "".join(c if " " <= c <= "~" else "?" for c in text)
    return clean if len(clean) <= limit else clean[:limit] + "..."


def audit(line):
    try:
        subprocess.run(["logger", "-t", "bench-agent", "--", line], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"audit log unavailable: {exc}")


class Shell:
    def __init__(self, client, raw, origin):
        self.client, self.raw, self.origin = client, raw, origin
        self.extra = []  # audit fields beyond rc: payload, stats
        self.work = None
        self.env = {**os.environ, "BENCH_CLIENT": client}

    # ---- argument helpers -------------------------------------------------------------------
    @staticmethod
    def number(value, low, high, name):
        if value is None or not value.isdigit() or not low <= int(value) <= high:
            raise Refuse(REFUSED, f"{name} must be a number from {low} to {high}: {value or '<none>'}")
        return int(value)

    @staticmethod
    def arity(words, low, high, usage):
        if not low <= len(words) - 1 <= high:
            raise Refuse(REFUSED, f"usage: {usage}")

    @staticmethod
    def slot(value):
        if value is None or not SLOT.fullmatch(value):
            raise Refuse(REFUSED, f"bad slot: {value or '<none>'}")
        try:
            slots = config.load_slots()
        except (OSError, ValueError) as exc:
            raise Refuse(FAILED, f"slots.conf: {exc}") from None
        if value not in slots:
            raise Refuse(REFUSED, f"{value} is not a configured slot")
        return value, slots[value]["path"]

    @staticmethod
    def need_kind(slot, path, kinds):
        device = usbdev.resolve(path)
        if device is None:
            raise Refuse(ABSENT, f"{slot}: no device at {path}")
        if device["kind"] not in kinds:
            raise Refuse(WRONG_KIND, f"{slot} holds a {device['kind']} board; this verb needs {'/'.join(kinds)}")
        return device

    def settings(self):
        try:
            return config.load_env()
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise Refuse(FAILED, f"bench.env: {exc}") from None

    def workdir(self):
        if self.work is None:
            self.work = tempfile.mkdtemp(prefix="bench-agent.")
        return self.work

    def read_payload(self, name):
        limit = self.settings().get("BENCH_MAX_FW_BYTES", str(DEFAULT_MAX_FW_BYTES))
        if not limit.isdigit():
            raise Refuse(FAILED, f"bad BENCH_MAX_FW_BYTES in bench.env: {limit}")
        limit = int(limit)
        data = sys.stdin.buffer.read(limit + 1)
        if not data:
            raise Refuse(REFUSED, "no firmware on stdin")
        if len(data) > limit:
            raise Refuse(REFUSED, f"firmware larger than {limit} bytes")
        path = os.path.join(self.workdir(), name)
        with open(path, "wb") as stream:
            stream.write(data)
        self.extra.append(f"payload sha256={hashlib.sha256(data).hexdigest()} size={len(data)}")
        return path, data

    # ---- running tools ----------------------------------------------------------------------
    def tool(self, argv, data_out=True, capture=False):
        """Run a bench tool with BENCH_CLIENT set. stdout is ours only for data-producing calls."""
        sys.stdout.flush()
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, env=self.env, check=False,
                                  stdout=subprocess.PIPE if capture else (1 if data_out else 2))
        except OSError as exc:
            raise Refuse(FAILED, f"cannot run {argv[0]}: {exc.strerror}") from None
        return (proc.returncode, proc.stdout) if capture else proc.returncode

    @staticmethod
    def emit_file(path):
        sys.stdout.flush()
        with open(path, "rb") as stream:
            shutil.copyfileobj(stream, sys.stdout.buffer)
        sys.stdout.buffer.flush()

    def emit_tar(self, directory):
        sys.stdout.flush()
        return self.tool(["tar", "-C", directory, "-cf", "-", "."])

    @staticmethod
    def serial(call, *args):
        try:
            return call(*args)
        except serialclient.NotRunning as exc:
            raise Refuse(FAILED, str(exc)) from None
        except serialclient.ProtocolError as exc:
            raise Refuse(FAILED, f"bench-serial: {exc}") from None
        except (OSError, ValueError) as exc:
            raise Refuse(FAILED, f"bench-serial: {exc}") from None

    # ---- verbs ------------------------------------------------------------------------------
    def v_help(self, words):
        print(HELP)
        return OK

    def v_status(self, words):
        self.arity(words, 0, 0, "status")
        try:
            slots = config.load_slots()
        except (OSError, ValueError) as exc:
            raise Refuse(FAILED, f"slots.conf: {exc}") from None
        for label, slot in slots.items():
            device = usbdev.resolve(slot["path"])
            fields = [f"{label:<7}", f"path={slot['path']}", f"kind={device['kind'] if device else '-'}",
                      f"present={'yes' if device else 'no'}"]
            try:
                state = serialclient.state(label)
                fields += [f"open={'yes' if state.get('open') else 'no'}",
                           f"paused={'by ' + str(state.get('paused_by')) if state.get('paused') else 'no'}"]
            except serialclient.NotRunning:
                fields.append("serial=not-running")
            except (serialclient.ProtocolError, OSError, ValueError) as exc:
                fields.append(f"serial=error({audit_text(str(exc), 60)})")
            hold = holds.get(label)
            if hold and hold.get("unreadable"):
                fields.append(f"hold=UNREADABLE reason=[{hold['reason']}]")
            elif hold:
                until = datetime.fromtimestamp(hold["until"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                fields.append(f"hold={hold['client']} until={until} reason=[{hold['reason']}]")
            else:
                fields.append("hold=-")
            print(" ".join(fields))
        device = self.settings().get("BENCH_CAMERA_DEVICE", "/dev/video0")
        try:
            with locks.camera_lock():
                busy = "no"
        except locks.Busy:
            busy = "yes"
        except OSError:
            busy = "unknown"
        print(f"camera  {'present' if os.path.exists(device) else 'MISSING'} ({device}) busy={busy}")
        esptool = os.environ.get("BENCH_ESPTOOL") or "/opt/bench/venv/bin/esptool"
        # uhubctl lives in /usr/sbin, outside this user's PATH; bench-usb runs it as root via sudo.
        uhubctl = shutil.which("uhubctl", path=os.pathsep.join(
            filter(None, [self.env.get("PATH"), "/usr/sbin", "/sbin"]))) or "uhubctl"
        for name, argv in (("picotool", ["picotool", "version"]), ("esptool", [esptool, "version"]),
                           ("v4l2-ctl", ["v4l2-ctl", "--version"]), ("uhubctl", [uhubctl, "-v"])):
            print(f"tool    {name}: {self.version(argv)}")
        return OK

    @staticmethod
    def version(argv):
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=TOOL_PROBE_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError):
            return "missing"
        lines = (proc.stdout or proc.stderr).strip().splitlines()
        return lines[-1].strip() if lines else f"rc={proc.returncode}"

    def v_health(self, words):
        self.arity(words, 0, 1, "health [--json]")
        if words[1:] not in ([], ["--json"]):
            raise Refuse(REFUSED, "usage: health [--json]")
        return self.tool(["bench-health", *words[1:]])

    def v_history(self, words):
        self.arity(words, 0, 1, "history [N]")
        count = self.number(words[1], 1, 200, "N") if len(words) > 1 else 20
        try:
            records = history.tail(count)
        except (OSError, ValueError) as exc:
            raise Refuse(FAILED, f"history: {exc}") from None
        for record in records:
            print(json.dumps(record))
        return OK

    def v_serial_tail(self, words):
        self.arity(words, 1, 2, "serial-tail SLOTn [lines]")
        slot, _ = self.slot(words[1])
        lines = self.number(words[2], 0, 1000, "lines") if len(words) > 2 else 50
        for line in self.serial(serialclient.tail, slot, lines):
            print(line.get("text", ""))
        return OK

    def v_serial_wait(self, words):
        if len(words) < 4:
            raise Refuse(REFUSED, "usage: serial-wait SLOTn SECONDS PATTERN")
        slot, _ = self.slot(words[1])
        seconds = self.number(words[2], 0, 300, "SECONDS")
        line = self.serial(serialclient.wait, slot, " ".join(words[3:]), seconds)
        print(json.dumps({"matched": True, "line": line} if line else {"matched": False}), flush=True)
        return OK if line else FAILED

    def v_serial_follow(self, words):
        self.arity(words, 2, 2, "serial-follow SLOTn SECONDS")
        slot, _ = self.slot(words[1])
        seconds = self.number(words[2], 1, 600, "SECONDS")

        def follow():
            for line in serialclient.follow(slot, seconds):
                print(line.get("text", ""), flush=True)
        self.serial(follow)
        return OK

    def v_serial_write(self, words):
        if len(words) < 2:
            raise Refuse(REFUSED, "usage: serial-write SLOTn TEXT")
        slot, _ = self.slot(words[1])
        # The slot lock spans the hold check and the write, so no hold can land in between
        # (holds.put takes the same lock).
        try:
            with locks.slot_lock(slot):
                self.check_hold(slot)
                sent = self.serial(serialclient.write, slot, " ".join(words[2:]))
        except locks.Busy:
            raise Refuse(BUSY, f"{slot} is busy (another operation holds its lock)") from None
        say(f"{slot}: wrote {sent} bytes")
        return OK

    def check_hold(self, slot):
        try:
            holds.check(slot, self.client)
        except holds.Held as exc:
            raise Refuse(HELD, f"{slot} {exc}") from None
        except (OSError, ValueError) as exc:
            raise Refuse(FAILED, f"{slot}: hold check failed: {exc}") from None

    def v_reset(self, words):
        self.arity(words, 1, 1, "reset SLOTn")
        slot, path = self.slot(words[1])
        device = self.need_kind(slot, path, ("esp-uart",))
        started, rc, note = time.monotonic(), OK, None
        try:
            with locks.slot_lock(slot):
                try:
                    self.check_hold(slot)
                    self.serial(serialclient.reset, slot)
                    say(f"{slot}: reset")
                except Refuse as exc:
                    rc, note = exc.code, str(exc)
                    raise
                finally:
                    history.append(client=self.client, verb="reset", slot=slot, kind=device["kind"],
                                   usb_serial=device["serial"], rc=rc, note=note,
                                   seconds=round(time.monotonic() - started, 3))
        except locks.Busy:
            raise Refuse(BUSY, f"{slot} is busy (another operation holds its lock)") from None
        return OK

    def camera_snap(self, out, opts):
        return self.tool(["bench-camera", "snap", out, *opts], data_out=False)

    def v_snap(self, words):
        out = os.path.join(self.workdir(), "snap.jpg")
        rc = self.camera_snap(out, words[1:])
        if rc == OK:
            self.emit_file(out)
        return rc

    def v_snap_stats(self, words):
        stat_opts = [w for w in words[1:] if w.startswith("threshold=")]
        snap_opts = [w for w in words[1:] if not w.startswith("threshold=")]
        out = os.path.join(self.workdir(), "snap.jpg")
        rc = self.camera_snap(out, snap_opts)
        if rc != OK:
            return rc
        rc, data = self.tool(["bench-camera", "stats", out, *stat_opts], capture=True)
        stats = data.decode("utf-8", errors="replace").strip()
        if rc == OK:
            self.extra.append(f"stats={audit_text(stats, 300)}")
            print(stats, flush=True)
        return rc

    def v_seq(self, words):
        if len(words) < 3:
            raise Refuse(REFUSED, "usage: seq COUNT INTERVAL [opts]")
        count = self.number(words[1], 1, 60, "COUNT")
        interval = self.number(words[2], 0, 600, "INTERVAL")
        directory = os.path.join(self.workdir(), "seq")
        os.mkdir(directory)
        for index in range(1, count + 1):
            rc = self.camera_snap(os.path.join(directory, f"frame-{index:02d}.jpg"), words[3:])
            if rc != OK:
                return rc
            if index < count:
                time.sleep(interval)
        return self.emit_tar(directory)

    def v_burst(self, words):
        if len(words) < 2:
            raise Refuse(REFUSED, "usage: burst COUNT [opts]")
        count = self.number(words[1], 1, 300, "COUNT")
        directory = os.path.join(self.workdir(), "burst")
        rc = self.tool(["bench-camera", "burst", directory, str(count), *words[2:]], data_out=False)
        return self.emit_tar(directory) if rc == OK else rc

    def v_pico_info(self, words):
        self.arity(words, 1, 1, "pico-info SLOTn")
        slot, _ = self.slot(words[1])
        return self.tool(["bench-flash-rp2", "info", slot])

    def v_pico_save(self, words):
        self.arity(words, 1, 1, "pico-save SLOTn")
        slot, _ = self.slot(words[1])
        out = os.path.join(self.workdir(), "backup.uf2")
        rc = self.tool(["bench-flash-rp2", "save", slot, out], data_out=False)
        if rc == OK:
            self.emit_file(out)
        return rc

    def v_flash_pico(self, words):
        self.arity(words, 1, 1, "flash-pico SLOTn < fw.uf2")
        slot, path = self.slot(words[1])
        self.need_kind(slot, path, ("rp2", "rp2-bootsel"))
        image, data = self.read_payload("fw.uf2")
        if not data.startswith(UF2_MAGIC):
            raise Refuse(REFUSED, "stdin is not a UF2 file")
        return self.tool(["bench-flash-rp2", "load", slot, image], data_out=False)

    def v_flash_esp(self, words):
        usage = "flash-esp SLOTn flash-id|chip-id | flash-esp SLOTn write-flash 0xOFF < image.bin"
        self.arity(words, 2, 3, usage)
        slot, path = self.slot(words[1])
        action = words[2]
        if action in ("flash-id", "chip-id") and len(words) == 3:
            self.need_kind(slot, path, ("esp-uart", "esp-usb"))
            return self.tool(["bench-flash-esp", slot, action])
        if action == "write-flash" and len(words) == 4 and OFFSET.fullmatch(words[3]):
            self.need_kind(slot, path, ("esp-uart", "esp-usb"))
            image, _ = self.read_payload("image.bin")
            return self.tool(["bench-flash-esp", slot, "write-flash", words[3], image], data_out=False)
        raise Refuse(REFUSED, f"usage: {usage}")

    def v_esp_save(self, words):
        self.arity(words, 1, 2, "esp-save SLOTn [SIZE]")
        slot, path = self.slot(words[1])
        self.need_kind(slot, path, ("esp-uart", "esp-usb"))
        out = os.path.join(self.workdir(), "esp-save.bin")
        rc = self.tool(["bench-flash-esp", slot, "read-flash", out, *words[2:]], data_out=False)
        if rc == OK:
            self.emit_file(out)
        return rc

    def v_wifi_provision(self, words):
        """Credentials pass from our stdin straight to the tool's stdin: never a file, an argv
        or a hash; the audit line records only their size."""
        self.arity(words, 1, 1, "wifi-provision SLOTn < creds")
        slot, path = self.slot(words[1])
        self.need_kind(slot, path, ("esp-uart", "esp-usb", "rp2"))
        data = sys.stdin.buffer.read(WIFI_MAX + 1)
        if not data or len(data) > WIFI_MAX:
            raise Refuse(REFUSED, f"stdin must be two lines, SSID then password (at most {WIFI_MAX} bytes)")
        self.extra.append(f"credentials size={len(data)}")
        sys.stdout.flush()
        try:
            proc = subprocess.run(["bench-wifi-provision", slot], input=data, env=self.env, check=False,
                                  stdout=1)
        except OSError as exc:
            raise Refuse(FAILED, f"cannot run bench-wifi-provision: {exc.strerror}") from None
        return proc.returncode

    def v_usb_reset(self, words):
        self.arity(words, 1, 1, "usb-reset SLOTn")
        slot, _ = self.slot(words[1])
        return self.tool(["sudo", "-n", BENCH_USB, "usb-reset", slot])

    def v_power_cycle(self, words):
        self.arity(words, 0, 0, "power-cycle")
        return self.tool(["sudo", "-n", BENCH_USB, "power-cycle"])

    def v_hold(self, words):
        if len(words) < 4:
            raise Refuse(REFUSED, "usage: hold SLOTn MINUTES REASON...")
        slot, _ = self.slot(words[1])
        minutes = self.number(words[2], 1, 720, "MINUTES")
        try:
            hold = holds.put(slot, self.client, minutes, " ".join(words[3:]))
        except holds.Held as exc:
            raise Refuse(HELD, f"{slot} {exc}") from None
        except locks.Busy:
            raise Refuse(BUSY, f"{slot} is busy (another operation holds its lock)") from None
        print(json.dumps({"slot": slot, **hold}))
        return OK

    def v_release(self, words):
        self.arity(words, 1, 1, "release SLOTn")
        slot, _ = self.slot(words[1])
        try:
            removed = holds.release(slot, self.client)
        except holds.Held as exc:
            raise Refuse(HELD, f"{slot} {exc}") from None
        except locks.Busy:
            raise Refuse(BUSY, f"{slot} is busy (another operation holds its lock)") from None
        say(f"{slot}: {'released' if removed else 'no hold to release'}")
        return OK

    VERBS = {"help": v_help, "-h": v_help, "--help": v_help, "status": v_status, "health": v_health,
             "history": v_history, "serial-tail": v_serial_tail, "serial-wait": v_serial_wait,
             "serial-follow": v_serial_follow, "serial-write": v_serial_write, "reset": v_reset,
             "snap": v_snap, "snap-stats": v_snap_stats, "seq": v_seq, "burst": v_burst,
             "pico-info": v_pico_info, "pico-save": v_pico_save, "flash-pico": v_flash_pico,
             "flash-esp": v_flash_esp, "esp-save": v_esp_save, "wifi-provision": v_wifi_provision,
             "usb-reset": v_usb_reset,
             "power-cycle": v_power_cycle, "hold": v_hold, "release": v_release}

    def dispatch(self):
        if len(self.raw) > MAX_INPUT:
            raise Refuse(REFUSED, f"command too long (max {MAX_INPUT} characters)")
        if not ALPHABET.fullmatch(self.raw):
            raise Refuse(REFUSED, "refused: characters outside [A-Za-z0-9 _.:=,/#@+-]")
        words = self.raw.split() or ["help"]
        verb = self.VERBS.get(words[0])
        if verb is None:
            print(HELP, file=sys.stderr)
            raise Refuse(REFUSED, f"unknown verb: {words[0]}")
        return verb(self, words)


def _interrupt(signum, frame):
    raise Interrupted(128 + signum)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    os.umask(0o077)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _interrupt)
    client = argv[0] if len(argv) == 1 else "unknown"
    origin = (os.environ.get("SSH_CONNECTION") or "local").split()[0]
    raw = os.environ.get("SSH_ORIGINAL_COMMAND", "help")
    shell = Shell(client, raw, origin)
    err = None
    try:
        if len(argv) != 1 or not CLIENT_RE.fullmatch(client):
            raise Refuse(REFUSED, "misconfigured forced command: expected bench-agent-shell CLIENT")
        rc = shell.dispatch()
    except Refuse as exc:
        rc, err = exc.code, str(exc)
        say(err)
    except BrokenPipeError:
        rc, err = 141, "client closed the connection"
    except Interrupted as exc:
        rc, err = exc.code, str(exc)
    except Exception as exc:  # noqa: BLE001 - every call must be audited
        rc, err = FAILED, f"internal error: {type(exc).__name__}: {exc}"
        say(err)
    finally:
        if shell.work:
            shutil.rmtree(shell.work, ignore_errors=True)
    logged = client if CLIENT_RE.fullmatch(client) else re.sub(r"[^A-Za-z0-9_.@-]", "?", client)[:64]
    line = f"client={logged} from={audit_text(origin, 64)} cmd=[{audit_text(raw)}] rc={rc}"
    line += "".join(" " + extra for extra in shell.extra)
    if err is not None:
        line += f" err=[{audit_text(err, 200)}]"
    audit(line)
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    return rc
