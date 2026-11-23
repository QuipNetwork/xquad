# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Behavioural tests for scripts/ensure-xqffi.sh.

The script is what keeps a synced CI Python job working on a reused runner
after `git clean` removed the untracked xqffi/xqffi.*.so. A fresh clone
always rebuilds, so a regression here would only show up intermittently.
Each case covers one exit: extension present, restored by the reinstall,
reinstall failed, and still missing after a reinstall that succeeded.

The script resolves the repo root from its own location and `cd`s there,
so each test copies it into a temporary `scripts/` beside a temporary
`xqffi/`; run in place it would find the real extension. `uv` is a stub
on a PATH built from stubs only, which keeps the suite offline.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ensure-xqffi.sh"

# Only what the script, or the uv stub, invokes is borrowed from the host.
BORROWED = ("sh", "dirname", "touch", "cat")

# Logs one argument per line, optionally creates the extension in the
# script's working directory (the temporary repo root), and exits with the
# status the test asks for.
UV_STUB = """#!/bin/sh
for arg in "$@"; do echo "$arg"; done >> "$UV_LOG"
if [ -n "$UV_CREATE" ]; then touch xqffi/xqffi.abi3.so; fi
exit "${UV_STATUS:-0}"
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "xqffi").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    return root


@pytest.fixture
def bindir(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    for name in BORROWED:
        for root in ("/bin", "/usr/bin"):
            candidate = Path(root) / name
            if candidate.exists():
                (directory / name).symlink_to(candidate)
                break
    uv = directory / "uv"
    uv.write_text(UV_STUB)
    uv.chmod(0o755)
    return directory


def _run(
    repo: Path, bindir: Path, *args: str, create: bool = False, status: int = 0
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = str(bindir)
    env["UV_LOG"] = str(repo.parent / "uv.log")
    env["UV_CREATE"] = "1" if create else ""
    env["UV_STATUS"] = str(status)
    return subprocess.run(
        ["/bin/bash", str(repo / "scripts" / SCRIPT.name), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _uv_calls(repo: Path) -> list[str]:
    log = repo.parent / "uv.log"
    return log.read_text().splitlines() if log.exists() else []


def test_present_extension_skips_uv(repo: Path, bindir: Path) -> None:
    (repo / "xqffi" / "xqffi.abi3.so").touch()

    result = _run(repo, bindir, "--extra", "quip")

    assert result.returncode == 0, result.stderr
    assert _uv_calls(repo) == []


def test_windows_extension_counts_as_present(repo: Path, bindir: Path) -> None:
    (repo / "xqffi" / "xqffi.pyd").touch()

    result = _run(repo, bindir)

    assert result.returncode == 0, result.stderr
    assert _uv_calls(repo) == []


def test_missing_extension_is_restored_with_the_callers_arguments(repo: Path, bindir: Path) -> None:
    result = _run(repo, bindir, "--extra", "quip", create=True)

    assert result.returncode == 0, result.stderr
    assert "reinstalling the xquad project" in result.stdout
    assert _uv_calls(repo) == ["sync", "--extra", "quip", "--reinstall-package", "xquad"]
    assert (repo / "xqffi" / "xqffi.abi3.so").exists()


def test_failed_reinstall_exits_with_uvs_status(repo: Path, bindir: Path) -> None:
    result = _run(repo, bindir, status=3)

    assert result.returncode == 3
    assert _uv_calls(repo) == ["sync", "--reinstall-package", "xquad"]
    assert "still missing" not in result.stderr


def test_extension_still_missing_after_reinstall_exits_1(repo: Path, bindir: Path) -> None:
    result = _run(repo, bindir)

    assert result.returncode == 1
    assert "still missing after reinstalling" in result.stderr
