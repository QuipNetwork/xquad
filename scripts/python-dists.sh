#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Build and validate (or build and publish) the xquad Python
# distributions. Single source of truth for the build/check/upload
# commands so the two CI jobs that touch them (release:validate,
# release:pypi) don't drift. The package list itself lives in
# scripts/python-packages.sh, shared with
# scripts/smoke-wheels.sh -- adding or renaming a package is a one-line
# edit there.
#
# Phases, in order. Both modes run build and verify; only publish mode
# reaches the third:
#   build    -- maturin (xqffi abi3 wheels + sdist), then uv build for
#               each peer. Nothing is checked or uploaded until every
#               distribution exists, because the smoke test installs the
#               whole set into one venv and because a peer failing to
#               build after xqffi had already uploaded used to leave a
#               partial release on PyPI.
#   verify   -- twine check across every dist dir, then
#               scripts/smoke-wheels.sh, which opens the built wheels and
#               installs and imports them.
#   publish  -- twine upload via PyPI Trusted Publishing (OIDC).
#
# Modes:
#   check    -- build + verify. No network upload, no token needed. Used
#               by release:validate, which runs on every pipeline
#               including tags.
#   publish  -- build + verify + publish. One PyPI API token is minted
#               per package (PyPI's mint-token endpoint is single-use per
#               GitLab JWT, so a monorepo needs one JWT per package --
#               see release.yml's id_tokens block). Requires
#               PYPI_ID_TOKEN_<PKG> in env for each PKG. Used by
#               release:pypi (tag only).
#
# Run from the workspace root.

set -euo pipefail

# CDYLIB, PEERS and PACKAGES. Shared with scripts/smoke-wheels.sh, which
# opens and imports what this builds, so neither can drift into a
# different idea of what "every package" means. Sourced by path rather
# than relative to the cwd: every other path here assumes the workspace
# root, but a `source` that misses is a harder failure to read.
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

if [[ "${mode}" == "publish" ]]; then
    for pkg in "${PACKAGES[@]}"; do
        var="PYPI_ID_TOKEN_$(printf '%s' "${pkg}" | tr '[:lower:]' '[:upper:]')"
        if [[ -z "${!var:-}" ]]; then
            echo "${var} is required for publish mode (OIDC trusted publishing — set by GitLab id_tokens block in .gitlab/ci/release.yml)" >&2
            exit 1
        fi
    done
fi

# Exchange a GitLab OIDC JWT for a per-project PyPI API token, then
# upload via twine using that token. PyPI's mint-token endpoint
# refuses to exchange the same JWT twice (anti-replay), so each
# package gets its own JWT (see release.yml's id_tokens block) and
# its own minted API token.
#
# We set TWINE_USERNAME/TWINE_PASSWORD explicitly rather than let
# twine auto-detect, because twine's `id.detect_credential` only
# reads the single `PYPI_ID_TOKEN` env var — it can't pick the right
# per-package JWT from `PYPI_ID_TOKEN_<PKG>`.
publish_pkg() {
    local pkg="$1"
    local dist_glob="$2"
    local jwt_var
    jwt_var="PYPI_ID_TOKEN_$(printf '%s' "${pkg}" | tr '[:lower:]' '[:upper:]')"
    local jwt="${!jwt_var}"

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

# --- build: the pyo3 cdylib ------------------------------------------------
# Three artefacts:
#   1. abi3 wheel for linux-x86_64 (native build on CI runner)
#   2. abi3 wheel for linux-aarch64 (cross-compiled via cargo-zigbuild)
#   3. sdist (universal source fallback for macOS/Windows/other)
#
# `maturin publish` has no OIDC support (no --trusted-publishing flag);
# it only accepts --username/--password. Split build from upload so the
# artefacts go through twine, which lets the cdylib follow the same
# OIDC-mint-and-upload path as the pure-Python peers below.

rm -rf "${CDYLIB}/dist"
mkdir -p "${CDYLIB}/dist"

# 1. Native abi3 wheel (abi3 tag comes from pyo3's abi3-py313 feature)
maturin build --release --manifest-path "${CDYLIB}/Cargo.toml" \
    --out "${CDYLIB}/dist"

# 2. Cross-compiled aarch64 abi3 wheel via zig linker
maturin build --release --manifest-path "${CDYLIB}/Cargo.toml" \
    --out "${CDYLIB}/dist" --target aarch64-unknown-linux-gnu --zig

# 3. sdist (universal source fallback)
maturin sdist --manifest-path "${CDYLIB}/Cargo.toml" --out "${CDYLIB}/dist"

# --- build: pure-Python peers + umbrella -----------------------------------
# `uv build` inside a workspace member defaults to the workspace-root
# `dist/`; `--out-dir dist` keeps each package's artefacts under its own
# subdir so twine and the smoke test resolve files locally to that package.
# The directory is cleared first so an artefact left by an earlier version
# cannot be picked up by either.
for pkg in "${PEERS[@]}"; do
    rm -rf "${pkg}/dist"
    (
        cd "${pkg}"
        uv build --out-dir dist
    )
done

# --- verify ----------------------------------------------------------------
# `twine check` reads metadata and renders the long description. It never
# opens the wheel, so it cannot see a mislaid package directory: that is
# what the smoke test is for, and QUI-1020 is what happens without it.
# Both run in either mode, so publish never uploads an artefact that check
# mode would have rejected.

for pkg in "${PACKAGES[@]}"; do
    twine check "${pkg}"/dist/*
done

bash scripts/smoke-wheels.sh

# --- publish ---------------------------------------------------------------
# Everything is built and verified by this point, so an upload failure is
# a PyPI or credential problem rather than a half-released workspace.

# PACKAGES is cdylib-first and its peers are in dependency order, which is
# exactly the order an upload must take: nothing goes live on PyPI before
# the packages it requires.
if [[ "${mode}" == "publish" ]]; then
    for pkg in "${PACKAGES[@]}"; do
        publish_pkg "${pkg}" "${pkg}/dist/*"
    done
fi
