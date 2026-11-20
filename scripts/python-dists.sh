#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Build and validate (or build and publish) the xquad Python
# distribution. Single source of truth for the build/check/upload
# commands so the two CI jobs that touch them (release:validate,
# release:pypi) don't drift. The distribution and the import packages
# it carries live in scripts/python-packages.sh, shared with
# scripts/smoke-wheels.sh.
#
# Phases, in order. Both modes run build and verify; only publish mode
# reaches the third:
#   build    -- maturin builds the root pyproject.toml: abi3 wheels and
#               an sdist, all into dist/. Nothing is checked or uploaded
#               until every artefact exists.
#   verify   -- the wheel-content guard, twine check, then
#               scripts/smoke-wheels.sh, which opens the built wheels and
#               installs and imports them.
#   publish  -- twine upload via PyPI Trusted Publishing (OIDC).
#
# Modes:
#   check    -- build + verify. No network upload, no token needed. Used
#               by release:validate, which runs on every pipeline
#               including tags.
#   publish  -- build + verify + publish. The GitLab JWT in
#               PYPI_ID_TOKEN_XQUAD (release.yml's id_tokens block) is
#               exchanged for a PyPI API token. Used by release:pypi (tag
#               only).
#
# Run from the repo root.

set -euo pipefail

# DIST, NATIVE and PACKAGES. Shared with scripts/smoke-wheels.sh, which
# opens and imports what this builds, so neither can drift into a
# different idea of what the wheel holds. Sourced by path rather than
# relative to the cwd: every other path here assumes the repo root, but
# a `source` that misses is a harder failure to read.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=python-packages.sh
source "${SCRIPT_DIR}/python-packages.sh"

mode="${1:-}"
case "${mode}" in
    check | publish) ;;
    *)
        echo "usage: $0 {check|publish}" >&2
        exit 2
        ;;
esac

JWT_VAR="PYPI_ID_TOKEN_$(printf '%s' "${DIST}" | tr '[:lower:]' '[:upper:]')"
if [[ "${mode}" == "publish" && -z "${!JWT_VAR:-}" ]]; then
    echo "${JWT_VAR} is required for publish mode (OIDC trusted publishing — set by GitLab id_tokens block in .gitlab/ci/release.yml)" >&2
    exit 1
fi

# Exchange the GitLab OIDC JWT for a PyPI API token scoped to the
# project, then upload via twine using that token.
#
# We set TWINE_USERNAME/TWINE_PASSWORD explicitly rather than let
# twine auto-detect, because twine's `id.detect_credential` only
# reads the `PYPI_ID_TOKEN` env var, and the JWT is named after the
# distribution (`PYPI_ID_TOKEN_XQUAD`), as it was when one job
# published several.
publish_pkg() {
    local pkg="$1"
    local dist_glob="$2"
    local jwt="${!JWT_VAR}"

    # Build the request body via python3 rather than string interpolation
    # so weird characters in the JWT (none expected — base64url-safe — but
    # belt-and-suspenders) can't break the JSON.
    local body
    body=$(python3 -c 'import json,sys; print(json.dumps({"token": sys.argv[1]}))' "${jwt}")

    # Capture the raw response, then extract the token. If the token is
    # missing (e.g. PyPI returned 200 with an error payload like
    # `{"message":"publisher config mismatch"}`), surface the whole
    # response in the failure log instead of a generic message.
    local response
    if ! response=$(curl -sS --fail-with-body -X POST \
        https://pypi.org/_/oidc/mint-token \
        -H "Content-Type: application/json" \
        -d "${body}"); then
        echo "Failed to mint PyPI API token for ${pkg}: ${response}" >&2
        return 1
    fi

    local api_token
    if ! api_token=$(printf '%s' "${response}" \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token") or "")' 2>/dev/null); then
        api_token=""
    fi

    if [[ -z "${api_token}" ]]; then
        # Redact any minted token before logging to avoid leaking
        # credentials in CI output.
        local safe_response
        safe_response=$(printf '%s' "${response}" \
            | sed 's/"token":"[^"]*"/"token":"[REDACTED]"/g')
        echo "Failed to mint PyPI API token for ${pkg}: ${safe_response}" >&2
        return 1
    fi

    # shellcheck disable=SC2086  # Intentional: dist_glob must expand
    TWINE_USERNAME=__token__ TWINE_PASSWORD="${api_token}" \
        twine upload --non-interactive --skip-existing ${dist_glob}
}

# --- build -----------------------------------------------------------------
# Three artefacts, all from the root pyproject.toml, all into dist/:
#   1. abi3 wheel for linux-x86_64 (native build on CI runner)
#   2. abi3 wheel for linux-aarch64 (cross-compiled via cargo-zigbuild)
#   3. sdist (universal source fallback for macOS/Windows/other)
#
# `maturin publish` has no OIDC support (no --trusted-publishing flag);
# it only accepts --username/--password. Split build from upload so the
# artefacts go through twine and the OIDC mint below.
#
# The directory is cleared first so an artefact left by an earlier
# version cannot be picked up by the guards or the upload.

rm -rf dist
mkdir -p dist

# 1. Native abi3 wheel (abi3 tag comes from pyo3's abi3-py313 feature)
maturin build --locked --release --out dist

# 2. Cross-compiled aarch64 abi3 wheel via zig linker
maturin build --locked --release --out dist --target aarch64-unknown-linux-gnu --zig

# 3. sdist (universal source fallback)
#
# No `--locked` here, unlike the two builds above: `maturin sdist` does
# not accept the flag (checked against maturin 1.13.3) even though it
# does re-resolve -- it shells out to `cargo metadata`, which rewrites a
# stale Cargo.lock in place. The ordering is what covers it. This script
# is `set -euo pipefail` and sdist runs LAST, so a lock that would have
# to change has already hard-failed at step 1's `maturin build
# --locked` and the run never reaches here. Moving sdist ahead of the
# builds, or dropping `--locked` from them, reopens the gap silently:
# the wheels uploaded to PyPI would be built from a re-resolved
# dependency set that no lockfile records.
maturin sdist --out dist

# --- verify ----------------------------------------------------------------

# Wheel-content guard (QUI-792, ported from quip-validator's
# scripts/python-dists.sh). A wheel is a zip; `twine check` validates
# metadata but never opens the archive. quip-signer once shipped a wheel
# whose maturin layout packed the extension and dropped its Python
# package, and every other check passed. Open each wheel and assert the
# whole import surface is there -- the xqffi shim, its stubs and typing
# marker and the extension itself, and each pure-Python package -- and
# that nothing from the source tree's build side came along with it
# (`python-source = "."` makes the repo root the source, so the
# `[tool.maturin]` excludes are all that keep it out).
check_wheel_contents() {
    local whl
    for whl in dist/*.whl; do
        python3 - "${whl}" "${NATIVE}" "${PACKAGES[@]}" <<'PY'
import sys
import zipfile

wheel, native, packages = sys.argv[1], sys.argv[2], sys.argv[3:]
names = set(zipfile.ZipFile(wheel).namelist())

required = [
    f"{native}/__init__.py",
    f"{native}/asm.pyi",
    f"{native}/checker.pyi",
    f"{native}/vm.pyi",
    f"{native}/xqffi.pyi",
    f"{native}/py.typed",
    f"{native}/xqffi.abi3.so",
    *(f"{package}/__init__.py" for package in packages),
    "xquad/cli/__init__.py",
]
missing = [member for member in required if member not in names]
forbidden = sorted(
    name
    for name in names
    if name.rsplit("/", 1)[-1] == "Cargo.toml" or name.endswith(".rs") or "tests" in name.split("/")[:-1]
)

name = wheel.rsplit("/", 1)[-1]
for member in missing:
    print(f"ERROR: {name} is missing {member} (QUI-792).", file=sys.stderr)
for member in forbidden:
    print(f"ERROR: {name} carries {member}, which is build input, not package content.", file=sys.stderr)
if missing or forbidden:
    print(
        "       Check [tool.maturin] in pyproject.toml: python-source, python-packages and the"
        " wheel-only excludes decide what the wheel holds.",
        file=sys.stderr,
    )
    sys.exit(1)
PY
    done
}

check_wheel_contents

# `twine check` reads metadata and renders the long description. It never
# opens the wheel; the guard above and the smoke test do. Both run in
# either mode, so publish never uploads an artefact that check mode would
# have rejected.
twine check dist/*

bash scripts/smoke-wheels.sh

# --- publish ---------------------------------------------------------------
# Everything is built and verified by this point, so an upload failure is
# a PyPI or credential problem rather than a half-built release.

if [[ "${mode}" == "publish" ]]; then
    publish_pkg "${DIST}" "dist/*"
fi
