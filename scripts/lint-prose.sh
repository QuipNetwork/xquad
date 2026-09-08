#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Prose linter for the handwritten book pages.
#
# Runs Vale over docs/book/src/**/*.md, minus the pages carrying the
# AUTO-GENERATED banner. Those are rendered from examples/*/README.md and
# from the opcode table, so a finding on one of them names a file nobody
# edits; the fix would belong in the generator or in the source README,
# and the page would be overwritten by the next `make regen-docs` either
# way.
#
# File selection lives here rather than in .vale.ini because Vale's own
# include/exclude patterns match paths, and "carries this banner" is a
# property of the content. Keeping it in bash also keeps the CI job on
# `bash grep` plus the Vale binary, with no Python or uv in the image.
#
# Usage:
#   scripts/lint-prose.sh [FILE...]
#
# With no arguments the handwritten pages are discovered. With arguments
# the named files are linted as given, banner or not, which is what makes
# a single page checkable while editing it.
#
# Exit codes:
#   0  -- pass
#   1  -- Vale reported at least one alert at or above MinAlertLevel
#   2  -- setup error (Vale missing, no pages found)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOK_SRC="${REPO_ROOT}/docs/book/src"
BANNER="AUTO-GENERATED FILE"
RULES_DIR="${REPO_ROOT}/.vale/styles/XQuad"

# The rules .vale.ini is expected to be enforcing, one per rule file.
#
# .vale.ini says `BasedOnStyles = XQuad`, which enables whatever rule
# files happen to be in RULES_DIR and says nothing at all about one that
# has gone missing. Delete Dashes.yml and the run stays green on
# em-dashes; delete all three and every page "passes" with no rule
# running, which this script then reports as "no findings". Only losing
# the directory itself is something Vale calls an error.
#
# So the set is asserted rather than assumed, and asserted in both
# directions: an explicit list is a claim about what coverage exists, and
# a rule added to the directory has to be named here before it counts.
EXPECTED_RULES="Dashes Emoji Spelling"

if ! command -v vale > /dev/null 2>&1; then
    echo "error: vale is not on PATH." >&2
    echo "" >&2
    echo "Install the pinned version (VALE_VERSION in the Makefile) from" >&2
    echo "https://github.com/errata-ai/vale/releases, or on macOS:" >&2
    echo "" >&2
    echo "    brew install vale" >&2
    exit 2
fi

present="$(
    find "${RULES_DIR}" -maxdepth 1 -name '*.yml' -exec basename {} .yml \; 2> /dev/null | sort | tr '\n' ' '
)"
expected="$(printf '%s\n' ${EXPECTED_RULES} | sort | tr '\n' ' ')"
if [[ "${present}" != "${expected}" ]]; then
    {
        echo "error: the XQuad rule set is not what this script expects."
        echo ""
        echo "  expected: ${expected:-(none)}"
        echo "  present:  ${present:-(none)}"
        echo ""
        echo "Vale enables whatever it finds under ${RULES_DIR#"${REPO_ROOT}/"} and"
        echo "reports nothing when a rule file is absent, so a dropped rule would"
        echo "otherwise show up as a clean prose run rather than as lost coverage."
        echo "Restore the rule, or add a new one to EXPECTED_RULES in this script."
    } >&2
    exit 2
fi

files=()
if [[ $# -gt 0 ]]; then
    files=("$@")
else
    # -L lists the files that do NOT contain the banner. -r walks the tree,
    # --include restricts it to Markdown so a stray image or CSS file under
    # docs/book/src cannot enter the list.
    while IFS= read -r page; do
        files+=("${page}")
    done < <(grep -rL --include='*.md' "${BANNER}" "${BOOK_SRC}" | sort)
fi

if [[ ${#files[@]} -eq 0 ]]; then
    echo "error: no pages to lint under ${BOOK_SRC}" >&2
    exit 2
fi

# --no-global is load-bearing, not tidiness. Without it Vale merges the
# developer's own ~/.config/vale configuration into this repository's, so
# a page passes on one machine and fails on another with no visible cause.
# Reproducing CI's verdict locally is the whole point of checking the
# config into the tree; QUI-977 linted every page it wrote against a
# global config nobody else had.
cd "${REPO_ROOT}"
alerts=""
status=0
alerts="$(vale --no-global --output line "${files[@]}")" || status=$?

if [[ -n "${alerts}" ]]; then
    printf '%s\n' "${alerts}"
fi

if [[ ${status} -eq 0 ]]; then
    echo "prose: ${#files[@]} handwritten page(s) checked, no findings."
    exit 0
fi

# Vale exits non-zero for two unrelated reasons, and this script's two
# non-zero exits mean two different things, so the reasons have to be
# told apart. Vale 3.15.1 writes alerts to stdout and exits 1, and writes
# configuration and runtime failures (E100, E201) to stderr and exits 2 --
# but the split tested here is the STREAM rather than the code, because
# Vale has historically exited with the alert COUNT and a future release
# returning to that would silently reclassify a two-alert run as a config
# error. Alert text on stdout is the durable signal that Vale ran and had
# something to say about the prose.
if [[ -z "${alerts}" ]]; then
    echo "" >&2
    echo "prose: vale exited ${status} without reporting a finding, so this is a" >&2
    echo "Vale configuration or runtime error and not a prose problem. Its own" >&2
    echo "diagnostic is above; .vale.ini and .vale/styles/XQuad/*.yml are what" >&2
    echo "it is complaining about." >&2
    exit 2
fi

echo "" >&2
echo "prose: ${#files[@]} handwritten page(s) checked, findings above." >&2
echo "Vocabulary lives in .vale/styles/XQuad/vocab.txt; the house rules" >&2
echo "are the .yml files beside it." >&2
exit 1
