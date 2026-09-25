#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Atomic spec-MR guard.
#
# Enforces the xquad two-impl development discipline: any MR that
# touches VM semantics must touch **all four** of
#
#   1. spec/xqvm/*.md                -- the normative specification
#   2. xqvm/src/**/*.rs              -- the Rust production impl
#   3. xqvm_py/{executor,opcodes,xqmx,state,vector,tracer,errors}.py
#                                     -- the Python reference impl
#   4. xqvm/tests/vectors/** or xqvm/opcodes.yaml
#                                     -- the specification vectors and table
#
# in the same MR. Partial changes silently create drift between the
# spec and the two implementations; this guard catches them at CI
# time so the atomic MR convention is enforced rather than just
# documented.
#
# Usage:
#   scripts/check-atomic-spec-mr.sh [BASE_REF] [HEAD_REF]
#
# Defaults:
#   - In GitLab CI: BASE_REF = $CI_MERGE_REQUEST_DIFF_BASE_SHA,
#     HEAD_REF = $CI_MERGE_REQUEST_SOURCE_BRANCH_SHA, then HEAD.
#   - Locally: BASE_REF = $(git merge-base origin/main HEAD),
#     HEAD_REF = HEAD.
#
# HEAD is the wrong head ref in a merged-result pipeline, where $HEAD is
# the source merged into the target. BASE_REF..HEAD then spans the files
# the target gained since the merge-base as well as the ones the merge
# request touched, which fails a docs-only MR the moment main gains an
# unrelated xqvm/src commit -- a finding whose only remedy is a rebase,
# which has nothing to do with the four-layer rule. It also widens the
# exemption scan below, so an Atomic-Spec-Exempt trailer on any main
# commit would bypass the guard for every open merge request.
#
# This is the same resolution scripts/check-commit-messages.sh does, for
# the same reason. Note it is a different question from the two-dot
# versus three-dot choice at the diff below: that comment argues for
# `..` so target-drift is not masked in stacked MRs, and it assumes a
# HEAD_REF that is the branch tip. Preferring the source branch sha is
# what makes that assumption true.
#
# Escape hatch:
#   If a commit message in the MR range carries a git-style trailer
#   `Atomic-Spec-Exempt: QUI-<id> <reason>` at column 0, on one line,
#   the guard is bypassed. Use this for deliberately one-sided changes
#   (e.g. a Python-only fix that aligns to existing Rust behaviour). The
#   format is enforced, not merely suggested: a trailer that wraps or
#   names no ticket fails the guard instead of bypassing it. See
#   _validate_exempt_trailers below for why.
#
# Exit codes:
#   0  -- pass (zero layers touched, all four touched, or exempt)
#   1  -- fail (partial change with no exemption, or a malformed trailer)
#   2  -- usage / setup error

set -euo pipefail

# --- Argument / environment resolution -------------------------------------

BASE_REF="${1:-}"
if [[ -z "${BASE_REF}" ]]; then
    BASE_REF="${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}"
fi
if [[ -z "${BASE_REF}" ]]; then
    if ! BASE_REF="$(git merge-base origin/main HEAD 2>/dev/null)"; then
        echo "error: could not derive BASE_REF (no arg, no CI var, no origin/main)" >&2
        exit 2
    fi
fi

HEAD_REF="${2:-}"
if [[ -z "${HEAD_REF}" ]]; then
    if [[ -n "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA:-}" ]] \
        && git rev-parse --verify "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA}^{commit}" >/dev/null 2>&1; then
        HEAD_REF="${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA}"
    else
        HEAD_REF="HEAD"
    fi
fi

if ! git rev-parse --verify "${BASE_REF}^{commit}" >/dev/null 2>&1; then
    echo "error: base ref '${BASE_REF}' does not resolve" >&2
    exit 2
fi
if ! git rev-parse --verify "${HEAD_REF}^{commit}" >/dev/null 2>&1; then
    echo "error: head ref '${HEAD_REF}' does not resolve" >&2
    exit 2
fi

# --- Escape hatch: commit-message exemption --------------------------------

# A trailer has to mean the same thing to this guard, to git, and to a
# reviewer, and until QUI-1030 it did not: the check here was a single
# line-anchored grep for the token, which accepted whatever followed it.
#
# What git actually does with a commit message, all four rules verified
# against git 2.50.1, because only the first is widely known:
#
#   1. Only the message's LAST paragraph is examined. A trailer in an
#      earlier paragraph is invisible. Every Atomic-Spec-Exempt trailer in
#      this repository's history is in its own paragraph above the
#      `Implements QUI-NNN` footer and the sign-off, so not one of them
#      parses.
#   2. A well-known trailer in that paragraph -- `Signed-off-by:`, which
#      DCO makes mandatory here -- switches git into a tolerant mode where
#      non-trailer lines beside it are dropped rather than fatal.
#   3. Without such a trailer, ONE non-trailer line in the paragraph kills
#      recognition of all of it.
#   4. An unindented continuation is a non-trailer line, so under rule 2 a
#      wrapped reason is silently TRUNCATED to its first line, and under
#      rule 3 it disappears entirely.
#
# Rule 4 is the nastiest, because under rule 2 the guard would see a
# perfectly good ticket-bearing value while a reviewer reading
# `git log --format=%(trailers)` sees half a sentence. Comparing what git
# reports against what the file says does NOT catch it -- any sed that
# reads the trailer line cuts the reason in exactly the same place git
# does. What catches it is asking whether git KEPT the line directly
# below.
#
# So both questions go to git, and neither reimplements its grammar. A
# hand-rolled predicate is what makes the guard and git disagree, which is
# the defect being fixed and not a shape to repeat: the first attempt at
# this check tested "the next line looks like Token: value" and rejected
# the repository's own documented `Fixes QUI-NNN` footer, which has no
# colon and is legal beside a sign-off under rule 2.
#
# The cost is one ordering constraint. A line git discards may not sit
# DIRECTLY below the trailer, because there is no way to tell a wrapped
# reason from a bare-word footer once git has dropped both -- and reading
# the difference out of the prose is the guessing this check exists to
# avoid. A `Fixes QUI-NNN` footer goes above the trailer, or below the
# sign-off. The diagnostic says so and names both causes rather than
# picking one.
#
# Detecting that an exemption was ATTEMPTED stays looser than git's
# grammar -- a column-0 token line anywhere in the message -- because that
# is what separates "no exemption wanted" from "exemption wanted and
# broken", and only the second should be loud. Column 0 also keeps an
# indented example in a commit body, the shape this repository's own
# documentation commits use, from reading as a real exemption.
#
# That looseness is for DETECTION only. Everything after it -- the
# below-the-line check and the ticket check -- runs over the LAST
# PARAGRAPH and over git's parse of it, never over the whole message. The
# distinction is load-bearing: a commit body may quote the trailer form in
# prose at column 0, and a message may carry more than one exemption. A
# check anchored on the first column-0 match anywhere sees neither
# correctly -- it reads a sentence of prose as the line below the trailer
# and rejects a well-formed exemption, and it never looks at a second
# trailer at all.

# Echo the message's last paragraph: every line after the final blank one.
# This is the block git reads trailers out of, and the only block the
# checks below have any business looking at.
_last_paragraph() {
    awk '
        /^[[:space:]]*$/ { buf = ""; next }
        { buf = buf $0 "\n" }
        END { printf "%s", buf }
    '
}

# Echo the line directly below each `Atomic-Spec-Exempt:` trailer in the
# last paragraph, one per trailer. A trailer that ends the paragraph has
# no line below it and contributes nothing.
#
# A one-line lookback rather than `getline`: reading ahead consumes the
# next record, so back-to-back trailers would leave the second one
# unscanned -- the first match would swallow it, and the line under it
# would never be looked at.
_lines_below_exempt_trailers() {
    awk '
        below { print; below = 0 }
        /^Atomic-Spec-Exempt:/ { below = 1 }
    '
}

_validate_exempt_trailers() {
    local sha="${1}" message paragraph parsed trailers next token value

    message="$(cat)"

    if ! grep -q '^Atomic-Spec-Exempt:' <<< "${message}"; then
        return 2
    fi

    paragraph="$(_last_paragraph <<< "${message}")"
    parsed="$(git interpret-trailers --parse <<< "${message}")"
    trailers="$(grep '^Atomic-Spec-Exempt:' <<< "${parsed}" || true)"

    if [[ -z "${trailers}" ]]; then
        {
            echo "  ${sha}: git does not parse the Atomic-Spec-Exempt line as a trailer,"
            echo "  ${sha}:   so nothing that reads trailers can see this exemption."
            echo "  ${sha}:   It must sit in the message's LAST paragraph, beside the"
            echo "  ${sha}:   sign-off. A paragraph break above the trailer, or a missing"
            echo "  ${sha}:   sign-off, hides it."
        } >&2
        return 1
    fi

    while IFS= read -r next; do
        [[ -z "${next//[[:space:]]/}" ]] && continue
        token="${next%%:*}"
        if [[ "${token}" == "${next}" ]] || ! grep -q "^${token}:" <<< "${parsed}"; then
            {
                echo "  ${sha}: the line below the Atomic-Spec-Exempt trailer is one git"
                echo "  ${sha}:   discards, so it is either a wrapped reason git is silently"
                echo "  ${sha}:   truncating, or a footer in the wrong place:"
                echo "  ${sha}:     ${next}"
                echo "  ${sha}:   Put the whole reason on the trailer line. Move a"
                echo "  ${sha}:   \`Fixes QUI-123\` footer above the trailer, or below the"
                echo "  ${sha}:   sign-off."
            } >&2
            return 1
        fi
    done < <(_lines_below_exempt_trailers <<< "${paragraph}")

    # Per trailer, not over the concatenation: one ticketed exemption must
    # not excuse an unticketed one beside it. Iterating whole trailer lines
    # out of `--parse` rather than sed-stripped values also keeps a folded
    # continuation line, which carries no ticket of its own, out of the
    # loop -- though such a message is rejected above before reaching here.
    while IFS= read -r value; do
        value="${value#Atomic-Spec-Exempt:}"
        value="${value#"${value%%[![:space:]]*}"}"
        if ! grep -Eq 'QUI-[0-9]+' <<< "${value}"; then
            echo "  ${sha}: Atomic-Spec-Exempt trailer names no QUI ticket: ${value}" >&2
            return 1
        fi
    done <<< "${trailers}"

    return 0
}

# Scan a commit range and report the strongest verdict found:
#
#   0  at least one well-formed trailer and no malformed one -- bypass
#   1  at least one malformed trailer -- fail, with diagnostics on stderr
#   2  no trailer anywhere in the range
#
# Per commit rather than over one concatenated `--format=%B` blob, so a
# diagnostic can name the commit that carries the bad trailer, and so a
# following commit's subject can never be mistaken for a wrapped reason.
#
# Deliberately not a pipeline. Under `set -o pipefail`, `git log | grep -q`
# is a race: `grep -q` exits at the first match, `git log` then dies of
# SIGPIPE and returns 141, and the pipeline reports 141 even though the
# trailer was found. The guard read that as "no exemption" and failed with a
# message asking for the trailer that was already present, on roughly two
# runs in three. Capture first, match second.
_exempt_status() {
    local shas sha short status=2 rc
    shas="$(git log --format=%H "${1}..${2}")"
    while IFS= read -r sha; do
        [[ -z "${sha}" ]] && continue
        short="$(git rev-parse --short "${sha}")"
        rc=0
        _validate_exempt_trailers "${short}" <<< "$(git log -1 --format=%B "${sha}")" || rc=$?
        case "${rc}" in
            0) [[ "${status}" -eq 2 ]] && status=0 ;;
            1) status=1 ;;
        esac
    done <<< "${shas}"
    return "${status}"
}

_report_bad_exempt() {
    echo "" >&2
    echo "guard: malformed Atomic-Spec-Exempt trailer, listed above." >&2
    echo "" >&2
    echo "The trailer goes at column 0 in the message's LAST paragraph," >&2
    echo "beside the sign-off, with the whole reason and a ticket on the one" >&2
    echo "line. A \`Fixes QUI-NNN\` footer may share the paragraph, above the" >&2
    echo "trailer or below the sign-off, but not directly beneath it:" >&2
    echo "" >&2
    echo "    Fixes QUI-453" >&2
    echo "    Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change" >&2
    echo "    Signed-off-by: You <you@example.com>" >&2
    echo "" >&2
    echo "See docs/guide/development-workflow.md for the contract." >&2
    exit 1
}

exempt_status=0
_exempt_status "${BASE_REF}" "${HEAD_REF}" || exempt_status=$?
case "${exempt_status}" in
    0)
        echo "guard: Atomic-Spec-Exempt trailer present in a commit message -- bypassed"
        exit 0
        ;;
    1) _report_bad_exempt ;;
esac

# In squash-merge train pipelines the HEAD is a squash commit that discards
# individual commit messages. Re-scan the un-squashed branch tip when
# CI_MERGE_REQUEST_SOURCE_BRANCH_SHA is available (set by GitLab in all MR
# and merge-train pipeline contexts).
#
# Normally redundant since HEAD_REF now resolves to that same sha, so the
# scan above already covered it. It still earns its place for a caller
# that passes HEAD_REF explicitly as $2 while the variable is set, and it
# costs one rev-parse when it does not fire. Kept rather than deleted
# because removing it would change behaviour for that caller.
#
# The `^{commit}` suffix on every rev-parse in this script is
# load-bearing. `git rev-parse --verify` answers "does this parse to one
# object name", not "does that object exist": a full 40-hex sha needs no
# object lookup to parse, so the bare form returns it whether or not the
# clone fetched it. Every sha this script validates -- both CI variables
# and this one -- is full 40-hex, so the bare form was blind to exactly
# the input class it receives. Peeling to ^{commit} forces git to read
# the object's type, which makes existence a precondition. Without it a
# missing sha here silently skips the bypass (`git log` on an unknown
# range makes _exempt_status find no commits and so report 2, "no
# trailer") and an MR with a legitimate Atomic-Spec-Exempt trailer is
# blocked.
if [[ -n "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA:-}" ]] \
    && git rev-parse --verify "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA}^{commit}" >/dev/null 2>&1; then
    exempt_status=0
    _exempt_status "${BASE_REF}" "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA}" || exempt_status=$?
    case "${exempt_status}" in
        0)
            echo "guard: Atomic-Spec-Exempt trailer present in branch commit (squash train) -- bypassed"
            exit 0
            ;;
        1) _report_bad_exempt ;;
    esac
fi

# --- Classify changed files into the four layers ---------------------------

# Two-dot A..B (direct diff) rather than three-dot A...B (symmetric
# difference from merge-base). By this point BASE_REF is already a
# resolved merge-base SHA -- either GitLab's $CI_MERGE_REQUEST_DIFF_BASE_SHA
# or a local `git merge-base origin/main HEAD` -- so `..` is what we want:
# files the MR actually touched, not files the target gained since the
# merge-base (which `...` would additionally exclude via symmetric
# difference, masking target-drift in stacked MRs).
changed_files="$(git diff --name-only "${BASE_REF}..${HEAD_REF}")"

has_spec=0
has_xqvm=0
has_xqvm_py=0
has_conformance=0

touched_spec=()
touched_xqvm=()
touched_xqvm_py=()
touched_conformance=()

while IFS= read -r file; do
    [[ -z "${file}" ]] && continue

    # Layer 1 -- normative spec
    if [[ "${file}" == spec/xqvm/*.md ]]; then
        has_spec=1
        touched_spec+=("${file}")
        continue
    fi

    # Layer 2 -- Rust production impl. Every .rs file under xqvm/src/
    # counts at any nesting depth; xqvm/build.rs, Cargo.toml, README
    # are build glue and don't carry semantics. Bash `[[ == ]]` globs
    # don't recurse, so we pair a prefix check with a suffix check
    # rather than chaining fixed-depth `*/*` patterns.
    if [[ "${file}" == xqvm/src/* ]] && [[ "${file}" == *.rs ]]; then
        has_xqvm=1
        touched_xqvm+=("${file}")
        continue
    fi

    # Layer 3 -- Python reference impl. Restricted to the core modules;
    # glue (program.py, __init__.py, __main__.py, cli/**) and tests
    # don't count.
    case "${file}" in
        xqvm_py/executor.py \
        | xqvm_py/opcodes.py \
        | xqvm_py/xqmx.py \
        | xqvm_py/state.py \
        | xqvm_py/vector.py \
        | xqvm_py/tracer.py \
        | xqvm_py/errors.py)
            has_xqvm_py=1
            touched_xqvm_py+=("${file}")
            continue
            ;;
    esac

    # Layer 4 -- the specification vectors and the opcode table.
    if [[ "${file}" == xqvm/opcodes.yaml ]] || [[ "${file}" == xqvm/tests/vectors/* ]]; then
        has_conformance=1
        touched_conformance+=("${file}")
        continue
    fi
done <<< "${changed_files}"

sum=$((has_spec + has_xqvm + has_xqvm_py + has_conformance))

# --- Verdict ---------------------------------------------------------------

if [[ "${sum}" -eq 0 ]]; then
    echo "guard: no VM-semantics layer touched -- guard does not apply"
    exit 0
fi

if [[ "${sum}" -eq 4 ]]; then
    echo "guard: all four layers touched ✓"
    echo "       spec        : ${touched_spec[*]}"
    echo "       xqvm        : ${touched_xqvm[*]}"
    echo "       xqvm_py     : ${touched_xqvm_py[*]}"
    echo "       conformance : ${touched_conformance[*]}"
    exit 0
fi

# Partial change -- report cleanly and fail.
echo "error: atomic spec-MR guard failed -- VM-semantics MRs must touch all four layers."
echo ""
echo "  spec        (spec/xqvm/*.md)                                    : $([[ ${has_spec} -eq 1 ]] && echo '✓' || echo '✗')"
echo "  xqvm        (xqvm/src/**/*.rs)                                  : $([[ ${has_xqvm} -eq 1 ]] && echo '✓' || echo '✗')"
echo "  xqvm_py     (xqvm_py/{executor,opcodes,xqmx,state,vector,..}.py): $([[ ${has_xqvm_py} -eq 1 ]] && echo '✓' || echo '✗')"
echo "  vectors     (xqvm/{tests/vectors/**,opcodes.yaml})              : $([[ ${has_conformance} -eq 1 ]] && echo '✓' || echo '✗')"
echo ""
echo "If this MR is deliberately one-sided (e.g. a Python-only fix aligning to"
echo "existing Rust behaviour), add a commit-message trailer at column 0 in the"
echo "last paragraph, on one line, naming the ticket the exemption belongs to:"
echo ""
echo "    Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change"
echo "    Signed-off-by: You <you@example.com>"
echo ""
echo "See docs/guide/development-workflow.md for the contract."
exit 1
