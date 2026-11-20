"""Exercise the runner's discovery and failure reporting in an isolated tree."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize("pytest_rc,shellcheck_rc", [(0, 0), (1, 0), (0, 1)])
def test_check_runner(tmp_path, pytest_rc, shellcheck_rc):
    repo = tmp_path / "repo"
    dev = repo / "bench/dev"
    dev.mkdir(parents=True)
    runner = dev / "check.sh"
    shutil.copyfile(Path(__file__).parents[1] / "dev/check.sh", runner)
    tools = repo / "bench/.venv/bin"
    tools.mkdir(parents=True)
    for name, body in {
        "python": '#!/bin/sh\nexit "$TEST_PYTEST_RC"\n',
        "shellcheck": '#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGS"\nexit "$TEST_SHELLCHECK_RC"\n',
    }.items():
        tool = tools / name
        tool.write_text(body)
        tool.chmod(0o755)
    (tools / "ignored.sh").write_text("not a project script")
    bins = repo / "bench/bin"
    bins.mkdir()
    for name, text in {"shell": "#!/bin/sh\n", "bash": "#!/usr/bin/env bash\n",
                       "python": "#!/usr/bin/env python3\n", "other.sh": "#!/bin/bash\n"}.items():
        (bins / name).write_text(text)
    (dev / "extra.sh").write_text("#!/bin/bash\n")
    args_file = tmp_path / "args"
    env = {**os.environ, "TEST_PYTEST_RC": str(pytest_rc),
           "TEST_SHELLCHECK_RC": str(shellcheck_rc), "TEST_ARGS": str(args_file)}
    result = subprocess.run(["bash", str(runner)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == (1 if pytest_rc or shellcheck_rc else 0)
    assert ("ALL PASS" in result.stdout) == (result.returncode == 0)
    assert ("FAIL pytest" if pytest_rc else "PASS pytest") in result.stdout
    assert ("FAIL shellcheck" if shellcheck_rc else "PASS shellcheck") in result.stdout
    assert set(args_file.read_text().splitlines()) == {
        "bench/dev/check.sh", "bench/dev/extra.sh", "bench/bin/other.sh",
        "bench/bin/shell", "bench/bin/bash"}