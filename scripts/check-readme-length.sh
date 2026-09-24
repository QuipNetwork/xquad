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
# Package README length guard.
#
# A package README is a landing page: it says what the package is, who it is
# for, and where the real documentation lives. QUI-977 cut every one of them
# back to that job and moved the reference material into the book. This guard
# stops them growing back into second copies of the book, which is how they
# drifted out of date the first time.
#
# The limit is a proxy, not a style rule. A README over it is not automatically
# wrong; it is a prompt to check whether the overflow belongs in the book
# instead. Raise MAX_LINES deliberately and in its own commit if the policy
# itself changes.
#
# Scope: every package that ships to a registry, discovered rather than listed,
# so a new package is covered the day it is added. A directory is in scope when
# it carries a `pyproject.toml` (ships to PyPI) or a `Cargo.toml` that does not
# set `publish = false` (ships to crates.io). That covers the eight distributed
# packages. Note `xqffi/` is in scope through its `pyproject.toml`: cargo does not publish it,
# maturin does. The repository root README is out of scope; it is the project
# landing page and answers to a different brief.
#
# Usage:
#   scripts/check-readme-length.sh          # enforce the limit
#   scripts/check-readme-length.sh --list   # print every package and its count
#
# Exit codes:
#   0  -- pass
#   1  -- a package README is missing or over the limit
#   2  -- setup error

set -euo pipefail

MAX_LINES=100

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

die_setup() {
    echo "error: $1" >&2
    exit 2
}

# Line count that agrees with the file's contents rather than with its final
# byte: `wc -l` counts newlines, so a README without a trailing newline is
# reported one line short.
count_lines() {
    awk 'END { print NR }' "${1}"
}

# A Cargo package is published unless it opts out. Matches `publish = false`
# with any spacing, which is the form the workspace manifests use.
cargo_is_published() {
    ! grep -Eq '^[[:space:]]*publish[[:space:]]*=[[:space:]]*false' "${1}"
}

# Emit the repository-relative directory of every in-scope package, sorted.
discover_packages() {
    local dir name

    for dir in "${REPO_ROOT}"/*/; do
        name="$(basename "${dir}")"

        if [[ -f "${dir}pyproject.toml" ]]; then
            printf '%s\n' "${name}"
            continue
        fi

        if [[ -f "${dir}Cargo.toml" ]] && cargo_is_published "${dir}Cargo.toml"; then
            printf '%s\n' "${name}"
        fi
    done | sort
}

main() {
    local mode="${1-}"
    local failed=0
    local packages readme lines

    case "${mode}" in
        '' | --list) ;;
        *) die_setup "unknown argument: ${mode}" ;;
    esac

    packages="$(discover_packages)"
    [[ -n "${packages}" ]] || die_setup "no packages discovered under ${REPO_ROOT}"

    while IFS= read -r name; do
        readme="${REPO_ROOT}/${name}/README.md"

        if [[ ! -f "${readme}" ]]; then
            echo "error: ${name}/README.md is missing; every published package needs a landing page" >&2
            failed=1
            continue
        fi

        lines="$(count_lines "${readme}")"

        if [[ "${mode}" == "--list" ]]; then
            printf '%-20s %4s / %s\n' "${name}/README.md" "${lines}" "${MAX_LINES}"
            continue
        fi

        if (( lines > MAX_LINES )); then
            echo "error: ${name}/README.md is ${lines} lines, over the ${MAX_LINES}-line limit" >&2
            echo "error: ${name}/README.md: move the reference material into docs/book/src/ and link to it" >&2
            failed=1
        fi
    done <<< "${packages}"

    if [[ "${failed}" -ne 0 ]]; then
        exit 1
    fi

    if [[ "${mode}" != "--list" ]]; then
        echo "package README length guard passed"
    fi
}

main "$@"
