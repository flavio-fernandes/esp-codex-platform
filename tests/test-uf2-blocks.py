#!/usr/bin/env python3
"""Offline tests for the generic UF2 block validator."""

from __future__ import annotations

import importlib.util
import struct
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UF2_PATH = ROOT / "tools" / "lib" / "uf2-blocks.py"

spec = importlib.util.spec_from_file_location("uf2_blocks", UF2_PATH)
assert spec and spec.loader
uf2_blocks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uf2_blocks)


def block(address: int, payload: bytes, block_no: int, count: int) -> bytes:
    data = bytearray(512)
    data[:32] = struct.pack(
        "<IIIIIIII",
        uf2_blocks.MAGIC0,
        uf2_blocks.MAGIC1,
        0,
        address,
        len(payload),
        block_no,
        count,
        0,
    )
    data[32 : 32 + len(payload)] = payload
    data[508:512] = struct.pack("<I", uf2_blocks.END_MAGIC)
    return bytes(data)


def write(path: Path, blocks: list[bytes]) -> None:
    path.write_bytes(b"".join(blocks))


def expect_exit(label: str, func, text: str) -> None:
    try:
        func()
    except SystemExit as exc:
        if text not in str(exc):
            got = str(exc)
            raise AssertionError(f"{label}: expected {text!r}, got {got!r}") from exc
        return
    raise AssertionError(f"{label}: expected SystemExit")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        requested = root / "requested.uf2"
        current = root / "current.uf2"

        write(requested, [block(0x10000, b"abc", 0, 2), block(0x10100, b"tail", 1, 2)])
        write(
            current,
            [
                block(0x10000, b"abc" + b"\xff" * 8, 0, 2),
                block(0x10100, b"tail" + b"\xff" * 8, 1, 2),
            ],
        )
        assert uf2_blocks.cmd_preflight(
            type("Args", (), {"uf2": str(requested), "minimum": "0x10000"})()
        ) == 0
        assert uf2_blocks.cmd_verify(
            type("Args", (), {"requested": str(requested), "current": str(current)})()
        ) == 0

        expect_exit(
            "full-mode/minimum guard",
            lambda: uf2_blocks.cmd_preflight(
                type("Args", (), {"uf2": str(requested), "minimum": "0x10001"})()
            ),
            "below app-only minimum",
        )

        duplicate_address = root / "duplicate-address.uf2"
        write(
            duplicate_address,
            [block(0x10000, b"one", 0, 2), block(0x10000, b"two", 1, 2)],
        )
        expect_exit(
            "duplicate target address",
            lambda: uf2_blocks.read_uf2(duplicate_address),
            "duplicate target address",
        )

        missing_current = root / "missing-current.uf2"
        write(missing_current, [block(0x10000, b"abc", 0, 1)])
        assert (
            uf2_blocks.cmd_verify(
                type(
                    "Args",
                    (),
                    {"requested": str(requested), "current": str(missing_current)},
                )()
            )
            == 1
        )

        mismatched_current = root / "mismatched-current.uf2"
        write(
            mismatched_current,
            [
                block(0x10000, b"abc", 0, 2),
                block(0x10100, b"nope", 1, 2),
            ],
        )
        assert (
            uf2_blocks.cmd_verify(
                type(
                    "Args",
                    (),
                    {"requested": str(requested), "current": str(mismatched_current)},
                )()
            )
            == 1
        )

        malformed_count = root / "malformed-count.uf2"
        write(malformed_count, [block(0x10000, b"abc", 1, 1)])
        expect_exit(
            "malformed block count",
            lambda: uf2_blocks.read_uf2(malformed_count),
            "outside count",
        )

        truncated = root / "truncated.uf2"
        truncated.write_bytes(block(0x10000, b"abc", 0, 1)[:-1])
        expect_exit(
            "truncated block",
            lambda: uf2_blocks.read_uf2(truncated),
            "multiple of 512",
        )

    print("test-uf2-blocks.py PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
