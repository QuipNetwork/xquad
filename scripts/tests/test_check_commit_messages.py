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

"""Behavioural tests for scripts/check-commit-messages.sh.

The guard picks which commits to validate from three inputs that only
exist in a real repository -- an argument pair, GitLab's merge request
variables, and `git merge-base origin/main HEAD` -- so the cases that
matter cannot be reached by calling a function. Each test builds a
throwaway repository under `tmp_path`, drives the script through
`subprocess` with the environment a given pipeline would supply, and
asserts on the exit code and output.

Landed mode is the reason this file exists. It runs only on `main`,
where the commit under test is already pushed and unamendable, so the
question of exactly which pipelines reach it is not one to answer by
merging and finding out.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-commit-messages.sh"

SIGNOFF = "Signed-off-by: Test Author <test@example.com>"

# Git refuses to commit without an identity, reads the invoking user's
# ~/.gitconfig unless told otherwise, and would run this repository's
# own commit-msg hook against the fixture's deliberately bad subjects.
# All three are settled per-invocation rather than by exporting
# environment variables, so a test cannot leak configuration into its
# neighbours.
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
    result = subprocess.run(
        ["git", "-C", str(repo), *GIT_CONFIG, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repo: Path, subject: str, *, signed: bool = True) -> str:
    """Add an empty commit with `subject` and return its sha."""
    message = f"{subject}\n\n{SIGNOFF}\n" if signed else f"{subject}\n"
    git(repo, "commit", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def run_guard(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Invoke the guard in `repo` with `env` overlaid on a clean base.

    The base clears every CI variable the script reads, so a test that
    does not name one is asserting about its absence rather than
    inheriting whatever the surrounding pipeline happened to set --
    this suite's own CI job is a merge request pipeline, which would
    otherwise supply three of them.
    """
    base = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(repo),
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": "",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": "",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "",
        "CI_COMMIT_BRANCH": "",
        "CI_COMMIT_BEFORE_SHA": "",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(repo),
        env={**base, **env},
        capture_output=True,
        text=True,
    )


def set_origin_main(repo: Path) -> None:
    """Point `origin/main` at the local `main`, as a fetch would.

    The guard's merge-base fallback needs `origin/main` to resolve. A
    remote-tracking ref written directly is enough and saves the
    fixture a second repository to clone from.
    """
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "main"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository on `main` with one good commit, mirrored to origin."""
    git(tmp_path, "init", "-b", "main")
    commit(tmp_path, "feat: add the first thing")
    set_origin_main(tmp_path)
    return tmp_path


def merge_branch(repo: Path, subject: str, *, signed: bool = True) -> None:
    """Land `subject` on `main` the way GitLab's squash-and-merge does.

    A squash commit off the pre-merge tip, then a non-fast-forward
    merge commit whose second parent is that squash commit -- the shape
    every merge on this repository's `main` actually has.
    """
    base = git(repo, "rev-parse", "main")
    git(repo, "checkout", "-q", "-b", "squash", base)
    commit(repo, subject, signed=signed)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-q", "-m", "merge: branch 'squash' into 'main'", "squash")


# --- Range mode -------------------------------------------------------------


def test_range_mode_reports_the_bad_commit(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature/x")
    commit(repo, "feat: add a good thing")
    commit(repo, "Broken subject with no type")

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "1 checked, 0 merge commit(s) skipped, 1 failed" not in result.stdout
    assert "2 checked, 0 merge commit(s) skipped, 1 failed" in result.stdout


def test_range_mode_passes_a_clean_branch(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature/x")
    commit(repo, "fix(xqvm): correct the second thing")

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 0, result.stderr


# --- Empty ranges that must not reach landed mode ---------------------------


def test_explicit_empty_range_exits_zero(repo: Path) -> None:
    """A range somebody named is an answer, not a cue to look elsewhere."""
    merge_branch(repo, "Bad squash subject")
    head = git(repo, "rev-parse", "HEAD")

    result = run_guard(repo, head, head)

    assert result.returncode == 0, result.stderr
    assert "no commits in" in result.stdout


def test_merge_request_empty_range_exits_zero(repo: Path) -> None:
    """$CI_MERGE_REQUEST_DIFF_BASE_SHA names a range the same way."""
    merge_branch(repo, "Bad squash subject")
    head = git(repo, "rev-parse", "HEAD")

    result = run_guard(repo, CI_MERGE_REQUEST_DIFF_BASE_SHA=head)

    assert result.returncode == 0, result.stderr
    assert "no commits in" in result.stdout


def test_branch_pipeline_with_no_own_commits_exits_zero(repo: Path) -> None:
    """A branch sitting at `main` must not be failed over `main`'s history.

    The commit is identical to `main`'s, so the merge-base range is
    empty by the same route a `main` pipeline's is. Only the ref name
    separates the two, and this is the case that ref name exists for.
    """
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)
    git(repo, "checkout", "-q", "-b", "feature/x")

    result = run_guard(repo, CI_COMMIT_BRANCH="feature/x")

    assert result.returncode == 0, result.stderr
    assert "no commits in" in result.stdout


def test_local_run_on_a_feature_branch_exits_zero(repo: Path) -> None:
    """Same case off CI, where the branch comes from symbolic-ref."""
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)
    git(repo, "checkout", "-q", "-b", "feature/x")

    result = run_guard(repo)

    assert result.returncode == 0, result.stderr
    assert "no commits in" in result.stdout


def test_tag_pipeline_exits_zero(repo: Path) -> None:
    """No $CI_COMMIT_BRANCH on a tag, and `main` already checked the commit."""
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)
    git(repo, "tag", "v9.9.9")
    git(repo, "checkout", "-q", "v9.9.9")

    result = run_guard(repo)

    assert result.returncode == 0, result.stderr
    assert "no commits in" in result.stdout


# --- Landed mode ------------------------------------------------------------


def test_landed_mode_checks_the_squash_commit(repo: Path) -> None:
    before = git(repo, "rev-parse", "main")
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert result.returncode == 1
    assert "Bad squash subject" in result.stderr
    assert "1 checked, 1 merge commit(s) skipped, 1 failed" in result.stdout


def test_landed_mode_passes_a_good_squash_commit(repo: Path) -> None:
    before = git(repo, "rev-parse", "main")
    merge_branch(repo, "feat(xqvm): add an opcode")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert result.returncode == 0, result.stderr
    assert "1 checked, 1 merge commit(s) skipped, 0 failed" in result.stdout


def test_landed_mode_checks_every_commit_of_a_direct_push(repo: Path) -> None:
    """A three-commit push to `main` must not be cut to its tip."""
    before = git(repo, "rev-parse", "main")
    commit(repo, "First bad subject")
    commit(repo, "Second bad subject")
    commit(repo, "fix(ci): a good third subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert result.returncode == 1
    assert "First bad subject" in result.stderr
    assert "Second bad subject" in result.stderr
    assert "3 checked, 0 merge commit(s) skipped, 2 failed" in result.stdout


def test_landed_mode_catches_a_missing_signoff(repo: Path) -> None:
    before = git(repo, "rev-parse", "main")
    merge_branch(repo, "feat(xqvm): add an opcode", signed=False)
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert result.returncode == 1
    assert "missing Signed-off-by" in result.stderr


# --- Landed mode without a push range ---------------------------------------


def test_landed_mode_falls_back_to_the_tip_parents(repo: Path) -> None:
    """Off CI there is no $CI_COMMIT_BEFORE_SHA, so the tip's parents serve."""
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)

    result = run_guard(repo)

    assert result.returncode == 1
    assert "Bad squash subject" in result.stderr
    assert "1 checked, 0 merge commit(s) skipped, 1 failed" in result.stdout


def test_landed_mode_falls_back_on_an_all_zero_before_sha(repo: Path) -> None:
    """GitLab sends all-zeros on a branch's first pipeline."""
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA="0" * 40)

    assert result.returncode == 1
    assert "Bad squash subject" in result.stderr


def test_landed_mode_falls_back_on_an_unfetched_before_sha(repo: Path) -> None:
    """A variable naming a commit is not the clone having fetched it."""
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA="0" * 39 + "1")

    assert result.returncode == 1
    assert "Bad squash subject" in result.stderr


def test_landed_mode_on_a_non_merge_tip_checks_that_commit(repo: Path) -> None:
    commit(repo, "Bad direct subject")
    set_origin_main(repo)

    result = run_guard(repo)

    assert result.returncode == 1
    assert "Bad direct subject" in result.stderr


# --- The failure message ----------------------------------------------------


def test_failure_message_names_the_branch_not_head(repo: Path) -> None:
    before = git(repo, "rev-parse", "main")
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert "already on main and cannot be amended" in result.stderr
    assert "already on HEAD" not in result.stderr


def test_failure_message_is_singular_for_one_commit(repo: Path) -> None:
    before = git(repo, "rev-parse", "main")
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert "This commit is already on main" in result.stderr


def test_failure_message_is_plural_for_several_commits(repo: Path) -> None:
    before = git(repo, "rev-parse", "main")
    commit(repo, "First bad subject")
    commit(repo, "Second bad subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert "These 2 commits are already on main" in result.stderr


def test_failure_message_preview_command_writes_to_stdout(repo: Path) -> None:
    """The hint must not leave a CHANGELOG.md behind the reader has to notice."""
    before = git(repo, "rev-parse", "main")
    merge_branch(repo, "Bad squash subject")
    set_origin_main(repo)

    result = run_guard(repo, CI_COMMIT_BRANCH="main", CI_COMMIT_BEFORE_SHA=before)

    assert "OUTPUT=/dev/stdout" in result.stderr


# --- Setup errors -----------------------------------------------------------


def test_unresolvable_base_ref_is_a_setup_error(repo: Path) -> None:
    result = run_guard(repo, "does-not-exist", "HEAD")

    assert result.returncode == 2
    assert "does not resolve" in result.stderr


def test_missing_origin_main_is_a_setup_error(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    commit(tmp_path, "feat: add the first thing")

    result = run_guard(tmp_path)

    assert result.returncode == 2
    assert "could not derive BASE_REF" in result.stderr


@pytest.mark.parametrize(("source", "code"), [("feature/qui-1", 1), ("release/v0.4.1", 0)])
def test_merge_request_carrying_a_merge_commit(repo: Path, source: str, code: int) -> None:
    """A feature branch that merged something in fails; a release branch may carry merges."""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "-c", "other")
    commit(repo, "feat: add the other thing")
    git(repo, "switch", "-q", "-c", "feature", base)
    commit(repo, "feat: add the feature")
    git(repo, "merge", "--no-ff", "-q", "-m", "merge: branch 'other' into 'feature'", "other")
    env = {
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": base,
        "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": git(repo, "rev-parse", "HEAD"),
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": source,
    }
    result = run_guard(repo, **env)
    assert result.returncode == code, result.stderr
