.PHONY: all xquad repl \
        preflight preflight-rs preflight-py preflight-parity \
        deps deps-docs deps-miri deps-py deps-wasm \
        install-hooks \
        lint lint-clippy lint-doc lint-deny-rs lint-py \
        fmt fmt-rs fmt-toml fmt-check fmt-check-rs fmt-check-toml fmt-py fmt-check-py \
        test test-unit-rs test-integ-rs test-doc test-miri test-py test-wasm test-substrate-fixture \
        opcode-parity opcode-parity-rs opcode-parity-py \
        conformance conformance-rs conformance-py \
        example-smoke \
        docs docs-regen docs-check docs-serve \
        changelog changelog-render changelog-release

all: fmt lint test

# -- Preflight --------------------------------------------------------------

# Run locally exactly what CI enforces, grouped by language so a single-
# language MR can pre-flight just its half. Composes the same leaf targets
# CI invokes; when a CI job is added, add its leaf target here.
#
# preflight-rs is pure-Rust (no uv / maturin prereqs); preflight-py and
# preflight-parity pull the deps-py maturin rebuild via their leaf prereqs.
# test-miri is deliberately excluded -- not a CI gate, needs nightly; it
# lives under Optional Checks in the MR template.
preflight-rs: fmt-check-rs fmt-check-toml lint-clippy lint-doc lint-deny-rs test-unit-rs test-integ-rs test-doc

preflight-py: fmt-check-toml fmt-check-py lint-py test-py

preflight-parity: opcode-parity conformance example-smoke

preflight: preflight-rs preflight-py preflight-parity

# -- Local setup ------------------------------------------------------------

# Bootstrap everything a contributor needs to use the XQuad toolchain
# locally:
#   - Python workspace (xqvm_py, xqcp, xqsa, xqffi) synced into .venv/
#     with the maturin-built xqffi extension and the workspace .pth
#     so any script in the repo can `import xqcp` etc.
#   - Rust CLI installed as the `xquad` binary under ~/.cargo/bin/ so
#     `xquad run …`, `xquad dsm …`, etc. work from any shell.
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
deps: deps-docs install-hooks
	rustup component add clippy rustfmt
	bash scripts/install-cargo-tools.sh

# `deps-docs` is a no-op alias for `deps` now that mdbook / mdbook-
# mermaid are bundled into the single tool-install flow. Kept as a
# phony target so CI jobs referencing `deps-docs` don't break.
deps-docs:
	bash scripts/install-cargo-tools.sh

deps-miri:
	rustup toolchain install nightly --component miri
	cargo +nightly miri setup

# Install wasm-pack and the WASM targets required for no_std correctness
# tests (fixtures/xqvm-wasm). wasm32-unknown-unknown is used by
# wasm-bindgen-test (Node.js runner); wasm32v1-none is the bare-metal
# target Substrate runtimes compile to -- we verify xqvm builds for it
# as a separate cargo-build check in test-wasm.
# Re-run if wasm-pack is not on PATH or a target is missing.
deps-wasm:
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
# The final step writes `xq-rs-workspace.pth` into the venv's
# site-packages, adding the repo root to sys.path. This closes a
# flat-layout editable-install quirk: hatchling puts each package's
# own directory on sys.path (e.g. /repo/xqcp) rather than the parent
# (/repo), so scripts run from sibling directories (examples/,
# scripts/) can't `import xqcp` unless they first inject the repo
# root themselves. With the .pth in place they just work.
deps-py:
	uv sync
	uv run --active maturin develop --manifest-path xqffi/Cargo.toml
	@.venv/bin/python -c "from pathlib import Path; import site; Path(site.getsitepackages()[0], 'xq-rs-workspace.pth').write_text(str(Path('.').resolve()))"

# Point git at the repo-tracked .githooks/ directory so the pre-commit
# hook runs on every commit. Run once per clone; bypass ad hoc with
# `git commit --no-verify`. The hook only runs fast format / lint
# checks on staged files (ruff for .py, taplo for .toml); heavier
# checks stay in CI / `make all`.
install-hooks:
	git config core.hooksPath .githooks
	@echo "pre-commit hook installed. bypass with 'git commit --no-verify'."

# -- Formatting -------------------------------------------------------------

fmt: fmt-rs fmt-toml fmt-py

fmt-rs:
	cargo fmt --all

fmt-toml:
	taplo fmt

fmt-py:
	uv run ruff format xqvm_py xqcp xqsa xqffi xquad examples

fmt-check: fmt-check-rs fmt-check-toml fmt-check-py

fmt-check-rs:
	cargo fmt --all -- --check

fmt-check-toml:
	taplo fmt --check

fmt-check-py:
	uv run ruff format --check xqvm_py xqcp xqsa xqffi xquad examples

# -- Lints ------------------------------------------------------------------

lint: lint-clippy lint-doc lint-deny-rs lint-py fmt-check

lint-clippy:
	cargo clippy --workspace --all-targets --all-features -- -D warnings

lint-doc:
	RUSTDOCFLAGS="-D warnings" cargo doc --workspace --all-features --no-deps

lint-deny-rs:
	cargo deny check

lint-py:
	uv run ruff check xqvm_py xqcp xqsa xqffi xquad examples

# -- Tests ------------------------------------------------------------------

test: test-unit-rs test-integ-rs test-doc test-py

test-unit-rs:
	cargo nextest run --workspace --all-features --lib --cargo-profile ci-test

# xquad-conformance is excluded here because its `python` feature gates
# a test file that shells out to `uv run python -m xqvm_py`, and the
# test:integration CI job does not install uv. The conformance suite
# has its own dedicated jobs (conformance:rust / conformance:python) that
# cover both runtimes with the proper before_script setup.
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
# on a missing device/token rather than skip. This job runs everywhere, so it
# must deselect them or they would run unconfigured in CI.
test-py: deps-py
	uv run --no-sync pytest xqvm_py/tests xqcp/tests xqsa/tests xquad/tests -m "not cuda and not qpu and not metal"

# Run the WASM no_std correctness tests (fixtures/xqvm-wasm).
# Two gates in sequence:
#   1. cargo build -p xqvm --target wasm32v1-none --no-default-features --
#      verifies xqvm compiles for the bare-metal Substrate runtime target
#      (no std, no JS ABI, no panic runtime).
#   2. wasm-pack test --node -- executes the wasm-bindgen-test suite
#      inside a real wasm32-unknown-unknown + Node.js environment.
# Requires: deps-wasm (wasm-pack + both wasm targets installed).
test-wasm:
	cargo build -p xqvm --target wasm32v1-none --no-default-features
	wasm-pack test --node fixtures/xqvm-wasm

# Run native pallet tests for the Substrate FRAME fixture
# (fixtures/pallet-xqvm). The fixture lives in a standalone workspace to
# isolate the QuipNetwork/polkadot-sdk git dep from the main build.
test-substrate-fixture:
	cargo test --manifest-path fixtures/pallet-xqvm/Cargo.toml

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

# conformance:python shells out to `uv run python -m xqvm_py run`
# from within the Rust test; the xqffi extension (maturin-built)
# and xqvm_py (editable) must both be installed in .venv/ first.
conformance-py: deps-py
	cargo test -p xquad-conformance --no-default-features --features python

# -- Dev ergonomics ---------------------------------------------------------

# Open a Python REPL with the xqffi extension fresh and the
# workspace packages (xqvm_py, xqcp, xqsa) importable. Depends on
# deps-py so the .so / .pth stay current; `uv run --no-sync`
# skips the implicit sync that would otherwise revert maturin's
# fresh extension build to a cached wheel.
repl: deps-py
	uv run --no-sync python

# -- Examples ---------------------------------------------------------------

# Run each top-level example on both the Python and the Rust XQVM
# interpreters with the canonical seed and diff the decoded outputs
# against the checked-in golden.json. Catches drift between the two
# interpreters and regressions in either path.
example-smoke: deps-py
	uv run --no-sync python scripts/example-smoke.py

# -- Documentation ----------------------------------------------------------

docs: docs-check
	mdbook-mermaid install .
	mdbook build

# Regenerate docs/bytecode-semantics.md from conformance/opcodes.yaml.
docs-regen:
	uv run python scripts/gen-bytecode-docs.py

# Assert the committed docs/bytecode-semantics.md matches the regenerated
# output; used by the CI docs-build job to prevent silent drift.
docs-check:
	uv run python scripts/gen-bytecode-docs.py --check

docs-serve:
	mdbook-mermaid install .
	mdbook serve --open

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
# by the lint stage to catch broken templates / parser regexes before
# they break a release. Writes to a temp file and discards.
changelog-render:
	@tmp=$$(mktemp); \
		git-cliff --config cliff.toml --output "$$tmp" >/dev/null; \
		rm -f "$$tmp"

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
