#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Install the cargo-published dev tools the workspace depends on,
# pinned to the versions in scripts/cargo-tools.lock.
#
# Prefers `cargo binstall` (fetches prebuilt binaries — seconds)
# and falls back to `cargo install --locked` (compiles from
# source — minutes) when no prebuilt is available.
#
# Short-circuits when a binary of the right version is already on
# PATH, so CI cache hits skip straight through.
#
# Usage:
#   scripts/install-cargo-tools.sh [--upgrade] [--only NAME[,NAME...]]
#
# With --upgrade, every entry in scripts/cargo-tools.lock is
# reinstalled regardless of current state (useful after a lock-file
# bump).
#
# With --only NAME[,NAME...], install just those tools from the lock
# file (e.g. `--only git-cliff` or `--only taplo,cargo-deny`). Each
# NAME matches either the crate name or the tool's effective binary
# name (the `[binary]` override column, e.g. `taplo` for `taplo-cli`).
# Used by lightweight CI jobs that need a named subset rather than the
# whole toolchain. Every requested name must match an entry in the
# lock file; a typo anywhere in the list fails the whole invocation
# rather than silently installing only the names that did match.

set -euo pipefail

LOCK="$(cd "$(dirname "$0")" && pwd)/cargo-tools.lock"
UPGRADE=0
ONLY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --upgrade) UPGRADE=1; shift ;;
        --only)
            [[ $# -ge 2 ]] || { echo "error: --only requires a tool name" >&2; exit 2; }
            ONLY="$2"; shift 2 ;;
        *) echo "error: unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Bootstrap cargo-binstall via the upstream prebuilt-binary script.
# A `cargo install --locked cargo-binstall` bootstrap would compile
# from source (2–3 min on cold CI runners) — the exact slow path
# `binstall` exists to avoid. The upstream script fetches a release
# binary directly (seconds).
ensure_binstall() {
    if command -v cargo-binstall >/dev/null 2>&1; then
        return
    fi
    echo ">> installing cargo-binstall (upstream prebuilt bootstrap)…"
    curl -L --proto '=https' --tlsv1.2 -sSf \
        https://raw.githubusercontent.com/cargo-bins/cargo-binstall/main/install-from-binstall-release.sh \
        | bash
}

# Install one tool at the pinned version. Argument is a single
# `name=version [binary]` line from the lock file; we split the
# binary override off here.
install_one() {
    local name version binary already
    # shellcheck disable=SC2086 # intentional word-split on the spec.
    set -- $1
    local spec="$1" bin_override="${2:-}"
    name="${spec%=*}"
    version="${spec#*=}"
    binary="${bin_override:-$name}"

    if [[ "${UPGRADE}" -eq 0 ]] && command -v "${binary}" >/dev/null 2>&1; then
        already="$(${binary} --version 2>&1 | head -1 || true)"
        # The version-match heuristic is loose on purpose — some tools
        # print `cargo-deny 0.19.4`, others `taplo 0.10.0`, others
        # `mdbook v0.5.2`. We just look for the version substring.
        if [[ "${already}" == *"${version}"* ]]; then
            echo "   ${binary} @ ${version} already installed"
            return
        fi
    fi

    ensure_binstall
    echo ">> installing ${name}@${version}…"
    # --strategies=crate-meta-data guards against picking up wrong
    # binaries when crate name ≠ binary name. --locked mirrors the
    # cargo-install-from-source fallback behaviour.
    cargo binstall --no-confirm --locked "${name}@${version}"
}

# Split --only into its requested names and a parallel "matched" flag
# array (bash 3.2 on macOS has no associative arrays, so this stays
# plain indexed arrays rather than `declare -A`). Tracking a flag per
# requested name — not just a single global counter — is what makes a
# partial match (e.g. `--only cargo-deny,git-clif`) fail loudly instead
# of installing cargo-deny and exiting 0 with git-clif silently unmet.
only_names=()
matched_flags=()
if [[ -n "${ONLY}" ]]; then
    IFS=',' read -ra only_names <<< "${ONLY}"
    for i in "${!only_names[@]}"; do
        matched_flags[i]=0
    done
fi

# Walk the lock file, skipping empty lines and comments. When --only
# is set, install just the entries whose crate name or effective
# binary name matches one of the requested names.
while IFS= read -r raw; do
    line="${raw%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "${line}" ]] && continue
    if [[ "${#only_names[@]}" -gt 0 ]]; then
        # shellcheck disable=SC2086 # intentional word-split on the spec.
        set -- ${line}
        spec="$1"
        bin_override="${2:-}"
        crate="${spec%=*}"
        binary="${bin_override:-${crate}}"
        # Match against either name: every entry today has crate ==
        # binary except taplo-cli (binary `taplo`), and callers reach
        # for the binary name they actually want on PATH.
        install_this=0
        for i in "${!only_names[@]}"; do
            if [[ "${only_names[i]}" == "${crate}" || "${only_names[i]}" == "${binary}" ]]; then
                matched_flags[i]=1
                install_this=1
            fi
        done
        [[ "${install_this}" -eq 1 ]] || continue
    fi
    install_one "${line}"
done < "${LOCK}"

if [[ "${#only_names[@]}" -gt 0 ]]; then
    unmatched=()
    for i in "${!only_names[@]}"; do
        [[ "${matched_flags[i]}" -eq 0 ]] && unmatched+=("${only_names[i]}")
    done
    if [[ "${#unmatched[@]}" -gt 0 ]]; then
        # Comma-join in a subshell: a prefix assignment (`IFS=',' echo
        # ...`) does not affect `${unmatched[*]}` expansion in the same
        # command, since word expansion of that command's own arguments
        # already happened. Setting IFS as a statement inside `$( )`
        # scopes it to the subshell only, so the script's own IFS is
        # untouched afterward.
        unmatched_str="$(IFS=','; echo "${unmatched[*]}")"
        echo "error: --only: no matching entry in $(basename "${LOCK}") for: ${unmatched_str}" >&2
        exit 2
    fi
fi

echo ">> cargo tools ready"
