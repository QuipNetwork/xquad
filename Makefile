.PHONY: all xquad repl \
        preflight preflight-rs preflight-py preflight-parity preflight-docs \
        preflight-policy preflight-release \
        lint-rust lint-python lint-policy check-atomic-spec check-commit-messages \
        check-release-notes \
        test-rust test-python check-parity check-docs-handwritten \
        check-crate-publish check-python-dists check-release \
        check-version-sites list-version-sites set-version \
        deps deps-miri deps-py deps-wasm \
        install-hooks \
        lint lint-clippy lint-doc lint-deny-rs lint-py check-uv-lock \
        fmt fmt-rs fmt-toml fmt-check fmt-check-rs fmt-check-toml fmt-py fmt-check-py \
        test test-unit-rs test-integ-rs test-doc test-miri test-py test-wasm test-substrate-fixture \
        test-quip test-quip-sign test-quip-e2e check-xqffi-fresh \
        test-cuda test-qpu test-metal \
        opcode-parity opcode-parity-rs opcode-parity-py \
        metering-parity \
        conformance conformance-rs conformance-py conformance-coverage \
        example-smoke \
        build-docs regen-docs regen-docs-opcodes regen-docs-examples \
        check-docs-generated check-docs-opcodes check-docs-examples \
        check-docs-drift check-docs-mermaid check-docs-readme check-docs-prose \
        serve-docs \
        changelog render-changelog changelog-release

all: fmt lint test

# -- Phase Aggregates -------------------------------------------------------

# Plain prerequisite lists, no recipes of their own. Each one maps 1:1 onto
# a future CI job's target -- a verify:rust job would run `make -k
# lint-rust`, test:rust would run `make -k test-rust`, and so on. CI always
# invokes these with `-k` so every prerequisite is attempted even after one
# fails; a plain local `make lint-rust` fails fast on the first, like any
# other non -k target. `preflight-*` below is built from these.
#
lint-rust: fmt-check-rs lint-clippy lint-doc

lint-python: fmt-check-py lint-py

# "Does the repo still agree with its own policy": advisory + license
# scanning, the changelog template smoke test, the atomic spec-MR guard,
# and the commit-message guard. Distinct from "does the Rust workspace
# build clean" (lint-rust) and "does the Python workspace format/lint
# clean" (lint-python).
#
# fmt-check-toml lives here rather than in lint-rust or lint-python. The
# repo's TOML spans Cargo.toml (workspace root and every crate),
# every Python package's pyproject.toml, deny.toml and cliff.toml, so it
# has no single-language home. taplo is an acknowledged outlier in a
# policy phase -- it is formatting, not policy -- but this phase is the
# only one that compiles nothing, and pairing a two-second TOML check
# with clippy and rustdoc would strap it to the slowest job in the
# pipeline. lint-python stays ruff-only on purpose so it maps onto a job
# that needs no cargo toolchain at all; taplo is a cargo-installed
# binary (scripts/cargo-tools.lock), so folding it in there would
# reintroduce the one dependency that phase exists to avoid.
#
# check-commit-messages joins the aggregate here rather than staying
# CI-inert: .githooks/commit-msg already enforces the same grammar
# locally, but it is opt-in (make install-hooks) and bypassable (git
# commit --no-verify), so it was never the actual gate. Putting the
# range-scanning script behind this same aggregate -- rather than
# calling it directly from the CI job -- keeps the "CI always calls
# make -k <target>" rule uniform: no check this phase runs is invoked
# from YAML that isn't also reachable, and checked, from a local `make
# lint-policy`.
#
# check-release-notes joins for the same reason and closes the gap that
# render-changelog alone leaves open: render-changelog proves cliff.toml
# parses and its Tera templates do not error, but it renders the whole,
# unscoped history, so it would stay green through the exact QUI-1096
# regression (every GitLab release page republishing every prior
# release's changelog). check-release-notes instead renders every
# non-rc tag's actual PREV..tag range and asserts each yields exactly
# one `## [` heading, which is the regression itself. It needs full tag
# history, not just depth, which verify:policy already provides via
# GIT_DEPTH: 0 (see verify.yml), so this line is the only CI-side change
# it needs.
#
# preflight-py lists fmt-check-toml directly so a Python-only
# contributor gets the TOML check without running the rest of the
# policy phase.
lint-policy: fmt-check-toml lint-deny-rs render-changelog check-atomic-spec check-commit-messages check-release-notes

# Wraps scripts/check-atomic-spec-mr.sh, forwarding the optional positional
# BASE/HEAD refs the way the script expects. Both are quoted so that
# `make ... HEAD=y` with no BASE passes an empty first argument rather
# than shifting y into BASE_REF's position; the scripts read each with
# `${N:-}` and fall back when it is empty. With neither set, the script
# falls back to $CI_MERGE_REQUEST_DIFF_BASE_SHA, then
# `git merge-base origin/main HEAD`.
#   make check-atomic-spec BASE=<sha> HEAD=<sha>
check-atomic-spec:
	bash scripts/check-atomic-spec-mr.sh "$(BASE)" "$(HEAD)"

# Wraps scripts/check-commit-messages.sh -- same BASE/HEAD forwarding and
# the same fallback order as check-atomic-spec above.
#   make check-commit-messages BASE=<sha> HEAD=<sha>
check-commit-messages:
	bash scripts/check-commit-messages.sh "$(BASE)" "$(HEAD)"

test-rust: test-unit-rs test-integ-rs test-doc

test-python: test-py

# conformance-coverage runs last and always passes: it prints the
# per-opcode coverage report into the CI log so the holes are visible on
# every pipeline rather than only when someone runs the target by hand.
# The check that can *fail* on coverage is the ratchet in
# conformance/tests/coverage.rs, which conformance-rs already runs.
check-parity: opcode-parity conformance example-smoke metering-parity conformance-coverage

# All three are alpine, handwritten-docs checks -- no uv, no generation, no
# mdbook. Kept apart from check-docs-generated (which needs uv) so the two
# doc-check aggregates map onto CI jobs with different runtime
# requirements. check-docs-prose adds one binary to that image and no
# language runtime: Vale is a Go executable fetched by the CI job. Not a
# static one, despite being Go -- it links glibc and libstdc++ for the
# spell checker, which is why docs:handwritten's APK_PACKAGES carries
# gcompat, libstdc++ and libgcc.
check-docs-handwritten: check-docs-drift check-docs-readme check-docs-prose

# Stdlib-only Python for the version-site guard below. Pinned to 3.13 for
# the same reason scripts/smoke-wheels.sh pins its venv rather than taking
# whatever `python3` the runner carries: the guard needs tomllib (3.11+),
# macOS ships 3.9 as `python3`, and the `python3` first on PATH inside
# release:validate belongs to maturin's uv-tool venv, whose minor version
# is not ours to choose. Unlike DOCSGEN this takes no `--with`: tomllib,
# re and pathlib are the whole dependency set, so it never touches an
# index, and `--no-project --isolated` keeps it independent of deps-py --
# which matters because check-release must run before anything is built.
VERPY := uv run --no-project --isolated --python 3.13 python

# Wraps scripts/check-version-sites.py, forwarding the optional positional
# TAG the way the script expects. With TAG unset the script falls back to
# $CI_COMMIT_TAG, and with neither set the version comparison is a no-op
# (its table sweeps still run).
#
# Quoted so an unset TAG passes an empty argument rather than none, which
# is safe only because the script reads an empty positional as an absent
# one. The parallel with check-atomic-spec above is a spelling and not a
# mechanism: that target calls a bash script reading `${N:-}`, where empty
# and absent coincide for free, whereas argparse distinguishes them and
# had to be told. release:validate reaches this guard only through
# `make -k check-release`, so that one distinction is the difference
# between a tag pipeline being checked and being waved through.
#   make check-version-sites TAG=v0.4.0-rc1
check-version-sites:
	$(VERPY) scripts/check-version-sites.py "$(TAG)"

# The authoritative list of every place a release version is written.
# RELEASING.md's bump step points at this target rather than restating the
# list, so the prose and the check cannot drift.
list-version-sites:
	$(VERPY) scripts/check-version-sites.py --list

# Writes every site in that list to VERSION, each in its own ecosystem's
# spelling -- SemVer in the Cargo manifests, PEP 440 in the Python ones, so
# `0.4.1-dev` lands as `0.4.1-dev` in one and `0.4.1.dev0` in the other.
#
# The lockfiles are not version sites and do not move on their own. Follow
# with `cargo check`, `uv lock`, and `cargo update -p xqvm --manifest-path
# fixtures/pallet-xqvm/Cargo.toml`; the target prints the same three.
#
# Every back-merge conflicts on these sites by construction, so the
# resolution is this one command rather than 23 hand edits.
#   make set-version VERSION=0.4.1-dev
set-version:
	$(VERPY) scripts/check-version-sites.py --set "$(VERSION)"

# "Is the release artefact publishable": the tag/manifest version check,
# crate dry-run packaging, and the Python distribution
# build/check/smoke-install -- the questions CI's release:validate job
# answers on every pipeline. Deliberately an
# aggregate of prerequisites rather than a scripts/check-release.sh
# wrapper: CI invokes this with `make -k`, and a `set -euo pipefail`
# wrapper defeats `-k` exactly the way a hand-rolled script would --
# the first failing command would end the run, so a broken crate
# manifest would hide a broken wheel and the next pipeline would find
# it instead of this one. Same argument as lint-policy above.
#
# check-crate-publish packages every workspace member and resolves each
# against the locally packaged siblings rather than against crates.io.
# That is what makes it work on a release MR where the bumped xqvm
# version is not yet published: a plain per-crate dry-run would fail
# resolving xqasm's path+version dependency on xqvm. It replaces
# scripts/validate-crate-publish.sh, deleted in this change.
#
# --workspace covers exactly xqvm, xqasm and xqcli. The workspace has
# six members (Cargo.toml `members`); the other three carry
# publish = false and are skipped: xqffi (pyo3 cdylib, shipped as a
# PyPI wheel), conformance (cross-implementation test harness) and
# fixtures/xqvm-wasm (no_std build fixture). Enumerated in full because
# this comment is the justification for using --workspace here at all,
# and a reader auditing it against `members` should not find a member
# it does not account for.
# Built in a scratch target dir AND under a scratch CARGO_HOME, both
# removed first, so the check depends on the checkout alone.
#
# CI caches all of target/, .cargo/registry/ and .cargo/git/ keyed on
# Cargo.lock, and every branch in a stack shares one Cargo.lock, so those
# caches are shared between commits that do not share sources. The
# workspace version is the same `-dev` string on all of them, which leaves
# cargo no way to tell one commit's staged xqvm from another's: for a
# given package id it neither re-extracts the .crate over an existing
# source directory of that name, nor rebuilds an rlib it already has. A
# sibling job's xqvm is linked instead of this commit's and the verify
# step fails on a method the tree plainly has.
#
# Both halves of that state have to go, because the dry-run writes to
# both. Verifying xqcli resolves xqvm and xqasm through the temporary
# local registry rather than by path, and cargo extracts those .crate
# files into $CARGO_HOME/registry/src/ -- the shared, cached one. Scoping
# only CARGO_TARGET_DIR left that extraction pointed at the cache and the
# stale-source failure survived; scoping CARGO_HOME as well is what makes
# the check hermetic. The cost is a cold index and dependency fetch on
# every run of this target, which is the price of the guarantee.
#
# Both scratch trees live under a mktemp -d root outside target/, not
# under it (the old target/publish-check). CI's default cache is keyed
# on Cargo.lock and covers all of target/, so a scratch dir inside it
# was itself a cache entry: every run uploaded a throwaway cargo home
# and workspace build that the next run's `rm -rf` deleted unread before
# the hermetic guarantee above even engaged. A trap on EXIT removes the
# mktemp root on success or failure so nothing throwaway is left behind
# either way.
check-crate-publish:
	@set -eu; scratch="$$(mktemp -d)"; trap 'rm -rf "$${scratch}"' EXIT; \
	CARGO_HOME="$${scratch}/cargo-home" \
	CARGO_TARGET_DIR="$${scratch}/target" \
	cargo publish --dry-run --locked --workspace

# Needs maturin, twine and uv on PATH -- the same kind of prerequisite
# note the hardware tiers below give for a CUDA device, a D-Wave QPU
# token or a Metal-capable Mac. Builds the five distributions, runs
# twine check, and smoke-installs each into a throwaway venv via
# scripts/smoke-wheels.sh (wired into python-dists.sh's verify phase).
check-python-dists:
	bash scripts/python-dists.sh check

# check-version-sites leads: it is sub-second, and on a tag pipeline the
# decisive failure belongs at the top of a 75-minute job's log rather than
# after the crate dry-run. `-k` means the other two run regardless of
# order, so this is about log readability, not gating.
check-release: check-version-sites check-crate-publish check-python-dists

# -- Preflight --------------------------------------------------------------

# Run locally exactly what CI enforces, grouped by language so a single-
# language MR can pre-flight just its half. Built from the phase
# aggregates above so the two structures stay in sync by construction
# rather than by discipline.
#
# preflight-rs is pure-Rust (no uv / maturin prereqs); preflight-py and
# preflight-parity pull the deps-py maturin rebuild via their leaf
# prereqs; preflight-docs needs uv for docs generation but not the
# maturin rebuild. test-wasm and test-substrate-fixture are blocking CI
# gates with no phase aggregate of their own (each is a single dedicated
# CI job), so preflight-rs lists them as leaves alongside lint-rust and
# test-rust -- omitting both here was a prior gap, not a deliberate
# exclusion. Both CI jobs are path-gated (.gitlab/ci/test.yml's "Path
# gating" section) while these targets are not: a local preflight runs
# them unconditionally, so it still covers the case where the MR
# pipeline decided the diff could not reach them. Still excluded on
# purpose: test-miri (not a CI gate, needs nightly; lives under Optional
# Checks in the MR template), the hardware / SolverQuip tiers
# (test-quip*, test-cuda, test-qpu, test-metal), which need a real
# device, token or devnet and are driven by hand or a dedicated runner,
# and preflight-release (below), which needs maturin/twine/uv and builds
# five distributions into a throwaway venv on top of a full-verify
# workspace packaging dry-run.
preflight-rs: lint-rust lint-deny-rs test-rust test-wasm test-substrate-fixture

# check-uv-lock joins here because it is cheap and read-only (uv lock
# --check does not sync, it only fails when uv.lock is stale against
# pyproject.toml).
#
# Its POSITION is load-bearing, not cosmetic: it has to precede test-py.
# test-py depends on deps-py, whose bare `uv sync` silently rewrites a
# stale uv.lock in place (see check-uv-lock's own comment below). make
# runs prerequisites left to right, so a check-uv-lock listed after
# test-py would only ever see a lock that deps-py had already
# regenerated -- it could not fail, and a contributor who edited a
# pyproject.toml and forgot `uv lock` would get a green preflight and
# then fail in CI's verify:python, which extends `.python-tools` and
# never syncs. Left-to-right ordering holds under serial make only, but
# this Makefile is already parallel-unsafe (recipes share .venv/ and
# target/), so that is not a new constraint.
#
# check-xqffi-fresh is deliberately NOT here even though it is also a
# Python check: it runs `uv sync --extra dwave` and a maturin rebuild,
# which mutate the developer's own .venv/, and a preflight aggregate
# that leaves dwave-system installed in a contributor's working
# environment as a side effect is a bad trade. It stays reachable only
# as its own leaf target (make check-xqffi-fresh) and through its own
# CI job (verify:xqffi).
preflight-py: fmt-check-toml check-uv-lock lint-python test-py

preflight-parity: check-parity

preflight-docs: check-docs-generated check-docs-handwritten

# The policy phase has no language of its own, so it gets its own
# preflight leg rather than riding on preflight-rs. Without it,
# render-changelog and check-atomic-spec are enforced by CI but
# unreachable from any preflight target -- which is what the
# "mirrors CI" claim above was quietly false about.
preflight-policy: lint-policy

# Stays OUT of the plain preflight aggregate below, on purpose, matching
# test-miri and the hardware tiers: check-release builds five
# distributions and installs them into a throwaway venv, on top of the
# workspace-wide cargo publish dry-run, so hanging it off preflight-rs
# or the default `make preflight` would be an unwelcome surprise for a
# contributor who just wants the fast local loop. Run it explicitly
# before a release MR.
preflight-release: check-release

preflight: preflight-rs preflight-py preflight-parity preflight-docs preflight-policy

# -- Local setup ------------------------------------------------------------

# Bootstrap everything a contributor needs to use the XQuad toolchain
# locally:
#   - Python workspace (xqvm_py, xqcp, xqsa, xqffi) synced into .venv/
#     with the maturin-built xqffi extension; each package's editable
#     install puts the repo root on sys.path, so any script in the
#     repo can `import xqcp` etc.
#   - Rust CLI installed as the `xquad` binary under ~/.cargo/bin/ so
#     `xquad run …`, `xquad dism …`, etc. work from any shell.
#
# Run once per environment; re-run after a pull that touches Rust
# sources or workspace deps. Publishing / wheel distribution is out
# of scope (see QUI-442).
xquad: deps-py
	cargo install --path xqcli --locked --force

# -- Dependencies -----------------------------------------------------------

# Install (or verify) the pinned cargo-based dev tools. Versions come
# from scripts/cargo-tools.lock; the install script short-circuits
# when a tool is already on PATH at the pinned version so a warm
# local environment — or a CI cache hit — pays zero cost. First-
# install goes through cargo-binstall (prebuilt binaries in seconds)
# with a cargo-install-from-source fallback.
#
# Deliberately does not depend on install-hooks: installing dependencies
# must not mutate the developer's git config as a side effect. Run
# `make install-hooks` once, separately, to opt in.
deps:
	rustup component add clippy rustfmt
	bash scripts/install-cargo-tools.sh

deps-miri:
	rustup toolchain install nightly --component miri
	cargo +nightly miri setup

# Install Node.js, wasm-pack and the WASM targets required for no_std
# correctness tests (fixtures/xqvm-wasm). wasm32-unknown-unknown is used by
# wasm-bindgen-test (Node.js runner); wasm32v1-none is the bare-metal
# target Substrate runtimes compile to -- we verify xqvm builds for it
# as a separate cargo-build check in test-wasm.
# Re-run if wasm-pack is not on PATH or a target is missing.
#
# Node is a real prerequisite of this target, not a CI detail:
# `wasm-pack test --node` runs the wasm-bindgen-test suite inside Node, so
# test-wasm cannot work without it. It used to be installed inline in
# test:wasm's before_script with a bare apt-get, which left this target and
# the CI job disagreeing about what deps-wasm covers.
#
# Moving it here has to DETECT rather than assume, because this is a
# contributor-facing target and the apt path does not exist on macOS:
#   node on PATH        -> nothing to do, just log the resolved version so
#                          runner image changes stay visible in job output
#   no node, apt-get    -> install it (the rust:latest CI image ships none)
#   no node, no apt-get -> stop here naming the platform's install route,
#                          rather than dying on `apt-get: command not found`
deps-wasm:
	@if command -v node >/dev/null 2>&1; then \
		echo "node $$(node --version) already installed"; \
	elif command -v apt-get >/dev/null 2>&1; then \
		apt-get update -q && apt-get install -yq nodejs; \
		node --version; \
	else \
		echo "error: node is required by 'wasm-pack test --node' but is not on PATH," >&2; \
		echo "and this platform has no apt-get to install it with. Install Node.js first:" >&2; \
		echo "  macOS: brew install node" >&2; \
		echo "  other: https://nodejs.org/en/download" >&2; \
		exit 2; \
	fi
	bash scripts/install-cargo-tools.sh --only wasm-pack
	rustup target add wasm32-unknown-unknown
	rustup target add wasm32v1-none

# Sync the Python workspace (xqffi, xqvm_py, xqcp, xqsa) into .venv/
# via uv. Assumes `uv` is already on $PATH; CI installs it in its
# before_script.
#
# `uv sync` alone can skip rebuilding the maturin-built xqffi cdylib
# when Cargo source has changed but uv's editable-wheel cache is
# still valid, leaving .venv/.../xqffi.*.so stale. An explicit
# `maturin develop` after sync guarantees the extension matches
# current Rust sources — essential for local runs of
# `make example-smoke`, `make test-py`, etc.
#
# `--locked` on that maturin call is the same contract every other
# resolving cargo invocation in this file carries (see CONTRIBUTING.md,
# "Dependencies and Lockfiles"), and it is worth naming here because
# deps-py is the contributor bootstrap: a Cargo.lock that has drifted
# from the manifests now fails `make deps-py` outright rather than being
# quietly re-resolved into a different dependency set. Regenerate with
# `cargo check` and commit the result.
#
# Each package's pyproject.toml sets `dev-mode-dirs = [".."]`, so its
# editable install puts the repo root on sys.path rather than just
# the package directory (a flat-layout quirk: without it, only
# /repo/xqcp would be importable, not /repo). That's what lets
# scripts run from sibling directories (examples/, scripts/) `import
# xqcp` etc.
deps-py:
	uv sync
	uv run --active maturin develop --locked --manifest-path xqffi/Cargo.toml

# Point git at the repo-tracked .githooks/ directory so the pre-commit
# hook runs on every commit. Run once per clone; bypass ad hoc with
# `git commit --no-verify`. The hook only runs fast format / lint
# checks on staged files (ruff for .py, taplo for .toml); heavier
# checks stay in CI / `make all`.
install-hooks:
	git config core.hooksPath .githooks
	@echo "pre-commit hook installed. bypass with 'git commit --no-verify'."

# -- Formatting -------------------------------------------------------------

# Pinned ruff version for the uvx invocations below (fmt-py, fmt-check-py,
# lint-py). `uvx` fetches an isolated ruff install rather than running
# through the project's own `uv run`, which -- because xqffi is a uv
# workspace member -- would trigger `uv sync` and, with it, a full
# maturin build of xqffi's cdylib just to check Python formatting. An
# unpinned `uvx ruff` would drift from uv.lock's resolved ruff version and
# could break CI on an unrelated upstream release; keep this in sync with
# uv.lock by hand when bumping ruff. .githooks/pre-commit reads this same
# value out of the Makefile at commit time rather than hardcoding a third
# pin, so there is exactly one source of truth.
RUFF_VERSION := 0.15.16

# Pinned PyYAML version for the DOCSGEN invocation below (see its comment
# for why the docs generators run this way). Same rationale and same
# by-hand-with-uv.lock upkeep as RUFF_VERSION above.
PYYAML_VERSION := 6.0.3

# Pinned Vale version for check-docs-prose. The single source of truth for
# it: the `vale` fragment in .gitlab/ci/setup.yml greps this line out of the
# Makefile to build the release URL, the same Makefile-as-source-of-truth
# pattern UV_VERSION uses below and .githooks/pre-commit uses for
# RUFF_VERSION.
#
# Vale fits neither of this repository's other two pinning mechanisms.
# Python tools pin through a _VERSION variable and run as
# `uvx <tool>@<version>`; Rust CLIs pin in scripts/cargo-tools.lock and
# install through cargo. Vale is a Go binary distributed as a release
# tarball, so it follows the third pattern already used for uv and
# release-cli: a pinned version plus a curl fetch. Locally it is whatever
# `vale` is on PATH -- nothing installs it for you, and scripts/lint-prose.sh
# says so when it is missing.
VALE_VERSION := 3.15.1

# Pinned uv version. The single source of truth for it: .gitlab/ci/setup.yml
# greps this line out of the Makefile to build the pinned installer URL
# (https://astral.sh/uv/${UV_VERSION}/install.sh), exactly the way
# .githooks/pre-commit already greps RUFF_VERSION out of this same file
# rather than hardcoding a second copy of the pin. There is no
# TOOLCHAIN_IMAGE-style precedent in this repo for pinning a tool version
# (the three floating CI image tags -- rust:latest, alpine:3, and the
# gitlab-org/cli image -- are a separate, out-of-scope finding); this
# Makefile-as-source-of-truth pattern is the real in-repo precedent.
# 0.11.7 is the version the reference developer machine runs today.
#
# Unlike RUFF_VERSION, this pin binds CI only. `uvx ruff@$(RUFF_VERSION)`
# installs ruff on the local side too, but nothing can install uv through
# uv, so a contributor runs whatever uv is on their PATH. check-uv-lock
# warns when that differs from this value -- see its comment for why a
# warning rather than a gate.
UV_VERSION := 0.11.7

fmt: fmt-rs fmt-toml fmt-py

fmt-rs:
	cargo fmt --all

fmt-toml:
	taplo fmt

fmt-py:
	uvx ruff@$(RUFF_VERSION) format xqvm_py xqcp xqsa xqffi xquad examples scripts

fmt-check: fmt-check-rs fmt-check-toml fmt-check-py

fmt-check-rs:
	cargo fmt --all -- --check

fmt-check-toml:
	taplo fmt --check

fmt-check-py:
	uvx ruff@$(RUFF_VERSION) format --check xqvm_py xqcp xqsa xqffi xquad examples scripts

# -- Lints ------------------------------------------------------------------

lint: lint-clippy lint-doc lint-deny-rs lint-py fmt-check

lint-clippy:
	cargo clippy --locked --workspace --all-targets --all-features -- -D warnings

lint-doc:
	RUSTDOCFLAGS="-D warnings" cargo doc --locked --workspace --all-features --no-deps

lint-deny-rs:
	cargo deny --locked check

lint-py:
	uvx ruff@$(RUFF_VERSION) check xqvm_py xqcp xqsa xqffi xquad examples scripts

# `uv lock --check` is read-only -- it is an alias of `uv sync --locked`
# with no environment sync at all, and fails when uv.lock is stale
# against pyproject.toml rather than silently rewriting it, which is what
# a plain `uv sync` (deps-py, test-py) does. This is the Python-side
# equivalent of the `--locked` flag every cargo invocation in this file
# already carries (check-crate-publish, test-unit-rs via nextest, etc.):
# a stale lockfile fails the check instead of being quietly regenerated
# out from under CI. No `--with`, `--isolated`, or maturin build needed,
# so it costs nothing beyond a resolver pass over the existing lock.
#
# The version probe in front of it is the local consumer of UV_VERSION,
# and it is deliberately a WARNING, not a failure. CI installs the pin
# (.gitlab/ci/setup.yml's `uv` fragment greps it out of this file); a
# contributor's `uv` is whatever they installed, so the two can differ.
# When they do, a resolver or lockfile-format difference between the two
# versions surfaces here as `uv lock --check` failing locally while CI
# is green, or the reverse -- and the obvious reading of that failure
# ("my uv.lock is stale") is the wrong one. Naming the mismatch turns a
# confusing failure into an obvious one. It does not gate: blocking
# every contributor whose uv is newer than the pin would cost more than
# the drift does, and `uv lock --check`'s own exit status is what
# decides this target.
check-uv-lock:
	@have="$$(uv --version 2>/dev/null | awk '{print $$2}')"; \
	if [ -n "$${have}" ] && [ "$${have}" != "$(UV_VERSION)" ]; then \
		echo "warning: local uv $${have} != pinned $(UV_VERSION) (Makefile UV_VERSION, installed by CI);" >&2; \
		echo "         a uv.lock disagreement with CI may be a resolver difference, not a stale lock" >&2; \
	fi
	uv lock --check

# -- Tests ------------------------------------------------------------------

test: test-unit-rs test-integ-rs test-doc test-py

test-unit-rs:
	cargo nextest run --locked --workspace --all-features --lib --cargo-profile ci-test

# xquad-conformance is excluded here because its `python` feature gates
# a test file that shells out to `uv run python -m xqvm_py`, and the
# test:rust CI job does not install uv. The conformance suite has its
# own dedicated job (verify:parity) that covers both runtimes with
# the proper before_script setup.
test-integ-rs:
	cargo nextest run --locked --workspace --exclude xquad-conformance --all-features --test '*' --cargo-profile ci-test

# nextest cannot execute rustdoc doctests, so they are driven by the
# built-in test harness on a dedicated target.
test-doc:
	cargo test --locked --doc --workspace --all-features --profile ci-test

test-miri: deps-miri
	cargo +nightly miri test --locked --workspace --all-features

# `uv run pytest` alone skips rebuilding xqffi's maturin-built cdylib
# when Rust sources have changed (uv's editable-wheel cache masks the
# edit). Depend on deps-py so a fresh maturin develop runs first. CI does
# NOT get this for free from the job's before_script -- `.python`'s
# before_script is a bare `uv sync`, with no maturin step at all (see
# setup.yml's `.fragments.uv-sync`). `test:python` only gets the rebuild
# because its script is `make -k test-py`, which pulls it in through this
# deps-py prerequisite; a job that called pytest directly would silently
# run against a stale cdylib.
# Excludes the hardware-backed solver tests (cuda/qpu/metal); those run in
# their own GPU/QPU runner jobs (.gitlab/ci/hardware.yml) where they hard-fail
# on a missing device/token rather than skip. Also excludes the Quip signing
# tests (quip), which need the `[quip]` extra and run in the dedicated
# `test:quip` job (.gitlab/ci/test.yml). This job runs everywhere, so it must
# deselect them or they would run unconfigured in CI.
test-py: deps-py
	uv run --no-sync pytest xqvm_py/tests xqcp/tests xqsa/tests xquad/tests scripts/tests -m "not cuda and not qpu and not metal and not quip"

# Run the WASM no_std correctness tests (fixtures/xqvm-wasm).
# Two gates in sequence:
#   1. cargo build -p xqvm --target wasm32v1-none --no-default-features --
#      verifies xqvm compiles for the bare-metal Substrate runtime target
#      (no std, no JS ABI, no panic runtime).
#   2. wasm-pack test --node -- executes the wasm-bindgen-test suite
#      inside a real wasm32-unknown-unknown + Node.js environment.
# Depends on deps-wasm (Node.js + wasm-pack + both wasm targets) so the
# target is self-sufficient everywhere: CI's test:wasm job just runs
# `make -k test-wasm` off the plain `.rust` before_script and picks the
# whole toolchain up through this prerequisite.
test-wasm: deps-wasm
	cargo build --locked -p xqvm --target wasm32v1-none --no-default-features
	wasm-pack test --node fixtures/xqvm-wasm --locked

# Run native pallet tests for the Substrate FRAME fixture
# (fixtures/pallet-xqvm). The fixture lives in a standalone workspace to
# isolate the QuipNetwork/polkadot-sdk git dep from the main build.
test-substrate-fixture:
	cargo test --locked --manifest-path fixtures/pallet-xqvm/Cargo.toml

# Full SolverQuip sweep -- the signing tests plus the live-devnet end-to-end
# suite. Run this when an MR changes SolverQuip (xqsa/quip*.py); it is the
# Optional Check the MR template names. Opt-in and NOT part of `make test` /
# preflight: the e2e leaf needs a running Quip devnet and hard-errors without
# QUIP_RPC_URL, so drive it with the devnet env vars set (see test-quip-e2e):
#
#   make test-quip \
#       QUIP_RPC_URL=ws://127.0.0.1:9944 \
#       QUIP_FAUCET_URL=http://127.0.0.1:8087
test-quip: test-quip-sign test-quip-e2e

# Run the quip-marked signing tests (xqsa/tests/test_quip_signing.py): the
# pure-Python SCALE / keystore / extrinsic tests that need the `[quip]` extra
# and so are deselected by `make test-py` (`-m "not ... quip"`). This is the
# local leaf for the CI `test:quip` job (.gitlab/ci/test.yml).
#
# Three-step shape, matching scripts/run-hardware-tests.sh:59-77 rather
# than test-py's `deps-py` + `uv run --no-sync` pair (QUI-1199 asked for
# the latter; it does not work here, see below):
#   1. `uv sync --extra quip` -- extras-bearing sync. setup.yml:256-267
#      names test:quip the SOLE writer of the quip extra's cache
#      contents, and it holds that role only because this call performs
#      its own sync (it pulls in the quip_signer wheel neither test:python
#      nor verify:parity install). Swapping in a `deps-py` prerequisite
#      plus `--no-sync` here, as the ticket's literal text suggested,
#      removes that sync and the cache-writer role with it.
#   2. `uv run --no-sync maturin develop --manifest-path xqffi/Cargo.toml`
#      -- explicit rebuild, so this job never runs against a cdylib
#      staled by step 1's editable-wheel cache restore, the same problem
#      test-py's deps-py prerequisite solves for that job.
#   3. `uv run --no-sync pytest ...` -- `--no-sync` here is load-bearing,
#      not decorative: a bare `uv run --extra quip pytest ...` re-syncs
#      the workspace and, per run-hardware-tests.sh:66-69, reverts xqffi
#      to the cached editable wheel, silently undoing step 2's rebuild
#      and reintroducing the exact stale-cdylib bug this reshape exists
#      to close.
test-quip-sign:
	uv sync --extra quip
	uv run --no-sync maturin develop --locked --manifest-path xqffi/Cargo.toml
	uv run --no-sync pytest xqsa/tests/test_quip_signing.py -m quip

# Live Quip Network devnet end-to-end tests for SolverQuip
# (xqsa/tests/test_quip_live.py) -- the chain-backed sibling of
# `test-quip-sign`. Opt-in and deliberately NOT part of `make test` /
# preflight: like the cuda / qpu / metal hardware tiers they need a running
# Quip devnet, so they are driven by hand or a dedicated runner. Point the two
# env vars at the devnet's RPC + faucet:
#
#   make test-quip-e2e \
#       QUIP_RPC_URL=ws://127.0.0.1:9944 \
#       QUIP_FAUCET_URL=http://127.0.0.1:8087
#
# QUIP_RPC_URL gates the whole module (unset -> every test skips), so the
# target hard-errors when it is missing rather than reporting a hollow, all-
# skipped pass. QUIP_FAUCET_URL is optional but needed for the funded submit
# and end-to-end tiers; without it only the read-only connectivity tests run.
#
# Same three-step shape as test-quip-sign above, for the same reason:
# extras-bearing sync to preserve the cache-writer role, an explicit
# maturin rebuild for a fresh cdylib, `--no-sync` on the pytest call so
# that rebuild is not immediately reverted. See test-quip-sign's comment
# for the full rationale; not repeated here.
test-quip-e2e:
	@if [ -z "$(QUIP_RPC_URL)" ]; then \
		echo "error: QUIP_RPC_URL is required (e.g. make test-quip-e2e QUIP_RPC_URL=ws://127.0.0.1:9944 QUIP_FAUCET_URL=http://127.0.0.1:8087)" >&2; \
		exit 2; \
	fi
	uv sync --extra quip
	uv run --no-sync maturin develop --locked --manifest-path xqffi/Cargo.toml
	QUIP_RPC_URL="$(QUIP_RPC_URL)" QUIP_FAUCET_URL="$(QUIP_FAUCET_URL)" \
		uv run --no-sync pytest xqsa/tests/test_quip_live.py -m quip -v

# Real-hardware xqsa solver tests -- CUDA, D-Wave QPU, Apple Metal -- each
# exercising the encode -> solve -> verify -> decode pipeline against an
# actual device rather than the mocked unit tests in `make test-py`. Each
# delegates to scripts/run-hardware-tests.sh <kind>, which syncs that
# solver's extra, rebuilds xqffi's cdylib through maturin (a plain `uv sync`
# reinstalls it from uv's editable-wheel cache, which is stale whenever only
# Rust sources changed -- the same trap deps-py above documents), preflights
# the device/token before running (hard-erroring on a missing GPU or API
# token rather than letting the tests skip themselves quietly) and treats a
# pytest "collected zero tests" result as a failure rather than a hollow
# pass; see that script for the full rationale.
#
# These three are NOT `deps-py` prerequisites and must not become them.
# deps-py's `uv sync` carries no extra, so ordered before the script it
# would rebuild xqffi and then have the script's own `uv sync --extra
# <solver>` reinstall the stale one straight back over it, and ordered after
# it would prune the extra. The rebuild has to sit between the extra-bearing
# sync and pytest, which is why it lives in the script rather than being
# reused from deps-py.
#
# Opt-in and deliberately NOT part of `make test` / preflight, same policy
# as test-quip-sign / test-quip-e2e above: each needs a real GPU, a D-Wave
# API token, or an Apple Silicon host with Metal available, so they are
# driven by a dedicated CI runner (.gitlab/ci/hardware.yml, whose three jobs
# now invoke these very targets rather than calling pytest directly) or by
# hand.
# No test-hardware aggregate: unlike test-quip (sign + e2e both run
# against the same devnet from any machine), no single environment has a
# GPU, a D-Wave token and Metal all at once, so bundling all three behind
# one target would either be unrunnable everywhere or misleadingly imply
# they belong in one invocation.
test-cuda:
	bash scripts/run-hardware-tests.sh cuda

test-qpu:
	bash scripts/run-hardware-tests.sh qpu

test-metal:
	bash scripts/run-hardware-tests.sh metal

# Asserts the xqffi cdylib xquad actually loads is fresh: build it from
# current Rust sources, then prove by runtime import that the result
# still satisfies the Python surface. `xquad/__init__.py` eagerly imports
# `asm`, `program`, `verifier` and `vm`, each doing a top-level `from
# xqffi.* import <symbol>`, so a bare `import xquad` genuinely exercises
# the compiled extension and binds nine concrete symbols across those
# four submodules. If any of them fails to resolve, the cdylib xqffi
# loaded is missing something the Python surface expects of it -- exactly
# the fresh-cdylib guarantee QUI-1199 exists to make systematic rather
# than hand-enforced at each quip/hardware call site.
#
# The maturin step is what binds the assertion to *this* tree, and it is
# not optional: `uv sync` does not rebuild the cdylib when only Rust
# sources changed, it reinstalls xqffi from uv's editable-wheel cache,
# which those sources do not invalidate (run-hardware-tests.sh:12 and
# deps-py above document the same trap). Without the rebuild this target
# would import whatever wheel the sync restored and report it fresh --
# a check that cannot fail, which is worse than no check. Hence the same
# three-step shape as test-quip-sign / test-quip-e2e above and
# run-hardware-tests.sh: extras-bearing sync, explicit maturin rebuild,
# `--no-sync` on everything after it so the rebuild is not reverted.
#
# `dwave` is the extra to sync, not `cuda` or `metal`: dwave-system
# installs on a plain Linux runner with no device attached, cuda pulls a
# ~1 GB cupy wheel for no reason (this check imports nothing cupy-side),
# and metal is gated behind a `sys_platform == 'darwin'` marker in
# pyproject.toml, so it would install nothing at all on the Linux runner
# this check actually runs on.
#
# Deliberately NOT a preflight-py prerequisite (see that target's
# comment): `uv sync --extra dwave` mutates .venv/, and a preflight that
# leaves dwave-system in a contributor's working environment as a side
# effect is a bad trade. It is still a make target, not a bare script
# invocation, because the house rule is that CI calls `make -k <target>`
# and never a script directly -- here that CI caller is the dedicated
# verify:xqffi job (.gitlab/ci/verify.yml), which runs on every merge
# request rather than being gated behind a protected-ref rule, since a
# pre-merge signal is the entire point.
#
# Running this locally mutates your own .venv/ the same way; re-run
# `make deps-py` afterwards to put it back to the plain (no-extra) sync
# the rest of local dev expects.
check-xqffi-fresh:
	uv sync --extra dwave
	uv run --no-sync maturin develop --locked --manifest-path xqffi/Cargo.toml
	uv run --no-sync python -c \
		"import xquad; \
		syms = [xquad.asm.assemble_source, xquad.asm.disassemble, xquad.asm.parse_xqasm, \
		xquad.program.Vm, xquad.vm.DEFAULT_STEP_LIMIT, xquad.program.XqmxModel, \
		xquad.program.XqmxSample, xquad.verifier.verify, xquad.verifier.verify_source]; \
		assert all(s is not None for s in syms), 'xqffi symbol failed to resolve'; \
		print(f'xqffi fresh: {len(syms)} symbols resolved')"

# -- Conformance ------------------------------------------------------------

# Cross-implementation parity (opcode table, spec conformance vectors).
# `cargo build -p xqvm` exercises the compile-time YAML ↔ opcodes! macro
# check via xqvm/build.rs; the Python script covers the xqvm_py side.
opcode-parity: opcode-parity-rs opcode-parity-py

opcode-parity-rs:
	cargo build --locked -p xqvm

# `deps-py` + `--no-sync` for the same reason as `test-py` and
# `example-smoke`: a bare `uv run` re-syncs the workspace and reinstalls
# xqffi from uv's editable-wheel cache, which is not invalidated by changes
# to Rust sources. That silently replaces the maturin-built extension with a
# stale one for every target that runs after it in the same `make` -- which
# is how `make preflight` could reach `example-smoke` with an xqffi older
# than the tree it just tested.
opcode-parity-py: deps-py
	uv run --no-sync python scripts/check-opcode-parity.py

# Cross-checks the step-cost constants across xqvm/src/metering.rs,
# xqvm_py/metering.py, and spec/xqvm/METERING.md -- the same "generated
# code vs. handwritten mirror vs. spec table" shape as opcode-parity-py,
# for the metering constants instead of the opcode table.
metering-parity: deps-py
	uv run --no-sync python scripts/check-metering-parity.py

conformance: conformance-rs conformance-py

conformance-rs:
	cargo test --locked -p xquad-conformance --no-default-features --features rust

# The Python side (run by CI's verify:parity job) shells out to
# `uv run python -m xqvm_py run` from within the Rust test; the xqffi
# extension (maturin-built) and xqvm_py (editable) must both be
# installed in .venv/ first.
conformance-py: deps-py
	cargo test --locked -p xquad-conformance --no-default-features --features python

# Per-opcode vector coverage: which of the 93 opcodes no vector covers.
# Reports only. The ratchet that stops coverage regressing is a test
# (conformance/tests/coverage.rs) and so already runs under
# conformance-rs; this target is for reading the list.
conformance-coverage:
	cargo run --locked -q -p xquad-conformance -- --coverage

# -- Dev ergonomics ---------------------------------------------------------

# Open a Python REPL with the xqffi extension fresh and the
# workspace packages (xqvm_py, xqcp, xqsa) importable. Depends on
# deps-py so the .so and per-package .pth files stay current;
# `uv run --no-sync` skips the implicit sync that would otherwise
# revert maturin's fresh extension build to a cached wheel.
repl: deps-py
	uv run --no-sync python

# -- Examples ---------------------------------------------------------------

# Run each top-level example on both the Python and the Rust XQVM
# interpreters with the canonical seed and check each finds a valid
# solution (valid == 1). The check is invariant-based rather than a
# golden-file diff: simulated annealing is sensitive to BQM construction
# order, so the two paths can land on different but equally valid optima.
example-smoke: deps-py
	uv run --no-sync python scripts/example-smoke.py

# -- Documentation ----------------------------------------------------------

# `mdbook-mermaid install` writes mermaid.min.js and a mermaid-init.js next
# to book.toml. We want the first and not the second: its initializer is
# written against mdBook 0.5.0, whose theme buttons had bare ids, so against
# our pinned 0.5.2 it throws on every page and diagrams never re-render on a
# theme switch. book.toml therefore lists the tracked initializer, which also
# carries the brand palette, and the generated one is simply never
# referenced. See the header of docs/book/theme/mermaid-init.js.
build-docs:
	mdbook-mermaid install docs/book
	mdbook build docs/book
	sh scripts/check-mermaid-render.sh

# Both generators (scripts/gen-bytecode-docs.py, scripts/gen-example-docs.py)
# import only scripts/_docsgen.py and PyYAML -- no workspace package, so
# they don't need `uv sync` (which would build xqffi's cdylib through
# maturin just to diff some YAML against some Markdown). `--no-project`
# skips project/workspace discovery; `--isolated` on top of that is what
# actually guarantees an ephemeral, PyYAML-only environment rather than
# reusing whatever venv uv finds by walking up parent directories.
#
# scripts/check-opcode-parity.py is NOT run through this: it lazily imports
# xqvm_py.opcodes (see the opcode-parity-py target below), so it genuinely
# needs the full workspace synced.
DOCSGEN := uv run --no-project --isolated --with pyyaml==$(PYYAML_VERSION) python

# Regenerate generated documentation from conformance/opcodes.yaml and
# examples/manifest.yaml.
regen-docs: regen-docs-opcodes regen-docs-examples

regen-docs-opcodes:
	$(DOCSGEN) scripts/gen-bytecode-docs.py

regen-docs-examples:
	$(DOCSGEN) scripts/gen-example-docs.py

# Assert committed generated documentation matches regenerated output. Split
# into two leaf targets so both generators are always attempted rather than
# the second being masked by the first's failure. That "both always run"
# property no longer lives in this recipe (it previously used a shell status
# accumulator) -- it now comes from CI invoking `make -k check-docs-generated`
# uniformly, per the phase-aggregate convention. A plain local
# `make check-docs-generated` fails fast on the first failing leaf, same as
# any other non -k make invocation.
check-docs-generated: check-docs-opcodes check-docs-examples

check-docs-opcodes:
	$(DOCSGEN) scripts/gen-bytecode-docs.py --check

check-docs-examples:
	$(DOCSGEN) scripts/gen-example-docs.py --check

# Guard book prose, SUMMARY.md coverage, and the relative links between book
# pages against known documentation drift and dead links. The in-script
# allowlist is a QUI-977 to-do list, not a permanent exemption.
check-docs-drift:
	bash scripts/check-docs-drift.sh

# Keep published package READMEs to a landing page rather than a second copy
# of the book. `--list` prints every package and its count without enforcing.
check-docs-readme:
	bash scripts/check-readme-length.sh

# House prose rules over the handwritten book pages: no emoji, no em-dash,
# and a spell check against .vale/styles/XQuad/vocab.txt. The config and the
# styles are both in the tree and the script passes --no-global, so this
# gives the same verdict here and in CI -- which is the point of the target.
# Pass file paths to check a single page while editing it.
check-docs-prose:
	bash scripts/lint-prose.sh

# Assert that book diagrams actually rendered, rather than trusting the
# mdbook-mermaid version-skew warning's absence. Needs mdbook and a built
# docs/book/build, so it stays out of check-docs-handwritten (alpine, no
# mdbook) and is not a preflight-docs leaf. build-docs runs it inline, which
# is where CI gets its coverage; this target is for re-checking a book that
# is already built.
check-docs-mermaid:
	sh scripts/check-mermaid-render.sh

serve-docs:
	mdbook-mermaid install docs/book
	mdbook serve docs/book --open

# -- Changelog --------------------------------------------------------------

# Generate CHANGELOG.md from git history via git-cliff (cliff.toml).
# CHANGELOG.md is gitignored: cliff.toml + the conventional-commit log
# are the in-tree source of truth, the file itself is a build output
# (like docs/book/). Run locally to preview unreleased notes; CI runs
# the same target on tag and publishes the result as the GitLab
# Release description.
changelog:
	git-cliff --config cliff.toml --output CHANGELOG.md

# Render-only validation of cliff.toml against current history. Used
# by the verify:policy job to catch broken templates / parser regexes
# before they break a release. Discards the rendered output via --output
# /dev/null rather than the previous mktemp/rm sequence, which also
# leaked the temp file whenever git-cliff failed, since the recipe
# aborted before the rm ran.
render-changelog:
	git-cliff --config cliff.toml --output /dev/null

# Wraps scripts/check-release-notes.sh, which renders every non-rc tag's
# PREV..tag range (the same range changelog-release derives below, for
# every past tag rather than just the one VERSION names) and asserts
# each render yields exactly one `## [` heading. render-changelog above
# only proves cliff.toml parses and its templates do not error -- it
# renders the whole, unscoped history, so it would stay green straight
# through the QUI-1096 regression class (a release page silently
# absorbing every prior release's changelog). This is the check that
# actually exercises the regression. Needs full tag history, which
# verify:policy already provides via GIT_DEPTH: 0 (verify.yml), and
# degrades to a skip-with-message when the clone has no tags at all, so
# a fresh fork or a shallow-clone CI change does not turn it red for the
# wrong reason.
check-release-notes:
	bash scripts/check-release-notes.sh

# Generate the changelog / release notes for a tagged release.
# Invoked from `release:notes` in .gitlab/ci/release.yml with
# VERSION set to the pushed tag, STRIP=all (the GitLab Release page
# supplies its own framing so the cliff.toml header/footer would
# duplicate it), and OUTPUT=release-notes.md. Locally, the same target
# previews a release's CHANGELOG.md before tagging:
#   make changelog-release VERSION=v0.2.0
# Optional flags:
#   STRIP=all|header|footer   forwarded to git-cliff --strip
#   OUTPUT=path               write target (default CHANGELOG.md, `-` for stdout)
#
# Passes an explicit PREV..VERSION (or PREV..HEAD) range rather than
# bare `--tag VERSION` with no range at all -- the pre-fix behaviour.
# With no range, git-cliff walks the ENTIRE history and renders every
# prior release's changelog into this one page: the QUI-1096 bug,
# verified live against the actual GitLab release pages (v0.3.2 showed 7
# sections where it should show 1, v0.4.0-rc1 showed 8).
#
# --- Why the derivation lives in the recipe, not in `$(shell ...)` ---------
#
# VERSION reaches the shell through the ENVIRONMENT (the three
# target-specific `export` lines below), never spliced into a command
# line. That is a security boundary, not a style choice.
# scripts/check-release-notes.sh drives this target in a loop over tag
# names taken verbatim from `git tag -l 'v[0-9]*'`, and that runs inside
# `make lint-policy` -> `verify:policy` on every pipeline. Git refnames
# forbid spaces but permit `;`, `$`, backticks, `&`, `|` and `'`, so a
# tag named `v1.0.0;touch/pwned` -- creatable by anyone with tag-push
# rights and matched by that filter -- would execute as a command if
# VERSION were interpolated. Single-quoting is not enough either: a
# refname may itself contain `'`. An exported variable read back as
# "$${VERSION}" cannot be re-parsed as code at all, which is the only
# form that closes this completely.
#
# Two secondary benefits of the same move. It resolves the range with
# one `git rev-parse` plus one `git describe`, where the previous
# `PREV`/`RANGE` variable pair re-tested the same condition and shelled
# out to git three times per invocation. And it retires the `=`-not-`:=`
# hazard entirely: a recipe body only runs when the target is invoked,
# so there is no longer any way for this git call to fire at Makefile
# PARSE time -- on every `make`, for every target, including from inside
# an extracted source tarball with no .git directory, where the failure
# would land before any guard clause could run.
#
# --- Why the branch shape is what it is -----------------------------------
#
# Two `git describe` branches because this target has two distinct
# callers and only one of them has a real tag to look behind:
#   - `release:notes` (.gitlab/ci/release.yml) runs after the tag is
#     pushed, with VERSION set to that tag -- `$${VERSION}^` resolves.
#   - RELEASING.md step 4's pre-flight preview runs
#     `make changelog-release VERSION=vX.Y.Z` BEFORE the tag is cut, so a
#     bad commit subject can still be fixed and re-merged. On that
#     caller VERSION is not yet a ref, so `$${VERSION}^` has nothing to
#     resolve and `git describe` would simply fail.
# `git rev-parse -q --verify "$${VERSION}"` tells the two apart: if it
# resolves, VERSION is a real tag and the search starts at its parent
# commit; if not, the search starts at HEAD instead, which is where the
# not-yet-tagged commit sits. That same test picks the upper bound of
# the range: `prev..VERSION` once the tag is real, `prev..HEAD` while it
# is still only a preview (VERSION is still passed to git-cliff
# separately via --tag, so the rendered section is labelled with the
# version being previewed rather than "unreleased").
#
# `--exclude='*-rc*'` is what keeps an rc tag from becoming the lower
# bound of a release's range: an rc predecessor is passed over in favour
# of the last real release, so the rc's own commits stay inside the
# range and fold into the next real release's notes.
#
# It pairs with cliff.toml's `tag_pattern` (NOT its `skip_tags`), which
# does the same job for the upper half -- git-cliff does not recognise
# rc tags as releases at all, so one cannot become a boundary inside the
# range either. Both halves are needed: this one is git's view of which
# tag `prev` resolves to, that one is git-cliff's view of which tags
# split a range. See cliff.toml's tag_pattern comment for why
# `skip_tags` is not sufficient there.
#
# An `if`/`then`/`else`, not a `rev-parse && describe-parent || describe-
# HEAD` chain: that reads shorter but is wrong the moment VERSION is a
# real tag with no predecessor of its own, i.e. the very first release a
# repository ever tags. There, `rev-parse` (A) succeeds but `describe
# "$${VERSION}^"` (B) fails with no earlier tag to find, and a plain
# `A && B || C` chain cannot tell "A failed" from "A succeeded, B
# failed" -- both take the `|| C` branch, so it silently falls through
# to describing HEAD instead and `prev` comes back with an unrelated,
# *newer* tag instead of the empty result that should trip the guard.
# Verified against this repo's own v0.1.0 (its parent commit has no
# earlier tag): the chain form resolves `prev` to v0.3.2 -- wrong, and
# wrong silently. The if/then/else form below asks the one question that
# matters (does VERSION already exist as a tag) exactly once, so a real
# tag with no predecessor stays on the parent-describe branch, fails it,
# and `prev` comes back empty, landing correctly in the guard.
#
# `|| true` on each `git describe`: the guard below is what reports an
# unresolvable predecessor, with a message that names the cause. Without
# it `set -e` would abort on the describe itself and print git's own
# error instead.
changelog-release: export VERSION := $(VERSION)
changelog-release: export STRIP   := $(STRIP)
changelog-release: export OUTPUT  := $(OUTPUT)
changelog-release:
	@set -eu; \
	if [ -z "$${VERSION}" ]; then \
		echo "error: VERSION is required (e.g. make changelog-release VERSION=v0.2.0)" >&2; \
		exit 2; \
	fi; \
	if git rev-parse -q --verify "$${VERSION}" >/dev/null 2>&1; then \
		prev="$$(git describe --tags --abbrev=0 --exclude='*-rc*' "$${VERSION}^" 2>/dev/null || true)"; \
		range="$${prev}..$${VERSION}"; \
	else \
		prev="$$(git describe --tags --abbrev=0 --exclude='*-rc*' HEAD 2>/dev/null || true)"; \
		range="$${prev}..HEAD"; \
	fi; \
	if [ -z "$${prev}" ]; then \
		echo "error: no predecessor tag found for VERSION=$${VERSION} (git describe --tags --abbrev=0 --exclude='*-rc*' <ref> returned nothing); changelog-release needs at least one earlier non-rc tag to bound the range" >&2; \
		exit 2; \
	fi; \
	echo "changelog-release: rendering $${range} as $${VERSION}" >&2; \
	set -- --config cliff.toml --tag "$${VERSION}" "$${range}" \
		--output "$${OUTPUT:-CHANGELOG.md}"; \
	if [ -n "$${STRIP}" ]; then set -- "$$@" --strip "$${STRIP}"; fi; \
	git-cliff "$$@"
