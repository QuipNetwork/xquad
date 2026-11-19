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

"""Behavioural tests for scripts/prepare-release.py.

The cases that matter are the ticket's idempotency claims: a fresh
branch, a branch someone already bumped, a re-run, a bump pushed by hand
while CI was bumping, and two runs racing to open the merge request.
Each ends with one bump commit and one merge request, or with a failure
that names what went wrong.

Git is real. Every case clones a bare repository standing in for GitLab
and runs the script against a detached checkout, the way CI checks a
branch out, so a rejected push is git's own rejection. The GitLab API is
the external boundary and is faked in-process. `make`, `cargo` and `uv`
are stubbed on PATH: what the script decides from their exit codes is
under test, not what they do, and the real ones need the whole
workspace. The stubs model the version as a VERSION file and each
lockfile as a copy of it, including uv's need for a current Cargo.lock.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "prepare-release.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_TEMPLATE = REPO_ROOT / ".gitlab" / "merge_request_templates" / "release.md"


def _load():
    spec = importlib.util.spec_from_file_location("prepare_release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pr = _load()

STUBS = {
    "make": """#!/bin/sh
for a in "$@"; do
    case "$a" in
        TAG=v*) tag="${a#TAG=v}" ;;
        VERSION=*) ver="${a#VERSION=}" ;;
        check-version-sites) mode=check ;;
        set-version) mode=set ;;
    esac
done
case "$mode" in
    check) [ "$(cat VERSION)" = "$tag" ] ;;
    set) [ -z "$STUB_SET_VERSION_FAILS" ] || exit 1; printf '%s\\n' "$ver" > VERSION ;;
    *) exit 2 ;;
esac
""",
    "cargo": """#!/bin/sh
case "$*" in
    "update --workspace --locked") [ "$(cat Cargo.lock)" = "$(cat VERSION)" ] ;;
    "update --workspace") cp VERSION Cargo.lock ;;
    *) exit 2 ;;
esac
""",
    "uv": """#!/bin/sh
current() { [ "$(cat "$1")" = "$(cat VERSION)" ]; }
case "$*" in
    "lock --check") current Cargo.lock && current uv.lock ;;
    "lock") current Cargo.lock || exit 1; cp VERSION uv.lock ;;
    *) exit 2 ;;
esac
""",
}

BOT = {"id": 99, "username": "project_1_bot", "name": "release bot", "email": "project_1_bot@noreply.example"}
USERS = {"alice": 11, "bob": 12}


class FakeGitLab:
    """The slice of the GitLab API the script uses, with merge requests held in memory."""

    def __init__(self) -> None:
        self.merge_requests: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        # Set to make the next create lose a race: another run's merge
        # request appears, and this run's POST gets GitLab's 409.
        self.lose_race_with: dict[str, Any] | None = None
        self.conflict_without_merge_request = False

    def get(self, path: str, query: dict[str, str] | None = None) -> Any:
        query = query or {}
        if path == "user":
            return BOT
        if path == "users":
            uid = USERS.get(query["username"])
            return [] if uid is None else [{"id": uid, "username": query["username"]}]
        if path == "projects/1/merge_requests":
            assert query == {"state": "opened", "source_branch": query["source_branch"]}
            return [mr for mr in self.merge_requests if mr["source_branch"] == query["source_branch"]]
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, body: dict[str, Any]) -> Any:
        assert path == "projects/1/merge_requests"
        if self.lose_race_with is not None:
            self.merge_requests.append(self.lose_race_with)
            self.lose_race_with = None
            raise pr.ApiError(409, "POST", path, '{"message":["Another open merge request already exists"]}')
        if self.conflict_without_merge_request:
            raise pr.ApiError(409, "POST", path, '{"message":["conflict"]}')
        created = {**body, "source_project_id": 1, "web_url": f"https://gitlab.example/mr/{len(self.created) + 1}"}
        self.created.append(created)
        self.merge_requests.append(created)
        return created


def git(*args: str, cwd: Path) -> str:
    """Run git with an identity of its own, for the test's setup commits."""
    result = subprocess.run(
        ["git", "-c", "user.name=maintainer", "-c", "user.email=maintainer@example", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def write_tree(root: Path, version: str, locks: str | None = None) -> None:
    (root / "VERSION").write_text(f"{version}\n")
    for lock in ("Cargo.lock", "uv.lock"):
        (root / lock).write_text(f"{locks or version}\n")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub make, cargo and uv on PATH, and keep the developer's git config out."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    git_dir = Path(shutil.which("git") or "/usr/bin/git").parent
    monkeypatch.setenv("PATH", f"{bin_dir}:{git_dir}:/usr/bin:/bin")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("STUB_SET_VERSION_FAILS", raising=False)
    monkeypatch.setattr(pr, "_SECRETS", [])


class Repo:
    """A bare remote with one release branch, and a detached CI-style checkout of it."""

    def __init__(self, tmp_path: Path, branch: str, version: str, locks: str | None = None) -> None:
        self.branch = branch
        self.remote = tmp_path / "remote.git"
        seed = tmp_path / "seed"
        seed.mkdir()
        git("init", "--quiet", cwd=seed)
        template = seed / pr.TEMPLATE
        template.parent.mkdir(parents=True)
        shutil.copy(REAL_TEMPLATE, template)
        write_tree(seed, version, locks)
        git("add", ".", cwd=seed)
        git("commit", "--quiet", "-m", "chore: seed", cwd=seed)
        git("init", "--quiet", "--bare", str(self.remote), cwd=tmp_path)
        git("push", "--quiet", str(self.remote), f"HEAD:refs/heads/{branch}", cwd=seed)
        self.seed = seed
        self.checkout = self.clone(tmp_path / "checkout")

    def clone(self, where: Path) -> Path:
        git("clone", "--quiet", "--branch", self.branch, str(self.remote), str(where), cwd=where.parent)
        git("checkout", "--quiet", "--detach", cwd=where)
        return where

    def log(self) -> list[str]:
        """Subjects on the remote branch, newest first."""
        return git("--git-dir", str(self.remote), "log", "--format=%s", self.branch, cwd=self.remote).split("\n")[:-1]

    def tip(self, fmt: str) -> str:
        return git("--git-dir", str(self.remote), "log", "-1", f"--format={fmt}", self.branch, cwd=self.remote)

    def push_from_elsewhere(self, tmp_path: Path, version: str, subject: str) -> None:
        """Another clone moves the remote branch, the way a person pushing mid-job would."""
        other = self.clone(tmp_path / "other")
        write_tree(other, version)
        (other / "CHANGE").write_text(f"{subject}\n")
        git("add", ".", cwd=other)
        git("commit", "--quiet", "-m", subject, cwd=other)
        git("push", "--quiet", str(self.remote), f"HEAD:refs/heads/{self.branch}", cwd=other)

    def prepare(self, api: FakeGitLab, root: Path | None = None) -> str:
        return pr.prepare(
            root or self.checkout,
            self.branch,
            api,
            "1",
            str(self.remote),
            "gitlab.example",
            ["alice", "bob"],
        )


def test_an_unbumped_branch_gets_one_bump_and_one_draft(tmp_path: Path) -> None:
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0-dev")
    api = FakeGitLab()

    url = repo.prepare(api)

    assert repo.log() == ["chore: bump workspace to 0.6.0", "chore: seed"]
    assert repo.tip("%an <%ae>").strip() == "release bot <project_1_bot@noreply.example>"
    assert "Signed-off-by: release bot <project_1_bot@noreply.example>" in repo.tip("%B")
    assert repo.tip("%B").count("ci.skip") == 0
    assert [mr["web_url"] for mr in api.created] == [url]
    mr = api.created[0]
    assert mr["title"] == "Draft: release: v0.6.0"
    assert mr["target_branch"] == "main"
    assert mr["source_branch"] == "release/v0.6.0"
    assert mr["reviewer_ids"] == [11, 12]
    assert mr["squash"] is False


def test_a_branch_bumped_by_hand_gets_no_new_commit(tmp_path: Path) -> None:
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0")
    api = FakeGitLab()

    repo.prepare(api)

    assert repo.log() == ["chore: seed"]
    assert len(api.created) == 1


def test_a_rerun_changes_nothing(tmp_path: Path) -> None:
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0-dev")
    api = FakeGitLab()
    first = repo.prepare(api)

    # The re-run starts from the pipeline's own commit, which is still the
    # unbumped one: a retried job checks out the SHA it was created for.
    second = repo.prepare(api, root=repo.clone(tmp_path / "rerun"))
    third = repo.prepare(api, root=repo.checkout)

    assert repo.log() == ["chore: bump workspace to 0.6.0", "chore: seed"]
    assert len(api.created) == 1
    assert first == second == third


def test_a_stale_lockfile_alone_is_bumped(tmp_path: Path) -> None:
    """Every version site at X.Y.Z is not enough: the locks have to follow."""
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0", locks="0.6.0-dev")

    repo.prepare(FakeGitLab())

    assert repo.log() == ["chore: bump workspace to 0.6.0", "chore: seed"]
    assert (repo.checkout / "uv.lock").read_text() == "0.6.0\n"


def test_a_bump_pushed_by_hand_mid_job_wins(tmp_path: Path) -> None:
    """The job's push is rejected; it checks the remote, finds the bump there, and passes."""
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0-dev")
    repo.push_from_elsewhere(tmp_path, "0.6.0", "chore: bump workspace to 0.6.0")
    api = FakeGitLab()

    repo.prepare(api)

    assert repo.log() == ["chore: bump workspace to 0.6.0", "chore: seed"]
    assert repo.tip("%an").strip() == "maintainer"
    assert len(api.created) == 1


def test_a_rejected_push_onto_an_unbumped_tip_fails(tmp_path: Path) -> None:
    """Someone pushed a fix, not a bump. Never force: fail, and let the fix's own pipeline bump."""
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0-dev")
    repo.push_from_elsewhere(tmp_path, "0.6.0-dev", "fix: something on the release branch")
    api = FakeGitLab()

    with pytest.raises(pr.BumpError, match="could not push"):
        repo.prepare(api)

    assert repo.log() == ["fix: something on the release branch", "chore: seed"]
    assert api.created == []


def test_losing_the_race_to_open_the_merge_request_passes(tmp_path: Path) -> None:
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0")
    api = FakeGitLab()
    api.lose_race_with = {
        "source_branch": "release/v0.6.0",
        "source_project_id": 1,
        "web_url": "https://gitlab.example/mr/other",
    }

    assert repo.prepare(api) == "https://gitlab.example/mr/other"
    assert api.created == []


def test_a_merge_request_from_a_fork_is_not_the_release(tmp_path: Path) -> None:
    """GitLab's source_branch filter matches a fork's branch of the same name too."""
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0")
    api = FakeGitLab()
    api.merge_requests.append(
        {"source_branch": "release/v0.6.0", "source_project_id": 2, "web_url": "https://gitlab.example/mr/fork"}
    )

    url = repo.prepare(api)

    assert [mr["web_url"] for mr in api.created] == [url]
    assert url != "https://gitlab.example/mr/fork"


def test_a_conflict_with_no_merge_request_behind_it_fails(tmp_path: Path) -> None:
    """A fork's merge request from the same branch name does not explain the 409."""
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0")
    api = FakeGitLab()
    api.conflict_without_merge_request = True
    api.merge_requests.append(
        {"source_branch": "release/v0.6.0", "source_project_id": 2, "web_url": "https://gitlab.example/mr/fork"}
    )

    with pytest.raises(pr.ApiError, match="409"):
        repo.prepare(api)


def test_an_unknown_reviewer_fails_before_the_merge_request_is_opened(tmp_path: Path) -> None:
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0")
    api = FakeGitLab()

    with pytest.raises(pr.SetupError, match="carol"):
        pr.prepare(repo.checkout, repo.branch, api, "1", str(repo.remote), "gitlab.example", ["alice", "carol"])
    assert api.created == []


def test_a_failed_bump_pushes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo(tmp_path, "release/v0.6.0", "0.6.0-dev")
    monkeypatch.setenv("STUB_SET_VERSION_FAILS", "1")
    api = FakeGitLab()

    with pytest.raises(pr.BumpError, match="set-version"):
        repo.prepare(api)

    assert repo.log() == ["chore: seed"]
    assert api.created == []


@pytest.mark.parametrize("branch", ["release/v0.6.0-rc1", "release/0.6.0", "feature/qui-2216", "release/v0.6"])
def test_only_a_plain_release_branch_is_prepared(branch: str) -> None:
    with pytest.raises(pr.SetupError, match="not a release branch"):
        pr.version_from_branch(branch)


@pytest.mark.parametrize(
    ("version", "patch", "ticked", "unticked"),
    [("0.6.0", 0, "Minor", "Patch"), ("0.5.3", 3, "Patch", "Minor")],
)
def test_the_real_template_is_filled_and_ticked(version: str, patch: int, ticked: str, unticked: str) -> None:
    description = pr.release_description(REAL_TEMPLATE.read_text(encoding="utf-8"), version, patch)

    assert f"- [x] **{ticked}**" in description
    assert f"- [ ] **{unticked}**" in description
    assert "X.Y.Z" not in description
    assert f"release/v{version}" in description
    assert "/assign_reviewer" not in description


def test_a_template_without_the_release_type_box_is_a_setup_error() -> None:
    with pytest.raises(pr.SetupError, match="Patch"):
        pr.release_description("## Release vX.Y.Z\n", "0.5.3", 3)


def test_the_push_remote_carries_the_token_and_output_never_does(capsys: pytest.CaptureFixture[str]) -> None:
    remote = pr._push_remote("https://gitlab.example/group/xquad", "glpat-s3cr/t")
    assert remote == "https://oauth2:glpat-s3cr%2Ft@gitlab.example/group/xquad.git"

    pr._SECRETS.extend(["glpat-s3cr/t", "glpat-s3cr%2Ft"])
    pr._run(["echo", remote], Path.cwd())

    out = capsys.readouterr().out
    assert "s3cr" not in out
    assert "https://oauth2:[MASKED]@gitlab.example/group/xquad.git" in out
