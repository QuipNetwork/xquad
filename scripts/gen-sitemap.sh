#!/usr/bin/env sh
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
# Sitemap generator for the published mdBook.
#
# mdBook emits no sitemap of its own. The mdbook-sitemap-generator crate was
# considered and rejected: it is a standalone CLI last published in 2023, it
# takes a bare domain with no way to express the /xquad/ path prefix this book
# is served under, and adding it to scripts/cargo-tools.lock would invalidate
# the .cargo/bin/ cache for every Rust CI job.
#
# Directory indexes are emitted at their directory URL rather than as
# .../index.html, so the canonical form matches what a visitor lands on.
# The 404 and print pages are excluded.
#
# Deliberately POSIX sh with no bashisms: the `pages` job runs on alpine:3,
# whose /bin/sh is busybox ash.
#
# Usage:
#   scripts/gen-sitemap.sh <build-dir> <base-url> > sitemap.xml
#
# Example:
#   scripts/gen-sitemap.sh public/xquad https://docs.quip.network/xquad
#
# Exit codes:
#   0  -- sitemap written to stdout
#   2  -- setup error (bad arguments, or no pages found)

set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <build-dir> <base-url>" >&2
    exit 2
fi

build_dir="$1"
base_url="${2%/}"

if [ ! -d "${build_dir}" ]; then
    echo "gen-sitemap: not a directory: ${build_dir}" >&2
    exit 2
fi

pages="$(cd "${build_dir}" && find . -type f -name '*.html' | sed 's|^\./||' | sort)"

# Fail closed: an empty sitemap almost certainly means the book did not build
# or the wrong directory was passed, and a silently empty file would look like
# a successful deploy.
if [ -z "${pages}" ]; then
    echo "gen-sitemap: no HTML pages found under ${build_dir}" >&2
    exit 2
fi

printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>'
printf '%s\n' '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'

echo "${pages}" | while IFS= read -r page; do
    case "${page}" in
        404.html | print.html) continue ;;
    esac

    loc="${page}"
    case "${loc}" in
        index.html) loc="" ;;
        */index.html) loc="${loc%index.html}" ;;
    esac

    loc="$(printf '%s' "${loc}" | sed 's|&|\&amp;|g')"
    printf '  <url><loc>%s/%s</loc></url>\n' "${base_url}" "${loc}"
done

printf '%s\n' '</urlset>'
