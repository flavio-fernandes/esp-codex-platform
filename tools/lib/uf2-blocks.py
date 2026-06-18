#!/usr/bin/env python3
"""Validate UF2 files and compare requested blocks with CURRENT.UF2."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

MAGIC0 = 0x0A324655
MAGIC1 = 0x9E5D5157
END_MAGIC = 0x0AB16F30
BLOCK_SIZE = 512
HEADER_SIZE = 32
MAX_PAYLOAD = 476


def read_uf2(path: Path) -> dict[int, bytes]:
    raw = path.read_bytes()
    if not raw or len(raw) % BLOCK_SIZE:
        raise SystemExit(f"{path}: UF2 size must be a non-zero multiple of 512 bytes")

    blocks: dict[int, bytes] = {}
    expected_count: int | None = None
    seen_block_numbers: set[int] = set()
    for offset in range(0, len(raw), BLOCK_SIZE):
        block = raw[offset : offset + BLOCK_SIZE]
        magic0, magic1, _flags, address, size, block_no, block_count, _family = (
            struct.unpack("<IIIIIIII", block[:HEADER_SIZE])
        )
        end_magic = struct.unpack("<I", block[508:512])[0]
        if magic0 != MAGIC0 or magic1 != MAGIC1 or end_magic != END_MAGIC:
            raise SystemExit(f"{path}: invalid UF2 magic in block at file offset {offset}")
        if size <= 0 or size > MAX_PAYLOAD:
            raise SystemExit(f"{path}: invalid UF2 payload size {size} at target 0x{address:x}")
        if block_count <= 0:
            raise SystemExit(f"{path}: invalid UF2 block count {block_count}")
        if block_no >= block_count:
            raise SystemExit(
                f"{path}: UF2 block number {block_no} is outside count {block_count}"
            )
        if expected_count is None:
            expected_count = block_count
        elif block_count != expected_count:
            raise SystemExit(f"{path}: inconsistent UF2 block count {block_count}")
        if block_no in seen_block_numbers:
            raise SystemExit(f"{path}: duplicate UF2 block number {block_no}")
        seen_block_numbers.add(block_no)

        payload = block[HEADER_SIZE : HEADER_SIZE + size]
        if address in blocks:
            raise SystemExit(f"{path}: duplicate target address 0x{address:x}")
        blocks[address] = payload

    if expected_count != len(seen_block_numbers):
        raise SystemExit(
            f"{path}: UF2 block count {expected_count} does not match file blocks "
            f"{len(seen_block_numbers)}"
        )
    return blocks


def cmd_preflight(args: argparse.Namespace) -> int:
    minimum = int(args.minimum, 0) if args.minimum else None
    blocks = read_uf2(Path(args.uf2))
    if minimum is not None:
        for address in blocks:
            if address < minimum:
                raise SystemExit(
                    f"{args.uf2}: target 0x{address:x} is below app-only minimum "
                    f"0x{minimum:x}"
                )

    addresses = sorted(blocks)
    print(
        f"UF2 preflight: blocks={len(addresses)} "
        f"first=0x{addresses[0]:x} last=0x{addresses[-1]:x}"
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    requested = read_uf2(Path(args.requested))
    current = read_uf2(Path(args.current))
    missing = []
    mismatch = []
    for address, payload in requested.items():
        got = current.get(address)
        if got is None:
            missing.append(address)
        elif got[: len(payload)] != payload:
            mismatch.append(address)

    print(
        f"UF2 verify: requested_blocks={len(requested)} "
        f"missing={len(missing)} mismatch={len(mismatch)}"
    )
    if missing:
        print(f"first_missing=0x{missing[0]:x}")
    if mismatch:
        print(f"first_mismatch=0x{mismatch[0]:x}")
    return 1 if missing or mismatch else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("uf2")
    preflight.add_argument("minimum", nargs="?")
    preflight.set_defaults(func=cmd_preflight)

    verify = subparsers.add_parser("verify")
    verify.add_argument("requested")
    verify.add_argument("current")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
