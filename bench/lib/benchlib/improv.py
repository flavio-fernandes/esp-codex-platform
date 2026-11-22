"""Improv Wi-Fi over serial (https://www.improv-wifi.com/serial/).

Provision Wi-Fi on a device (e.g. ESPHome with `improv_serial:`) through its serial port, so the
credentials never go into a firmware build. Packet:

    "IMPROV" | version (1) | type (1) | length (1) | data | checksum (sum of all previous bytes & 0xFF)

Types: 0x01 current state, 0x02 error state, 0x03 RPC command, 0x04 RPC result.
RPC data: command (1) | length (1) | payload. Command 0x01 sends Wi-Fi settings
(ssid length, ssid, password length, password); 0x02 requests the current state.
The device's log output is interleaved with packets, so the reader scans for the header.
"""
import time

HEADER = b"IMPROV"
VERSION = 1
TYPE_STATE, TYPE_ERROR, TYPE_RPC, TYPE_RESULT = 0x01, 0x02, 0x03, 0x04
CMD_WIFI, CMD_STATE = 0x01, 0x02
STATE_AUTHORIZE, STATE_READY, STATE_PROVISIONING, STATE_PROVISIONED = 0x01, 0x02, 0x03, 0x04
STATES = {0x01: "awaiting authorization", 0x02: "ready", 0x03: "provisioning", 0x04: "provisioned"}
ERRORS = {0x00: "none", 0x01: "invalid RPC packet", 0x02: "unknown RPC command",
          0x03: "unable to connect", 0xFF: "unknown error"}


class ImprovError(RuntimeError):
    pass


def packet(ptype, data=b""):
    if len(data) > 255:
        raise ValueError("improv packet data too long")
    body = HEADER + bytes([VERSION, ptype, len(data)]) + data
    return body + bytes([sum(body) & 0xFF]) + b"\n"


def rpc(command, payload=b""):
    return packet(TYPE_RPC, bytes([command, len(payload)]) + payload)


def wifi_payload(ssid, password):
    s, p = ssid.encode("utf-8"), password.encode("utf-8")
    if not 1 <= len(s) <= 32 or len(p) > 64:
        raise ValueError("ssid must be 1-32 bytes and the password at most 64 bytes")
    return bytes([len(s)]) + s + bytes([len(p)]) + p


def strings(data):
    """Decode an RPC result's length-prefixed strings."""
    out, i = [], 0
    while i < len(data):
        n = data[i]
        out.append(data[i + 1:i + 1 + n].decode("utf-8", errors="replace"))
        i += 1 + n
    return out


class Reader:
    """Scan a byte stream for improv packets; everything else (log text) is ignored."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        packets = []
        while True:
            start = self.buf.find(HEADER)
            if start < 0:
                del self.buf[:max(0, len(self.buf) - len(HEADER) + 1)]
                return packets
            del self.buf[:start]
            if len(self.buf) >= 8 and (self.buf[6] != VERSION or not TYPE_STATE <= self.buf[7] <= TYPE_RESULT):
                del self.buf[:1]   # "IMPROV" in log text: don't wait on its bogus length byte
                continue
            if len(self.buf) < 9:
                return packets
            length = self.buf[8]
            end = 9 + length + 1
            if len(self.buf) < end:
                return packets
            frame = bytes(self.buf[:end])
            if (sum(frame[:-1]) & 0xFF) == frame[-1]:
                packets.append((frame[7], frame[9:9 + length]))
                del self.buf[:end]
            else:
                del self.buf[:1]   # not a real packet: keep scanning past this header


def provision(port, ssid, password, timeout=45.0, log=lambda _m: None, clock=time.monotonic):
    """Send Wi-Fi settings over an open serial port; return {"state", "urls"} or raise ImprovError.

    `port` needs read(n) honouring its timeout, write(bytes) and flush(). The device may still be
    booting (opening a USB-UART can reset it), so the state request is repeated for a while.
    """
    reader, deadline = Reader(), clock() + timeout

    def packets_until(stop, budget):
        end = min(deadline, clock() + budget)
        while clock() < end:
            for ptype, data in reader.feed(port.read(256) or b""):
                done = stop(ptype, data)
                if done is not None:
                    return done
        return None

    state = None
    for _ in range(8):  # ~16 s for the device to boot and answer
        port.write(rpc(CMD_STATE)); port.flush()
        state = packets_until(lambda t, d: d[0] if t == TYPE_STATE and d else None, 2.0)
        if state is not None:
            break
    if state is None:
        raise ImprovError("no improv answer: is `improv_serial:` in the firmware, and the logger on this port?")
    log(f"device improv state: {STATES.get(state, hex(state))}")
    if state == STATE_AUTHORIZE:
        raise ImprovError("device awaits authorization (physical button); not supported by the bench")

    port.write(rpc(CMD_WIFI, wifi_payload(ssid, password))); port.flush()
    result = {"state": None, "urls": []}

    def watch(ptype, data):
        if ptype == TYPE_ERROR and data and data[0] != 0:
            raise ImprovError(f"device error: {ERRORS.get(data[0], hex(data[0]))}")
        if ptype == TYPE_STATE and data:
            result["state"] = STATES.get(data[0], hex(data[0]))
            log(f"device improv state: {result['state']}")
        if ptype == TYPE_RESULT and data and data[0] == CMD_WIFI:
            result["urls"] = strings(data[2:2 + data[1]])
            return result
        if ptype == TYPE_STATE and data and data[0] == STATE_PROVISIONED and result["urls"]:
            return result
        return None

    done = packets_until(watch, timeout)
    if done is None:
        if result["state"] == "provisioned":
            return result
        raise ImprovError(f"timed out after {timeout:g}s waiting for the device to join (last state: {result['state']})")
    return done
