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
# Ensure a usable Rust toolchain is on PATH, installing one when the host
# has none.
#
# Every Rust-compiling job in this pipeline except one takes its toolchain
# from the root `default: image: rust:latest`. The exception is
# hardware:metal, which runs on a macOS SHELL executor -- `image:` is
# ignored there, nothing is containerised, and the toolchain is whatever
# the host happens to carry. Since QUI-1191 that job compiles Rust
# (`maturin develop` rebuilds xqffi's cdylib), so it acquired a hard
# dependency on host state without acquiring any way to satisfy it.
#
# That dependency is not one the fleet can meet by accident. `[macos,
# arm64]` selects a POOL of self-hosted group runners rather than a single
# machine, any member can answer the tag query, and no member's owner has
# a reason to know xquad compiles Rust there. Provisioning the toolchain
# in the job makes every pool member equally able to run it and asks
# nothing of anyone's box.
#
# Detection is by `command -v` over the whole PATH, so it finds any
# toolchain the caller's PATH reaches -- not one nominated directory, as
# the `export PATH="${HOME}/.cargo/bin:${PATH}"` this replaced effectively
# did. Be precise about what that does and does not buy: the caller
# composes the PATH, and `.fragments`' `rust-toolchain` entry composes it
# from the two rustup locations plus whatever the runner inherited. A
# Homebrew, mise or asdf toolchain is found when the runner's own PATH
# reaches it and NOT otherwise -- a shell executor's non-login PATH
# routinely omits /opt/homebrew/bin. The improvement is that such a host
# now gets a working job either way: found if visible, installed if not.
# Discovery is wider than before, not exhaustive.
#
# RUSTUP_HOME is deliberately left at its default (${HOME}/.rustup) rather
# than redirected into the build directory, and this is a real trade-off
# rather than an oversight. Redirecting it would keep the toolchain inside
# ${CI_PROJECT_DIR}, leave the host's home untouched, and make the cached
# proxies self-consistent with the toolchain they forward to. It would
# also put roughly 450 MB of unpacked toolchain into a cache archive that
# is written and read on every run, to save an install that costs well
# under a minute. Leaving it in ${HOME}/.rustup lets a shell executor keep
# the toolchain between jobs for free, at the price of that much disk in
# the runner user's home -- so this DOES leave something behind on the
# host, and the honest claim is that it asks nothing of the host's owner,
# not that it writes nothing. Revisit if a runner ever comes under disk
# pressure.
#
# Verification is the point of the script as much as the install is, and
# it runs BEFORE the decision as well as after it. `maturin` reports any
# toolchain problem as "rustc, the rust compiler, is not installed or not
# in PATH", which distinguishes neither "no Rust here" from "rustup
# proxies whose RUSTUP_HOME was wiped", nor says what was searched.
#
# Testing that the proxies RUN, rather than that they exist, is also what
# makes the second case recoverable. `.cargo/bin/` is restored from cache
# independently of ${HOME}/.rustup, so a host whose RUSTUP_HOME was
# cleared gets proxies pointing at nothing. An existence check would take
# that as "already provisioned", skip installing, and fail -- identically,
# on every retry, until someone cleared that runner's cache by hand, which
# is the "one machine has to be special" failure this script exists to
# remove. A usability check falls through to the repair branch instead.
#
# The repair is `rustup toolchain install`, NOT deleting ${CARGO_HOME}/bin
# and reinstalling. rustup-init does refuse to run over an existing
# ${CARGO_HOME}/bin/rustup, so a reinstall would need that removal first
# -- but CARGO_HOME defaults to ${HOME}/.cargo when this script is run by
# hand, where that directory holds a developer's whole cargo-tools set.
# The stale case leaves the `rustup` proxy itself working, so asking it to
# reinstall its own toolchain fixes the same fault destroying nothing.
#
# PATH export note: a child process cannot amend its caller's PATH. When
# this script installs a toolchain it puts ${CARGO_HOME}/bin on PATH for
# its own verification only, so a caller that needs the toolchain in
# LATER steps must place that directory on PATH itself. `.fragments`'
# `rust-toolchain` entry in .gitlab/ci/setup.yml does exactly that before
# invoking this script.
#
# Usage:
#   scripts/ensure-rust-toolchain.sh
#
# Environment:
#   CARGO_HOME      -- install prefix; defaults to ${HOME}/.cargo, matching
#                      rustup's own default. CI points it at
#                      ${CI_PROJECT_DIR}/.cargo, which is inside the cached
#                      build directory.
#   RUST_TOOLCHAIN  -- channel to install; defaults to `stable`, matching
#                      the `rust:latest` image every other job builds under.
#                      The repository pins no rust-toolchain.toml.
#
# Exit codes:
#   0  -- rustc and cargo are present and runnable
#   1  -- no toolchain could be provisioned, or the one found is unusable

set -euo pipefail

toolchain="${RUST_TOOLCHAIN:-stable}"
: "${CARGO_HOME:="${HOME}/.cargo"}"
export CARGO_HOME

have() {
    command -v "$1" >/dev/null 2>&1
}

# Present AND working. Locating a proxy proves nothing about the toolchain
# behind it; see the header note on the stale-cache case.
usable() {
    have rustc && have cargo \
        && rustc --version >/dev/null 2>&1 \
        && cargo --version >/dev/null 2>&1
}

if usable; then
    echo ">> rust toolchain: found rustc at $(command -v rustc)"
elif have rustup; then
    # Proxies without a toolchain. rustup itself still runs, so it can
    # rebuild what is missing -- no removal, and no second download of the
    # installer. `default` is not redundant after `install`: a RUSTUP_HOME
    # rebuilt from empty has no default toolchain configured, and the bare
    # `rustc` proxy the rest of this job calls needs one.
    echo ">> rust toolchain: rustup at $(command -v rustup) has no usable '${toolchain}' -- repairing"
    rustup toolchain install "${toolchain}" || echo "error: rustup toolchain install failed" >&2
    rustup default "${toolchain}" || echo "error: rustup default failed" >&2
else
    echo ">> rust toolchain: no rustc on PATH -- installing '${toolchain}' into ${CARGO_HOME}"
    # Not guarded by `set -e`: a failed install must fall through to the
    # diagnostic below rather than abort with curl's or rustup's exit code
    # and no statement of what the job was looking for.
    if ! curl --proto '=https' --tlsv1.2 -sSf --retry 3 --retry-all-errors https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --profile minimal --default-toolchain "${toolchain}"; then
        echo "error: rustup installation failed" >&2
    fi
    # For this script's own verification only. A child cannot amend its
    # caller's PATH, so the CI fragment puts this directory on PATH too.
    export PATH="${CARGO_HOME}/bin:${PATH}"
fi

if ! usable; then
    echo "error: no usable Rust toolchain after provisioning" >&2
    echo "  rustc:       $(command -v rustc || echo '<not found>')" >&2
    echo "  cargo:       $(command -v cargo || echo '<not found>')" >&2
    echo "  rustup:      $(command -v rustup || echo '<not found>')" >&2
    echo "  CARGO_HOME:  ${CARGO_HOME}" >&2
    echo "  RUSTUP_HOME: ${RUSTUP_HOME:-${HOME}/.rustup}" >&2
    echo "  PATH:        ${PATH}" >&2
    # Whatever rustc had to say about itself. When a proxy is present this
    # is the line that separates "no Rust installed" from "toolchain gone
    # from under the proxy", which is the distinction maturin's message
    # loses and the reason a reader is looking at this output at all.
    if have rustc; then
        rustc --version >&2 || true
    fi
    exit 1
fi

rustc --version
cargo --version
