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
# It also holds the known-issue defect blocks (QUI-1036) to their registry, so a
# block cannot outlive the defect it describes.
#
# Finally it resolves every relative link between book pages against the source
# tree (QUI-1040), so a link that would 404 on the published site is caught
# here rather than by a reader.
#
# Usage:
#   scripts/check-docs-drift.sh
#
# Escape hatch:
#   Known legacy prose is allowlisted below as rule-scoped entries of the form
#   `rule|path|count|reason`. Every entry must name a tracking issue
#   (`QUI-NNN`), must point at an existing file, must still match its rule, and
#   must not gain more matches than the count records. Remove each entry in the
#   same MR that fixes the corresponding page.
#
#   The list is empty as of QUI-977, which drained the 28 entries QUI-853
#   shipped. Empty is the intended steady state: an entry is a debt marker, and
#   the guard errors on one that no longer matches so it cannot outlive its
#   page.
#
# Exit codes:
#   0  -- pass
#   1  -- drift detected, stale allowlist entry, SUMMARY coverage mismatch, a
#         defect block that disagrees with the registry, or a dead page link
#   2  -- setup error

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BOOK_SRC="${REPO_ROOT}/docs/book/src"
SUMMARY="${BOOK_SRC}/SUMMARY.md"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

RULE_NAMES=()
RULE_PATTERNS=()

# The allowlist is expected to reach zero entries, and an empty array is the
# steady state rather than an edge case. Under `set -u`, bash before 4.4 treats
# `"${ALLOW[@]}"` on an empty array as an unbound variable: the guard then
# aborted inside `validate_allowlist` and `main`'s `|| failed=1` swallowed it,
# so the script exited 0 without printing "passed" and without validating
# anything. macOS ships bash 3.2, which is what `make check-docs-drift` runs against
# locally, so every expansion below uses the `${ALLOW[@]+...}` form.
ALLOW=()

add_rule() {
    RULE_NAMES+=("${1}")
    RULE_PATTERNS+=("${2}")
}

allow() {
    ALLOW+=("${1}|${2}|${3}|${4}")
}

add_rule "aglais-product" '(^|[^[:alnum:]_-])[Aa]glais'

add_rule "aglais-crate" 'aglais-xqvm-'

add_rule "crates-path" '(^|[^/.[:alnum:]])crates/'

add_rule "xq-binary" '(^|[^[:alnum:]_.-])xq($|[^[:alnum:]_])'

# Two alternatives, because the stale prose and the live API share a name.
#
# `d43791e` moved the jump table out of the wire format and into a load-time
# scan; it did not remove it. `Program::jump_table()`
# (`xqvm/src/bytecode/program.rs`) and the exported `JumpTable` type are both
# live public API that the embedding chapter has to be able to document.
#
# So the rule bans the two spellings that only ever appear in stale prose --
# the English phrase, whitespace- or hyphen-separated, and the subscripted
# pseudo-code form `jump_table[...]` describing the removed wire-format
# lookup -- and spares the two that are correct today, `jump_table()` and
# `JumpTable`, neither of which is ever followed by `[`.
add_rule "jump-table" '[Jj]ump[[:space:]-][Tt]able|jump_table\['

# Known-issue defect blocks (QUI-1036).
#
# Each reader-visible block is introduced by an `<!-- xquad:defect QUI-NNN -->`
# marker naming the ticket that removes it. The registry below declares where
# every block lives, so the relationship is checked in both directions: a
# declared block that has gone missing fails, and a marker nobody declared
# fails. That is what makes "remove its drift-guard entry" a real step in each
# fix ticket's acceptance criteria rather than a promise.
#
# Entries are `ticket|path|count`. Delete the entry in the same MR that removes
# the block, exactly as with the allowlist above.
#
# Generated pages are skipped: `examples/bin_packing/README.md` is the source of
# truth for the block that reaches `docs/book/src/examples/bin_packing.md`, and
# `make check-docs-generated` already fails if the two disagree. Declaring both would be
# the same fact twice.
DEFECTS=()

defect() {
    DEFECTS+=("${1}|${2}|${3}")
}

DEFECT_TRACKER='https://gitlab.com/quip.network/xquad/-/issues'

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

    for entry in ${ALLOW[@]+"${ALLOW[@]}"}; do
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

    for entry in ${ALLOW[@]+"${ALLOW[@]}"}; do
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

    for entry in ${ALLOW[@]+"${ALLOW[@]}"}; do
        IFS='|' read -r rule path expected_count reason <<< "${entry}"
        [[ -n "${rule}" && -n "${path}" && -n "${expected_count}" && -n "${reason}" ]] \
            || die_setup "malformed allowlist entry: ${entry}"
        [[ "${expected_count}" =~ ^[1-9][0-9]*$ ]] \
            || die_setup "allowlist entry count must be a positive integer: ${entry}"
        [[ "${reason}" =~ QUI-[0-9]+ ]] \
            || die_setup "allowlist entry reason must name a tracking issue (QUI-NNN): ${entry}"
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

# Collapse `.` and `..` segments in a slash-separated relative path.
#
# Pure string work: there is no `realpath` on macOS bash 3.2, the alpine job
# installs only bash and grep, and the path being resolved is a link target
# that need not exist for the answer to be defined.
#
# A path that climbs above its own starting point keeps its leading `..`, which
# is how `check_page_links` spots a link escaping the book source tree.
normalise_path() {
    local rest="${1}"
    local out=""
    local segment

    while [[ -n "${rest}" ]]; do
        if [[ "${rest}" == */* ]]; then
            segment="${rest%%/*}"
            rest="${rest#*/}"
        else
            segment="${rest}"
            rest=""
        fi

        case "${segment}" in
            "" | ".") ;;
            "..")
                if [[ -z "${out}" || "${out}" == ".." || "${out}" == *"/.." ]]; then
                    out="${out:+${out}/}.."
                elif [[ "${out}" == */* ]]; then
                    out="${out%/*}"
                else
                    out=""
                fi
                ;;
            *) out="${out:+${out}/}${segment}" ;;
        esac
    done

    printf '%s\n' "${out}"
}

# Relative links between book pages (QUI-1040).
#
# mdBook's index preprocessor renames `<dir>/README.md` to `<dir>/index.html`
# in the output, but in-page relative links are rewritten by swapping `.md` for
# `.html`. The two rules disagree, so `](concepts/README.md)` renders as
# `concepts/README.html` -- a file mdBook never generates -- and every such link
# 404s on the published site. `](concepts/index.md)` resolves on the site but
# names a file that does not exist in the repository. The trailing-slash
# directory form `](concepts/)` is the only spelling that works in both places,
# so it is the one this check requires.
#
# The check resolves every link rather than only banning the `README.md` form,
# because the same pass then catches a mistyped filename or a page renamed
# without its inbound links, which is the same defect one keystroke earlier.
#
# SUMMARY.md is excluded. mdBook resolves its entries against `src/` as source
# paths rather than as hrefs, so `README.md` is correct there and the build
# fails without it; `check_summary_coverage` is what validates that file.
#
# Deliberately a source-text check and not a build-output one: `lint:docs-drift`
# runs on alpine:3 with no Rust toolchain and no mdBook, and walking the source
# catches this class without a build.
check_page_links() {
    local failed=0
    local file rel rel_page page_dir line lineno rest link target suggestion resolved abspath
    local link_re=']\(([^)]*)\)'

    while IFS= read -r file; do
        rel="$(rel_path "${file}")"
        rel_page="${file#"${BOOK_SRC}/"}"
        if [[ "${rel_page}" == */* ]]; then
            page_dir="${rel_page%/*}"
        else
            page_dir=""
        fi

        lineno=0
        while IFS= read -r line || [[ -n "${line}" ]]; do
            lineno=$((lineno + 1))
            rest="${line}"

            while [[ "${rest}" =~ ${link_re} ]]; do
                link="${BASH_REMATCH[1]}"
                rest="${rest#*"${BASH_REMATCH[0]}"}"

                case "${link}" in
                    *://* | mailto:* | "#"*) continue ;;
                    /*)
                        failed=1
                        echo "error: page-link: ${rel}:${lineno}: ${link} is an absolute path; the book is served under a path prefix, so links between pages must be relative" >&2
                        continue
                        ;;
                esac

                # An anchor plays no part in resolving the target, and a link
                # that is nothing but an anchor was skipped above.
                target="${link%%#*}"
                [[ -n "${target}" ]] || continue

                case "${target}" in
                    README.md | */README.md)
                        failed=1
                        suggestion="${link/README.md/}"
                        case "${suggestion}" in
                            "" | "#"*) suggestion="./${suggestion}" ;;
                        esac
                        echo "error: page-link: ${rel}:${lineno}: ${link} renders to README.html, which mdBook never generates; use the directory form ${suggestion}" >&2
                        continue
                        ;;
                esac

                resolved="$(normalise_path "${page_dir:+${page_dir}/}${target}")"
                if [[ "${resolved}" == ".." || "${resolved}" == "../"* ]]; then
                    failed=1
                    echo "error: page-link: ${rel}:${lineno}: ${link} resolves outside docs/book/src, so it cannot resolve in the built site; link the file on GitLab by its full URL instead" >&2
                    continue
                fi

                abspath="${BOOK_SRC}${resolved:+/${resolved}}"
                if [[ "${target}" == */ ]]; then
                    if [[ ! -f "${abspath}/README.md" ]]; then
                        failed=1
                        echo "error: page-link: ${rel}:${lineno}: ${link} is not a section directory holding a README.md" >&2
                    fi
                elif [[ ! -f "${abspath}" ]]; then
                    failed=1
                    echo "error: page-link: ${rel}:${lineno}: ${link} does not exist" >&2
                fi
            done
        done < "${file}"
    done < <(find "${BOOK_SRC}" -type f -name '*.md' ! -name 'SUMMARY.md' | sort)

    return "${failed}"
}

validate_defect_block() {
    local rel="${1}"
    local start="${2}"
    local ticket="${3}"
    local text="${4}"
    local failed=0

    # No marker seen yet, so there is no block to validate.
    [[ -n "${ticket}" ]] || return 0

    if [[ -z "${text}" ]]; then
        echo "error: defect-block: ${rel}:${start}: marker is not followed by a blockquote" >&2
        return 1
    fi

    case "${text}" in
        *QUI-[0-9]*)
            failed=1
            echo "error: defect-block: ${rel}:${start}: block text names a ticket; tracking belongs in the marker comment, not in prose the reader cannot resolve" >&2
            ;;
    esac

    case "${text}" in
        *"${DEFECT_TRACKER}"*) ;;
        *)
            failed=1
            echo "error: defect-block: ${rel}:${start}: block does not link the issue tracker" >&2
            ;;
    esac

    return "${failed}"
}

defect_declared() {
    local key="${1}"
    local entry

    for entry in ${DEFECTS[@]+"${DEFECTS[@]}"}; do
        if [[ "${entry}" == "${key}|"* ]]; then
            return 0
        fi
    done

    return 1
}

# Book pages plus the example READMEs that feed the generator. `BOOK_SRC` alone
# would miss `examples/*/README.md`, which is where a block on a generated page
# has to be authored.
defect_scan_files() {
    find "${BOOK_SRC}" -type f -name '*.md' ! -name 'SUMMARY.md'
    find "${REPO_ROOT}/examples" -type f -name 'README.md'
}

check_defect_blocks() {
    local failed=0
    local file rel line lineno key entry
    local entry_ticket entry_path entry_count actual
    local block_ticket block_start block_text expect_quote
    local marker_re='^<!--[[:space:]]+xquad:defect[[:space:]]+(QUI-[0-9]+)[[:space:]]+-->[[:space:]]*$'

    : > "${tmp_dir}/defect-markers"

    while IFS= read -r file; do
        # Generated pages carry a copy of whatever their source authored, and
        # `make check-docs-generated` is what holds the two together.
        if grep -Fq 'AUTO-GENERATED FILE. DO NOT EDIT.' "${file}"; then
            continue
        fi

        rel="$(rel_path "${file}")"
        lineno=0
        block_ticket=""
        block_start=0
        block_text=""
        expect_quote=0

        while IFS= read -r line || [[ -n "${line}" ]]; do
            lineno=$((lineno + 1))

            if [[ "${line}" =~ ${marker_re} ]]; then
                validate_defect_block "${rel}" "${block_start}" "${block_ticket}" "${block_text}" || failed=1
                block_ticket="${BASH_REMATCH[1]}"
                block_start="${lineno}"
                block_text=""
                expect_quote=1
                printf '%s|%s\n' "${block_ticket}" "${rel}" >> "${tmp_dir}/defect-markers"
                continue
            fi

            case "${line}" in
                ">"*)
                    if [[ -n "${block_ticket}" ]]; then
                        block_text="${block_text} ${line}"
                        expect_quote=0
                    fi
                    ;;
                *)
                    if [[ "${expect_quote}" -eq 1 ]]; then
                        # A blank line between the marker and its blockquote is
                        # still a detached marker: mdBook renders the two apart.
                        validate_defect_block "${rel}" "${block_start}" "${block_ticket}" "" || failed=1
                        block_ticket=""
                        expect_quote=0
                    elif [[ -n "${block_ticket}" ]]; then
                        validate_defect_block "${rel}" "${block_start}" "${block_ticket}" "${block_text}" || failed=1
                        block_ticket=""
                        block_text=""
                    fi
                    ;;
            esac
        done < "${file}"

        validate_defect_block "${rel}" "${block_start}" "${block_ticket}" "${block_text}" || failed=1
    done < <(defect_scan_files | sort)

    for entry in ${DEFECTS[@]+"${DEFECTS[@]}"}; do
        IFS='|' read -r entry_ticket entry_path entry_count <<< "${entry}"

        if [[ ! "${entry_count}" =~ ^[1-9][0-9]*$ ]]; then
            die_setup "defect registry count must be a positive integer: ${entry}"
        fi

        if [[ ! -f "${REPO_ROOT}/${entry_path}" ]]; then
            die_setup "defect registry entry points at a missing file: ${entry}"
        fi

        actual="$(grep -Fxc "${entry_ticket}|${entry_path}" "${tmp_dir}/defect-markers" || true)"
        actual="${actual//[[:space:]]/}"

        if [[ "${actual}" -ne "${entry_count}" ]]; then
            failed=1
            echo "error: defect-block: ${entry_path}: registry expects ${entry_count} ${entry_ticket} block(s), found ${actual}; remove the registry entry in the MR that removes the block" >&2
        fi
    done

    sort -u "${tmp_dir}/defect-markers" > "${tmp_dir}/defect-keys"

    while IFS= read -r key; do
        [[ -n "${key}" ]] || continue
        if ! defect_declared "${key}"; then
            failed=1
            echo "error: defect-block: ${key#*|}: ${key%%|*} block is not declared in the defect registry in scripts/check-docs-drift.sh" >&2
        fi
    done < "${tmp_dir}/defect-keys"

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
    check_page_links || failed=1
    check_defect_blocks || failed=1

    if [[ "${failed}" -ne 0 ]]; then
        exit 1
    fi

    echo "docs drift guard passed"
}

main "$@"
