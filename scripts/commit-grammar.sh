#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Shared Conventional Commits + DCO grammar.
#
# Sourced by both .githooks/commit-msg (local, opt-in, one staged
# commit at a time) and scripts/check-commit-messages.sh (CI, the full
# commit range of a merge request), so the two enforcement points
# cannot drift into different rules. See AGENTS.md's Conventional
# Commits section for the human-readable version of what this encodes.
#
# Not standalone: `source` it, don't execute it directly.

# Keep in sync with cliff.toml's `commit_parsers`: `security` and
# `deprecate` are changelog groups there, so a commit of either type
# must be able to pass this grammar or git-cliff would never see it
# (it would already have been rejected as an invalid subject).
TYPES="feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|security|deprecate"
PATTERN="^(${TYPES})(\([a-zA-Z0-9_-]+\))?\!?: .+"

# Matches a GitLab-generated merge commit subject, in either the
# historical "Merge branch '...' into ..." form or the newer "merge:
# ..." form this repo also uses. "merge" is deliberately not a
# Conventional Commits type (see TYPES above), and these subjects are
# server-generated rather than authored, so grammar checking does not
# apply to them. Mirrors cliff.toml's first-position
# `{ message = "^[Mm]erge", skip = true }` parser.
MERGE_SUBJECT_PATTERN="^[Mm]erge"

# True when `subject` is a merge-commit subject that should skip
# grammar checking entirely.
is_merge_subject() {
    [[ "$1" =~ ${MERGE_SUBJECT_PATTERN} ]]
}

# Extract the commit subject from a raw commit message read on stdin:
# strip `#`-prefixed comment lines (git's own commit-msg file
# convention), then take the first non-blank line.
commit_subject() {
    sed '/^#/d' | sed '/./,$!d' | head -n1
}

# Validate one commit subject against Conventional Commits: known
# type, optional scope, optional `!`, max 72 characters, lowercase
# description start, no trailing period. Prints one error line per
# violation to stderr; returns non-zero if any violation was found.
check_commit_subject() {
    local subject="$1" desc first_char status=0

    if ! printf '%s\n' "${subject}" | grep -Eq "${PATTERN}"; then
        echo "commit-msg: subject does not match Conventional Commits format." >&2
        echo "" >&2
        echo "  Expected: <type>[(<scope>)][!]: <description>" >&2
        echo "  Types:    $(tr '|' ' ' <<< "${TYPES}")" >&2
        echo "" >&2
        echo "  Got: ${subject}" >&2
        status=1
    fi

    if [[ "${#subject}" -gt 72 ]]; then
        echo "commit-msg: subject exceeds 72 characters (${#subject})." >&2
        echo "  ${subject}" >&2
        status=1
    fi

    desc="$(printf '%s\n' "${subject}" | sed -E "s/^(${TYPES})(\([a-zA-Z0-9_-]+\))?\!?: //")"
    first_char="$(printf '%s' "${desc}" | cut -c1)"
    if printf '%s' "${first_char}" | grep -q '[A-Z]'; then
        echo "commit-msg: description must start lowercase." >&2
        echo "  Got: ${subject}" >&2
        status=1
    fi

    if printf '%s' "${subject}" | grep -q '\.$'; then
        echo "commit-msg: subject must not end with a period." >&2
        echo "  Got: ${subject}" >&2
        status=1
    fi

    return "${status}"
}

# Check the DCO sign-off trailer on a raw commit message read on
# stdin. Prints an error to stderr and returns non-zero when absent.
check_signed_off() {
    if ! grep -qi '^Signed-off-by:' -; then
        echo "commit-msg: missing Signed-off-by line (use git commit -s)." >&2
        return 1
    fi
    return 0
}
