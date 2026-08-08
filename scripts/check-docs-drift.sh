#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Documentation drift guard.
#
# Enforces the QUI-853 documentation architecture rule that known-stale
# legacy prose cannot silently spread through the published mdBook. The guard
# scans book Markdown for pre-rename branding, dead crate paths, the removed
# `xq` binary name, and jump-table prose, then checks that every book page is
# listed in SUMMARY.md.
#
# Usage:
#   scripts/check-docs-drift.sh
#
# Escape hatch:
#   Known legacy prose is allowlisted below as rule-scoped entries of the form
#   `rule|path|count|reason`. Every entry must name QUI-977, must point at an
#   existing file, must still match its rule, and must not gain more matches
#   than the count records. Remove each entry in the same MR that fixes the
#   corresponding page.
#
# Exit codes:
#   0  -- pass
#   1  -- drift detected, stale allowlist entry, or SUMMARY coverage mismatch
#   2  -- setup error

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BOOK_SRC="${REPO_ROOT}/docs/book/src"
SUMMARY="${BOOK_SRC}/SUMMARY.md"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

RULE_NAMES=()
RULE_PATTERNS=()
ALLOW=()

add_rule() {
    RULE_NAMES+=("${1}")
    RULE_PATTERNS+=("${2}")
}

allow() {
    ALLOW+=("${1}|${2}|${3}|${4}")
}

add_rule "aglais-product" '(^|[^[:alnum:]_-])[Aa]glais'
allow "aglais-product" "docs/book/src/README.md" "6" "Legacy Aglais platform and crate-name prose; QUI-977 naming sweep must replace it."
allow "aglais-product" "docs/book/src/xqvm/assembly.md" "1" "Legacy assembler crate name in moved XQVM prose; QUI-977 naming sweep must replace it."
allow "aglais-product" "docs/book/src/xqvm/instructions/energy.md" "1" "Legacy host type path in moved instruction prose; QUI-977 naming sweep must replace it."
allow "aglais-product" "docs/book/src/embedding/builder-api.md" "5" "Legacy builder crate imports in moved embedding prose; QUI-977 naming sweep must replace them."
allow "aglais-product" "docs/book/src/embedding/pallet.md" "1" "Legacy VM error type path in moved pallet prose; QUI-977 naming sweep must replace it."

add_rule "aglais-crate" 'aglais-xqvm-'
allow "aglais-crate" "docs/book/src/README.md" "5" "Legacy hyphenated crate-name table; QUI-977 naming sweep must replace it."
allow "aglais-crate" "docs/book/src/xqvm/assembly.md" "1" "Legacy hyphenated assembler crate name; QUI-977 naming sweep must replace it."

add_rule "crates-path" '(^|[^/.[:alnum:]])crates/'
allow "crates-path" "docs/book/src/README.md" "1" "Legacy pre-workspace opcode-table path; QUI-977 path sweep must replace it."
allow "crates-path" "docs/book/src/xqvm/assembly.md" "1" "Legacy pre-workspace grammar path; QUI-977 path sweep must replace it."
allow "crates-path" "docs/book/src/xqvm/limits-and-errors.md" "1" "Legacy pre-workspace VM error path; QUI-977 path sweep must replace it."

add_rule "xq-binary" '(^|[^[:alnum:]_.-])xq($|[^[:alnum:]_])'
allow "xq-binary" "docs/book/src/README.md" "2" "Legacy binary name in introduction and CLI table; QUI-977 CLI sweep must replace it."
allow "xq-binary" "docs/book/src/start/README.md" "7" "Legacy binary name in unsplit getting-started page; QUI-977 CLI sweep must replace it."
allow "xq-binary" "docs/book/src/xqvm/assembly.md" "1" "Legacy binary name in assembly page; QUI-977 CLI sweep must replace it."
allow "xq-binary" "docs/book/src/xqvm/io.md" "3" "Legacy binary name in calldata/output examples; QUI-977 CLI sweep must replace it."
allow "xq-binary" "docs/book/src/xqvm/cli/README.md" "8" "Legacy CLI overview for old binary name; QUI-977 CLI sweep must replace it."
allow "xq-binary" "docs/book/src/xqvm/cli/asm.md" "5" "Legacy asm command examples for old binary name; QUI-977 CLI sweep must replace them."
allow "xq-binary" "docs/book/src/xqvm/cli/dism.md" "4" "Legacy dism command examples for old binary name; QUI-977 CLI sweep must replace them."
allow "xq-binary" "docs/book/src/xqvm/cli/run.md" "10" "Legacy run command examples for old binary name; QUI-977 CLI sweep must replace them."
allow "xq-binary" "docs/book/src/embedding/pallet.md" "1" "Legacy off-chain assembly command; QUI-977 CLI sweep must replace it."
allow "xq-binary" "docs/book/src/xqvm/instructions/energy.md" "2" "Legacy xq-py and xq-rs names in energy prose; QUI-977 CLI sweep must replace them."
allow "xq-binary" "docs/book/src/xqvm/instructions/bitwise.md" "1" "Legacy xq-py name in bitwise prose; QUI-977 CLI sweep must replace it."

add_rule "jump-table" '[Jj]ump[[:space:]_-][Tt]able'
allow "jump-table" "docs/book/src/README.md" "1" "Legacy wire-format description with removed jump table; QUI-977 encoding sweep must replace it."
allow "jump-table" "docs/book/src/xqvm/assembly.md" "2" "Legacy label-resolution prose with removed jump table; QUI-977 encoding sweep must replace it."
allow "jump-table" "docs/book/src/xqvm/execution.md" "3" "Legacy execution prose with removed jump table; QUI-977 encoding sweep must replace it."
allow "jump-table" "docs/book/src/xqvm/cli/dism.md" "2" "Legacy disassembler prose with removed jump table; QUI-977 encoding sweep must replace it."
allow "jump-table" "docs/book/src/xqvm/instructions/README.md" "1" "Legacy label-operand notation with removed jump table; QUI-977 encoding sweep must replace it."
allow "jump-table" "docs/book/src/xqvm/instructions/control-flow.md" "3" "Legacy control-flow instruction prose with removed jump table; QUI-977 encoding sweep must replace it."
allow "jump-table" "docs/book/src/embedding/builder-api.md" "5" "Legacy builder API section for removed jump table; QUI-977 encoding sweep must replace it."

die_setup() {
    echo "error: $1" >&2
    exit 2
}

rel_path() {
    local path="${1}"
    case "${path}" in
        "${REPO_ROOT}/"*) printf '%s\n' "${path#"${REPO_ROOT}/"}" ;;
        *) printf '%s\n' "${path}" ;;
    esac
}

rule_pattern() {
    local name="${1}"
    local index

    for index in "${!RULE_NAMES[@]}"; do
        if [[ "${RULE_NAMES[${index}]}" == "${name}" ]]; then
            printf '%s\n' "${RULE_PATTERNS[${index}]}"
            return 0
        fi
    done

    return 1
}

is_allowed() {
    local rule="${1}"
    local path="${2}"
    local entry

    for entry in "${ALLOW[@]}"; do
        if [[ "${entry}" == "${rule}|${path}|"* ]]; then
            return 0
        fi
    done

    return 1
}

allow_expected_count() {
    local rule="${1}"
    local path="${2}"
    local entry entry_rule entry_path expected_count reason

    for entry in "${ALLOW[@]}"; do
        IFS='|' read -r entry_rule entry_path expected_count reason <<< "${entry}"
        if [[ "${entry_rule}" == "${rule}" && "${entry_path}" == "${path}" ]]; then
            printf '%s\n' "${expected_count}"
            return 0
        fi
    done

    return 1
}

record_allowed_match() {
    local key="${1}|${2}"

    if ! grep -Fxq "${key}" "${tmp_dir}/allow-matches" 2>/dev/null; then
        printf '%s\n' "${key}" >> "${tmp_dir}/allow-matches"
    fi
}

validate_setup() {
    [[ -d "${BOOK_SRC}" ]] || die_setup "missing book source directory: docs/book/src"
    [[ -f "${SUMMARY}" ]] || die_setup "missing docs/book/src/SUMMARY.md"
}

validate_allowlist() {
    local failed=0
    local entry rule path expected_count reason pattern key
    : > "${tmp_dir}/allow-keys"
    : > "${tmp_dir}/allow-matches"
    : > "${tmp_dir}/stale-allow-keys"

    for entry in "${ALLOW[@]}"; do
        IFS='|' read -r rule path expected_count reason <<< "${entry}"
        [[ -n "${rule}" && -n "${path}" && -n "${expected_count}" && -n "${reason}" ]] \
            || die_setup "malformed allowlist entry: ${entry}"
        [[ "${expected_count}" =~ ^[1-9][0-9]*$ ]] \
            || die_setup "allowlist entry count must be a positive integer: ${entry}"
        [[ "${reason}" == *QUI-977* ]] \
            || die_setup "allowlist entry must name QUI-977: ${entry}"
        pattern="$(rule_pattern "${rule}")" \
            || die_setup "allowlist entry names unknown rule '${rule}': ${entry}"
        [[ -f "${REPO_ROOT}/${path}" ]] \
            || die_setup "allowlist entry names missing file: ${path}"

        key="${rule}|${path}"
        if grep -Fxq "${key}" "${tmp_dir}/allow-keys"; then
            die_setup "duplicate allowlist entry for ${key}"
        fi
        printf '%s\n' "${key}" >> "${tmp_dir}/allow-keys"

        if ! grep -Eq "${pattern}" "${REPO_ROOT}/${path}"; then
            echo "error: allowlist entry no longer matches and should be removed: ${entry}" >&2
            printf '%s\n' "${key}" >> "${tmp_dir}/stale-allow-keys"
            failed=1
        fi
    done

    return "${failed}"
}

check_content_rules() {
    local failed=0
    local index rule pattern file rel line status actual_count expected_count
    local matches_file="${tmp_dir}/matches"

    for index in "${!RULE_NAMES[@]}"; do
        rule="${RULE_NAMES[${index}]}"
        pattern="${RULE_PATTERNS[${index}]}"

        while IFS= read -r file; do
            if grep -nE "${pattern}" "${file}" > "${matches_file}"; then
                rel="$(rel_path "${file}")"
                if expected_count="$(allow_expected_count "${rule}" "${rel}")"; then
                    record_allowed_match "${rule}" "${rel}"
                    actual_count="$(wc -l < "${matches_file}")"
                    actual_count="${actual_count//[[:space:]]/}"
                    if (( actual_count <= expected_count )); then
                        continue
                    fi

                    failed=1
                    echo "error: allowlist entry exceeded expected matches: ${rule}|${rel}|expected=${expected_count}|actual=${actual_count}" >&2
                    while IFS= read -r line; do
                        echo "error: ${rule}: ${rel}:${line}" >&2
                    done < "${matches_file}"
                    continue
                elif is_allowed "${rule}" "${rel}"; then
                    die_setup "allowlist count lookup failed for ${rule}|${rel}"
                fi

                failed=1
                while IFS= read -r line; do
                    echo "error: ${rule}: ${rel}:${line}" >&2
                done < "${matches_file}"
            else
                status=$?
                if [[ "${status}" -ne 1 ]]; then
                    die_setup "grep failed for rule ${rule} on $(rel_path "${file}")"
                fi
            fi
        done < <(find "${BOOK_SRC}" -type f -name '*.md' | sort)
    done

    return "${failed}"
}

extract_summary_links() {
    local failed=0
    local line rest link target rel
    local link_re=']\(([^)]*\.md)(#[^)]*)?\)'

    : > "${tmp_dir}/summary-links"
    while IFS= read -r line; do
        rest="${line}"
        while [[ "${rest}" =~ ${link_re} ]]; do
            link="${BASH_REMATCH[1]}"
            rest="${rest#*"${BASH_REMATCH[0]}"}"

            case "${link}" in
                *://* | mailto:* | /*)
                    echo "error: summary-coverage: SUMMARY.md entry is not a book-relative Markdown path: ${link}" >&2
                    failed=1
                    continue
                    ;;
            esac

            target="${BOOK_SRC}/${link#./}"
            rel="$(rel_path "${target}")"
            printf '%s\n' "${rel}" >> "${tmp_dir}/summary-links"
        done
    done < "${SUMMARY}"

    sort -u "${tmp_dir}/summary-links" -o "${tmp_dir}/summary-links"
    return "${failed}"
}

check_summary_coverage() {
    local failed=0
    local file rel linked

    extract_summary_links || failed=1

    while IFS= read -r file; do
        rel="$(rel_path "${file}")"
        printf '%s\n' "${rel}"
    done < <(find "${BOOK_SRC}" -type f -name '*.md' ! -name 'SUMMARY.md' | sort) \
        | sort -u > "${tmp_dir}/book-pages"

    while IFS= read -r file; do
        if ! grep -Fxq "${file}" "${tmp_dir}/summary-links"; then
            echo "error: summary-coverage: ${file} is not listed in docs/book/src/SUMMARY.md" >&2
            failed=1
        fi
    done < "${tmp_dir}/book-pages"

    while IFS= read -r linked; do
        [[ -n "${linked}" ]] || continue
        if ! grep -Fxq "${linked}" "${tmp_dir}/book-pages"; then
            echo "error: summary-coverage: docs/book/src/SUMMARY.md lists ${linked}, which is not a book page" >&2
            failed=1
        fi
    done < "${tmp_dir}/summary-links"

    return "${failed}"
}

check_matched_allowlist() {
    local failed=0
    local key

    while IFS= read -r key; do
        [[ -n "${key}" ]] || continue
        if grep -Fxq "${key}" "${tmp_dir}/stale-allow-keys"; then
            continue
        fi
        if ! grep -Fxq "${key}" "${tmp_dir}/allow-matches"; then
            echo "error: allowlist entry was not exercised by the scan: ${key}" >&2
            failed=1
        fi
    done < "${tmp_dir}/allow-keys"

    return "${failed}"
}

main() {
    local failed=0

    validate_setup
    validate_allowlist || failed=1
    check_content_rules || failed=1
    check_matched_allowlist || failed=1
    check_summary_coverage || failed=1

    if [[ "${failed}" -ne 0 ]]; then
        exit 1
    fi

    echo "docs drift guard passed"
}

main "$@"
