.PHONY: all xquad repl \
        preflight preflight-rs preflight-py preflight-parity preflight-docs \
        preflight-policy \
        lint-rust lint-python lint-policy check-atomic-spec check-commit-messages \
        test-rust test-python check-parity check-docs-handwritten \
        deps deps-miri deps-py deps-wasm \
        install-hooks \
        lint lint-clippy lint-doc lint-deny-rs lint-py \
        fmt fmt-rs fmt-toml fmt-check fmt-check-rs fmt-check-toml fmt-py fmt-check-py \
        test test-unit-rs test-integ-rs test-doc test-miri test-py test-wasm test-substrate-fixture \
        test-quip test-quip-sign test-quip-e2e \
        test-cuda test-qpu test-metal \
        opcode-parity opcode-parity-rs opcode-parity-py \
        conformance conformance-rs conformance-py \
        example-smoke \
        build-docs regen-docs regen-docs-opcodes regen-docs-examples \
        check-docs-generated check-docs-opcodes check-docs-examples \
        check-docs-drift check-docs-mermaid check-docs-readme serve-docs \
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
# preflight-py lists fmt-check-toml directly so a Python-only
# contributor gets the TOML check without running the rest of the
# policy phase.
lint-policy: fmt-check-toml lint-deny-rs render-changelog check-atomic-spec check-commit-messages

# Wraps scripts/check-atomic-spec-mr.sh, forwarding the optional positional
# BASE/HEAD refs the way the script expects. With neither set, the script
# falls back to $CI_MERGE_REQUEST_DIFF_BASE_SHA, then
# `git merge-base origin/main HEAD`.
#   make check-atomic-spec BASE=<sha> HEAD=<sha>
check-atomic-spec:
	bash scripts/check-atomic-spec-mr.sh $(BASE) $(HEAD)

# Wraps scripts/check-commit-messages.sh -- same BASE/HEAD forwarding and
# the same fallback order as check-atomic-spec above.
#   make check-commit-messages BASE=<sha> HEAD=<sha>
check-commit-messages:
	bash scripts/check-commit-messages.sh $(BASE) $(HEAD)

test-rust: test-unit-rs test-integ-rs test-doc

test-python: test-py

check-parity: opcode-parity conformance example-smoke

# Both are alpine, handwritten-docs checks -- no uv, no generation, no
# mdbook. Kept apart from check-docs-generated (which needs uv) so the two
# doc-check aggregates map onto CI jobs with different runtime
# requirements.
check-docs-handwritten: check-docs-drift check-docs-readme

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
# Checks in the MR template) and the hardware / SolverQuip tiers
# (test-quip*, test-cuda, test-qpu, test-metal), which need a real
# device, token or devnet and are driven by hand or a dedicated runner.
preflight-rs: lint-rust lint-deny-rs test-rust test-wasm test-substrate-fixture

preflight-py: fmt-check-toml lint-python test-py

preflight-parity: check-parity

preflight-docs: check-docs-generated check-docs-handwritten

# The policy phase has no language of its own, so it gets its own
# preflight leg rather than riding on preflight-rs. Without it,
# render-changelog and check-atomic-spec are enforced by CI but
# unreachable from any preflight target -- which is what the
# "mirrors CI" claim above was quietly false about.
preflight-policy: lint-policy

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
# Each package's pyproject.toml sets `dev-mode-dirs = [".."]`, so its
# editable install puts the repo root on sys.path rather than just
# the package directory (a flat-layout quirk: without it, only
# /repo/xqcp would be importable, not /repo). That's what lets
# scripts run from sibling directories (examples/, scripts/) `import
# xqcp` etc.
deps-py:
	uv sync
	uv run --active maturin develop --manifest-path xqffi/Cargo.toml

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
	cargo clippy --workspace --all-targets --all-features -- -D warnings

lint-doc:
	RUSTDOCFLAGS="-D warnings" cargo doc --workspace --all-features --no-deps

lint-deny-rs:
	cargo deny check

lint-py:
	uvx ruff@$(RUFF_VERSION) check xqvm_py xqcp xqsa xqffi xquad examples scripts

# -- Tests ------------------------------------------------------------------

test: test-unit-rs test-integ-rs test-doc test-py

test-unit-rs:
	cargo nextest run --workspace --all-features --lib --cargo-profile ci-test

# xquad-conformance is excluded here because its `python` feature gates
# a test file that shells out to `uv run python -m xqvm_py`, and the
# test:rust CI job does not install uv. The conformance suite has its
# own dedicated job (verify:parity) that covers both runtimes with
# the proper before_script setup.
test-integ-rs:
	cargo nextest run --workspace --exclude xquad-conformance --all-features --test '*' --cargo-profile ci-test

# nextest cannot execute rustdoc doctests, so they are driven by the
# built-in test harness on a dedicated target.
test-doc:
	cargo test --doc --workspace --all-features --profile ci-test

test-miri: deps-miri
	cargo +nightly miri test --workspace --all-features

# `uv run pytest` alone skips rebuilding xqffi's maturin-built cdylib
# when Rust sources have changed (uv's editable-wheel cache masks the
# edit). Depend on deps-py so a fresh maturin develop runs first;
# CI already has this via the job's before_script.
# Excludes the hardware-backed solver tests (cuda/qpu/metal); those run in
# their own GPU/QPU runner jobs (.gitlab/ci/hardware.yml) where they hard-fail
# on a missing device/token rather than skip. Also excludes the Quip signing
# tests (quip), which need the `[quip]` extra and run in the dedicated
# `test:quip` job (.gitlab/ci/python.yml). This job runs everywhere, so it must
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
	cargo build -p xqvm --target wasm32v1-none --no-default-features
	wasm-pack test --node fixtures/xqvm-wasm

# Run native pallet tests for the Substrate FRAME fixture
# (fixtures/pallet-xqvm). The fixture lives in a standalone workspace to
# isolate the QuipNetwork/polkadot-sdk git dep from the main build.
test-substrate-fixture:
	cargo test --manifest-path fixtures/pallet-xqvm/Cargo.toml

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
# local leaf for the CI `test:quip` job (.gitlab/ci/python.yml); `--extra quip`
# pulls the quip_signer wheel those tests importorskip on. No chain required --
# the live-devnet suite is the separate `test-quip-e2e` target below.
test-quip-sign:
	uv run --extra quip pytest xqsa/tests/test_quip_signing.py -m quip

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
test-quip-e2e:
	@if [ -z "$(QUIP_RPC_URL)" ]; then \
		echo "error: QUIP_RPC_URL is required (e.g. make test-quip-e2e QUIP_RPC_URL=ws://127.0.0.1:9944 QUIP_FAUCET_URL=http://127.0.0.1:8087)" >&2; \
		exit 2; \
	fi
	QUIP_RPC_URL="$(QUIP_RPC_URL)" QUIP_FAUCET_URL="$(QUIP_FAUCET_URL)" \
		uv run --extra quip pytest xqsa/tests/test_quip_live.py -m quip -v

# Real-hardware xqsa solver tests -- CUDA, D-Wave QPU, Apple Metal -- each
# exercising the encode -> solve -> verify -> decode pipeline against an
# actual device rather than the mocked unit tests in `make test-py`. Each
# delegates to scripts/run-hardware-tests.sh <kind>, which preflights the
# device/token before running (hard-erroring on a missing GPU or API token
# rather than letting the tests skip themselves quietly) and treats a
# pytest "collected zero tests" result as a failure rather than a hollow
# pass; see that script for the full rationale.
#
# Opt-in and deliberately NOT part of `make test` / preflight, same policy
# as test-quip-sign / test-quip-e2e above: each needs a real GPU, a D-Wave
# API token, or an Apple Silicon host with Metal available, so they are
# driven by a dedicated CI runner (.gitlab/ci/hardware.yml) or by hand.
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

# -- Conformance ------------------------------------------------------------

# Cross-implementation parity (opcode table, spec conformance vectors).
# `cargo build -p xqvm` exercises the compile-time YAML ↔ opcodes! macro
# check via xqvm/build.rs; the Python script covers the xqvm_py side.
opcode-parity: opcode-parity-rs opcode-parity-py

opcode-parity-rs:
	cargo build -p xqvm

opcode-parity-py:
	uv run python scripts/check-opcode-parity.py

conformance: conformance-rs conformance-py

conformance-rs:
	cargo test -p xquad-conformance --no-default-features --features rust

# The Python side (run by CI's verify:parity job) shells out to
# `uv run python -m xqvm_py run` from within the Rust test; the xqffi
# extension (maturin-built) and xqvm_py (editable) must both be
# installed in .venv/ first.
conformance-py: deps-py
	cargo test -p xquad-conformance --no-default-features --features python

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

# Generate the changelog / release notes for a tagged release.
# Invoked from `release:changelog` in .gitlab/ci/release.yml with
# VERSION set to the pushed tag, STRIP=all (the GitLab Release page
# supplies its own framing so the cliff.toml header/footer would
# duplicate it), and OUTPUT=release-notes.md. Locally, the same target
# previews a release's CHANGELOG.md before tagging:
#   make changelog-release VERSION=v0.2.0
# Optional flags:
#   STRIP=all|header|footer   forwarded to git-cliff --strip
#   OUTPUT=path               write target (default CHANGELOG.md)
changelog-release:
	@if [ -z "$(VERSION)" ]; then \
		echo "error: VERSION is required (e.g. make changelog-release VERSION=v0.2.0)" >&2; \
		exit 2; \
	fi
	git-cliff --config cliff.toml --tag $(VERSION) \
		$(if $(STRIP),--strip $(STRIP)) \
		--output $(or $(OUTPUT),CHANGELOG.md)
