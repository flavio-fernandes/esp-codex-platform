#!/usr/bin/env python3
"""Mirror skills/bench to dedicated skill directories, preserving root LOCAL.md.

No third-party dependencies. Check compares contents, types and permission bits,
not timestamps. Refuse links/special files rather than following them outside DEST.
Destinations must not overlap each other or the repository. Not concurrent-writer
safe: stop agents editing a destination while installing. LOCAL.md is never read.
"""
import argparse
import os
from pathlib import Path
import shutil
import stat
import sys

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "skills" / "bench"


def inventory(root):
    result = {}
    if not root.exists():
        return result
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            rel = path.relative_to(root)
            if rel == Path("LOCAL.md"):
                if name in dirs:
                    dirs.remove(name)
                continue
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f"symlink or special file refused: {path}")
            result[rel] = ("dir" if stat.S_ISDIR(mode) else "file", stat.S_IMODE(mode))
    return result


def overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def destinations(values):
    paths = []
    for value in values:
        path = Path(os.path.abspath(Path(value).expanduser()))
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError(f"symlink destination refused: {parent}")
        if overlap(path, REPO) or any(overlap(path, old) for old in paths):
            raise ValueError(f"overlapping destination refused: {path}")
        paths.append(path)
    return paths


def differences(dest, expected, actual):
    changes = []
    if not dest.exists():
        changes.append((Path("."), "missing directory"))
    for rel in sorted(expected.keys() | actual.keys()):
        if rel not in expected:
            changes.append((rel, "extra"))
        elif rel not in actual:
            changes.append((rel, "missing"))
        elif expected[rel] != actual[rel]:
            changes.append((rel, "type or mode differs"))
        elif expected[rel][0] == "file" and (SOURCE / rel).read_bytes() != (dest / rel).read_bytes():
            changes.append((rel, "content differs"))
    return changes


def install(dest, expected, actual):
    # Remove obsolete entries and type conflicts deepest-first; never traverse LOCAL.md.
    for rel in sorted(actual, key=lambda p: len(p.parts), reverse=True):
        if rel not in expected or actual[rel][0] != expected[rel][0]:
            path = dest / rel
            if actual[rel][0] == "dir":
                path.rmdir()
            else:
                path.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    for rel in sorted(expected, key=lambda p: (len(p.parts), str(p))):
        path = dest / rel
        kind, mode = expected[rel]
        if kind == "dir":
            path.mkdir(exist_ok=True)
            path.chmod(mode)
        else:
            # Unlink first: never overwrite through a destination hard link.
            if path.exists():
                path.unlink()
            shutil.copyfile(SOURCE / rel, path)
            path.chmod(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read-only; exit 1 on drift")
    parser.add_argument("dest", metavar="DEST", nargs="+", help="dedicated skill directory (extras removed except LOCAL.md)")
    args = parser.parse_args()
    try:
        if not (SOURCE / "SKILL.md").is_file():
            raise ValueError(f"canonical skill missing: {SOURCE}")
        expected = inventory(SOURCE)
        paths = destinations(args.dest)
        # Validate ALL targets before making any writes.
        targets = [(dest, inventory(dest)) for dest in paths]
        drift = False
        for dest, actual in targets:
            changes = differences(dest, expected, actual)
            if args.check:
                for rel, reason in changes:
                    print(f"DRIFT {dest / rel}: {reason}")
                drift |= bool(changes)
            else:
                install(dest, expected, actual)
                if differences(dest, expected, inventory(dest)):
                    raise ValueError(f"post-copy verification failed: {dest}")
                print(f"SYNCED {dest}")
        if args.check and not drift:
            print("NO DRIFT")
        return int(drift)
    except (OSError, ValueError) as exc:
        print(f"sync-skill: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
