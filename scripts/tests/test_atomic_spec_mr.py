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

"""Behavioural tests for the Atomic-Spec-Exempt trailer in the spec-MR guard.

The trailer only exists inside a commit message in a commit range, so
these cases cannot be reached by calling a function: each builds a
throwaway repository under `tmp_path`, writes a real commit, and drives
scripts/check-atomic-spec-mr.sh through `subprocess`.

Every commit here touches `xqvm_py/executor.py` alone, which is one of
the four layers, so the guard would fail on a partial change and only
the trailer can save it. That makes the exit code a direct read of what
the guard made of the trailer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-atomic-spec-mr.sh"

SIGNOFF = "Signed-off-by: Test Author <test@example.com>"

# Git refuses to commit without an identity, reads the invoking user's
# ~/.gitconfig unless told otherwise, and would run this repository's own
# commit-msg hook against the fixture's commits. All three are settled
# per-invocation rather than by exporting environment variables, so a test
# cannot leak configuration into its neighbours.
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


def commit_layer_change(repo: Path, message: str, *, name: str) -> None:
    """Commit a change to one atomic-spec layer with `message` as the body."""
    target = repo / "xqvm_py"
    target.mkdir(exist_ok=True)
    (target / "executor.py").write_text(f"# {name}\n", encoding="utf-8")
    git(repo, "add", "xqvm_py/executor.py")
    git(repo, "commit", "-m", message)


def run_guard(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the guard in `repo` with every CI variable it reads cleared.

    This suite's own CI job is a merge request pipeline, which would
    otherwise supply the base and source-branch shas and change which
    range the guard scans.
    """
    base = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(repo),
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": "",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": "",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(repo),
        env=base,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository on `main` with one commit touching no layer."""
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-m", f"docs: seed the fixture\n\n{SIGNOFF}\n")
    return tmp_path


def test_single_line_ticketed_trailer_bypasses(repo: Path) -> None:
    """The documented form is what the guard accepts."""
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        f"{SIGNOFF}\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n",
        name="aligned",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bypassed" in result.stdout


def test_trailer_without_a_ticket_fails(repo: Path) -> None:
    """A reason with nowhere to read the reasoning is not an exemption."""
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        f"{SIGNOFF}\n"
        "Atomic-Spec-Exempt: one-sided Python fix, no semantics change\n",
        name="unticketed",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "names no QUI ticket" in result.stderr


def test_wrapped_trailer_fails(repo: Path) -> None:
    """A wrapped reason is silently truncated, so the guard rejects it.

    With the mandatory sign-off in the same paragraph git tolerates the
    unindented continuation and drops it, leaving a value that looks
    healthy to any check that only asks whether git found something. It
    is half the sentence the author wrote, which is why the guard
    compares rather than merely asking.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        f"{SIGNOFF}\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix bringing impl in line\n"
        "with existing Rust behaviour -- no semantics change.\n",
        name="wrapped",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "is one git" in result.stderr


def test_wrapped_trailer_is_invisible_to_git(repo: Path) -> None:
    """The premise the guard rests on, asserted rather than assumed.

    Neither message here carries a sign-off, so git is in its strict
    mode: one non-trailer line in the last paragraph kills recognition of
    the whole paragraph. The single-line form parses and the unindented
    continuation yields nothing at all.
    """
    single = "subject\n\nbody\n\nAtomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n"
    wrapped = (
        "subject\n\nbody\n\nAtomic-Spec-Exempt: QUI-453 one-sided Python fix bringing impl\n"
        "in line with existing Rust behaviour.\n"
    )

    def parse(message: str) -> str:
        return subprocess.run(
            ["git", "interpret-trailers", "--parse"],
            input=message,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    assert "Atomic-Spec-Exempt:" in parse(single)
    assert "Atomic-Spec-Exempt:" not in parse(wrapped)


def test_indented_trailer_is_not_an_exemption(repo: Path) -> None:
    """An indented example in a commit body must not bypass the guard.

    git does not read an indented line as a trailer, so neither does the
    guard. A commit documenting the trailer form indents its example for
    exactly this reason.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "The trailer to use looks like this:\n\n"
        "    Atomic-Spec-Exempt: QUI-453 one-sided Python fix\n\n"
        f"{SIGNOFF}\n",
        name="documented",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "must touch all four layers" in result.stdout + result.stderr


def test_one_good_trailer_does_not_excuse_a_malformed_one(repo: Path) -> None:
    """A malformed trailer fails the range even beside a well-formed one."""
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): first half\n\n"
        f"{SIGNOFF}\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n",
        name="first",
    )
    commit_layer_change(
        repo,
        f"fix(xqvm_py): second half\n\n{SIGNOFF}\nAtomic-Spec-Exempt: no ticket here at all\n",
        name="second",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "names no QUI ticket" in result.stderr


def test_bare_word_footer_above_the_trailer_bypasses(repo: Path) -> None:
    """`Fixes QUI-NNN` shares the trailer paragraph, above the trailer.

    The bare-word footer AGENTS.md and CONTRIBUTING.md prescribe carries
    no colon and so is not a trailer. It is legal in the paragraph
    anyway: DCO makes `Signed-off-by:` mandatory, and a well-known
    trailer there switches git into a mode where non-trailer lines beside
    it are dropped rather than fatal.

    This is the case that made the first version of this guard a
    blocker. It tested "the next line looks like Token: value" and
    rejected the repository's own documented footer on a message git
    parses perfectly.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "Fixes QUI-453\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n"
        f"{SIGNOFF}\n",
        name="bareword",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bypassed" in result.stdout


def test_bare_word_footer_directly_below_the_trailer_is_reported(repo: Path) -> None:
    """Directly below, a discarded footer is indistinguishable from a wrap.

    Once git has dropped the line there is nothing left to tell a
    bare-word footer from a truncated reason, and guessing is what this
    check exists to avoid. The guard reports both possibilities and the
    fix for each rather than picking one and being wrong half the time.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n"
        "Fixes QUI-453\n"
        f"{SIGNOFF}\n",
        name="belowfooter",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "Fixes QUI-453" in result.stderr
    assert "above the trailer" in result.stderr


def test_trailer_in_an_earlier_paragraph_fails(repo: Path) -> None:
    """git reads only the last paragraph, and this is what history does.

    Every Atomic-Spec-Exempt trailer written before QUI-1030 sits in a
    paragraph of its own above the ticket footer and the sign-off, so
    none of them parses. Being in the wrong paragraph, not the wrapping,
    is the reason.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n\n"
        "Implements QUI-453\n\n"
        f"{SIGNOFF}\n",
        name="stranded",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "git does not parse" in result.stderr


def test_trailer_without_a_signoff_fails(repo: Path) -> None:
    """Strip the sign-off and the same bare-word footer becomes fatal.

    Pins the dependency the case above rests on, so a future reader does
    not take "a bare-word footer is fine" as unconditional. The commit-msg
    hook rejects an unsigned commit anyway; this asserts what git does if
    one reaches the guard.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n"
        "Fixes QUI-453\n",
        name="unsigned",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "git does not parse" in result.stderr


def test_indented_continuation_fails(repo: Path) -> None:
    """An indented fold parses without loss, and is still rejected.

    git joins the continuation into the value, so nothing disappears --
    but the line below the trailer is one git discards, which is
    indistinguishable from the lossy unindented case at the point the
    check runs. One form is right rather than two being nearly right.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix bringing impl in line\n"
        "  with existing Rust behaviour, no semantics change\n"
        f"{SIGNOFF}\n",
        name="folded",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "is one git" in result.stderr


def test_trailer_quoted_in_prose_does_not_anchor_the_check(repo: Path) -> None:
    """A body may name the trailer at column 0 and still be exempt.

    The below-the-line check reads the LAST paragraph, because that is the
    only block git reads trailers out of. Anchored on the first column-0
    match anywhere in the message instead, it lands on the sentence
    following a quoted trailer and rejects a message git parses perfectly
    -- the diagnostic then points at prose, and there is nothing the
    author can do to the real trailer that would help.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "The trailer we take here reads:\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided fix\n"
        "because the Rust side already behaves this way.\n\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n"
        f"{SIGNOFF}\n",
        name="quoted",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bypassed" in result.stdout


def test_second_trailer_is_checked_for_a_ticket(repo: Path) -> None:
    """A ticketed trailer does not excuse an unticketed one beside it.

    Both parse, so both are exemptions a reader can find -- and the second
    is one nobody can trace back to a decision. Checking the trailers as
    one concatenated blob passes on the first ticket it sees and never
    reads the rest.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n"
        "Atomic-Spec-Exempt: no ticket at all here\n"
        f"{SIGNOFF}\n",
        name="twotrailers",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "names no QUI ticket" in result.stderr
    assert "no ticket at all here" in result.stderr


def test_second_trailer_gets_the_below_the_line_check(repo: Path) -> None:
    """Every trailer is checked, not just the first one in the paragraph.

    Inspecting only the first match leaves a wrapped reason under a second
    trailer silently truncated -- the exact defect the check exists to
    catch, reintroduced one line further down.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n"
        "Atomic-Spec-Exempt: QUI-454 second one-sided fix bringing impl in line\n"
        "with existing Rust behaviour -- no semantics change.\n"
        f"{SIGNOFF}\n",
        name="secondwrapped",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 1
    assert "is one git" in result.stderr


def test_trailer_ending_the_message_bypasses(repo: Path) -> None:
    """A trailer with no line under it has no below-the-line case to fail.

    Pins the end-of-paragraph case: there is no next line to read, so a
    lookback that fired anyway would re-report the sign-off above as the
    line below this trailer.
    """
    base = git(repo, "rev-parse", "HEAD")
    commit_layer_change(
        repo,
        "fix(xqvm_py): align to existing Rust behaviour\n\n"
        f"{SIGNOFF}\n"
        "Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change\n",
        name="lastline",
    )

    result = run_guard(repo, base, "HEAD")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bypassed" in result.stdout
