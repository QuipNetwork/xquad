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
# Direct-push guard for `main`.
#
# The code owners can push to `main` so that reopening it after a release
# (the `-dev` version bump) needs no merge request of its own. Branch
# protection cannot say "version bumps only", so this guard says it
# instead: every commit a push puts on `main`'s first-parent line must
# be one of
#
#   - a merge request's merge commit, recognised by the
#     "merge request <project>!<iid>" reference GitLab writes into it,
#     naming this project ($CI_PROJECT_PATH, quip.network/xquad off
#     CI), or
#   - a version-only change: each file it touches is identical before
#     and after once the old and new workspace versions, in both their
#     Cargo and PEP 440 spellings, are masked out on both sides.
#
# Anything else turns `main`'s pipeline red. Detection, not prevention:
# the commit is on `main` by the time this runs. See
# docs/guide/gitflow-protocol.md, "Protected branches".
#
# The masking is symmetric, so an unrelated dependency that happens to
# sit at one of the two versions in a lockfile compares equal. The
# price is that a change moving such a dependency between exactly those
# two versions would pass. That is not a change a version bump makes.
#
# What is judged
# --------------
# Only push pipelines on `main`, over $CI_COMMIT_BEFORE_SHA..HEAD --
# exactly what the push added. The merge request's own commits sit
# behind the merge commit's second parent and are not on the
# first-parent line, so a release merge is judged by its merge commit
# alone. Off CI, the tip commit is judged when `main` is checked out.
# An explicit RANGE argument judges that range on any branch.
#
# Usage:
#   scripts/check-main-direct-push.sh [RANGE]
#
# Exit codes:
#   0  -- every first-parent commit in the range is allowed, or nothing
#         is judged on this ref
#   1  -- a commit landed on main outside a merge request and changed
#         more than the version
#   2  -- setup error

set -euo pipefail

MASK="@VERSION@"
PROJECT_PATH="${CI_PROJECT_PATH:-quip.network/xquad}"

if [[ $# -gt 0 ]]; then
    RANGE="$1"
else
    REF_NAME="${CI_COMMIT_BRANCH:-$(git symbolic-ref --quiet --short HEAD || true)}"
    if [[ -n "${CI_MERGE_REQUEST_IID:-}" || "${REF_NAME}" != "main" ]]; then
        echo "guard: ${REF_NAME:-<detached HEAD>} is not a push to main -- direct pushes not checked"
        exit 0
    fi
    before="${CI_COMMIT_BEFORE_SHA:-}"
    if [[ -n "${before}" && ! "${before}" =~ ^0+$ ]] \
        && git rev-parse -q --verify "${before}^{commit}" >/dev/null; then
        RANGE="${before}..HEAD"
    else
        RANGE="HEAD~1..HEAD"
    fi
fi

if ! commits="$(git rev-list --first-parent --reverse "${RANGE}")"; then
    echo "error: direct-push: cannot resolve range ${RANGE}." >&2
    exit 2
fi

# The workspace version at a commit, in the given file's spelling.
version_at() {
    git show "$1:$2" | sed -nE "s/^$3[[:space:]]*=[[:space:]]*\"([^\"]+)\".*/\1/p" | head -n1
}

# Mask every version spelling in stdin, longest first so that `0.4.1`
# never eats the front of `0.4.1-dev`. sed, not bash's ${//}: the
# latter is quadratic and takes minutes over Cargo.lock.
mask() {
    local spelling script=""
    while IFS= read -r spelling; do
        [[ -n "${spelling}" ]] && script+="s/$(printf '%s' "${spelling}" | sed 's/[.[\*^$/]/\\&/g')/${MASK}/g;"
    done < <(printf '%s\n' "${SPELLINGS[@]}" | awk '{ print length, $0 }' | sort -rn | cut -d' ' -f2-)
    sed -e "${script}"
}

# Prints why a single-parent commit is not version-only, or nothing.
version_only_violation() {
    local sha="$1" status path
    SPELLINGS=(
        "$(version_at "${sha}^" xqvm/Cargo.toml version)"
        "$(version_at "${sha}" xqvm/Cargo.toml version)"
        "$(version_at "${sha}^" xqvm_py/__init__.py __version__)"
        "$(version_at "${sha}" xqvm_py/__init__.py __version__)"
    )
    if [[ "${SPELLINGS[0]}" == "${SPELLINGS[1]}" ]]; then
        echo "it does not change the workspace version (${SPELLINGS[0]})"
        return
    fi
    while IFS=$'\t' read -r status path; do
        if [[ "${status}" != "M" ]]; then
            echo "it adds, deletes or renames ${path}"
            return
        fi
        if [[ "$(git show "${sha}^:${path}" | mask)" != "$(git show "${sha}:${path}" | mask)" ]]; then
            echo "${path} changes more than the version (${SPELLINGS[0]} -> ${SPELLINGS[1]})"
            return
        fi
    done < <(git diff-tree -r --no-commit-id --name-status "${sha}^" "${sha}")
}

checked=0
failed=0

while IFS= read -r sha; do
    [[ -z "${sha}" ]] && continue
    checked=$((checked + 1))
    subject="$(git log -1 --format='%h %s' "${sha}")"
    if [[ "$(git rev-list --parents -n1 "${sha}" | wc -w)" -gt 2 ]]; then
        if git log -1 --format=%B "${sha}" | grep -oE 'merge request [^[:space:]]+![0-9]+' \
            | grep -xE "merge request ${PROJECT_PATH//./\\.}![0-9]+" >/dev/null; then
            continue
        fi
        reason="it is a merge commit that no merge request produced"
    else
        reason="$(version_only_violation "${sha}")"
        [[ -z "${reason}" ]] && continue
    fi
    failed=$((failed + 1))
    echo "FAIL: ${subject}" >&2
    echo "  landed on main outside a merge request, and ${reason}." >&2
done <<<"${commits}"

if [[ "${failed}" -gt 0 ]]; then
    {
        echo ""
        echo "  main takes direct pushes for the reopening version bump only."
        echo "  Everything else lands by merge request; see"
        echo "  docs/guide/gitflow-protocol.md, \"Protected branches\"."
    } >&2
    exit 1
fi

echo "direct pushes OK: ${checked} first-parent commit(s) in ${RANGE}"
