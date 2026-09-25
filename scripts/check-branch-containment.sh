#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Branch containment guard (QUI-1256).
#
# The two-branch protocol's one invariant is that `main` is never
# synchronised with `dev` -- it is contained in it:
#
#     git merge-base --is-ancestor origin/main origin/dev
#
# `main` is the non-breaking line and `dev` is the breaking one, so every
# fix and every non-breaking feature lands on `main` and has to reach
# `dev` by a back-merge after each tag. Skip one and the two lines
# diverge: `dev` stops carrying what `main` shipped, and the next release
# cut from `dev` is missing it.
#
# Not the reason given in the ticket. That said a missed back-merge
# "silently reverts a hotfix", which is true only of a squashed release
# merge -- and `release/*` into `main` must not squash (see
# docs/guide/gitflow-protocol.md). A true merge keeps what `main` did
# that the branch never touched. The real cost is divergence, which is
# slower and quieter, and quiet is what a guard is for.
#
# What is judged, and when
# ------------------------
# The ref under test is the branch the work is on, which is not the same
# variable on every pipeline:
#
#   push pipeline        $CI_COMMIT_BRANCH, tested at HEAD
#   merge request        the SOURCE branch, tested at its own tip
#   tag pipeline         neither is set; nothing is judged
#   local                the checked-out branch, tested at HEAD
#
# Only `dev` and `release/*` are judged. Everything else exits 0 without
# reading anything, which is what keeps this runnable from the existing
# `lint-policy` aggregate rather than needing a `rules:`-gated job of its
# own -- .gitlab/ci/verify.yml deliberately has no `rules:` anywhere.
#
# Two consequences of that scoping are deliberate:
#
#   - `main` is never judged. It is the ref being checked *against*, and
#     it is the line consumers pin; a red `main` for the legitimate
#     window between a release merge and its back-merge costs more than
#     the late signal does.
#
#   - A merge request into `dev` is not judged, because its source branch
#     is a feature branch. Work on `dev` continues in parallel while a
#     back-merge is outstanding; what surfaces instead is a standing red
#     on `dev`'s own push pipelines until the back-merge lands. That is
#     the intended signal, not a side effect.
#
# The release path is judged, and it is the half that gates. A merge
# request from `release/vX.Y.Z` has its source branch tested, so a
# candidate that does not contain `main` cannot merge -- which is the
# case that would otherwise ship a release missing everything `main` had
# fixed. A fix landing *onto* a release branch is a feature-branch
# source and is not judged, so stabilisation is not blocked either.
#
# The source tip, never the merged result. The project has
# `merge_pipelines_enabled`, so a merge request pipeline usually runs on
# the merged result -- source already merged into target. Where the
# target is `main` that result contains `main` by construction, and
# testing it would pass every time while proving nothing. When GitLab
# cannot build a merged result it runs a detached pipeline instead, and
# there HEAD is the source tip, so HEAD is what gets tested.
#
# A missing `origin/dev` is not a pass. This guard exists because the
# failure it catches is silent, and one that read an absent branch as
# success would stay green if someone deleted `dev`. It is not reached
# before `dev` exists because the scope test excludes every other ref.
#
# What `dev` is held to
# ----------------------
# `dev` is judged against the highest release tag on `main`, not against
# `main`'s tip. The protocol back-merges after every tag, and `main`
# takes non-breaking work every day in between; judged against the tip,
# `dev` was red after almost every push and the signal meant nothing.
# Against the tag, `dev` goes red when a release ships and green at its
# back-merge. A release tag is three numeric fields and nothing else
# (cliff.toml's tag_pattern), so a beta or rc never counts. `release/*`
# is still judged against `main`'s tip: that is the gate, and a
# candidate must carry everything `main` has.
#
# Usage:
#   scripts/check-branch-containment.sh
#
# Exit codes:
#   0  -- contained, or this ref is not judged
#   1  -- the ref does not contain origin/main
#   2  -- setup error (an unresolvable ref, or origin/main absent)

set -euo pipefail

MAIN_REF="origin/main"

# --- Which ref is judged, and at which commit -------------------------------

if [[ -n "${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:-}" ]]; then
    REF_NAME="${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME}"
    # CI_MERGE_REQUEST_SOURCE_BRANCH_SHA is populated only on a
    # merged-result pipeline, which is exactly where HEAD cannot be
    # used -- see the merged-result note above. GitLab falls back to a
    # detached pipeline when it cannot build a merged result (a
    # conflicting merge request, for one), and there the variable is
    # empty but HEAD *is* the source tip. So HEAD is the right fallback,
    # not `origin/<source>`: a merge request clone does not fetch the
    # source branch under that name, and resolving it would fail the
    # release merge request with a setup error.
    REF_SHA="${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA:-HEAD}"
else
    REF_NAME="${CI_COMMIT_BRANCH:-$(git symbolic-ref --quiet --short HEAD || true)}"
    REF_SHA="HEAD"
fi

case "${REF_NAME}" in
    dev | release/*) ;;
    *)
        echo "guard: ${REF_NAME:-<detached HEAD>} is neither dev nor a release branch -- containment not checked"
        exit 0
        ;;
esac

# --- Setup ------------------------------------------------------------------

if ! git rev-parse -q --verify "${MAIN_REF}^{commit}" >/dev/null; then
    echo "error: containment: ${MAIN_REF} is not in this clone." >&2
    echo "error: containment: fetch it (git fetch origin main) and run again." >&2
    exit 2
fi

BASE_REF="${MAIN_REF}"
if [[ "${REF_NAME}" == "dev" ]]; then
    # See "What `dev` is held to" above. No release tag is a setup
    # error, not a pass: the guard must not go quiet because the clone
    # was fetched without tags.
    BASE_REF="$(git tag --merged "${MAIN_REF}" --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-version:refname \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n1 || true)"
    if [[ -z "${BASE_REF}" ]]; then
        echo "error: containment: no release tag is reachable from ${MAIN_REF}." >&2
        echo "error: containment: fetch tags (git fetch --tags origin) and run again." >&2
        exit 2
    fi
fi

if ! git rev-parse -q --verify "${REF_SHA}^{commit}" >/dev/null; then
    echo "error: containment: cannot resolve ${REF_SHA} for branch ${REF_NAME}." >&2
    exit 2
fi

# --- The invariant ----------------------------------------------------------

if git merge-base --is-ancestor "${BASE_REF}" "${REF_SHA}"; then
    echo "containment OK: ${BASE_REF} is contained in ${REF_NAME}"
    exit 0
fi

missing="$(git rev-list --count "${REF_SHA}..${BASE_REF}")"

{
    echo ""
    echo "FAIL: ${REF_NAME} does not contain ${BASE_REF}."
    echo ""
    echo "  ${missing} commit(s) are on ${BASE_REF} and not on ${REF_NAME}:"
    git log --oneline --no-decorate -10 "${REF_SHA}..${BASE_REF}" | sed 's/^/    /'
    if [[ "${missing}" -gt 10 ]]; then
        echo "    ... and $((missing - 10)) more"
    fi
    echo ""
    echo "  A back-merge is outstanding. The recipe is in"
    echo "  docs/guide/gitflow-protocol.md, under \"Back-merging\": carry this"
    echo "  branch's version onto a scratch branch off origin/main first, so the"
    echo "  version sites never conflict and what does is a real conflict."
    echo ""
} >&2

exit 1
