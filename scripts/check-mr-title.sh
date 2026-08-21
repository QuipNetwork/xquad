#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Merge request title guard.
#
# The MR title used to be checked for free. With a merge train and
# squash-on-merge, the squash commit's subject *is* the MR title
# (`squash_commit_template = %{title}`), so that subject reached
# scripts/check-commit-messages.sh as an authored commit and the
# shared grammar rejected a non-conventional title before it could
# land on main. Disabling the merge train removed that: merges now
# produce a GitLab-generated `merge: branch '...' into '...'` subject,
# which the range guard deliberately skips, and the MR title is never
# checked by anything. This script is the replacement -- it runs the
# same grammar (scripts/commit-grammar.sh) directly against the title.
#
# Two rules from the commit path deliberately do not carry over:
#
#   - No DCO sign-off check. A title is not a commit message and has
#     no trailer block; the sign-off requirement is enforced per
#     commit by scripts/check-commit-messages.sh.
#   - No merge-subject exemption. `is_merge_subject` exists because
#     GitLab *generates* merge commit subjects; an MR title is always
#     authored, so a title starting with "Merge" is a real violation
#     and is reported as one rather than skipped.
#
# A `Draft:` (or legacy `WIP:`) prefix is stripped before checking.
# GitLab prepends it to $CI_MERGE_REQUEST_TITLE while the MR is a
# draft, and it is removed by the author before merge, so failing on
# it would make every draft pipeline red for a state that cannot
# reach main.
#
# Called directly from verify:policy's script block, not through the
# lint-policy make aggregate the other checks in that job go through.
# Every one of those reads the working tree or git history, so a
# target keeps them locally runnable; this one's only input is
# pipeline metadata with no local equivalent, so a target could never
# do more than print a skip line. See .gitlab/ci/verify.yml's
# verify:policy job for the full reasoning.
#
# Usage:
#   scripts/check-mr-title.sh [TITLE]
#
# With no argument the title comes from $CI_MERGE_REQUEST_TITLE. When
# neither is set the check is a no-op that exits 0. verify:policy runs
# on every pipeline, not just merge request ones, so this no-op is
# what lets the check ride that job without a rule of its own -- the
# same degrade-quietly shape the range-based guards use on an empty
# commit range. Pass TITLE explicitly to try a candidate title
# locally.
#
# Exit codes:
#   0  -- the title passes, or there is no title to check
#   1  -- the title fails the grammar
#   2  -- usage / setup error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=commit-grammar.sh
source "${SCRIPT_DIR}/commit-grammar.sh"

# --- Argument / environment resolution -------------------------------------

if [[ "$#" -gt 1 ]]; then
    echo "usage: $(basename "$0") [TITLE]" >&2
    exit 2
fi

TITLE="${1:-${CI_MERGE_REQUEST_TITLE:-}}"

if [[ -z "${TITLE}" ]]; then
    echo "guard: no merge request title to check (not a merge request pipeline)"
    exit 0
fi

# --- Strip any draft marker -------------------------------------------------

# GitLab documents exactly three draft prefixes -- `[Draft]`, `Draft:`
# and `(Draft)` -- so those three, case-insensitively, are the whole
# set this strips. The loop removes a doubled-up prefix rather than
# half-removing it; each iteration consumes at least six characters, so
# the subject strictly shrinks and the loop terminates.
#
# Matching a looser set is not the safe direction. `WIP` stopped being
# a draft marker in GitLab 14.0, so `WIP: feat(ci): x` is an ordinary
# title: stripping it would pass a subject that then lands on main
# verbatim through squash_commit_template and gets dropped from the
# release notes by filter_unconventional -- the exact failure this
# script exists to catch. The same argument rules out treating a bare
# space as a separator (`Draft feat(x): y`) or letting the brackets go
# unbalanced (`Draft]: ...`), both of which an earlier revision of this
# pattern accepted. Anything not on GitLab's list is title text and is
# judged as title text.
title="${TITLE}"
shopt -s nocasematch
while [[ "${title}" =~ ^[[:space:]]*(\[draft\]|\(draft\)|draft:)[[:space:]]*(.*)$ ]]; do
    title="${BASH_REMATCH[2]}"
done
shopt -u nocasematch

if [[ -z "${title}" ]]; then
    echo "mr-title: title is empty once the draft marker is stripped." >&2
    echo "  Got: ${TITLE}" >&2
    exit 1
fi

# Report the untouched title alongside the checked one whenever stripping
# changed it, so an author is never shown an error quoting a string they
# did not type.
if [[ "${title}" != "${TITLE}" ]]; then
    ORIGINAL_NOTE="  (draft prefix stripped from: ${TITLE})"
else
    ORIGINAL_NOTE=""
fi

# --- Validate ---------------------------------------------------------------

if ! check_commit_subject "${title}" "mr-title"; then
    echo "" >&2
    echo "FAIL mr-title: ${title}" >&2
    if [[ -n "${ORIGINAL_NOTE}" ]]; then
        echo "${ORIGINAL_NOTE}" >&2
    fi
    exit 1
fi

echo "pass mr-title: ${title}"
exit 0
