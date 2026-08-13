#!/bin/sh
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
# Mermaid render guard.
#
# mdbook-mermaid 0.17.0 (the latest release on crates.io) is built against
# mdBook 0.5.0, while we pin mdBook 0.5.2 (scripts/cargo-tools.lock). Every
# `mdbook build` therefore prints a version-skew warning that has no clean
# fix short of downgrading mdBook. That warning is cosmetic; what actually
# matters is whether the preprocessor ran. This guard checks the built HTML
# for that directly, rather than trusting the warning's absence.
#
# It counts the ```mermaid fences in the book source and confirms at least
# as many `class="mermaid"` blocks landed in the built HTML, then confirms
# no `language-mermaid` block survived -- that class is what mdBook falls
# back to when the preprocessor does not run, so its presence is a sharper
# signal of failure than a low mermaid count on its own.
#
# Usage:
#   scripts/check-mermaid-render.sh
#
# Requires `make build-docs` (or `mdbook build`) to have already produced
# docs/book/build.
#
# Exit codes:
#   0  -- pass, including the no-diagrams-yet case
#   1  -- a diagram failed to render
#   2  -- setup error (book not built)

set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
BOOK_SRC="${REPO_ROOT}/docs/book/src"
BOOK_BUILD="${REPO_ROOT}/docs/book/build"

die_setup() {
    echo "error: $1" >&2
    exit 2
}

count_matches() {
    # Sums per-file `grep -c` matches across the given file list, read from
    # stdin one path per line. `grep -c` never fails the enclosing command
    # substitution on a no-match file, so this stays safe under `set -e`.
    while IFS= read -r file; do
        grep -c "$1" "${file}" 2>/dev/null || true
    done | awk '{sum += $1} END {print sum + 0}'
}

# Checked before the count, not alongside the BOOK_BUILD check below. `find`
# on a missing directory fails, but its status is discarded by the pipeline --
# this is POSIX sh with no `pipefail`, so the exit status is `awk`'s, and awk
# prints 0. Without this, renaming or moving the book source would leave
# `expected` at 0 and the guard would report "nothing to check" and pass,
# turning itself off with no signal.
[ -d "${BOOK_SRC}" ] || die_setup "docs/book/src does not exist; the book source has moved or been renamed"

expected="$(find "${BOOK_SRC}" -type f -name '*.md' | count_matches '^```mermaid$')"

if [ "${expected}" -eq 0 ]; then
    echo "no mermaid diagrams in docs/book/src; nothing to check"
    exit 0
fi

[ -d "${BOOK_BUILD}" ] || die_setup "docs/book/build does not exist; run 'make build-docs' first"

html_files="$(find "${BOOK_BUILD}" -type f -name '*.html' | grep -v -e '/print\.html$' -e '/404\.html$' || true)"

rendered="$(printf '%s\n' "${html_files}" | count_matches 'class="mermaid"')"
fallback="$(printf '%s\n' "${html_files}" | count_matches 'language-mermaid')"

failed=0

if [ "${rendered}" -lt "${expected}" ]; then
    echo "error: expected at least ${expected} rendered mermaid diagram(s), found ${rendered} in docs/book/build" >&2
    failed=1
fi

if [ "${fallback}" -ne 0 ]; then
    echo "error: found ${fallback} 'language-mermaid' block(s) in docs/book/build; the mermaid preprocessor did not run and mdBook fell back to plain code blocks" >&2
    failed=1
fi

if [ "${failed}" -ne 0 ]; then
    exit 1
fi

echo "mermaid render guard passed (${rendered} diagram(s) rendered)"
