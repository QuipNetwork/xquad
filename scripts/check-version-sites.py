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
# Version site guard.
#
# Fails a tag pipeline when the tag and the workspace manifests disagree
# about what version is being released (QUI-1146).
#
# Nothing else in the release pipeline compares the two. `.on-release-tag`
# (.gitlab/ci/release.yml) matches /^v\d/, which tests the tag's shape and
# nothing else, and `release:validate` runs `make -k check-release` without
# ever reading $CI_COMMIT_TAG. Until 2026-08-21 the mismatch failed by
# accident: main carried the last released version, so a wrong tag made
# `cargo publish -p xqvm` fail on an already-published version. Main now
# carries the next version with a prerelease suffix, so tagging v0.4.0
# against it would publish `xqvm 0.4.0-dev` as a novel version -- green
# pipeline, and cargo has no unpublish.
#
# This guard is what stands in front of the manual tag path documented in
# RELEASING.md ("Legacy manual flow"), which has no merge request and no
# review, and which is the path release candidates use.
#
# The site table below is the single source of truth for where a release
# version is written. RELEASING.md's bump step points here rather than
# restating the list, so the prose and the check cannot drift.
#
# Two spellings, one version
# --------------------------
# Cargo wants SemVer (0.4.0-dev, 0.4.0-rc1) and Python wants PEP 440
# (0.4.0.dev0, 0.4.0rc1). maturin performs no conversion between them: it
# hands the raw Cargo string to a PEP 440 parser and renders the normalised
# form (maturin 1.13.3, src/metadata.rs). So this guard has one
# canonicaliser, PEP 440's, applied to the tag and to both ecosystems.
#
# Observed with `maturin sdist --manifest-path xqffi/Cargo.toml`, maturin
# 1.13.3, 2026-08-26 -- xqffi/pyproject.toml declares dynamic = ["version"]
# and maturin sources it from xqffi/Cargo.toml, so this one site feeds both
# ecosystems and the mapping had to be measured rather than assumed:
#
#     Cargo 0.4.0-dev      ->  xqffi-0.4.0.dev0.tar.gz   Version: 0.4.0.dev0
#     Cargo 0.4.0-alpha.1  ->  xqffi-0.4.0a1.tar.gz      Version: 0.4.0a1
#     Cargo 0.4.0-rc1      ->  xqffi-0.4.0rc1.tar.gz     Version: 0.4.0rc1
#
# Comparison is two-layer. Agreement: every site must canonicalise to the
# tag's canonical form. Spelling: Python sites must additionally match that
# canonical form exactly, because 0.4.0.dev and 0.4.0.dev0 are PEP 440
# equivalent but produce different wheel filenames. Cargo sites are held to
# valid SemVer plus agreement instead, because 0.4.0-alpha.1 is the correct
# authored string and canonicalising it back would demand 0.4.0-a1.
#
# What runs when
# --------------
# The tag comparison is gated on a tag being present, so merge request and
# branch pipelines are unaffected. The two table sweeps run always: they
# compare no versions, they assert the table still describes the tree, and
# they exist so that a crate added in September is caught on that merge
# request rather than at tag time in November.
#
# `release:validate` carries no `rules:`, so it sees every tag, not only
# release tags. A tag that does not match ^v<digit> is skipped: it cannot
# trigger `release:crates` or `release:pypi`, so failing would break an
# unrelated tag for no safety gain. A tag that does match but is not
# expressible in PEP 440 (v0.4.0-SNAPSHOT) is a setup error, because it
# will trigger the publish chain.
#
# That skip applies to the tag read from $CI_COMMIT_TAG, which is a real
# pushed ref whose publishing behaviour `.on-release-tag` decides. An
# explicit positional argument is not a ref at all -- it is a releaser
# running the guard by hand -- so it additionally accepts the bare
# `0.4.0-rc1` spelling, where demanding the `v` is friction with no safety
# content. An empty positional means what no positional means: fall back
# to the environment. `make check-version-sites` with no TAG passes one,
# so that equivalence is what makes the guard run at all in CI.
#
# This guard reads $CI_COMMIT_TAG and files on disk. It never shells out to
# git, so shallow clones are not a consideration.
#
# Usage:
#   scripts/check-version-sites.py [TAG]      # empty or absent TAG falls back
#                                             # to $CI_COMMIT_TAG
#   scripts/check-version-sites.py --list     # print every site and its value
#   scripts/check-version-sites.py --print-version
#                                             # canonical PEP 440 version of
#                                             # the tree, for smoke-wheels.sh
#   scripts/check-version-sites.py --root DIR # check a tree other than this one
#
# Exit codes:
#   0  -- pass, or no tag to check
#   1  -- a version site disagrees with the tag
#   2  -- usage error, or the site table no longer describes the tree

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories a manifest sweep must not walk into. Build output, virtual
# environments and dependency caches carry vendored manifests by the
# thousand, and none of them is a version site of ours.
#
# The four dot-directories are not incidental. CI points CARGO_HOME at
# ${CI_PROJECT_DIR}/.cargo and UV_CACHE_DIR at ${CI_PROJECT_DIR}/.uv-cache
# (.gitlab-ci.yml, .gitlab/ci/setup.yml) so both caches survive as job
# artefacts -- which puts the whole crates.io registry and every uv sdist
# build inside the tree this sweep walks. Locally, per-ticket worktrees
# live under .orca/worktrees/ and .claude/worktrees/ -- two tools, two
# roots, and either one a full checkout of this repo and so a second copy
# of every declared site. All four are absent from a clean clone and
# present in exactly the environments that run this guard: RELEASING.md
# and the release MR template both send the releaser here by hand, so a
# worktree open on unrelated work must not be able to fail the check.
PRUNE_DIRS = frozenset(
    {".git", ".venv", ".cargo", ".uv-cache", ".orca", ".claude", "target", "dist", "node_modules", "graphify-out"}
)


# --- Version parsing -------------------------------------------------------

# PEP 440 Appendix B's published regex, verbatim, so the parse this guard
# performs is the one the packaging ecosystem performs. Kept as re.VERBOSE
# rather than reflowed to fit the line-length lint: it is quoted source, and
# a hand-rewrapped copy is a copy that can be wrong.
VERSION_PATTERN = r"""
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?                           # epoch
        (?P<release>[0-9]+(?:\.[0-9]+)*)                  # release segment
        (?P<pre>                                          # pre-release
            [-_\.]?
            (?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)
            [-_\.]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>                                         # post release
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [-_\.]?
                (?P<post_l>post|rev|r)
                [-_\.]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>                                          # dev release
            [-_\.]?
            (?P<dev_l>dev)
            [-_\.]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?       # local version
"""

_PEP440_RE = re.compile(r"^\s*" + VERSION_PATTERN + r"\s*$", re.VERBOSE | re.IGNORECASE)

# SemVer 2.0.0's published regex. Anchored, so `0.4.0.dev0` -- four dotted
# components, which cargo itself rejects -- fails loudly in a Cargo.toml.
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_PRE_SPELLING = {"alpha": "a", "a": "a", "beta": "b", "b": "b", "c": "rc", "pre": "rc", "preview": "rc", "rc": "rc"}


def canon(raw: str) -> str | None:
    """Return the PEP 440 normalised form of `raw`, or None if it is not a PEP 440 version."""
    match = _PEP440_RE.match(raw)
    if match is None:
        return None
    parts = match.groupdict()

    out = ""
    epoch = int(parts["epoch"] or 0)
    if epoch:
        out += f"{epoch}!"
    out += ".".join(str(int(segment)) for segment in parts["release"].split("."))

    if parts["pre_l"] is not None:
        out += _PRE_SPELLING[parts["pre_l"].lower()] + str(int(parts["pre_n"] or 0))
    if parts["post_n1"] is not None:
        out += f".post{int(parts['post_n1'])}"
    elif parts["post_l"] is not None:
        out += f".post{int(parts['post_n2'] or 0)}"
    if parts["dev_l"] is not None:
        out += f".dev{int(parts['dev_n'] or 0)}"
    if parts["local"] is not None:
        out += "+" + ".".join(re.split(r"[-_.]", parts["local"].lower()))
    return out


def is_semver(raw: str) -> bool:
    """Whether `raw` is a valid SemVer 2.0.0 version, which is what cargo accepts."""
    return _SEMVER_RE.match(raw) is not None


def normalise_dist(name: str) -> str:
    """PEP 503 name normalisation. uv.lock writes `xqvm-py` where the manifests write `xqvm_py`."""
    return re.sub(r"[-_.]+", "-", name).lower()


_REQUIREMENT_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<spec>.*)$")


def parse_requirement(raw: str) -> tuple[str, tuple[str, ...], str] | None:
    """Split a PEP 508 requirement into (name, extras, specifier). Environment markers are discarded."""
    match = _REQUIREMENT_RE.match(raw.split(";", 1)[0].strip())
    if match is None:
        return None
    extras = tuple(extra.strip() for extra in (match["extras"] or "").split(",") if extra.strip())
    return match["name"], extras, match["spec"].strip()


# --- Site table ------------------------------------------------------------


class Eco(StrEnum):
    """Which of the two spellings a site is written in."""

    CARGO = "cargo"
    PYTHON = "python"


class Role(StrEnum):
    """What kind of site it is. Shown in `--list` and in every finding line."""

    PACKAGE_VERSION = "package version"
    PROJECT_VERSION = "project version"
    WORKSPACE_DEP = "workspace dependency"
    PEER_PIN = "peer pin"
    MODULE_DUNDER = "__version__"
    LOCK_ENTRY = "locked version"


@dataclass(frozen=True)
class Found:
    """A version string read out of a file, with where it was written."""

    value: str
    lineno: int | None
    display: str


@dataclass(frozen=True)
class Site:
    """One place a release version is written.

    `path` is repo-relative. `dist` names the distribution or crate the value
    belongs to, and is used both to locate the value and to label it.
    """

    path: str
    eco: Eco
    role: Role
    dist: str = ""
    extras: tuple[str, ...] = ()
    table: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        if self.role is Role.PEER_PIN:
            extras = f"[{','.join(self.extras)}]" if self.extras else ""
            return f"peer pin {self.dist}{extras}"
        if self.role in (Role.WORKSPACE_DEP, Role.LOCK_ENTRY):
            return f"{self.role} {self.dist}"
        return str(self.role)

    @property
    def pin_name(self) -> str:
        extras = f"[{','.join(self.extras)}]" if self.extras else ""
        return f"{self.dist}{extras}"


_CRATES = ("xqvm", "xqasm", "xqcli", "xqffi", "conformance")

SITES: tuple[Site, ...] = (
    # The `version` alongside `path` on the two workspace dependency aliases.
    # cargo rewrites the spec to drop the path on publish, so `cargo publish`
    # fails without them. The root has no [workspace.package] version.
    #
    # Note what is deliberately NOT here: xqvm/Cargo.toml's dev-dependency on
    # xqasm is path-only and must stay version-less. cargo strips
    # dev-dependencies from the published tarball, and xqvm publishes before
    # xqasm, so a version there makes xqvm's dry-run chase a crates.io version
    # that does not exist yet. Its own comment says so; do not "complete" the
    # table by adding it.
    Site("Cargo.toml", Eco.CARGO, Role.WORKSPACE_DEP, dist="xqvm", table=("workspace", "dependencies")),
    Site("Cargo.toml", Eco.CARGO, Role.WORKSPACE_DEP, dist="xqasm", table=("workspace", "dependencies")),
    # Every crate manifest. conformance and xqffi are publish = false, but
    # xqffi is what maturin stamps on the PyPI wheel and conformance is
    # packaged by `cargo publish --dry-run --workspace`.
    *(Site(f"{crate}/Cargo.toml", Eco.CARGO, Role.PACKAGE_VERSION, dist=crate) for crate in _CRATES),
    # The three hatchling packages that carry a literal version. xqvm_py and
    # xqffi are dynamic and appear under ASSERTIONS instead.
    Site("xqcp/pyproject.toml", Eco.PYTHON, Role.PROJECT_VERSION, dist="xqcp"),
    Site("xqsa/pyproject.toml", Eco.PYTHON, Role.PROJECT_VERSION, dist="xqsa"),
    Site("xquad/pyproject.toml", Eco.PYTHON, Role.PROJECT_VERSION, dist="xquad"),
    # xqvm_py's version lives here, not in its pyproject: it declares
    # dynamic = ["version"] and hatch reads this file.
    Site("xqvm_py/__init__.py", Eco.PYTHON, Role.MODULE_DUNDER, dist="xqvm_py"),
    # Exact peer pins. The five distributions share one workspace version and
    # are uploaded as a set, so a mixed-version install is never supported.
    Site("xqcp/pyproject.toml", Eco.PYTHON, Role.PEER_PIN, dist="xqvm_py", table=("project", "dependencies")),
    Site("xqsa/pyproject.toml", Eco.PYTHON, Role.PEER_PIN, dist="xqvm_py", table=("project", "dependencies")),
    Site("xqvm_py/pyproject.toml", Eco.PYTHON, Role.PEER_PIN, dist="xqffi", table=("project", "dependencies")),
    *(
        Site("xquad/pyproject.toml", Eco.PYTHON, Role.PEER_PIN, dist=dist, table=("project", "dependencies"))
        for dist in ("xqffi", "xqcp", "xqsa", "xqvm_py")
    ),
    *(
        Site(
            "xquad/pyproject.toml",
            Eco.PYTHON,
            Role.PEER_PIN,
            dist="xqsa",
            extras=(extra,),
            table=("project", "optional-dependencies", extra),
        )
        for extra in ("cuda", "dwave", "metal", "quip")
    ),
    # uv.lock's internal consistency (every workspace member's locked entry
    # agreeing with its manifest) is now enforced by `uv lock --check`
    # (`make check-uv-lock`, run by `verify:python` and `preflight-py`), so
    # entries here would be redundant duplication of what uv already checks.
    # The pallet fixture's standalone lock. RELEASING.md's bump step already
    # says a stale xqvm entry here "drifts silently for releases", because the
    # fixture is an excluded workspace that no job builds with --locked.
    Site("fixtures/pallet-xqvm/Cargo.lock", Eco.CARGO, Role.LOCK_ENTRY, dist="xqvm"),
)


class AssertKind(StrEnum):
    """The two shapes of "this site must carry no version" invariant."""

    DYNAMIC_PYPROJECT = "dynamic-pyproject"
    LOCK_NO_VERSION = "lock-no-version"


@dataclass(frozen=True)
class Assertion:
    """A site whose invariant is the absence of a version, not its value."""

    path: str
    kind: AssertKind
    dist: str
    note: str


ASSERTIONS: tuple[Assertion, ...] = (
    Assertion(
        "xqffi/pyproject.toml",
        AssertKind.DYNAMIC_PYPROJECT,
        "xqffi",
        "maturin sources the version from xqffi/Cargo.toml; a literal here is ignored and drifts forever",
    ),
    Assertion(
        "xqvm_py/pyproject.toml",
        AssertKind.DYNAMIC_PYPROJECT,
        "xqvm_py",
        "hatch sources the version from xqvm_py/__init__.py; a literal here is ignored and drifts forever",
    ),
    Assertion(
        "uv.lock",
        AssertKind.LOCK_NO_VERSION,
        "xqffi",
        "uv records no version for a dynamic package; if that changes this becomes an unguarded site",
    ),
    Assertion(
        "uv.lock",
        AssertKind.LOCK_NO_VERSION,
        "xqvm-py",
        "uv records no version for a dynamic package; if that changes this becomes an unguarded site",
    ),
)

# Manifests that exist but are not version sites, each with the reason. The
# sweep below requires every manifest in the tree to be either declared above
# or listed here, so a new crate cannot arrive unnoticed.
EXCLUDED: dict[str, str] = {
    "fixtures/pallet-xqvm/Cargo.toml": "standalone excluded workspace; its own 0.1.0 is never published",
    "fixtures/xqvm-wasm/Cargo.toml": "no_std build fixture, publish = false, permanently 0.0.0",
    "pyproject.toml": "uv workspace root; declares [tool.uv.workspace] members only, no [project]",
}


# --- Reading ---------------------------------------------------------------


class SetupError(Exception):
    """The tree does not look the way the site table says it does. Exit 2, not 1."""


def load_toml(root: Path, rel: str) -> dict:
    path = root / rel
    if not path.is_file():
        raise SetupError(f"{rel}: declared in the site table but missing from the tree")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SetupError(f"{rel}: not valid TOML: {exc}") from exc


def load_text(root: Path, rel: str) -> str:
    path = root / rel
    if not path.is_file():
        raise SetupError(f"{rel}: declared in the site table but missing from the tree")
    return path.read_text(encoding="utf-8")


def find_line(text: str, *needles: str) -> int | None:
    """First 1-based line containing every needle, or None.

    Values come from the TOML parse, which is authoritative; this only
    recovers where the value was written so a finding can be clicked. A miss
    degrades the report to `path:?` and never to a false pass -- do not turn
    it into a hard failure.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        if all(needle in line for needle in needles):
            return number
    return None


def walk(doc: dict, keys: tuple[str, ...], rel: str) -> object:
    node: object = doc
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise SetupError(f"{rel}: no [{'.'.join(keys[:-1])}] {keys[-1]}")
        node = node[key]
    return node


def read_lock_entry(root: Path, rel: str, dist: str) -> tuple[str | None, int | None]:
    """Return (version, lineno) for a [[package]] entry, with version None when the entry carries none."""
    doc = load_toml(root, rel)
    wanted = normalise_dist(dist)
    for entry in doc.get("package", []):
        if normalise_dist(entry.get("name", "")) != wanted:
            continue
        version = entry.get("version")
        lineno = None
        if version is not None:
            text = load_text(root, rel)
            lineno = find_line(text, f'version = "{version}"')
            name_line = find_line(text, f'name = "{entry["name"]}"')
            if name_line is not None:
                for offset in range(name_line, min(name_line + 6, len(text.splitlines()) + 1)):
                    candidate = text.splitlines()[offset - 1]
                    if candidate.strip().startswith("version ="):
                        lineno = offset
                        break
        return version, lineno
    raise SetupError(f"{rel}: no [[package]] entry named {dist}")


def read_site(root: Path, site: Site) -> Found:
    """Read one site's authored version string."""
    if site.role is Role.MODULE_DUNDER:
        text = load_text(root, site.path)
        match = re.search(r'^__version__\s*=\s*"(?P<value>[^"]+)"', text, re.MULTILINE)
        if match is None:
            raise SetupError(f"{site.path}: no __version__ assignment")
        value = match["value"]
        return Found(value, find_line(text, f'__version__ = "{value}"'), value)

    if site.role is Role.LOCK_ENTRY:
        value, lineno = read_lock_entry(root, site.path, site.dist)
        if value is None:
            raise SetupError(f"{site.path}: [[package]] {site.dist} carries no version")
        return Found(value, lineno, value)

    doc = load_toml(root, site.path)
    text = load_text(root, site.path)

    if site.role is Role.PACKAGE_VERSION:
        value = walk(doc, ("package", "version"), site.path)
        if not isinstance(value, str):
            raise SetupError(f"{site.path}: [package] version is not a string")
        return Found(value, find_line(text, "version", f'"{value}"'), value)

    if site.role is Role.PROJECT_VERSION:
        value = walk(doc, ("project", "version"), site.path)
        if not isinstance(value, str):
            raise SetupError(f"{site.path}: [project] version is not a string")
        return Found(value, find_line(text, "version", f'"{value}"'), value)

    if site.role is Role.WORKSPACE_DEP:
        value = walk(doc, (*site.table, site.dist, "version"), site.path)
        if not isinstance(value, str):
            raise SetupError(f"{site.path}: [{'.'.join(site.table)}] {site.dist} has no version string")
        return Found(value, find_line(text, site.dist, f'"{value}"'), value)

    # Role.PEER_PIN
    requirements = walk(doc, site.table, site.path)
    if not isinstance(requirements, list):
        raise SetupError(f"{site.path}: [{'.'.join(site.table)}] is not a list")
    wanted = normalise_dist(site.dist)
    for raw in requirements:
        parsed = parse_requirement(str(raw))
        if parsed is None or normalise_dist(parsed[0]) != wanted or parsed[1] != site.extras:
            continue
        spec = parsed[2]
        if not spec.startswith("=="):
            raise SetupError(f"{site.path}: {site.pin_name} is pinned '{spec}', expected an == pin")
        value = spec[2:].strip()
        return Found(value, find_line(text, f"{site.pin_name}=="), f"{site.pin_name}=={value}")
    raise SetupError(f"{site.path}: no {site.pin_name} pin in [{'.'.join(site.table)}]")


# --- Sweeps ----------------------------------------------------------------


def workspace_dists(root: Path) -> frozenset[str]:
    """The distribution set, parsed from scripts/python-packages.sh.

    That file's header promises adding or renaming a distribution is a
    one-line edit there and nowhere else, so this reads it rather than
    keeping a second copy.
    """
    text = load_text(root, "scripts/python-packages.sh")
    cdylib = re.search(r"^CDYLIB=(\S+)", text, re.MULTILINE)
    peers = re.search(r"^PEERS=\(([^)]*)\)", text, re.MULTILINE)
    if cdylib is None or peers is None:
        raise SetupError("scripts/python-packages.sh: could not parse CDYLIB / PEERS")
    return frozenset(normalise_dist(name) for name in (cdylib[1], *peers[1].split()))


def discover_manifests(root: Path) -> list[str]:
    """Every Cargo.toml and pyproject.toml in the tree, repo-relative and sorted.

    Prunes during the walk rather than after it. `root.rglob("*.toml")` would
    descend .cargo and .uv-cache in full before PRUNE_DIRS rejected what came
    back, and CI points CARGO_HOME and UV_CACHE_DIR inside ${CI_PROJECT_DIR} --
    so the restored crates.io registry lives in the tree this walks, and
    runtime would scale with cache size rather than repo size. Makefile's
    check-release orders this first on the strength of it being sub-second.

    os.walk does not follow symlinks, matching rglob's recurse_symlinks=False,
    so a worktree's symlink back to the primary checkout is not descended
    either.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in PRUNE_DIRS]
        base = Path(dirpath)
        found.extend(
            (base / name).relative_to(root).as_posix() for name in filenames if name in ("Cargo.toml", "pyproject.toml")
        )
    return sorted(found)


def sweep_manifests(root: Path, report: list[str]) -> None:
    """Every manifest in the tree is either a declared site or explicitly excluded."""
    declared = {site.path for site in SITES} | {assertion.path for assertion in ASSERTIONS}
    for rel in discover_manifests(root):
        if rel in declared or rel in EXCLUDED:
            continue
        report.append(
            f"error: site-table: {rel}: neither declared in SITES nor listed in EXCLUDED. "
            "Add it to scripts/check-version-sites.py, with a reason if it carries no release version."
        )


def sweep_peer_pins(root: Path, dists: frozenset[str], report: list[str]) -> None:
    """Every workspace-distribution pin in a declared pyproject is a declared site."""
    declared = {(site.path, normalise_dist(site.dist), site.extras) for site in SITES if site.role is Role.PEER_PIN}
    pyprojects = sorted(
        {site.path for site in SITES if site.path.endswith("pyproject.toml")}
        | {a.path for a in ASSERTIONS if a.path.endswith("pyproject.toml")}
    )
    for rel in pyprojects:
        project = load_toml(root, rel).get("project", {})
        groups: list[list] = [project.get("dependencies", [])]
        groups.extend(project.get("optional-dependencies", {}).values())
        for group in groups:
            for raw in group:
                parsed = parse_requirement(str(raw))
                if parsed is None:
                    continue
                name, extras, _ = parsed
                if normalise_dist(name) not in dists:
                    continue
                if (rel, normalise_dist(name), extras) in declared:
                    continue
                report.append(
                    f"error: site-table: {rel}: pin on {raw!r} is not declared in SITES. "
                    "Every workspace-distribution pin is a version site."
                )


def check_assertions(root: Path, report: list[str]) -> None:
    """The four sites whose invariant is that they carry no version."""
    for assertion in ASSERTIONS:
        if assertion.kind is AssertKind.DYNAMIC_PYPROJECT:
            project = load_toml(root, assertion.path).get("project", {})
            if "version" not in project.get("dynamic", []):
                report.append(
                    f'error: site-table: {assertion.path}: [project] dynamic no longer includes "version". '
                    f"{assertion.note}."
                )
            if "version" in project:
                report.append(
                    f"error: site-table: {assertion.path}: [project] version is set on a dynamic package. "
                    f"{assertion.note}."
                )
        else:
            version, _ = read_lock_entry(root, assertion.path, assertion.dist)
            if version is not None:
                report.append(
                    f"error: site-table: {assertion.path}: [[package]] {assertion.dist} now records "
                    f'version "{version}". {assertion.note}.'
                )


# --- Modes -----------------------------------------------------------------


def read_all(root: Path) -> list[tuple[Site, Found]]:
    return [(site, read_site(root, site)) for site in SITES]


def do_list(root: Path) -> int:
    for site, found in read_all(root):
        where = f"{site.path}:{found.lineno if found.lineno is not None else '?'}"
        print(f"{where:<40} {site.label:<28} {site.eco:<7} {found.value}")
    print(f"{len(SITES)} version sites")
    return 0


def do_print_version(root: Path) -> int:
    canonical = {canon(found.value) for _, found in read_all(root)}
    if len(canonical) != 1 or None in canonical:
        print("error: version-sites: the version sites do not agree on one version", file=sys.stderr)
        print("error: version-sites: run `make list-version-sites` to see them", file=sys.stderr)
        return 2
    print(canonical.pop())
    return 0


def check_tag(root: Path, tag: str) -> int:
    body = tag[1:] if tag.startswith("v") else tag

    expected_pep440 = canon(body)
    if expected_pep440 is None:
        emit(
            f"tag {tag} is not expressible as a PEP 440 version.",
            "Cargo accepts it; Python cannot, so this tag names no single version",
            "and cannot be released by this pipeline. Use a PEP 440-compatible",
            "prerelease instead (-rc1, -alpha.1, -dev).",
        )
        return 2
    if not is_semver(body):
        emit(
            f"tag {tag} is not a valid SemVer version.",
            "Python accepts it; cargo cannot, so this tag names no single version",
            "and cannot be released by this pipeline.",
        )
        return 2
    if "+" in body:
        emit(
            f"tag {tag} carries a local version segment.",
            "PyPI refuses local versions on upload, so release:pypi would fail",
            "after release:crates had already written to crates.io.",
        )
        return 2

    findings: list[tuple[str, str]] = []
    for site, found in read_all(root):
        where = f"{site.path}:{found.lineno if found.lineno is not None else '?'}"
        if site.eco is Eco.PYTHON:
            ok = found.value == expected_pep440
            expected_display = _expected_display(site, expected_pep440)
        else:
            ok = is_semver(found.value) and canon(found.value) == expected_pep440
            expected_display = _expected_display(site, body)
        if not ok:
            findings.append((where, f"{site.label}: found {found.display}, expected {expected_display}"))

    if not findings:
        print(f"version sites OK ({len(SITES)} sites at {body} / {expected_pep440}, tag {tag})")
        return 0

    lines = [
        f"tag {tag} disagrees with {len(findings)} of {len(SITES)} version sites.",
        f"  expected  Cargo   {body}    (SemVer)",
        f"  expected  Python  {expected_pep440}    (PEP 440)",
        "",
        *(f"{where}: {detail}" for where, detail in sorted(findings)),
        "",
        "main carries the next version with a prerelease suffix by convention",
        '(RELEASING.md, "Cutting a release"). Tagging without bumping publishes',
        "those versions to crates.io and PyPI as real releases, and neither",
        "registry allows an unpublish.",
        "",
        "To fix: bump every site above, regenerate the lockfiles (cargo check;",
        "uv lock; cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml),",
        "then delete the tag and re-cut it.",
        "  make list-version-sites",
        f"  make check-version-sites TAG={tag}",
    ]
    emit(*lines)
    return 1


def _expected_display(site: Site, version: str) -> str:
    return f"{site.pin_name}=={version}" if site.role is Role.PEER_PIN else version


def emit(*lines: str) -> None:
    for line in lines:
        print(f"error: version-sites: {line}".rstrip(), file=sys.stderr)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=True, description="Version site guard (QUI-1146).")
    parser.add_argument("tag", nargs="?", default=None, help="tag to check; defaults to $CI_COMMIT_TAG")
    parser.add_argument("--root", default=None, help="tree to check; defaults to this script's repository")
    parser.add_argument("--list", action="store_true", help="print every version site and its value")
    parser.add_argument("--print-version", action="store_true", help="print the tree's canonical PEP 440 version")
    args = parser.parse_args(argv)

    if args.list and args.print_version:
        print("error: version-sites: --list and --print-version are mutually exclusive", file=sys.stderr)
        return 2

    root = Path(args.root).resolve() if args.root else REPO_ROOT

    try:
        if args.list:
            return do_list(root)
        if args.print_version:
            return do_print_version(root)

        # The table sweeps run whether or not there is a tag. They compare no
        # versions; they assert the table still describes the tree, so a new
        # crate or a new peer pin is caught on the merge request that adds it.
        report: list[str] = []
        sweep_manifests(root, report)
        dists = workspace_dists(root)
        sweep_peer_pins(root, dists, report)
        check_assertions(root, report)
        for site in SITES:
            read_site(root, site)
        if report:
            for line in report:
                print(line, file=sys.stderr)
            return 2
    except SetupError as exc:
        print(f"error: site-table: {exc}", file=sys.stderr)
        return 2

    # An empty positional is treated as an absent one. `make check-version-sites`
    # always passes a quoted "$(TAG)", so with TAG unset argparse sees "" rather
    # than None -- and reading that as "a tag was given, and it is empty" is what
    # kept the comparison from ever running on a tag pipeline.
    tag = (args.tag or "").strip()
    from_argument = bool(tag)
    if not from_argument:
        tag = os.environ.get("CI_COMMIT_TAG", "").strip()

    if not tag:
        print(f"guard: no tag to check (CI_COMMIT_TAG unset); {len(SITES)} version sites declared")
        return 0

    # A ref only publishes if it matches `.on-release-tag` (/^v\d/), so a pushed
    # tag that cannot match it must not be able to redden release:validate --
    # which sees every tag, having no `rules:` of its own. A hand-typed argument
    # is not a ref and carries the bare-version ergonomics instead.
    if not re.match(r"^v?[0-9]" if from_argument else r"^v[0-9]", tag):
        print(f"guard: tag {tag} is not a release tag (no `v<digit>` prefix); nothing publishes from it")
        return 0

    try:
        return check_tag(root, tag)
    except SetupError as exc:
        print(f"error: site-table: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
