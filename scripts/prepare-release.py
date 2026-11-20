#!/usr/bin/env python3
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
#
# Release branch preparation (QUI-2216).
#
# Invoked by release:prepare (.gitlab/ci/release.yml) on a push to a
# `release/vX.Y.Z` branch. It does two things a maintainer used to do by
# hand:
#
#   1. Bump. If the branch's tree is not already at X.Y.Z with current
#      lockfiles, run `make set-version`, regenerate Cargo.lock and
#      uv.lock, commit `chore: bump workspace to X.Y.Z` with a sign-off,
#      and push it to the branch. Never with force: a rejected push means
#      the branch moved, so the remote tip is fetched and checked instead,
#      and the run passes if someone else's bump is already there.
#   2. Open the release merge request. If none is open from the branch,
#      open a draft into `main` titled `release: vX.Y.Z`, with the
#      reviewers set and the release template filled in.
#
# Both steps are idempotent, so a re-run, a bump pushed by hand before
# CI's, or two runs racing each other all end with one bump commit and
# one merge request. The bump commit carries no `ci.skip`: its own
# pipeline is the one that checks it, and that run finds nothing to bump.
#
# The pushes and the merge request are made with GITLAB_TOKEN, the
# project access token release:auto-tag already uses. Its bot user is
# the merge request's author, which is the point: GitLab blocks an
# author's own approval, so a release opened by a maintainer could only
# ever be approved by the other one.
#
# "Current" is three checks:
#
#   * `make check-version-sites TAG=vX.Y.Z` -- every version site, the
#     pallet fixture's lock entry for xqvm among them. That entry is the
#     only part of the fixture's lock a version bump changes, and
#     `make set-version` writes it, so the fixture is never resolved here;
#     resolving it would clone polkadot-sdk.
#   * `cargo update --workspace --locked` -- fails when Cargo.lock still
#     names the old workspace versions.
#   * `uv lock --check`, which must come after Cargo.lock: uv asks
#     maturin for xqffi's metadata, and maturin runs `cargo metadata
#     --locked`.
#
# Standard library only. The Makefile runs repository scripts under
# `uv run --no-project --isolated`, where nothing else is installed.
#
# Environment (set by GitLab CI):
#   CI_COMMIT_BRANCH  the pushed branch, `release/vX.Y.Z`
#   CI_API_V4_URL     the GitLab API root
#   CI_PROJECT_ID     the project the merge request is opened in
#   CI_PROJECT_URL    the repository the bump is pushed to
#   GITLAB_TOKEN      the project access token (api, write_repository)
#   RELEASE_REVIEWERS space-separated usernames to request review from
#
# Exit codes: 0 done (bumped or not, merge request opened or found),
# 1 the bump could not be made or pushed, or a GitLab call failed,
# 2 setup error.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(".gitlab/merge_request_templates/release.md")
TARGET_BRANCH = "main"

# Three numeric fields and nothing else, as release:auto-tag reads it. A
# release candidate is cut on a throwaway branch, never on release/*
# (docs/guide/gitflow-protocol.md), so a suffix here is a mistake.
_BRANCH_RE = re.compile(r"^release/v(\d+)\.(\d+)\.(\d+)$")

# GitLab answers 409 when an open merge request already exists for the
# source branch, which is how a second run racing the first finds out.
_CONFLICT = 409


class SetupError(Exception):
    """The run cannot start: a missing variable, a bad branch name, an unreadable template."""


class BumpError(Exception):
    """The bump could not be made, or could not reach the branch."""


class ApiError(Exception):
    """A GitLab API call failed."""

    def __init__(self, status: int, method: str, path: str, body: str) -> None:
        super().__init__(f"GitLab API {method} {path} returned {status}: {body}")
        self.status = status


def version_from_branch(branch: str) -> tuple[str, int]:
    """The version a release branch names, and its patch number.

    Raises:
        SetupError: if `branch` is not `release/vX.Y.Z`.
    """
    match = _BRANCH_RE.match(branch)
    if match is None:
        raise SetupError(f"{branch!r} is not a release branch; expected release/vX.Y.Z")
    major, minor, patch = match.groups()
    return f"{major}.{minor}.{patch}", int(patch)


def release_description(template: str, version: str, patch: int) -> str:
    """The release template with the version filled in and the release type ticked.

    A patch release (Z > 0) is cut from `main` and a minor (Z == 0) from
    `dev`, so the type follows from the version alone.

    Raises:
        SetupError: if the template has lost the release-type box this ticks.
    """
    kind = "Patch" if patch > 0 else "Minor"
    box = f"- [ ] **{kind}**"
    if template.count(box) != 1:
        raise SetupError(f"{TEMPLATE} must carry exactly one {box!r} line, to tick for v{version}")
    return template.replace("X.Y.Z", version).replace(box, f"- [x] **{kind}**")


# Values never printed. The push remote embeds the token, and both the
# echoed command line and git's own error messages can repeat a remote URL.
_SECRETS: list[str] = []


def _redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "[MASKED]")
    return text


def _run(cmd: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    """Run `cmd` in `root`, echoing it and its output to the job log, redacted."""
    print(_redact(f"$ {' '.join(cmd)}"), flush=True)
    result = subprocess.run(cmd, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    print(_redact(result.stdout), end="", flush=True)
    return result


def _run_quiet(cmd: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    """Run `cmd` in `root`, capturing its output for the caller to report."""
    return subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=False)


def _must(cmd: list[str], root: Path) -> None:
    """Run `cmd` and raise BumpError if it fails."""
    if _run(cmd, root).returncode != 0:
        raise BumpError(f"`{' '.join(cmd)}` failed")


def stale_parts(root: Path, version: str) -> list[str]:
    """What is not yet at `version` in the tree at `root`; empty when the bump is done."""
    checks = (
        ("version sites", ["make", "-s", "check-version-sites", f"TAG=v{version}"]),
        ("Cargo.lock", ["cargo", "update", "--workspace", "--locked"]),
        ("uv.lock", ["uv", "lock", "--check"]),
    )
    stale = []
    for name, cmd in checks:
        if _run_quiet(cmd, root).returncode != 0:
            stale.append(name)
    return stale


def bump(root: Path, version: str) -> None:
    """Write `version` to every site and regenerate both lockfiles.

    Raises:
        BumpError: if a step fails, or the tree is still not current afterwards.
    """
    _must(["make", "-s", "set-version", f"VERSION={version}"], root)
    _must(["cargo", "update", "--workspace"], root)
    _must(["uv", "lock"], root)
    stale = stale_parts(root, version)
    if stale:
        raise BumpError(f"after the bump, {', '.join(stale)} still not at {version}")


@dataclass(frozen=True)
class Identity:
    """Who the bump commit is authored and signed off by."""

    name: str
    email: str


def commit_bump(root: Path, version: str, who: Identity) -> bool:
    """Commit every tracked change as the bump, signed off by `who`. False when there was nothing to commit.

    Nothing to commit means the tree was current all along and a check
    only failed for another reason, such as cargo failing to reach the index.
    """
    _must(["git", "add", "--update"], root)
    if _run_quiet(["git", "diff", "--cached", "--quiet"], root).returncode == 0:
        return False
    _must(
        [
            "git",
            "-c",
            f"user.name={who.name}",
            "-c",
            f"user.email={who.email}",
            "commit",
            "--signoff",
            "--quiet",
            "--message",
            f"chore: bump workspace to {version}",
        ],
        root,
    )
    return True


def push_bump(root: Path, remote: str, branch: str) -> bool:
    """Push HEAD to `branch` without force. False when the push is rejected."""
    return _run(["git", "push", remote, f"HEAD:refs/heads/{branch}"], root).returncode == 0


def remote_tip_is_current(root: Path, remote: str, branch: str, version: str) -> bool:
    """Check out the branch's remote tip and report whether it is already bumped."""
    _must(["git", "fetch", "--quiet", remote, f"refs/heads/{branch}"], root)
    _must(["git", "checkout", "--quiet", "--force", "--detach", "FETCH_HEAD"], root)
    stale = stale_parts(root, version)
    if stale:
        print(f"the remote tip of {branch} is not bumped either: {', '.join(stale)}", flush=True)
    return not stale


class Api(Protocol):
    """The GitLab calls this script makes."""

    def get(self, path: str, query: dict[str, str] | None = None) -> Any: ...

    def post(self, path: str, body: dict[str, Any]) -> Any: ...


class GitLab:
    """A minimal GitLab REST client authenticated with a project access token."""

    def __init__(self, api_url: str, token: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._token = token

    def _request(self, method: str, path: str, data: bytes | None = None) -> Any:
        request = urllib.request.Request(f"{self._api_url}/{path}", data=data, method=method)
        request.add_header("PRIVATE-TOKEN", self._token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, method, path.split("?", 1)[0], exc.read().decode(errors="replace")) from exc
        except urllib.error.URLError as exc:
            # No HTTP status at all: the API was not reached.
            raise ApiError(0, method, path.split("?", 1)[0], str(exc.reason)) from exc

    def get(self, path: str, query: dict[str, str] | None = None) -> Any:
        suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
        return self._request("GET", f"{path}{suffix}")

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("POST", path, json.dumps(body).encode())


def bot_identity(api: Api, server_host: str) -> Identity:
    """The token's own user, as a commit identity."""
    user = api.get("user")
    email = user.get("commit_email") or user.get("email") or f"{user['username']}@noreply.{server_host}"
    return Identity(name=user.get("name") or user["username"], email=email)


def open_merge_requests(api: Api, project: str, branch: str) -> list[dict[str, Any]]:
    """The merge requests open from `branch` in `project` itself, into any target.

    The API's `source_branch` filter also matches merge requests opened
    from a fork's branch of the same name. One of those is not the
    release, so it is left out here: otherwise a fork's `release/vX.Y.Z`
    would be reported as the release merge request and ours never opened.
    """
    found = api.get(f"projects/{project}/merge_requests", {"state": "opened", "source_branch": branch})
    return [mr for mr in found if mr["source_project_id"] == int(project)]


def reviewer_ids(api: Api, usernames: list[str]) -> list[int]:
    """GitLab user ids for `usernames`.

    Raises:
        SetupError: if a username matches no user.
    """
    ids = []
    for username in usernames:
        found = api.get("users", {"username": username})
        if not found:
            raise SetupError(f"RELEASE_REVIEWERS names {username!r}, which matches no GitLab user")
        ids.append(found[0]["id"])
    return ids


def ensure_merge_request(
    api: Api, project: str, branch: str, version: str, reviewers: list[str], description: str
) -> str:
    """Open the draft release merge request unless one is open from `branch`; return its URL."""
    existing = open_merge_requests(api, project, branch)
    if existing:
        print(f"a merge request is already open from {branch}: {existing[0]['web_url']}", flush=True)
        return existing[0]["web_url"]
    body = {
        "source_branch": branch,
        "target_branch": TARGET_BRANCH,
        "title": f"Draft: release: v{version}",
        "description": description,
        "reviewer_ids": reviewer_ids(api, reviewers),
        # A release merge never squashes (RELEASING.md). Stated here rather
        # than left to the project default, which a settings change could flip.
        "squash": False,
    }
    try:
        created = api.post(f"projects/{project}/merge_requests", body)
    except ApiError as exc:
        if exc.status != _CONFLICT:
            raise
        existing = open_merge_requests(api, project, branch)
        if not existing:
            raise
        print(f"another run opened the merge request first: {existing[0]['web_url']}", flush=True)
        return existing[0]["web_url"]
    print(f"opened {created['web_url']}", flush=True)
    return created["web_url"]


def prepare(
    root: Path, branch: str, api: Api, project: str, remote: str, server_host: str, reviewers: list[str]
) -> str:
    """Bump `branch` if it needs it, then make sure its release merge request is open. Returns the MR's URL.

    Raises:
        SetupError: if the branch name or the template is unusable.
        BumpError: if the bump fails, or is rejected and the remote is not bumped either.
        ApiError: if a GitLab call fails.
    """
    version, patch = version_from_branch(branch)
    try:
        template = (root / TEMPLATE).read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"cannot read {TEMPLATE}: {exc}") from exc
    description = release_description(template, version, patch)

    stale = stale_parts(root, version)
    if not stale:
        print(f"{branch} is already at {version} with current lockfiles; no bump", flush=True)
    else:
        print(f"{branch} needs a bump: {', '.join(stale)} not at {version}", flush=True)
        bump(root, version)
        if not commit_bump(root, version, bot_identity(api, server_host)):
            print(f"the bump changed nothing; {branch} was already at {version}", flush=True)
        elif push_bump(root, remote, branch):
            print(f"pushed chore: bump workspace to {version}", flush=True)
        elif remote_tip_is_current(root, remote, branch, version):
            print(f"push rejected, but {branch} already carries a bump to {version}", flush=True)
        else:
            raise BumpError(f"could not push the bump to {branch}, and its tip is not bumped")

    return ensure_merge_request(api, project, branch, version, reviewers, description)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SetupError(f"{name} is not set")
    return value


def _push_remote(project_url: str, token: str) -> str:
    """The HTTPS remote for `project_url`, authenticated with the project access token.

    CI's own checkout authenticates with CI_JOB_TOKEN, which cannot push.
    """
    parts = urllib.parse.urlsplit(project_url)
    netloc = f"oauth2:{urllib.parse.quote(token, safe='')}@{parts.netloc}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, f"{parts.path}.git", "", ""))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Bump a release branch and open its release MR (QUI-2216).")
    parser.add_argument("--root", default=None, help="the release branch's checkout; defaults to this repository")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else REPO_ROOT

    try:
        branch = _require_env("CI_COMMIT_BRANCH")
        token = _require_env("GITLAB_TOKEN")
        _SECRETS.extend([token, urllib.parse.quote(token, safe="")])
        project_url = _require_env("CI_PROJECT_URL")
        url = prepare(
            root,
            branch,
            GitLab(_require_env("CI_API_V4_URL"), token),
            _require_env("CI_PROJECT_ID"),
            _push_remote(project_url, token),
            urllib.parse.urlsplit(project_url).hostname or "gitlab.com",
            _require_env("RELEASE_REVIEWERS").split(),
        )
    except SetupError as exc:
        print(f"error: prepare-release: {exc}", file=sys.stderr)
        return 2
    except (BumpError, ApiError) as exc:
        print(f"error: prepare-release: {exc}", file=sys.stderr)
        return 1
    print(f"release merge request: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
