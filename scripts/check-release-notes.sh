#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Release-notes range guard (QUI-1096).
#
# Every GitLab release page used to republish every prior release's
# changelog: `changelog-release` passed git-cliff `--tag $(VERSION)`
# with no revision range, so git-cliff rendered the tag's section plus
# every earlier one reachable by walking the full history. Verified
# live on 2026-09-01: the v0.3.2 release page carried 7 sections, the
# v0.4.0-rc1 page carried 8.
#
# The fix derives a `PREV` tag in `changelog-release` and renders
# `PREV..VERSION` (or `PREV..HEAD --tag VERSION` for a pre-tag preview)
# instead of an unbounded `--tag VERSION`. This script is the
# regression guard for that fix: for every non-rc tag that has a
# non-rc predecessor, it renders release notes the same way
# `make changelog-release` will and asserts the render contains
# exactly one `^## \[` heading -- i.e. exactly one release section, not
# a re-run of every release ever cut.
#
# Deliberately goes through `make changelog-release` rather than
# calling git-cliff directly, so the guard exercises the exact PREV
# derivation the release job (release:notes in
# .gitlab/ci/release.yml) uses at tag time, instead of a
# reimplementation of that logic that could drift from the real
# target.
#
# Requires full tag history to mean anything -- verify:policy already
# sets `GIT_DEPTH: 0` for the same reason git-cliff itself needs it: a
# shallow clone silently truncates the commit history a range render
# walks. A clone with no tags at all (a fresh fork, or a checkout taken
# before the first tag) is a valid state and is treated as a skip, not
# a failure.
#
# Usage:
#   scripts/check-release-notes.sh
#
# Exit codes:
#   0  -- pass (every testable tag rendered exactly one section, or
#         there was nothing to test)
#   1  -- at least one tag rendered zero or more than one section
#   2  -- usage / tooling error (git-cliff or make not on PATH)

set -euo pipefail

die() {
    echo "error: $*" >&2
    exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

command -v git-cliff >/dev/null 2>&1 \
    || die "git-cliff is not on PATH (scripts/install-cargo-tools.sh --only git-cliff, or 'cargo install git-cliff')"
command -v make >/dev/null 2>&1 || die "make is not on PATH"

# --- Enumerate testable tags -------------------------------------------------

# A clone with no tags at all is a valid state, not a failure -- degrade
# quietly, the same shape scripts/check-mr-title.sh and
# scripts/check-commit-messages.sh use for an empty input.
if [[ -z "$(git tag -l)" ]]; then
    echo "guard: no tags in this clone -- nothing to verify"
    exit 0
fi

# Non-rc release tags. Mirrors .on-release-tag's shape
# (.gitlab/ci/release.yml: /^v\d/) for "is this a release tag at all",
# and cliff.toml's tag_pattern for "is it an rc".
release_tags=()
while IFS= read -r tag; do
    [[ -z "${tag}" ]] && continue
    release_tags+=("${tag}")
done < <(git tag -l 'v[0-9]*' | grep -v -- '-rc' | sort -V)

if [[ "${#release_tags[@]}" -eq 0 ]]; then
    echo "guard: no non-rc release tags in this clone -- nothing to verify"
    exit 0
fi

# A non-rc predecessor must exist, or this is the first release ever
# tagged: there is no prior section for it to accidentally absorb, and
# `make changelog-release` hits its own "neither branch resolves" error
# for exactly this input, by design (see the Makefile's PREV
# derivation). Skip that tag rather than fail on it.
testable_tags=()
for tag in "${release_tags[@]}"; do
    if git describe --tags --abbrev=0 --exclude='*-rc*' "${tag}^" >/dev/null 2>&1; then
        testable_tags+=("${tag}")
    fi
done

if [[ "${#testable_tags[@]}" -eq 0 ]]; then
    echo "guard: no non-rc tag with a non-rc predecessor -- nothing to verify"
    exit 0
fi

# --- Render each testable tag and count sections -----------------------------

failed_tags=()

for tag in "${testable_tags[@]}"; do
    rendered=""
    if ! rendered="$(make changelog-release VERSION="${tag}" STRIP=all OUTPUT=- 2>&1)"; then
        echo "" >&2
        echo "FAIL ${tag}: 'make changelog-release' exited non-zero" >&2
        echo "${rendered}" >&2
        failed_tags+=("${tag}")
        continue
    fi

    # `|| true`: grep -c exits 1 on zero matches even though the count
    # it printed ("0") is exactly the answer we want, and that exit
    # would otherwise abort the script under `set -e`.
    heading_count="$(grep -c '^## \[' <<< "${rendered}" || true)"

    if [[ "${heading_count}" -ne 1 ]]; then
        echo "" >&2
        echo "FAIL ${tag}: expected exactly 1 '## [' heading, got ${heading_count}" >&2
        failed_tags+=("${tag}")
    else
        echo "pass ${tag}: 1 section"
    fi
done

# --- Render the pre-tag preview path ----------------------------------------

# The loop above only exercises tags that already exist. That is not
# where this bug class actually bites: the pre-tag preview -- `make
# changelog-release VERSION=vX.Y.Z` against an untagged HEAD, step 4 of
# RELEASING.md's pre-flight -- takes a different branch of the PREV
# derivation AND a different git-cliff code path, because the upper
# bound of the range is a synthetic `--tag` override rather than a real
# tag. That path regressed independently of the tagged path once before
# (it rendered ZERO sections and still exited 0, so a release manager
# would have seen an empty preview with no signal anything was wrong);
# it is fixed by cliff.toml's `tag_pattern`, and this is what keeps it
# fixed.
#
# Synthesise a version rather than guessing the next release number --
# the assertion is about the shape of the render, not about which
# version ships next. It must still LOOK like a release version
# (cliff.toml's tag_pattern), or the render would take a different path
# from the real preview this is standing in for; v9999.0.0 satisfies
# that and cannot collide with a real tag.
latest_tag="${release_tags[${#release_tags[@]} - 1]}"
preview_version="v9999.0.0"

if [[ -z "$(git log --oneline "${latest_tag}..HEAD")" ]]; then
    # Nothing has landed since the last release, so a preview would
    # legitimately render an empty section. Skip rather than fail: this
    # is the normal state of a freshly-tagged tree.
    echo "skip preview: no commits since ${latest_tag}"
else
    preview_rendered=""
    if ! preview_rendered="$(make changelog-release VERSION="${preview_version}" STRIP=all OUTPUT=- 2>&1)"; then
        echo "" >&2
        echo "FAIL preview (${preview_version}): 'make changelog-release' exited non-zero" >&2
        echo "${preview_rendered}" >&2
        failed_tags+=("${preview_version}")
    else
        preview_count="$(grep -c '^## \[' <<< "${preview_rendered}" || true)"
        if [[ "${preview_count}" -ne 1 ]]; then
            echo "" >&2
            echo "FAIL preview (${preview_version}): expected exactly 1 '## [' heading, got ${preview_count}" >&2
            echo "  the pre-tag preview path renders the range ${latest_tag}..HEAD with a synthetic --tag." >&2
            echo "  A count of 0 usually means an rc tag in that range is being treated as a release" >&2
            echo "  boundary -- check cliff.toml's tag_pattern." >&2
            failed_tags+=("${preview_version}")
        else
            echo "pass preview (${preview_version}): 1 section"
        fi
    fi
fi

# --- Verdict -----------------------------------------------------------------

if [[ "${#failed_tags[@]}" -gt 0 ]]; then
    echo "" >&2
    echo "error: check-release-notes failed for: ${failed_tags[*]}" >&2
    exit 1
fi

echo "pass: ${#testable_tags[@]} tagged release(s) plus the pre-tag preview each render exactly one section"
exit 0
