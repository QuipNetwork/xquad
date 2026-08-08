#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Atomic spec-MR guard.
#
# Enforces the xquad two-impl development discipline: any MR that
# touches VM semantics must touch **all four** of
#
#   1. spec/xqvm/*.md                — the normative specification
#   2. xqvm/src/**/*.rs              — the Rust production impl
#   3. xqvm_py/{executor,opcodes,xqmx,state,vector,tracer,errors}.py
#                                     — the Python reference impl
#   4. conformance/vectors/** or conformance/opcodes.yaml
#                                     — cross-impl parity coverage
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
#     HEAD_REF = HEAD.
#   - Locally: BASE_REF = $(git merge-base origin/main HEAD),
#     HEAD_REF = HEAD.
#
# Escape hatch:
#   If a commit message in the MR range carries a git-style trailer
#   `Atomic-Spec-Exempt: <reason>` on its own line, the guard is
#   bypassed. Use this for deliberately one-sided changes (e.g. a
#   Python-only fix that aligns to existing Rust behaviour). The
#   trailer format is deliberate — prose mentions of the token in a
#   commit body don't accidentally bypass the guard.
#
# Exit codes:
#   0  — pass (zero layers touched, all four touched, or exempt)
#   1  — fail (partial change, no exemption)
#   2  — usage / setup error

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

HEAD_REF="${2:-HEAD}"

if ! git rev-parse --verify "${BASE_REF}" >/dev/null 2>&1; then
    echo "error: base ref '${BASE_REF}' does not resolve" >&2
    exit 2
fi
if ! git rev-parse --verify "${HEAD_REF}" >/dev/null 2>&1; then
    echo "error: head ref '${HEAD_REF}' does not resolve" >&2
    exit 2
fi

# --- Escape hatch: commit-message exemption --------------------------------

_has_exempt() {
    git log --format=%B "${1}..${2}" \
        | grep -Eq '^[[:space:]]*Atomic-Spec-Exempt:[[:space:]]*[^[:space:]]'
}

if _has_exempt "${BASE_REF}" "${HEAD_REF}"; then
    echo "guard: Atomic-Spec-Exempt trailer present in a commit message — bypassed"
    exit 0
fi

# In squash-merge train pipelines the HEAD is a squash commit that discards
# individual commit messages. Re-scan the un-squashed branch tip when
# CI_MERGE_REQUEST_SOURCE_BRANCH_SHA is available (set by GitLab in all MR
# and merge-train pipeline contexts).
if [[ -n "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA:-}" ]] \
    && git rev-parse --verify "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA}" >/dev/null 2>&1 \
    && _has_exempt "${BASE_REF}" "${CI_MERGE_REQUEST_SOURCE_BRANCH_SHA}"; then
    echo "guard: Atomic-Spec-Exempt trailer present in branch commit (squash train) — bypassed"
    exit 0
fi

# --- Classify changed files into the four layers ---------------------------

# Two-dot A..B (direct diff) rather than three-dot A...B (symmetric
# difference from merge-base). By this point BASE_REF is already a
# resolved merge-base SHA — either GitLab's $CI_MERGE_REQUEST_DIFF_BASE_SHA
# or a local `git merge-base origin/main HEAD` — so `..` is what we want:
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

    # Layer 1 — normative spec
    if [[ "${file}" == spec/xqvm/*.md ]]; then
        has_spec=1
        touched_spec+=("${file}")
        continue
    fi

    # Layer 2 — Rust production impl. Every .rs file under xqvm/src/
    # counts at any nesting depth; xqvm/build.rs, Cargo.toml, README
    # are build glue and don't carry semantics. Bash `[[ == ]]` globs
    # don't recurse, so we pair a prefix check with a suffix check
    # rather than chaining fixed-depth `*/*` patterns.
    if [[ "${file}" == xqvm/src/* ]] && [[ "${file}" == *.rs ]]; then
        has_xqvm=1
        touched_xqvm+=("${file}")
        continue
    fi

    # Layer 3 — Python reference impl. Restricted to the core modules;
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

    # Layer 4 — cross-impl parity coverage.
    if [[ "${file}" == conformance/opcodes.yaml ]] || [[ "${file}" == conformance/vectors/* ]]; then
        has_conformance=1
        touched_conformance+=("${file}")
        continue
    fi
done <<< "${changed_files}"

sum=$((has_spec + has_xqvm + has_xqvm_py + has_conformance))

# --- Verdict ---------------------------------------------------------------

if [[ "${sum}" -eq 0 ]]; then
    echo "guard: no VM-semantics layer touched — guard does not apply"
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

# Partial change — report cleanly and fail.
echo "error: atomic spec-MR guard failed — VM-semantics MRs must touch all four layers."
echo ""
echo "  spec        (spec/xqvm/*.md)                                    : $([[ ${has_spec} -eq 1 ]] && echo '✓' || echo '✗')"
echo "  xqvm        (xqvm/src/**/*.rs)                                  : $([[ ${has_xqvm} -eq 1 ]] && echo '✓' || echo '✗')"
echo "  xqvm_py     (xqvm_py/{executor,opcodes,xqmx,state,vector,..}.py): $([[ ${has_xqvm_py} -eq 1 ]] && echo '✓' || echo '✗')"
echo "  conformance (conformance/{vectors/**,opcodes.yaml})             : $([[ ${has_conformance} -eq 1 ]] && echo '✓' || echo '✗')"
echo ""
echo "If this MR is deliberately one-sided (e.g. a Python-only fix aligning to"
echo "existing Rust behaviour), add a commit-message trailer of the form:"
echo ""
echo "    Atomic-Spec-Exempt: <reason>"
echo ""
echo "See docs/guide/development-workflow.md for the contract."
exit 1
