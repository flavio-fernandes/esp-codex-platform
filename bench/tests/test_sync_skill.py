"""Canonical skill sync is exercised only in temporary directories, never real profiles."""
import os
from pathlib import Path
import subprocess

import pytest

from benchlib.agentshell import HELP

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bench/sync-skill.sh"
SOURCE = REPO / "skills/bench"


def run(*args):
    return subprocess.run([str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, p.stat().st_mtime_ns,
            p.read_bytes() if p.is_file() else None) for p in root.rglob("*")}


def test_install_multiple_and_check_read_only(tmp_path):
    a, b = tmp_path / "client one/bench", tmp_path / "client two/bench"
    assert run(a, b).returncode == 0
    for dest in (a, b):
        assert (dest / "SKILL.md").read_bytes() == (SOURCE / "SKILL.md").read_bytes()
        assert sorted(p.relative_to(dest) for p in dest.rglob("*")) == sorted(p.relative_to(SOURCE) for p in SOURCE.rglob("*"))
    before = snapshot(tmp_path)
    result = run("--check", a, b)
    assert result.returncode == 0 and result.stdout == "NO DRIFT\n"
    assert snapshot(tmp_path) == before
    assert run(a, b).returncode == 0
    assert run("--check", a, b).returncode == 0


def test_check_missing_does_not_create(tmp_path):
    dest = tmp_path / "missing/bench"
    result = run("--check", dest)
    assert result.returncode == 1
    assert "missing" in result.stdout and "SKILL.md" in result.stdout
    assert not dest.parent.exists()


@pytest.mark.parametrize("change", ["content", "missing", "extra", "mode", "file_to_dir", "dir_to_file"])
def test_drift_reports_then_repairs(tmp_path, change):
    dest = tmp_path / "bench"
    assert run(dest).returncode == 0
    skill = dest / "SKILL.md"
    if change == "content":
        skill.write_text("drift\n")
    elif change == "missing":
        skill.unlink()
    elif change == "extra":
        (dest / "obsolete/subdir").mkdir(parents=True)
        (dest / "obsolete/subdir/stale").write_text("old")
    elif change == "mode":
        skill.chmod(0o600)
    elif change == "file_to_dir":
        skill.unlink()
        skill.mkdir()
        (skill / "extra").write_text("old")
    else:
        ref = dest / "references"
        for p in ref.iterdir():
            p.unlink()
        ref.rmdir()
        ref.write_text("wrong type")
    before = snapshot(dest)
    result = run("--check", dest)
    assert result.returncode == 1 and "DRIFT " in result.stdout
    assert snapshot(dest) == before
    assert run(dest).returncode == 0
    assert run("--check", dest).returncode == 0


@pytest.mark.parametrize("kind", ["file", "symlink", "directory"])
def test_local_never_touched(tmp_path, kind):
    dest = tmp_path / "bench"
    dest.mkdir()
    local = dest / "LOCAL.md"
    if kind == "file":
        local.write_text("private notes\n")
        local.chmod(0o600)
    elif kind == "symlink":
        local.symlink_to(tmp_path / "absent-private-notes")
    else:
        local.mkdir()
        (local / "notes").write_text("local")
    before = local.lstat()
    for args in [(dest,), ("--check", dest), (dest,)]:
        result = run(*args)
        assert result.returncode == 0, result.stderr
    after = local.lstat()
    assert (before.st_ino, before.st_mode, before.st_mtime_ns) == (after.st_ino, after.st_mode, after.st_mtime_ns)
    if kind == "file":
        assert local.read_text() == "private notes\n"
    elif kind == "symlink":
        assert not local.exists() and local.is_symlink()
    else:
        assert (local / "notes").read_text() == "local"


def test_symlink_refused_before_any_destination_written(tmp_path):
    first, bad, outside = (tmp_path / x for x in ("first", "bad", "outside"))
    bad.mkdir()
    outside.write_text("untouched")
    (bad / "SKILL.md").symlink_to(outside)
    result = run(first, bad)
    assert result.returncode == 2 and "symlink" in result.stderr
    assert not first.exists() and outside.read_text() == "untouched"
    assert run("--check", bad).returncode == 2


def test_hard_link_does_not_overwrite_other_file(tmp_path):
    dest = tmp_path / "bench"
    dest.mkdir()
    outside = tmp_path / "other"
    outside.write_text("untouched")
    os.link(outside, dest / "SKILL.md")
    assert run(dest).returncode == 0
    assert outside.read_text() == "untouched"


def test_unsafe_targets_and_usage(tmp_path):
    dest = tmp_path / "bench"
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    for args in [(), ("--unknown",), (SOURCE,), (REPO,), (REPO.parent,),
                 (dest, dest), (dest, dest / "child"), (link / "bench",)]:
        assert run(*args).returncode == 2
    assert not dest.exists()
    dest.write_text("not a directory")
    assert run(dest).returncode == 2
    dest.unlink()
    dest.mkdir()
    os.mkfifo(dest / "pipe")
    assert run(dest).returncode == 2


def test_check_lists_all_destinations(tmp_path):
    a, b = tmp_path / "one", tmp_path / "two"
    result = run("--check", a, b)
    assert result.returncode == 1
    assert str(a / "SKILL.md") in result.stdout
    assert str(b / "SKILL.md") in result.stdout


def test_skill_help_is_exact_and_links_present():
    text = (SOURCE / "SKILL.md").read_text()
    assert f"```text\n{HELP}\n```" in text
    assert "name: bench\n" in text
    assert "Machine-local notes: see LOCAL.md if present" in text
    for name in ("rp2", "esp", "camera", "pitfalls"):
        assert (SOURCE / "references" / f"{name}.md").is_file()
    assert not (REPO / "skills/workbench").exists()
    assert (REPO / "docs/history/skills-workbench/references/bench-hello.patch").is_file()
