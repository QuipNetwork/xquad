# AGENTS.md

This file is the authoritative, tool-neutral source of truth for AI coding assistants
and human contributors working in this repository. It holds repo-wide conventions for
any collaborator.

Tool-specific agent config files (`CLAUDE.md`, etc.) are not checked into git -- each
developer manages their own locally. Claude Code does not read `AGENTS.md` natively, so
Claude Code users should add a gitignored `CLAUDE.md` (or `CLAUDE.local.md`) whose first
line is `@AGENTS.md` to auto-load this file. Personal, repository-specific overrides go
in `AGENTS.local.md` (gitignored), imported at the end of this file and silently skipped
for anyone who does not have one.

The **XQuad Toolchain** is a hardware-agnostic quantum VM and SDK: a problem is
expressed once in XQVM bytecode and executed on any supported quantum backend
(annealers, gate-based chips, etc.). Think LLVM for quantum computing. The codebase
is dual-language: a Rust core (VM, assembler, bytecode, CLI) with Python interfaces
(reference VM, constraint programming DSL, solver adapters, FFI bindings).

You are a senior engineer with deep expertise in Rust 2024 edition and Python 3.13+,
specializing in compiler engineering, systems programming, and high-performance
quantum computing SDKs. You emphasize memory safety, zero-cost abstractions, and
cross-language correctness.

## Quick-Reference Commands

```sh
# Full suite (what CI runs)
make all              # fmt + lint + test (Rust + Python)
make xquad            # bootstrap local dev: Python venv + install xquad CLI
make install-hooks    # point git at .githooks/ pre-commit hook

# Preflight (run locally exactly what CI enforces; N/A a language you didn't touch)
make preflight         # preflight-rs + preflight-py + preflight-parity + preflight-docs + preflight-policy
make preflight-rs      # fmt, taplo, clippy, rustdoc, deny, unit/integration/doc tests
make preflight-py      # taplo, ruff format + lint, pytest, uv.lock freshness
make preflight-parity  # opcode parity, conformance, example smoke
make preflight-docs    # generated-doc freshness + docs drift + README length + prose (needs vale)
make preflight-release # crate packaging dry-run + five Python dists (needs maturin/twine/uv; not in `preflight`)

# Rust
make fmt              # cargo fmt + taplo fmt + ruff format
make lint             # lint-clippy + lint-doc + lint-deny-rs + lint-py + fmt-check
make lint-clippy      # cargo clippy --workspace --all-targets --all-features -- -D warnings
make lint-doc         # RUSTDOCFLAGS="-D warnings" cargo doc --workspace --all-features --no-deps
make lint-deny-rs     # cargo deny check
make test             # test-unit-rs + test-integ-rs + test-doc + test-py
make test-unit-rs     # cargo nextest run --workspace --all-features --lib
make test-integ-rs    # cargo nextest run --workspace --exclude xquad-conformance --all-features --test '*'
make test-doc         # cargo test --doc --workspace --all-features
make test-miri        # cargo +nightly miri test --workspace --all-features
make deps             # install rustup components + pinned cargo tools
make deps-miri        # install nightly + miri

# Single Rust test by name
cargo nextest run --workspace -E 'test(my_test_name)'
cargo test --workspace my_test_name

# Python
make deps-py          # uv sync + maturin develop (editable installs)
make fmt-py           # ruff format across all Python packages
make fmt-check-py     # ruff format --check
make lint-py          # ruff check across all Python packages
make test-py          # pytest xqvm_py/tests xqcp/tests xqsa/tests xquad/tests
make check-uv-lock     # uv lock --check -- fails if uv.lock is stale against pyproject.toml
make check-xqffi-fresh # uv sync --extra dwave + import xquad -- asserts the xqffi cdylib is
                       # fresh; mutates .venv/, re-run `make deps-py` afterwards
make repl             # Python REPL with xqffi + workspace packages

# Cross-language
make opcode-parity    # opcode-parity-rs + opcode-parity-py
make conformance      # conformance-rs + conformance-py
make example-smoke    # run examples on both interpreters, check valid == 1

# Documentation
make build-docs            # mdbook build
make regen-docs            # regenerate generated opcode and example book pages
make check-docs-generated  # assert generated docs match regenerated output
make check-docs-drift      # guard book prose, SUMMARY.md coverage, and page links
make check-docs-mermaid    # assert book diagrams rendered (needs make build-docs first)
make check-docs-readme     # guard published package READMEs against the 100-line limit
make check-docs-prose      # Vale over the handwritten book pages. Needs vale on PATH:
                           # `brew install vale`; pinned by VALE_VERSION in the Makefile
make serve-docs            # mdbook serve --open

# Changelog (CHANGELOG.md is gitignored; cliff.toml + git history is source of truth)
make changelog                              # generate CHANGELOG.md (preview unreleased)
make changelog-release VERSION=v0.2.0       # preview a tag's release notes (renders exactly one section)
make render-changelog                       # render-only validation (lint smoke)
make check-release-notes                    # regression guard: every release renders exactly one section

# Versions and branches (docs/guide/gitflow-protocol.md is normative)
make list-version-sites                     # every place a release version is written
make set-version VERSION=0.4.1-dev          # write them all; then cargo check && uv lock
make check-branch-containment               # origin/main contained in dev / release/* (no-op elsewhere)
```

## Shared Conventions

### License Header

Every new source file must begin with the AGPL license header. Use `//` comments for Rust, `#` comments for Python. In Zed, the `agpl` snippet (`.zed/snippets.json`) inserts the Rust header automatically.

```
Copyright (C) 2026 Postquant Labs Incorporated

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

SPDX-License-Identifier: AGPL-3.0-or-later
```

### DCO Sign-Off

Commits must be signed off: `git commit -s` (DCO requirement from `CONTRIBUTING.md`).

### Branching

Two long-lived branches. [`docs/guide/gitflow-protocol.md`](docs/guide/gitflow-protocol.md) is normative for routing, releases, betas and the back-merge; do not restate it. Before 1.0:

- **`main`** is the non-breaking line, at the next patch's `-dev` version. Everything lands by merge request, except the version bump that reopens it after a release: Maintainers push that directly, and `verify:policy` (`scripts/check-main-direct-push.sh`) fails any other direct push.
- **`dev`** is the breaking line, at the next minor's `-dev` version. Maintainers push to it for back-merges and version bumps only. Anything authored goes through a merge request, because a direct push skips the title check and the atomic spec-MR rule.

Route every change by one question: **does it break?** Before 1.0 the minor is the breaking bump, so "is this a fix?" is the wrong question -- a non-breaking feature goes to `main` and a breaking fix goes to `dev`. A breaking change branches from `dev`, targets `dev`, and carries `!` in its merge request title and commit subjects. Everything else branches from and targets `main`. There is no `hotfix/` branch before 1.0. Branch names stay `feature/qui-<id>` on either line; the target is set on the merge request, not in the name.

When opening a merge request, answer the **Compatibility** block in `.gitlab/merge_request_templates/default.md` -- GitLab auto-populates that template, and its answer decides the target branch, so it has to match the branch chosen on the form. Leaving it blank means an unrouted change. A non-breaking claim needs two or three lines naming what the change touched and why a consumer pinned to the current minor can take it without editing their code. Releases use `release.md` instead (`glab mr create --template release`).

Nothing is squashed on merge. The one exception is post-1.0 `hotfix/*` into `main`.

Feature branches stay linear: update one by rebasing onto its target, never by merging the target in, and merge stacked merge requests bottom-up rather than into each other. A merge commit inside a branch reaches the line with it; `verify:policy` fails the merge request. See "Merge commits" in the git protocol.

### Conventional Commits

All commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <subject>

[optional body]

[optional footer(s)]
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`, `security`, `deprecate`, `release`

**Scope** is optional. When used, it should be the crate or package name (e.g. `xqvm`, `xqcp`, `conformance`).

**Rules:**
- Subject line: imperative mood, lowercase start, no trailing period, max 72 characters
- Body: wrap at 72 characters, explain what and why
- Footer: reference the tracking issue (e.g. `Fixes #123`) to auto-link and close it on merge
- Breaking changes: append `!` after type/scope (e.g. `feat(xqvm)!: remove deprecated API`) or add a `BREAKING CHANGE:` footer
- NEVER add `Co-Authored-By` trailers for AI assistants

**Example:**

```
fix(xqasm): handle forward label references in nested loops

The two-pass label resolver was not accounting for label offsets
inside nested RANGE blocks, causing incorrect jump targets when
a forward reference crossed a loop boundary.

Fixes #456
```

### Post-Edit Linting

After modifying files, run `make fmt` to format everything, or the per-file equivalents from the commands section: `cargo fmt` for Rust, `uvx ruff@<pinned version> check --fix <file>` + `uvx ruff@<pinned version> format <file>` for Python (pin lives in the Makefile's `RUFF_VERSION`), `taplo fmt` for TOML.

### Constraints

- NEVER use emojis in code, documentation, or commit messages.
- NEVER use em-dash in documentation -- prefer using `--`.
- Use 4 spaces for indentation (no tabs).
- Aggregate edits to a single file into one pass. Do not thrash multiple small edits to the same file in sequence.

### Naming

- `snake_case` for functions, modules, and variables.
- `PascalCase` for types, traits, and classes.
- Follow `CONTRIBUTING.md` for additional Rust conventions and ruff/pycodestyle for Python.

## Rust

### Principles

- **Safety First:** zero `unsafe` code unless absolutely necessary. All `unsafe` usage must be documented with `// SAFETY: invariants` and tested with `cargo +nightly miri test`.
- **Idiomatic Rust:** follow `CONTRIBUTING.md` for contribution standards and idiomatic code.
- **Functional-style code:** prefer functional interfaces over imperative code. Use imperative code if functional-style code is less clear.
- **Performance:** zero-cost abstractions. Be efficient in terms of memory use and performance. Prefer stack allocation over heap.
- **Ownership:** design ownership/borrowing structures *before* writing logic.
- **DRY:** extract repeated error construction, span computation, and validation logic into private helper functions. Duplicated patterns are a signal to introduce a named abstraction -- even for internal, non-public code.

### Development Workflow

- **Architecture:** analyze crates, lifetimes, and public APIs first (methods, traits, etc.). Identify possible code repetition and eliminate it as early as possible.
- **Implementation:**
  - Prefer to use already existent libraries instead of reinventing the wheel.
  - Do not use `unwrap()`. Use safer alternatives.
  - Avoid subscripting or explicit slicing (e.g. `foo[3]`, `bar[0..2]`) and use `get`, `get_mut` instead.
  - Use `panic`s and `assert`s only for testing and invariant violations.
  - Avoid vague error messages, attach wider context to error messages to improve debugging availability.
  - Use `miette` (`eyreish` sub-module) for application errors.
  - Use `clap` for CLIs.
  - Use `rayon` for CPU-bound tasks that may benefit from parallelism.
  - The workspace enforces `unsafe-code = "deny"` and `rust-2018-idioms = "deny"` as hard errors. Key warnings that become blocking on CI: `indexing-slicing` (use `.get()`/`.get_mut()` with proper error handling instead of `[]`), `unused-results` (must handle or discard with `let _ =`).
- **Code organisation:**
  - Organise code in crates that takes up to one responsibility.
  - Every crate should consist of `lib.rs` -- facade module that exposes public API by re-exporting other module items. Keep inner modules as private as possible.
  - Design code using `newtype`s rather than type aliases.
- **Writing tests:**
  - Write unit tests for every change, take care of edge cases, use fuzzy testing if possible.
  - Write integration tests in `tests/` directory.
  - Write micro-benchmarks in `benches/` directory.
- **Documentation:** document every publicly exposed element of API with this format:

  ```rust
  /// Short description, up to two sentences: Does this and that.
  ///
  /// Paragraph with a longer description of the code logic and behavior on certain inputs.
  ///
  /// # Examples
  ///
  /// ```rust
  /// let foo: Foo = Foo::foo();
  /// assert!(foo.works_ok());
  /// ```
  ///
  /// # Panics
  /// Description when function panics for unexpected reason.
  ///
  /// # Errors
  /// Description when the function returns a business logic error.
  ///
  /// # Safety
  /// Safety invariants and how the function is safe to use, if marked `unsafe`.
  ```

  When documenting a publicly exposed module, write a simple description of what the module is doing and how to use code written there. Add examples of how to use the API inside the module.
- **Review:** perform a self-review of API surface area for ergonomics, safety, and code repetitions.
- **Validation:** run `make lint` for checking lints. Then run `make test` to test the code.
- **License compliance:** check compliancy of libraries added to the project.
  - Check whether `cargo deny` passes.
  - If not, check if the license of the library is compatible with `AGPL-3.0-or-later`.
  - If compatible, add the license to `deny.toml`.
  - If not compatible, look for compatible alternatives in `crates.io`.
  - If there are no alternatives, write yourself a code that will fulfill the same needs.
  - Update `NOTICE` file accordingly as the library added to the project.

### Staff-Level Responsibilities

- Focus on reducing complexity in `Cargo.toml`.
- Optimize for build times (parallel processing, reducing dependencies).
- Ensure high test coverage for edge cases (fuzz testing if necessary).
- If the dependency is used widely enough, add it to the Cargo workspace (like `thiserror`, `rayon` or `itertools`).

### Architecture

#### Crate Map

| Crate | Path | Role |
| --- | --- | --- |
| `xqvm` | `xqvm/` | Bytecode definitions, opcode table, instruction types, builder, codec, stream reader, VM interpreter, disassembler |
| `xqasm` | `xqasm/` | Text assembler: pest parser -> AST -> bytecode |
| `xqcli` | `xqcli/` | CLI binary (`xquad`): asm, dism, run, verify subcommands |
| `xqffi` | `xqffi/` | PyO3 bindings exposing xqasm + xqvm to Python |
| `xquad-conformance` | `conformance/` | Cross-implementation conformance harness |

#### Key Patterns

**X-Macro opcode table** (`xqvm/src/bytecode/types/table.rs`) -- The `opcodes!` macro is the single source of truth for all 93 instructions. The `Opcode` enum, `Instruction` enum, mnemonic strings, and operand arity are all derived from it. When adding or changing an opcode, edit only this table.

**Two-pass label resolution** (`xqvm/src/bytecode/builder.rs`) -- `InstructionBuilder` records unresolved jump fixups on the first pass and patches offsets at `build()` time, supporting both forward and backward label references.

**Binary codec** (`xqvm/src/bytecode/codec.rs`) -- Uses `oxicode` with BE fixint encoding: opcode byte followed by operand fields at their natural width in big-endian byte order (`i16` = 2 bytes, `[u8; N]` = N bytes, `u8`/`Register` = 1 byte). No varints, no length prefixes. `InstructionStream` (`stream.rs`) is an incremental seekable reader over encoded bytes. Mnemonic strings inside the bytecode crate use `pastey` for no-std compact string storage (avoids heap allocation for fixed-length identifiers).

**Assembly pipeline** (`xqasm/`) -- `pest` grammar -> `ast::Program` -> `assembler::assemble()` -> `InstructionBuilder` -> `codec::encode`. Rich `miette` diagnostics with source spans are emitted at the assembler stage.

**VM interpreter** (`xqvm/`) -- `Vm` executes a `Program` (raw instruction bytes) via an incremental `InstructionStream` reader. State: 256-slot register file (`RegVal` enum: `Int(i64)`, `VecInt(Vec<i64>)`, `VecXqmx(Vec<XqmxModel>)`, `Model(XqmxModel)`, `Sample(XqmxSample)`), an unbounded integer stack, and a loop stack of `LoopFrame` records (one per `RANGE`/`ITER`). `StepResult` drives control flow: `Continue`, `Jump(offset)`, `Halt`, `StartLoop`. Default step limit is 10,000,000 (configurable via `set_step_limit()`). A second budget bounds memory: every allocating opcode is charged bytes before it allocates, against a 1 GiB default (configurable via `set_memory_limit()`), and raises `MemoryLimitExceeded` when it cannot pay. Calldata and output slots are injected before `run()` via `set_calldata()` / `set_output_slots()`. VM errors carry `into_diagnostic(&program, source_name)` which disassembles the failing offset for miette source annotation. `clippy::result_large_err` is explicitly allowed in the asm crate because `NamedSource<Arc<str>>` on the error path is intentional.

#### Instruction Set Categories (93 total)

Control flow, stack/register I/O, arithmetic (including `SQR`, `ABS`, `INC`, `DEC`, `MIN`, `MAX`), comparison, logical/bitwise, QUBO/Ising/integer matrix allocators (`BQMX`, `SQMX`, `XQMX`), sample allocators, vector ops, index math, matrix coefficient access, grid ops, high-level constraints (`ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `EQUALITY`, `ATLEAST`, `ATLEASTW`, `REDUCE`), and `ENERGY`.

## Python

### Dependencies & Environment

- **Dependencies:** manage via each package's `pyproject.toml`. The repo-root `pyproject.toml` hosts the `uv` workspace declaration and dev-tool pins (maturin, pytest, pyyaml, ruff); the xqvm_py / xqcp / xqsa / xqffi members carry their own. Never modify the dev-dep pins without explicit user approval.
- **Virtual environment:** always use the workspace `.venv/` managed by `uv sync` / `uv run`. Never install packages globally or create ad-hoc venvs. Invoke scripts and tests via `uv run` so the maturin-built `xqffi` extension is picked up without a manual activation step.
- **Setup:** `make deps-py` runs `uv sync` + `maturin develop`. Each package's editable install carries `dev-mode-dirs = [".."]`, which puts the repo root on `sys.path`. Re-run after pulls that touch Rust sources or workspace deps.

### Package Map

| Package | Path | Role |
| --- | --- | --- |
| `xqvm_py` | `xqvm_py/` | Python reference VM implementation (conformance oracle) |
| `xqcp` | `xqcp/` | High-level constraint programming DSL compiling to XQVM assembly |
| `xqsa` | `xqsa/` | Solver adapters for XQMX models (dwave-samplers; pluggable solver interface) |
| `xqffi` | `xqffi/` | PyO3 FFI bindings (maturin-built); also a Rust crate |
| `xquad` | `xquad/` | Umbrella meta-package re-exporting xqffi, xqcp, xqsa under unified namespace |

### Testing

`make test-py` runs pytest across `xqvm_py/tests`, `xqcp/tests`, `xqsa/tests`, `xquad/tests`. Test paths are configured in the root `pyproject.toml` under `[tool.pytest.ini_options]`.

## Cross-Language

### Specifications

The `spec/` directory contains authoritative specifications for each toolchain component. Read the relevant spec before modifying that component:

- `spec/xqvm/` -- XQVM architecture (opcodes, control flow, type system, encoding)
- `spec/xqcp/README.md` -- XQCP constraint programming DSL
- `spec/xqsa/README.md` -- XQSA solver adapter interface

Spec changes are governed by the conformance harness: any modification affecting the opcode table, control-flow rules, stack depth, type system, or HLF expansions must be mirrored in `conformance/opcodes.yaml` and validated against `xqvm_py/opcodes.py` via `scripts/check-opcode-parity.py`.

### Conformance Vectors

Behavioural parity between `xqvm_py` (Python reference) and the Rust `xqvm` crate is enforced by the `xquad-conformance` test suite. Vectors live in `conformance/vectors/`. New semantics require a new vector; divergence between impls fails CI with no drift-tracking middle ground.

### Atomic Spec-MR Rule

Any MR that changes VM semantics must touch **all four** layers in the same MR: (1) `spec/xqvm/*.md`, (2) `xqvm/src/**/*.rs`, (3) `xqvm_py/{executor,opcodes,xqmx,state,vector,tracer,errors}.py`, (4) `conformance/vectors/**` or `conformance/opcodes.yaml`. CI enforces this via `verify:policy` (`scripts/check-atomic-spec-mr.sh`). MRs touching 0 or all 4 layers pass; partial changes (1-3 layers) fail.

For deliberately one-sided changes (e.g. aligning one impl to existing behaviour), add an `Atomic-Spec-Exempt: QUI-<id> <reason>` trailer to a commit message. It goes in the message's last paragraph at column 0, beside the sign-off, with the whole reason and the ticket on that one line; git reads trailers from the last paragraph only and truncates a wrapped reason, so the guard fails on either instead of bypassing. A `Fixes QUI-NNN` footer may share the paragraph but not the line directly below the trailer. The guard scans every commit in the MR range and bypasses when it finds at least one well-formed trailer. See `docs/guide/development-workflow.md` for the full rationale and exempt cases.

### Opcode Addition Gate

Adding a row to the `opcodes!` table in `xqvm/src/bytecode/types/table.rs` is a change to VM semantics. The MR that adds it argues in its description that the new opcode clears all six clauses of the on-chain admissibility bar: no floating point, no host I/O or ambient state, no nondeterministic iteration order, bounded allocation, bounded per-instruction work, and behaviour specified in `spec/xqvm/` with a conformance vector covering it. An opcode that cannot clear all six does not ship; the operation belongs in `xqcp`, `xqsa`, the `xquad` API or a helper library instead.

There is deliberately no CI guard for this one -- it is a correctness argument a reviewer weighs, not a string a script can find. `docs/guide/development-workflow.md` is required reading before changing VM semantics and carries the six clauses in full.

### Rust-Python Bindings (xqffi)

`xqvm_py` consumes `xqffi.asm` only -- its executor stays pure-Python so `xqvm_py` remains an independent conformance oracle. Build with `maturin develop --manifest-path xqffi/Cargo.toml` (handled by `make deps-py`).

### Examples & Smoke Tests

`examples/tsp/` (Travelling Salesman) and `examples/maxcut/` (Max-Cut) each consist of `.xqasm` programs driven by a Python runner (`runner.py`) that exercises both the Rust and Python interpreters via the `--interpreter` flag. These are the canonical references for how host code loads and runs `.xqasm` programs via the toolchain. `make example-smoke` runs both interpreters and checks each produces a valid solution (`valid == 1`); the check is invariant-based, not golden-file diffing.

### CI Pipeline

Five phases, each answering one question about the change. `verify` and
`test` read as synonyms, so the boundary is stated explicitly rather than
left to be inferred per job -- an unwritten boundary is exactly how the
old `lint` stage decayed into four unrelated concerns (source formatting,
Rust compilation, cross-implementation parity, release packaging) that
happened to share a stage barrier and nothing else:

- **`verify`** asks whether something matches what it is required to
  match -- a licence against policy, one VM implementation's output
  against the other's.
- **`test`** asks whether something does what it should when actually
  executed. Not a static-vs-dynamic split (`verify:parity` runs the VM);
  it is consistency between artefacts versus correctness of one artefact.

| Phase | Question it answers | What it covers |
| --- | --- | --- |
| `verify` | Does the workspace match what it's required to match? | clippy, rustdoc, cargo-deny, ruff, `uv.lock` freshness, the fresh-xqffi-cdylib check, opcode parity, Rust + Python conformance vectors, example smoke tests, atomic spec-MR guard, commit-message guard, merge-request-title guard, branch containment guard, changelog render |
| `test` | Does the workspace do what it should when executed? | unit, integration, doc tests (Rust); pytest (Python); Quip signing-layer tests; WASM no_std tests; Substrate pallet fixture |
| `hardware` | Does it work on real hardware? | CUDA, D-Wave QPU, and Metal solver tests on real hardware (protected refs only) |
| `docs` | Is the documentation correct and buildable? | generated-docs freshness, docs drift guard, package README length guard, mdbook build, GitLab Pages publish (release tags only) |
| `release` | Is the artefact publishable, and (on a tag) published? | `release:validate` packaging checks on every pipeline including tags; crates.io + PyPI publishing and GitLab Release notes via git-cliff on a pushed tag |

**Gating topology.** `verify:*` and `test:*` jobs all carry `needs: []`
and start together at t=0 -- peers, not a chain; a `verify` failure does
not hold `test` back. Every `hardware:*` job then `needs:` the full
verify+test set, so nothing touches real hardware before the code is
known to compile and pass its own test suite -- a GPU, a metered D-Wave
QPU token, and a macOS runner are all scarce and/or billed, and none of
them should be spent on code that does not even build. `docs:*` and
`release:validate` jobs in turn `needs:` verify+test+hardware, and the
tag-only publish chain (`release:crates` -> `release:pypi` ->
`release:notes`) is gated transitively through `release:validate`'s
`needs:` edge rather than through the stage barrier -- so the
documentation site and the release path both wait on hardware being
proven, not just on the workspace compiling. The accepted tradeoff: an
offline GPU runner or an expired D-Wave token stalls the documentation
site, even though nothing in the docs content depends on hardware
passing -- we would rather stall the docs than publish a book
describing an opcode or solver behaviour the hardware suite just
proved broken. On a tag the same gate now sits ahead of the publish
chain too, so the same expired token stalls a release and not just the
docs -- `test:substrate`'s cold-cache build is the largest single wait
on that path. The exact graph and per-edge reasoning (including why
`hardware:*` needs are marked `optional: true`) live in
`.gitlab/ci/setup.yml`'s "Dependency gating" section.

**Path gating.** Two jobs do not run on every pipeline. `test:wasm` and
`test:substrate` are gated on `rules: changes:`, because each builds one
crate (`xqvm`) into one fixture and so has a narrow, writable input
footprint, while every other job in the pipeline is a whole-workspace
check whose verdict a change anywhere can flip. They are the two most
expensive jobs in the pipeline and the least often relevant, so gating
them is most of the merge-request latency available to save. Both stay
unconditional on protected refs and on tags: the gate buys latency, not
coverage, and a path list is a claim about a build graph that can be
wrong -- keeping the protected refs unconditional means a wrong list
costs a late signal on `main` rather than a shipped regression. Because
either job can be absent, every `needs:` edge into them is
`optional: true`; GitLab refuses to create a pipeline whose job needs an
absent job. The path lists, the per-clause reasoning, and the per-entry
justification live in `.gitlab/ci/test.yml`'s "Path gating" section.
Local `make preflight-rs` runs both targets unconditionally.

**CI signals.** Two reds are expected, and each means a step of the release protocol is outstanding rather than that something is broken. Do not "fix" either by anything but the step it names:

- **`dev` red from `check-branch-containment`** (in `verify:policy`): `main` has moved since the last back-merge. Every push pipeline on `dev` stays red until someone back-merges `main` into `dev`. Merge requests into `dev` are not judged by it, so work continues in parallel.
- **`main` red from `check-version-sites`** (in `release:validate`): `main` carries a release version. This follows every release merge until the direct push reopening `main` at the next patch's `-dev` version lands.

A third is a gate, not a signal: a merge request from `release/*` fails `verify:policy` when the release branch does not contain `origin/main`. The fix is a back-merge of `main` into the release branch.

**Naming.** A job's name prefix is its phase (`verify:rust` runs in the
`verify` stage, `docs:build` in `docs`) -- that is the CI-side taxonomy.
The Makefile stays action-first (`<action>-<subject>`, e.g. `lint-rust`,
`build-docs`) -- that is the local-workflow taxonomy -- so most job names
do not map onto their target mechanically: `verify:rust` runs `make -k
lint-rust`, `docs:build` runs `make -k build-docs`. CI always invokes
these with `-k` so every prerequisite in a merged job is attempted even
after one fails; a plain local `make lint-rust` fails fast on the first,
like any other non-`-k` target.

Jobs are authored in per-stage files under `.gitlab/ci/` and composed via `include:` in the root `.gitlab-ci.yml`.

### Changelog

`CHANGELOG.md` is **not** committed to the source tree. The source of truth is `cliff.toml` plus the conventional-commit log; the file is regenerated on demand via `make changelog` and published as the GitLab Release description on tag (`release:notes` in `.gitlab/ci/release.yml`).

Caveats:

- Pre-conventional-commits history (everything before QUI-480) is filtered out by `filter_unconventional = true`; only commits on or after the QUI-480 enforcement appear in the rendered output. `make changelog` renders the whole unreleased history with no other scoping, so an empty render is the expected state until the first user-visible `feat`/`fix` lands.
- `chore`, `style`, `test`, `ci`, `build` are **dropped silently** -- if a commit under one of those types ships a user-visible change (e.g. a security-relevant dep bump under `chore`), promote it to `feat`/`fix`/`security` before merging or it will be invisible in release notes.
- `cliff.toml`'s `tag_pattern` scopes each release's notes to that release alone: it is three numeric fields and nothing else, so a prerelease tag (`-rcN`, `-betaN`) never becomes a range boundary and its commits fold into the following release's section instead of getting a page of their own. `changelog-release` (the Makefile target `release:notes` invokes) pairs this with an explicit `PREV..VERSION` range, where `PREV` is the nearest release predecessor tag (`git describe --exclude='*-*'`, the same pattern in glob form), rather than an unbounded `--tag`. `release:notes` and `docs:publish` match the same pattern, so a prerelease publishes to both registries with no Release page and no documentation. `make check-release-notes` (`scripts/check-release-notes.sh`, run as part of `lint-policy` / `verify:policy`) is the regression guard: it renders every release tag's range plus the pre-tag preview and asserts each yields exactly one `## [` heading. Mirror the pattern wherever a tag is classified; a denylist of prerelease spellings is what let `-beta` through before.
- Before tagging a release, run `make changelog-release VERSION=vX.Y.Z` locally to preview what the GitLab Release page will say -- the render contains exactly one section. Bad commit subjects can be fixed on the source branch and re-merged before the tag is cut.

## Local overrides

Personal, repository-specific configuration goes in `AGENTS.local.md` (gitignored). It is
imported below and silently skipped for collaborators who do not have one.

@AGENTS.local.md
