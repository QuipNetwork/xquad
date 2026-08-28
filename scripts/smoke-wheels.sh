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
# literally named `.`, so four of the five published distributions raised
# ModuleNotFoundError as installed while every release job stayed green.
#
# Four checks, cheapest first:
#
#   1. Layout and payload. Every top-level entry of every built wheel must be
#      the import package itself or its own dist-info -- `.`, `PKG-INFO` and
#      `.gitignore` all fail there -- and every file inside the package
#      directory must be Python source, plus the extension module for the
#      cdylib. `only-include = ["."]` sweeps in every committed,
#      non-gitignored file at the package root, so `exclude` is all that keeps
#      a stray AGENTS.md or notes.txt out of a published wheel. This names the
#      defect directly rather than surfacing it as an import error further
#      down. Wheels only: the sdist legitimately carries the tests, the
#      pyproject and the README that the wheel must not, and where pip falls
#      back to it, it rebuilds the wheel from the configuration this checks.
#   2. Artefact versions. Every wheel filename, dist-info directory, sdist
#      filename and PKG-INFO must carry the version the manifests declare.
#      Check 1 reads the dist-info name but only its prefix, and nothing else
#      in the pipeline looks at the version at all -- which matters most for
#      xqffi, whose pyproject is `dynamic` and whose version maturin takes
#      from xqffi/Cargo.toml while its peers pin it in the PEP 440 spelling.
#      With scripts/check-version-sites.py comparing the manifests to the tag
#      on a tag pipeline, this closes the chain artefact == manifests == tag.
#   3. Install and import. Install all five distributions into a throwaway
#      venv, import them from a directory outside the repository so the source
#      tree cannot satisfy the import, and confirm each module resolves inside
#      that venv rather than out of the checkout.
#   4. Extras. `xquad` must forward every extra `xqsa` declares, so that
#      `pip install xquad[quip]` resolves like its cuda/dwave/metal siblings.
#      Resolving an extra for real belongs in manual release verification,
#      not in every pipeline; this asserts only that the metadata agrees.
#
# Reads artefacts that have already been built, one `dist/` per package, which
# is the layout scripts/python-dists.sh produces. Run it after that script's
# build phase, or after building the same directories by hand.
#
# Usage:
#   scripts/smoke-wheels.sh
#
# Exit codes:
#   0  -- pass
#   1  -- a wheel is mislaid, carries a file that is not package content,
#         carries the wrong version, does not import, or drops an extra
#   2  -- setup error (no dist directory, no wheel in one, wheel metadata that
#         cannot be read, uv or python3 unavailable)
#
# Nothing branches on the difference: CI runs this under `set -e` and either
# status fails the job. The split is for whoever reads the log, which is
# reason enough to keep it honest -- a documented exit code that lies is worse
# than one that was never documented. It is not exhaustive either: `uv` runs
# bare under `set -e`, so a failure inside it exits with uv's own status.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# CDYLIB, PEERS and PACKAGES. Shared with scripts/python-dists.sh, which
# builds what this opens: a package that script publishes but this one does
# not know about is a package with no guard at all.
# shellcheck source=python-packages.sh
source "${REPO_ROOT}/scripts/python-packages.sh"

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

# Assert each wheel holds the package and its metadata and nothing else, at
# both levels: the top-level entries must be the package plus its own
# dist-info, and every file inside the package directory must be Python source
# -- plus, for the cdylib, its extension module.
#
# The second level matters because `only-include = ["."]` sweeps in every
# committed, non-gitignored file at the package root, so `exclude` is the only
# thing keeping a stray AGENTS.md or notes.txt out of a published wheel. An
# exclude list only ever knows about yesterday's strays, which is the same
# shape of mistake that let QUI-1020 ship: `twine check` looked only for what
# it already knew to look for.
#
# Wheels only. The sdist legitimately carries what the wheel must not -- the
# tests, the pyproject, the README -- and where pip falls back to it, it
# rebuilds the wheel from the same hatchling configuration this checks.
#
# Sets FAILED rather than exiting, so one run reports every mislaid wheel
# instead of the first.
check_layout() {
    local name="$1"
    local dist="${REPO_ROOT}/$1/dist"
    local wheel
    local found=0

    [[ -d "${dist}" ]] || die_setup "${name}/dist does not exist; build the distributions first"

    # Only the cdylib ships a compiled extension. Allowing a .so anywhere in
    # every package's payload would weaken the guard for four packages to
    # accommodate one. An `if` rather than `[[ ... ]] && native=1`, because a
    # trailing test whose status leaks is exactly what broke `cleanup` above.
    local native=0
    if [[ "${name}" == "${CDYLIB}" ]]; then
        native=1
    fi

    for wheel in "${dist}"/*.whl; do
        [[ -e "${wheel}" ]] || continue
        found=1

        python3 - "${wheel}" "${name}" "${native}" <<'PY' || FAILED=1
import sys
import zipfile

wheel, name = sys.argv[1], sys.argv[2]
native = sys.argv[3] == "1"

with zipfile.ZipFile(wheel) as archive:
    # Directory members carry no content and neither builder currently emits
    # them, but a bare `xqcp/` entry would otherwise read as a stray file.
    entries = sorted(n for n in archive.namelist() if not n.endswith("/"))

tops = sorted({entry.split("/", 1)[0] for entry in entries})

metadata = [top for top in tops if top.endswith((".dist-info", ".data"))]
payload = [top for top in tops if top not in metadata]

# The import surface is either the package directory or, for the maturin-built
# cdylib, the extension module sitting at the wheel root. maturin currently
# emits a real package directory with a shim __init__.py beside the extension
# module, so the second case is unexercised today; it costs nothing and covers
# the shim-less layout.
bad = [top for top in payload if top != name and top.split(".", 1)[0] != name]
bad += [top for top in metadata if top.split("-", 1)[0] != name]


def is_source(entry):
    leaf = entry.rsplit("/", 1)[-1]
    if leaf.endswith(".py"):
        return True
    # The cdylib's extension module. Its middle suffix carries the ABI tag
    # (`xqffi.abi3.so`) and its final suffix varies by platform, so it is
    # matched the way the top-level rule above matches it: on the stem before
    # the first dot.
    return native and leaf.split(".", 1)[0] == name and leaf.endswith((".so", ".pyd"))


# An allowlist, not a denylist of known-bad names. The set of files that could
# wash into a package root is open-ended -- .envrc, notes.txt, conftest.py, a
# Makefile -- so enumerating them is the losing half of the trade. The cost is
# that a legitimate new kind of file fails here until someone widens the rule,
# and that is the point: the wheel is the published surface, and a change to
# what it carries deserves a decision rather than a silent inclusion.
stray = [entry for entry in entries if entry.split("/", 1)[0] in payload and not is_source(entry)]

errors = []
if bad:
    errors.append("unexpected top-level entries: " + ", ".join(bad))
if not payload:
    errors.append(f"nothing importable at the wheel root, expected {name}")
if stray:
    errors.append("payload holds files that are not package source: " + ", ".join(stray))

for error in errors:
    print(f"error: {wheel}: {error}", file=sys.stderr)

if bad or not payload:
    print(f"error: {wheel}: expected {name} plus {name}-<version>.dist-info only", file=sys.stderr)

if stray:
    print(
        f"error: {wheel}: a wheel should carry only what a user imports. Either drop the"
        f" file from the package root or add it to `exclude` in {name}/pyproject.toml."
        " If it genuinely belongs in the wheel -- a data file, py.typed, a .pyi stub --"
        " widen is_source in check_layout in scripts/smoke-wheels.sh and record why.",
        file=sys.stderr,
    )

if errors:
    sys.exit(1)
PY
    done

    (( found == 1 )) || die_setup "no wheel in ${name}/dist; build the distributions first"
}

# Assert every built artefact carries the version the manifests declare.
#
# check_layout above reads the dist-info directory but only its name prefix
# (`top.split("-", 1)[0] != name`); the version segment goes uninspected, and
# nothing else in the pipeline looks at it either. That leaves a gap on the
# one site that feeds both ecosystems: xqffi/pyproject.toml declares
# `dynamic = ["version"]` and maturin takes the version from
# xqffi/Cargo.toml, while xqvm_py and xquad pin `xqffi==<PEP 440 version>`.
# Cargo spells a prerelease `0.4.0-rc1` and Python spells it `0.4.0rc1`, so
# the two sites cannot be compared as strings and the mapping has to be
# checked against what the builder actually stamps.
#
# scripts/check-version-sites.py --print-version is the manifests' side of
# that comparison, and it refuses a tree whose sites disagree. Together with
# that guard's tag check on a tag pipeline, this closes the chain: artefact
# == manifests == tag. It covers all five distributions rather than xqffi
# alone, so hatchling's rendering of xqvm_py/__init__.py is checked too.
#
# Sets FAILED rather than exiting, so one run reports every mismatched
# artefact instead of the first.
check_artefact_versions() {
    local expected

    if ! expected="$(uv run --no-project --isolated --python 3.13 python \
        "${REPO_ROOT}/scripts/check-version-sites.py" --print-version)"; then
        die_setup "could not read the workspace version; run \`make list-version-sites\`"
    fi

    python3 - "${REPO_ROOT}" "${expected}" "${PACKAGES[@]}" <<'PY' || FAILED=1
import pathlib
import sys
import tarfile
import zipfile

root, expected, names = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
findings = []


def check(where, found):
    if found != expected:
        findings.append(f"{where}: version {found}, expected {expected}")


for name in names:
    for artefact in sorted((root / name / "dist").iterdir()):
        rel = f"{name}/dist/{artefact.name}"
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
                # and the remaining distributions go unreported.
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
        " 0.4.0rc1), so check xqffi/Cargo.toml and xqvm_py/__init__.py against"
        " `make list-version-sites` before assuming the builder is at fault.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"artefact version check passed: every distribution is {expected}")
PY
}

# Install every distribution into a throwaway venv and import it from outside
# the repository, where the source tree cannot answer the import.
check_install_and_import() {
    local venv="${WORKDIR}/venv"
    local sandbox="${WORKDIR}/sandbox"
    local python="${venv}/bin/python"
    local peer

    mkdir -p "${sandbox}"

    # Pin the interpreter rather than taking whatever `python3` the runner
    # happens to carry: every package sets requires-python >=3.13 and xqffi
    # ships cp313-abi3 wheels, so an older default would fail the install for
    # a reason that has nothing to do with the wheels. uv fetches a managed
    # 3.13 when the machine has none.
    uv venv --python 3.13 "${venv}"

    # xqffi carries no third-party dependencies, so it resolves with the index
    # switched off. That stops the copy already on PyPI shadowing the wheel
    # just built, and lets uv pick the platform tag that matches this machine:
    # xqffi/dist also holds the cross-compiled aarch64 wheel and the sdist.
    uv pip install --python "${python}" --no-index \
        --find-links "${REPO_ROOT}/${CDYLIB}/dist" "${CDYLIB}"

    # The peers go in by path rather than by name, for the same reason -- an
    # explicit file cannot be shadowed by the same version on an index. Their
    # third-party dependencies still resolve from PyPI, so this one keeps the
    # index enabled.
    local wheels=()
    for peer in "${PEERS[@]}"; do
        wheels+=("${REPO_ROOT}/${peer}"/dist/*.whl)
    done
    uv pip install --python "${python}" "${wheels[@]}"

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
    print("error: the published distributions do not import as installed", file=sys.stderr)
    sys.exit(1)

print("import check passed: " + ", ".join(sys.argv[1:]))
PY
    ) || FAILED=1
}

# `pip install xquad[quip]` must work like its cuda/dwave/metal siblings, which
# means the umbrella has to forward whatever xqsa declares.
#
# The python block distinguishes a finding (exit 1: an extra is missing) from a
# setup error (exit 2: no wheel, or a wheel with no METADATA), the same split
# this script documents for itself. A bare `|| FAILED=1` flattened both to 1,
# so the status is mapped rather than discarded. The block prints its own
# diagnosis before exiting 2, so routing it through `die_setup` would say the
# same thing twice; the bare `exit` carries the code and nothing else.
#
# The `*)` arm rather than `2)`: an unexpected status is far likelier to be an
# environment problem than a missing extra, and misfiling it as a finding sends
# whoever reads the log to xquad/pyproject.toml for a defect that is not there.
check_extras() {
    local status=0

    python3 - "${REPO_ROOT}/xqsa/dist" "${REPO_ROOT}/xquad/dist" <<'PY' || status=$?
import glob
import sys
import zipfile


def extras(dist_dir):
    wheels = sorted(glob.glob(f"{dist_dir}/*.whl"))
    if not wheels:
        print(f"error: no wheel in {dist_dir}", file=sys.stderr)
        sys.exit(2)

    with zipfile.ZipFile(wheels[0]) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if not names:
            print(f"error: {wheels[0]} has no dist-info/METADATA", file=sys.stderr)
            sys.exit(2)
        text = archive.read(names[0]).decode("utf-8")

    return {
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Provides-Extra:")
    }


missing = sorted(extras(sys.argv[1]) - extras(sys.argv[2]))

if missing:
    print("error: xquad does not forward every extra xqsa declares: " + ", ".join(missing), file=sys.stderr)
    print('error: add `<extra> = [ "xqsa[<extra>]" ]` to xquad/pyproject.toml', file=sys.stderr)
    sys.exit(1)

print("extras check passed: xquad forwards every xqsa extra")
PY

    case "${status}" in
        0) ;;
        1) FAILED=1 ;;
        *) exit 2 ;;
    esac
}

main() {
    local name

    require python3
    require uv

    WORKDIR="$(mktemp -d)"

    for name in "${PACKAGES[@]}"; do
        check_layout "${name}"
    done

    if (( FAILED == 0 )); then
        echo "layout check passed: ${PACKAGES[*]}"
    fi

    check_artefact_versions
    check_install_and_import
    check_extras

    if (( FAILED != 0 )); then
        exit 1
    fi

    echo "built-distribution smoke test passed"
}

main "$@"
