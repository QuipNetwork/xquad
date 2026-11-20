#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Rebuild xqffi's compiled extension when a `uv sync` left it missing.
#
# The root `xquad` project installs editable, and maturin builds the
# extension into the source tree as xqffi/xqffi.*.so (gitignored). Nothing
# uv tracks covers that file: a cache-key on it would rebuild on every sync,
# because the build rewrites it. So when the file goes while every tracked
# input keeps its modification time, uv reuses what it has -- the installed
# project if .venv/ survived, or its cached editable build if not -- and
# `import xqffi` fails with `No module named 'xqffi.xqffi'`.
#
# That is the normal state of a CI runner that fetches into a reused build
# directory: `git clean` removes the ignored .so, the tracked files keep
# their old times, and .venv/ or .uv-cache/ comes back from the job cache.
# Locally it follows a `git clean -fdX` that spares .venv/.
#
# Run it straight after a `uv sync`, with that sync's own arguments: the
# reinstall repeats them, so an extra the sync installed stays installed.
#
# Usage:
#   uv sync --extra quip && scripts/ensure-xqffi.sh --extra quip
#
# Exit codes: 0 when the extension is present or the reinstall restored
# it; uv's own status when the reinstall fails; 1 when the reinstall
# succeeded but the extension is still missing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_ROOT}"

# `xqffi.abi3.so` on Linux and macOS; on Windows an abi3 build is a bare
# `xqffi.pyd`, and a version-tagged one `xqffi.<tag>.pyd`.
present() {
    compgen -G "xqffi/xqffi.*.so" >/dev/null || compgen -G "xqffi/xqffi*.pyd" >/dev/null
}

present && exit 0

echo "xqffi/xqffi.*.so is missing; reinstalling the xquad project"
uv sync "$@" --reinstall-package xquad

if ! present; then
    echo "error: xqffi/xqffi.*.so is still missing after reinstalling the xquad project" >&2
    exit 1
fi
