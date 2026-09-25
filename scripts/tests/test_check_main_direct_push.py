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
"""Behavioural tests for scripts/check-main-direct-push.sh.

Each test builds a throwaway repository with the two files the guard
reads the workspace version from, adds one commit to `main`, and runs
the guard over the push the way a `main` push pipeline would.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-main-direct-push.sh"

GIT_CONFIG = [
    "-c",
    "user.name=Test Author",
    "-c",
    "user.email=test@example.com",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=/dev/null",
]


def git(repo: Path, *args: str) -> str:
    """Run one git command in `repo` and return its stdout, stripped."""
    result = subprocess.run(["git", "-C", str(repo), *GIT_CONFIG, *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def write_version(repo: Path, cargo: str, pep440: str, extra: str = "") -> None:
    """Write the workspace version in both spellings, plus a lockfile line that shares it."""
    (repo / "xqvm").mkdir(exist_ok=True)
    (repo / "xqvm_py").mkdir(exist_ok=True)
    (repo / "xqvm" / "Cargo.toml").write_text(f'[package]\nname = "xqvm"\nversion = "{cargo}"\n{extra}')
    (repo / "xqvm_py" / "__init__.py").write_text(f'__version__ = "{pep440}"\n')
    # An unrelated package pinned at the old release version: masking
    # must treat it the same on both sides.
    (repo / "Cargo.lock").write_text(
        f'[[package]]\nname = "xqvm"\nversion = "{cargo}"\n\n[[package]]\nname = "other"\nversion = "0.4.1"\n'
    )


def commit_all(repo: Path, subject: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def released_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose `main` has just merged a 0.4.1 release."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write_version(repo, "0.4.1", "0.4.1")
    return repo, commit_all(repo, "chore: bump workspace to 0.4.1")


def run_push(repo: Path, before: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CI_COMMIT_BRANCH": "main", "CI_COMMIT_BEFORE_SHA": before, **overrides}
    for name in ("CI_MERGE_REQUEST_IID", "CI_PROJECT_PATH"):
        if name not in overrides:
            env.pop(name, None)
    return subprocess.run(["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True)


def test_reopening_bump_passes(tmp_path: Path) -> None:
    repo, before = released_repo(tmp_path)
    write_version(repo, "0.4.2-dev", "0.4.2.dev0")
    commit_all(repo, "chore: reopen main at 0.4.2-dev")
    result = run_push(repo, before)
    assert result.returncode == 0, result.stderr


def test_bump_carrying_another_change_fails(tmp_path: Path) -> None:
    repo, before = released_repo(tmp_path)
    write_version(repo, "0.4.2-dev", "0.4.2.dev0", extra='[dependencies]\nserde = "1"\n')
    commit_all(repo, "chore: reopen main at 0.4.2-dev")
    result = run_push(repo, before)
    assert result.returncode == 1
    assert "xqvm/Cargo.toml changes more than the version" in result.stderr


def test_direct_change_without_bump_fails(tmp_path: Path) -> None:
    repo, before = released_repo(tmp_path)
    (repo / "README.md").write_text("hello\n")
    commit_all(repo, "docs: add readme")
    result = run_push(repo, before)
    assert result.returncode == 1
    assert "does not change the workspace version" in result.stderr


def test_merge_request_merge_passes_and_bare_merge_fails(tmp_path: Path) -> None:
    repo, before = released_repo(tmp_path)
    git(repo, "switch", "-q", "-c", "feature")
    (repo / "README.md").write_text("hello\n")
    commit_all(repo, "docs: add readme")
    git(repo, "switch", "-q", "main")
    git(
        repo,
        "merge",
        "--no-ff",
        "-q",
        "feature",
        "-m",
        "merge: branch 'feature' into 'main'\n\nrefer to merge request quip.network/xquad!1",
    )
    assert run_push(repo, before).returncode == 0

    git(repo, "reset", "-q", "--hard", before)
    git(repo, "merge", "--no-ff", "-q", "feature", "-m", "merge: branch 'feature' into 'main'")
    result = run_push(repo, before)
    assert result.returncode == 1
    assert "no merge request produced" in result.stderr


MR_TRAILER = "refer to merge request quip.network/xquad!1"


def test_bump_that_adds_a_file_fails(tmp_path: Path) -> None:
    repo, before = released_repo(tmp_path)
    write_version(repo, "0.4.2-dev", "0.4.2.dev0")
    (repo / "NEW.md").write_text("new\n")
    commit_all(repo, "chore: reopen main at 0.4.2-dev")
    result = run_push(repo, before)
    assert result.returncode == 1
    assert "adds, deletes or renames NEW.md" in result.stderr


def test_missing_before_sha_judges_the_tip(tmp_path: Path) -> None:
    """A zero or absent CI_COMMIT_BEFORE_SHA falls back to HEAD~1..HEAD."""
    repo, _ = released_repo(tmp_path)
    (repo / "README.md").write_text("hello\n")
    commit_all(repo, "docs: add readme")
    assert run_push(repo, "0" * 40).returncode == 1
    assert run_push(repo, "").returncode == 1


def test_off_main_and_merge_request_pipelines_are_not_judged(tmp_path: Path) -> None:
    repo, before = released_repo(tmp_path)
    (repo / "README.md").write_text("hello\n")
    commit_all(repo, "docs: add readme")
    assert run_push(repo, before, CI_COMMIT_BRANCH="dev").returncode == 0
    assert run_push(repo, before, CI_MERGE_REQUEST_IID="1").returncode == 0


def test_merge_request_reference_must_name_this_project(tmp_path: Path) -> None:
    repo, before = released_repo(tmp_path)
    git(repo, "switch", "-q", "-c", "feature")
    (repo / "README.md").write_text("hello\n")
    commit_all(repo, "docs: add readme")
    git(repo, "switch", "-q", "main")
    git(
        repo,
        "merge",
        "--no-ff",
        "-q",
        "feature",
        "-m",
        "merge: branch 'feature' into 'main'\n\nrefer to merge request other/project!1",
    )
    result = run_push(repo, before)
    assert result.returncode == 1
    assert "no merge request produced" in result.stderr


def test_release_merge_is_judged_by_its_merge_commit_alone(tmp_path: Path) -> None:
    """Merges and direct commits inside the release branch sit behind the second parent."""
    repo, before = released_repo(tmp_path)
    git(repo, "switch", "-q", "-c", "fix")
    (repo / "FIX.md").write_text("fix\n")
    commit_all(repo, "fix: a fix")
    git(repo, "switch", "-q", "-c", "release/v0.4.2", "main")
    git(repo, "merge", "--no-ff", "-q", "fix", "-m", "merge: branch 'fix' into 'release/v0.4.2'")
    write_version(repo, "0.4.2", "0.4.2")
    commit_all(repo, "chore: bump workspace to 0.4.2")
    git(repo, "switch", "-q", "main")
    git(
        repo,
        "merge",
        "--no-ff",
        "-q",
        "release/v0.4.2",
        "-m",
        f"merge: branch 'release/v0.4.2' into 'main'\n\n{MR_TRAILER}",
    )
    result = run_push(repo, before)
    assert result.returncode == 0, result.stderr
    assert "1 first-parent commit(s)" in result.stdout
