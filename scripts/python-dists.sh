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
#               upload via PyPI Trusted Publishing (OIDC). twine
#               auto-detects PYPI_ID_TOKEN in env; no long-lived
#               token. Used by release:publish-pypi (tag only).
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
    : "${PYPI_ID_TOKEN:?PYPI_ID_TOKEN is required for publish mode (OIDC trusted publishing — set by GitLab id_tokens block)}"
fi

# --- xqffi (pyo3 cdylib) ---------------------------------------------------
# `maturin publish` has no OIDC support (no --trusted-publishing flag);
# it only accepts --username/--password. Split build from upload so the
# wheel goes through twine, which auto-detects PYPI_ID_TOKEN and does
# the OIDC token exchange just like the pure-Python peers below.
maturin build --release --manifest-path xqffi/Cargo.toml --out xqffi/dist
if [[ "${mode}" == "check" ]]; then
    twine check xqffi/dist/*
else
    twine upload --non-interactive --skip-existing xqffi/dist/*
fi

# --- pure-Python peers + umbrella -----------------------------------------
# `uv build` inside a workspace member defaults to the workspace-root
# `dist/`; `--out-dir dist` keeps each package's artefacts under its own
# subdir so subsequent twine ops resolve files locally to that package.
#
# twine ≥6.1 with no --username/--password and PYPI_ID_TOKEN in env
# uses OIDC trusted publishing automatically (PyPI mints a per-project
# API token for each upload).
for pkg in "${PEERS[@]}"; do
    (
        cd "${pkg}"
        uv build --out-dir dist
        if [[ "${mode}" == "publish" ]]; then
            twine upload --non-interactive --skip-existing dist/*
        else
            twine check dist/*
        fi
    )
done
