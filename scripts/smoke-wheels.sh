#!/usr/bin/env bash
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
# Built-distribution smoke test.
#
# `twine check` validates metadata and long-description rendering. It never
# opens a wheel and it never imports anything, which is how QUI-1020 shipped:
# `packages = ["."]` bundled each pure-Python package under a directory
# literally named `.`, so four of the five distributions published then
# raised ModuleNotFoundError as installed while every release job stayed
# green.
#
# Three checks, cheapest first:
#
#   1. Layout and payload. Every top-level entry of every built wheel must be
#      one of the import packages or the distribution's own dist-info -- `.`,
#      `PKG-INFO`, `scripts/` and `examples/` all fail there -- and every file
#      inside a package directory must be Python source, plus the extension
#      module, stubs and typing marker of the native package.
#      `python-source = "."` makes the repo root the Python source, so the
#      `[tool.maturin]` excludes are all that keep a stray AGENTS.md or
#      notes.txt out of a published wheel. This names the defect directly
#      rather than surfacing it as an import error further down. Wheels only:
#      the sdist legitimately carries the tests, the Cargo workspace and the
#      READMEs that the wheel must not, and where pip falls back to it, it
#      rebuilds the wheel from the configuration this checks.
#   2. Artefact versions. Every wheel filename, dist-info directory, sdist
#      filename and PKG-INFO must carry the version the manifests declare.
#      The root pyproject.toml is `dynamic`, so maturin takes the version from
#      xqffi/Cargo.toml and converts the Cargo spelling to PEP 440 itself;
#      nothing else in the pipeline looks at what it stamped. With
#      scripts/check-version-sites.py comparing the manifests to the tag on a
#      tag pipeline, this closes the chain artefact == manifests == tag.
#   3. Install and import. Install the wheel into a throwaway venv, import
#      every package from a directory outside the repository so the source
#      tree cannot satisfy the import, and confirm each module resolves inside
#      that venv rather than out of the checkout.
#
# Reads artefacts that have already been built, in dist/ at the repo root,
# which is the layout scripts/python-dists.sh produces. Run it after that
# script's build phase, or after building the same directory by hand.
#
# Usage:
#   scripts/smoke-wheels.sh
#
# Exit codes:
#   0  -- pass
#   1  -- a wheel is mislaid, carries a file that is not package content,
#         carries the wrong version, or does not import
#   2  -- setup error (no dist directory, no wheel in it, uv or python3
#         unavailable)
#
# Nothing branches on the difference: CI runs this under `set -e` and either
# status fails the job. The split is for whoever reads the log, which is
# reason enough to keep it honest -- a documented exit code that lies is worse
# than one that was never documented. It is not exhaustive either: `uv` runs
# bare under `set -e`, so a failure inside it exits with uv's own status.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# DIST, NATIVE and PACKAGES. Shared with scripts/python-dists.sh, which
# builds what this opens: a package that script publishes but this one does
# not know about is a package with no guard at all.
# shellcheck source=python-packages.sh
source "${REPO_ROOT}/scripts/python-packages.sh"

DIST_DIR="${REPO_ROOT}/dist"
FAILED=0
WORKDIR=""

# The handler fires on every exit path, including the `require` failures that
# run before WORKDIR exists. There, `[[ -n "" ]]` returns 1, and an EXIT
# handler's status replaces the script's -- which turned every `die_setup`
# exit 2 into an exit 1 and made the two documented codes indistinguishable.
# The explicit `return 0` also stops a failed `rm` rewriting a real verdict.
cleanup() {
    [[ -n "${WORKDIR}" ]] && rm -rf "${WORKDIR}"
    return 0
}
trap cleanup EXIT

die_setup() {
    echo "error: $1" >&2
    exit 2
}

require() {
    command -v "$1" >/dev/null 2>&1 || die_setup "$1 is required but not on PATH"
}

# Assert each wheel holds the packages and their metadata and nothing else, at
# both levels: the top-level entries must be the import packages plus the
# distribution's own dist-info, and every file inside a package directory must
# be Python source -- plus, for the native package, its extension module,
# stubs and typing marker.
#
# The second level matters because `python-source = "."` makes the repo root
# the Python source, so the `[tool.maturin]` excludes are the only thing
# keeping a stray AGENTS.md or notes.txt out of a published wheel. An exclude
# list only ever knows about yesterday's strays, which is the same shape of
# mistake that let QUI-1020 ship: `twine check` looked only for what it
# already knew to look for.
#
# Wheels only. The sdist legitimately carries what the wheel must not -- the
# tests, the Cargo workspace, the READMEs -- and where pip falls back to it,
# it rebuilds the wheel from the same maturin configuration this checks.
#
# Sets FAILED rather than exiting, so one run reports every mislaid wheel
# instead of the first.
check_layout() {
    local wheel
    local found=0

    [[ -d "${DIST_DIR}" ]] || die_setup "dist/ does not exist; build the distribution first"

    for wheel in "${DIST_DIR}"/*.whl; do
        [[ -e "${wheel}" ]] || continue
        found=1

        python3 - "${wheel}" "${DIST}" "${NATIVE}" "${PACKAGES[@]}" <<'PY' || FAILED=1
import sys
import zipfile

wheel, dist, native, packages = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]

with zipfile.ZipFile(wheel) as archive:
    # Directory members carry no content and maturin does not emit them, but
    # a bare `xqcp/` entry would otherwise read as a stray file.
    entries = sorted(n for n in archive.namelist() if not n.endswith("/"))

tops = sorted({entry.split("/", 1)[0] for entry in entries})

metadata = [top for top in tops if top.endswith((".dist-info", ".data"))]
payload = [top for top in tops if top not in metadata]

bad = [top for top in payload if top not in packages]
bad += [top for top in metadata if top.split("-", 1)[0] != dist]
absent = [package for package in packages if package not in payload]


def is_source(entry):
    top, leaf = entry.split("/", 1)[0], entry.rsplit("/", 1)[-1]
    if leaf.endswith(".py"):
        return True
    if top != native:
        return False
    # The extension module. Its middle suffix carries the ABI tag
    # (`xqffi.abi3.so`) and its final suffix varies by platform, so it is
    # matched on the stem before the first dot.
    if leaf.split(".", 1)[0] == native and leaf.endswith((".so", ".pyd")):
        return True
    # The type stubs and PEP 561 marker: the extension is compiled, so the
    # stubs are the only typing a checker can read.
    return leaf.endswith(".pyi") or leaf == "py.typed"


# An allowlist, not a denylist of known-bad names. The set of files that could
# wash into a package directory is open-ended -- .envrc, notes.txt,
# conftest.py, a Makefile -- so enumerating them is the losing half of the
# trade. The cost is that a legitimate new kind of file fails here until
# someone widens the rule, and that is the point: the wheel is the published
# surface, and a change to what it carries deserves a decision rather than a
# silent inclusion.
stray = [entry for entry in entries if entry.split("/", 1)[0] in payload and not is_source(entry)]

errors = []
if bad:
    errors.append("unexpected top-level entries: " + ", ".join(bad))
if absent:
    errors.append("packages missing from the wheel root: " + ", ".join(absent))
if stray:
    errors.append("payload holds files that are not package source: " + ", ".join(stray))

for error in errors:
    print(f"error: {wheel}: {error}", file=sys.stderr)

if bad or absent:
    print(
        f"error: {wheel}: expected {', '.join(packages)} plus {dist}-<version>.dist-info only",
        file=sys.stderr,
    )

if stray:
    print(
        f"error: {wheel}: a wheel should carry only what a user imports. Either drop the"
        " file from the package directory or add it to the `[tool.maturin]` excludes in"
        " pyproject.toml. If it genuinely belongs in the wheel -- a data file, a .pyi stub"
        " -- widen is_source in check_layout in scripts/smoke-wheels.sh and record why.",
        file=sys.stderr,
    )

if errors:
    sys.exit(1)
PY
    done

    (( found == 1 )) || die_setup "no wheel in dist/; build the distribution first"
}

# Assert every built artefact carries the version the manifests declare.
#
# check_layout above reads the dist-info directory but only its name prefix
# (`top.split("-", 1)[0] != dist`); the version segment goes uninspected, and
# nothing else in the pipeline looks at it either. The root pyproject.toml
# declares `dynamic = ["version"]` and maturin takes the version from
# xqffi/Cargo.toml. Cargo spells a prerelease `0.4.0-rc1` and Python spells it
# `0.4.0rc1`, so the mapping has to be checked against what the builder
# actually stamps.
#
# scripts/check-version-sites.py --print-version is the manifests' side of
# that comparison, and it refuses a tree whose sites disagree. Together with
# that guard's tag check on a tag pipeline, this closes the chain: artefact
# == manifests == tag.
#
# Sets FAILED rather than exiting, so one run reports every mismatched
# artefact instead of the first.
check_artefact_versions() {
    local expected

    if ! expected="$(uv run --no-project --isolated --python 3.13 python \
        "${REPO_ROOT}/scripts/check-version-sites.py" --print-version)"; then
        die_setup "could not read the workspace version; run \`make list-version-sites\`"
    fi

    python3 - "${DIST_DIR}" "${expected}" <<'PY' || FAILED=1
import pathlib
import sys
import tarfile
import zipfile

dist_dir, expected = pathlib.Path(sys.argv[1]), sys.argv[2]
findings = []


def check(where, found):
    if found != expected:
        findings.append(f"{where}: version {found}, expected {expected}")


for artefact in sorted(dist_dir.iterdir()):
    rel = f"dist/{artefact.name}"
    if artefact.name.endswith(".whl"):
        # {name}-{version}-{python}-{abi}-{platform}.whl
        check(rel, artefact.name.split("-")[1])
        with zipfile.ZipFile(artefact) as archive:
            tops = {entry.split("/", 1)[0] for entry in archive.namelist()}
        for top in sorted(top for top in tops if top.endswith(".dist-info")):
            check(f"{rel} ({top})", top.removesuffix(".dist-info").split("-", 1)[1])
    elif artefact.name.endswith(".tar.gz"):
        stem = artefact.name.removesuffix(".tar.gz")
        check(rel, stem.split("-", 1)[1])
        with tarfile.open(artefact) as archive:
            # extractfile raises KeyError for an absent member and returns
            # None only for one that exists but is not a regular file, so
            # the finding below is reachable only with the catch. Without
            # it a missing PKG-INFO -- or a root directory not named after
            # the filename stem -- kills the loop on an uncaught KeyError
            # and the remaining artefacts go unreported.
            try:
                member = archive.extractfile(f"{stem}/PKG-INFO")
            except KeyError:
                member = None
            if member is None:
                findings.append(f"{rel}: no PKG-INFO")
                continue
            for line in member.read().decode("utf-8").splitlines():
                if line.startswith("Version:"):
                    check(f"{rel} (PKG-INFO)", line.split(":", 1)[1].strip())
                    break

for finding in findings:
    print(f"error: {finding}", file=sys.stderr)

if findings:
    print(
        "error: a built artefact does not carry the version its manifests declare."
        " The builder normalises the Cargo spelling into PEP 440 (0.4.0-rc1 becomes"
        " 0.4.0rc1), so check xqffi/Cargo.toml against"
        " `make list-version-sites` before assuming the builder is at fault.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"artefact version check passed: every artefact is {expected}")
PY
}

# Install the wheel into a throwaway venv and import every package from
# outside the repository, where the source tree cannot answer the import.
check_install_and_import() {
    local venv="${WORKDIR}/venv"
    local sandbox="${WORKDIR}/sandbox"
    local python="${venv}/bin/python"

    mkdir -p "${sandbox}"

    # Pin the interpreter rather than taking whatever `python3` the runner
    # happens to carry: the project sets requires-python >=3.13 and ships
    # cp313-abi3 wheels, so an older default would fail the install for a
    # reason that has nothing to do with the wheels. uv fetches a managed
    # 3.13 when the machine has none.
    uv venv --python 3.13 "${venv}"

    # Two steps. The first installs xquad alone with the index switched off,
    # so the copy already on PyPI cannot shadow the wheel just built, and uv
    # picks the platform tag that matches this machine: dist/ also holds the
    # cross-compiled aarch64 wheel and the sdist. The second resolves the
    # third-party dependencies from PyPI; xquad is already satisfied, so uv
    # leaves the built wheel in place.
    uv pip install --python "${python}" --no-index --no-deps \
        --find-links "${DIST_DIR}" "${DIST}"
    uv pip install --python "${python}" --find-links "${DIST_DIR}" "${DIST}"

    (
        cd "${sandbox}"
        "${python}" - "${PACKAGES[@]}" <<'PY' || exit 1
import importlib
import sys

failures = []

for name in sys.argv[1:]:
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 -- any import failure is the defect
        failures.append(f"{name}: {type(exc).__name__}: {exc}")
        continue

    origin = getattr(module, "__file__", None)
    if origin is None:
        failures.append(f"{name}: imported with no __file__, cannot confirm its origin")
    elif not origin.startswith(sys.prefix):
        failures.append(f"{name}: resolved to {origin}, outside the test venv")

for failure in failures:
    print(f"error: {failure}", file=sys.stderr)

if failures:
    print("error: the built distribution does not import as installed", file=sys.stderr)
    sys.exit(1)

print("import check passed: " + ", ".join(sys.argv[1:]))
PY
    ) || FAILED=1
}

main() {
    require python3
    require uv

    WORKDIR="$(mktemp -d)"

    check_layout

    if (( FAILED == 0 )); then
        echo "layout check passed: ${PACKAGES[*]}"
    fi

    check_artefact_versions
    check_install_and_import

    if (( FAILED != 0 )); then
        exit 1
    fi

    echo "built-distribution smoke test passed"
}

main "$@"
