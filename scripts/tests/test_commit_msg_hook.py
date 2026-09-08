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

"""Behavioural tests for .githooks/commit-msg.

The hook takes the message file path as its one argument ($1), so
these tests invoke it directly (`bash .githooks/commit-msg <path>`)
rather than driving a real `git commit` -- there is no need for a
fixture repository or per-invocation git identity the way
test_check_commit_messages.py needs one, because the hook itself never
shells out to git: it only sources scripts/commit-grammar.sh (path
resolved relative to the hook's own location, not the caller's cwd)
and reads the message file it is given.

The merge exemption is the reason this file exists. The hook sources
scripts/commit-grammar.sh, which exports `is_merge_subject` -- the
same guard scripts/check-commit-messages.sh uses to skip a
GitLab-generated merge subject entirely during the CI-side range
check. Before this fix the hook never called it, so a merge commit
that CI would report as skipped was rejected outright by the local
hook.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".githooks" / "commit-msg"

SIGNOFF = "Signed-off-by: Test Author <test@example.com>"


def write_message(tmp_path: Path, body: str) -> Path:
    """Write `body` to a commit-message file under `tmp_path` and return its path."""
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(body)
    return msg_file


def run_hook(msg_file: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the hook against `msg_file` and return the completed process."""
    return subprocess.run(
        ["bash", str(HOOK), str(msg_file)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


def test_merge_branch_subject_accepted_without_signoff(tmp_path: Path) -> None:
    """The historical `Merge branch '...'` form needs no sign-off."""
    msg_file = write_message(tmp_path, "Merge branch 'feature/x'\n")

    result = run_hook(msg_file)

    assert result.returncode == 0, result.stderr


def test_repository_merge_subject_accepted(tmp_path: Path) -> None:
    """This repository's own conventional merge form is exempt too.

    Confirms `merge: branch '...' into '...'` matches
    MERGE_SUBJECT_PATTERN in scripts/commit-grammar.sh.
    """
    subject = "merge: branch 'feature/x' into 'main'"
    msg_file = write_message(tmp_path, f"{subject}\n")

    result = run_hook(msg_file)

    assert result.returncode == 0, result.stderr


def test_wellformed_subject_with_signoff_accepted(tmp_path: Path) -> None:
    msg_file = write_message(tmp_path, f"feat(xqvm): add an opcode\n\n{SIGNOFF}\n")

    result = run_hook(msg_file)

    assert result.returncode == 0, result.stderr


def test_malformed_subject_rejected(tmp_path: Path) -> None:
    msg_file = write_message(tmp_path, f"Bad: subject.\n\n{SIGNOFF}\n")

    result = run_hook(msg_file)

    assert result.returncode != 0


def test_authored_merge_subject_is_still_checked(tmp_path: Path) -> None:
    """ "Merge the two loaders into one" is authored prose, not a merge subject.

    The exemption skips the sign-off check as well as the grammar check,
    so a prefix loose enough to catch an authored subject lets a commit
    land with no DCO sign-off. The pattern requires the noun git puts
    after "Merge" for exactly this reason.
    """
    msg_file = write_message(tmp_path, "Merge the two loaders into one\n")

    result = run_hook(msg_file)

    assert result.returncode != 0
    assert "Conventional Commits" in result.stderr


def test_hand_merge_forms_are_exempt(tmp_path: Path) -> None:
    """The generated forms git produces outside GitLab are exempt too."""
    for subject in (
        "Merge remote-tracking branch 'origin/main'",
        "Merge tag 'v0.4.0'",
        "Merge commit '0123abc'",
        "Merge pull request #12 from fork/branch",
    ):
        msg_file = write_message(tmp_path, f"{subject}\n")

        result = run_hook(msg_file)

        assert result.returncode == 0, f"{subject}: {result.stderr}"
