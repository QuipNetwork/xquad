#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Wrap `cargo publish --dry-run` for a workspace member, tolerating the
# "workspace dep not yet on crates.io" failure that occurs at every
# version bump of a multi-crate workspace.
#
# Why this wrapper exists
# -----------------------
# `cargo publish --dry-run` runs two phases:
#
#   1. Pack -- copy source files into a `.crate` tarball and resolve
#      dependencies against the registry to construct the resolved
#      manifest.
#   2. Verify -- extract the tarball and compile from the packaged
#      source. Skipped via `--no-verify`.
#
# Phase 1 still resolves deps against crates.io even with `--no-verify`.
# For a workspace member like xqasm that depends on `xqvm = "0.2.0"`,
# that resolution fails at validate-time because xqvm 0.2.0 hasn't been
# pushed yet -- it won't land until `release:publish-crates` actually
# runs (which publishes xqvm first, then xqasm, then xqcli).
#
# Cargo emits one of two error messages depending on whether any version
# of the dep exists on the registry:
#   - First-ever publish: "no matching package named `<crate>`"
#   - Subsequent version bumps: "failed to select a version for the
#     requirement `<crate> = "^X.Y.Z"`"
#
# This wrapper:
#   - Runs cargo publish --dry-run for the named crate.
#   - On exit 0: passes through.
#   - On exit non-zero with either workspace-dep-not-on-crates.io
#     pattern: logs the expected miss and exits 0. The actual sequential
#     publish handles ordering.
#   - On exit non-zero with any other error (metadata bugs, license
#     missing, etc.): exits with the original code, blocking the
#     pipeline.
#
# Usage:
#   scripts/validate-crate-publish.sh <crate-name> [extra cargo flags]
# Example:
#   scripts/validate-crate-publish.sh xqasm --no-verify

set -euo pipefail

crate="${1:?usage: $0 <crate-name> [extra cargo publish flags...]}"
shift

# Workspace member names. If cargo's "no matching package named ..."
# error names one of these, treat it as the expected first-publish
# miss. Adding a new workspace crate? Add it here too.
WORKSPACE_CRATES=(xqvm xqasm xqcli xqffi)

set +e
out=$(cargo publish --dry-run --locked "$@" -p "${crate}" 2>&1)
exit_code=$?
set -e

# Always echo the cargo output so the pipeline log is intact.
echo "${out}"

if [[ "${exit_code}" -eq 0 ]]; then
    exit 0
fi

# Match either cargo error for a workspace dep not yet on crates.io:
#   "no matching package named `<crate>`"        (dep never published)
#   "failed to select a version for ... `<crate> = ..."  (newer version not yet published)
for ws in "${WORKSPACE_CRATES[@]}"; do
    if echo "${out}" | grep -qE "no matching package named \`${ws}\`|failed to select a version for the requirement \`${ws} ="; then
        echo
        echo "[validate-crate-publish] ${crate} dry-run failed because workspace dep '${ws}'"
        echo "                         is not yet on crates.io at the required version. This is"
        echo "                         expected -- '${ws}' will be published before '${crate}'"
        echo "                         by release:publish-crates, which runs sequentially."
        echo "                         Treating as success."
        exit 0
    fi
done

# Any other failure: real error -- propagate cargo's exit code.
echo
echo "[validate-crate-publish] ${crate} dry-run failed with an unexpected error" >&2
echo "                         (not a missing-workspace-crate first-publish miss)." >&2
exit "${exit_code}"
