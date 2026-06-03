#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Build and validate (or build and publish) the xquad Python
# distributions. Single source of truth for the peer/umbrella package
# list and the build/check/upload commands so the three CI jobs that
# touch them (release:dry-run:pypi, release:validate, release:publish-
# pypi) don't drift -- adding or renaming a package is a one-line
# edit here.
#
# Modes:
#   check    -- maturin build (xqffi cdylib) + uv build (peers) +
#               twine check. No network upload, no token needed. Used
#               by release:dry-run:pypi (MR/push) and release:validate
#               (tag).
#   publish  -- maturin build (xqffi) + uv build (peers) + twine
#               upload via PyPI Trusted Publishing (OIDC). One PyPI
#               API token is minted per package (PyPI's mint-token
#               endpoint is single-use per GitLab JWT, so a monorepo
#               needs one JWT per package — see release.yml's
#               id_tokens block). Requires PYPI_ID_TOKEN_<PKG> in
#               env for each PKG. Used by release:publish-pypi (tag
#               only).
#
# Run from the workspace root.

set -euo pipefail

# Pure-Python peers + umbrella, in any order: each is its own sdist.
# The pyo3 cdylib (xqffi) is handled separately because it ships as
# a maturin-built wheel rather than an sdist.
PEERS=(xqvm_py xqcp xqsa xquad)

mode="${1:-}"
case "${mode}" in
    check | publish) ;;
    *)
        echo "usage: $0 {check|publish}" >&2
        exit 2
        ;;
esac

if [[ "${mode}" == "publish" ]]; then
    for pkg in xqffi "${PEERS[@]}"; do
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
    local jwt_var="PYPI_ID_TOKEN_$(printf '%s' "${pkg}" | tr '[:lower:]' '[:upper:]')"
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
    response=$(curl -fsS -X POST https://pypi.org/_/oidc/mint-token \
        -H "Content-Type: application/json" \
        -d "${body}")

    local api_token
    api_token=$(printf '%s' "${response}" \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token") or "")')

    if [[ -z "${api_token}" ]]; then
        echo "Failed to mint PyPI API token for ${pkg}: ${response}" >&2
        return 1
    fi

    TWINE_USERNAME=__token__ TWINE_PASSWORD="${api_token}" \
        twine upload --non-interactive --skip-existing ${dist_glob}
}

# --- xqffi (pyo3 cdylib) ---------------------------------------------------
# `maturin publish` has no OIDC support (no --trusted-publishing flag);
# it only accepts --username/--password. Split build from upload so the
# wheel goes through twine, which lets the cdylib follow the same
# OIDC-mint-and-upload path as the pure-Python peers below.
maturin build --release --manifest-path xqffi/Cargo.toml --out xqffi/dist
if [[ "${mode}" == "check" ]]; then
    twine check xqffi/dist/*
else
    publish_pkg xqffi "xqffi/dist/*"
fi

# --- pure-Python peers + umbrella -----------------------------------------
# `uv build` inside a workspace member defaults to the workspace-root
# `dist/`; `--out-dir dist` keeps each package's artefacts under its own
# subdir so subsequent twine ops resolve files locally to that package.
for pkg in "${PEERS[@]}"; do
    (
        cd "${pkg}"
        uv build --out-dir dist
        if [[ "${mode}" == "publish" ]]; then
            publish_pkg "${pkg}" "dist/*"
        else
            twine check dist/*
        fi
    )
done
