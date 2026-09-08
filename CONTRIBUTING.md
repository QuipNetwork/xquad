<!--
Copyright (C) 2026 Postquant Labs Incorporated
SPDX-License-Identifier: AGPL-3.0-or-later
-->
# Contributing to XQuad

Thank you for your interest in contributing. This document covers the development workflow and requirements for getting changes merged.

## Conduct

Be respectful in all project spaces, including issues, merge requests, and code review.

## Prerequisites

- Rust (stable, latest recommended)
- Dev tools -- install everything in one step:

```sh
make deps
```

This installs: `clippy`, `rustfmt`, `taplo-cli`, `cargo-deny`, `cargo-nextest`.

- Miri interpreter -- optional, but highly recommended; see [Undefined Behaviour](#undefined-behaviour):

```sh
make deps-miri
```

- [Vale](https://vale.sh) -- the prose linter behind `make check-docs-prose`,
  and so a prerequisite of `make preflight-docs` and `make preflight`.
  `make deps` does not install it: it is a Go binary, not a cargo or uv tool.
  Pinned by `VALE_VERSION` in the Makefile; CI fetches that exact release.

```sh
brew install vale          # macOS
# or download the pinned release from https://github.com/errata-ai/vale/releases
```

- Python 3.13+ and [uv](https://docs.astral.sh/uv/) -- install the Python workspace in one step:

```sh
make deps-py
```

This runs `uv sync` plus `maturin develop`, giving you editable installs of
`xqvm_py`, `xqcp`, `xqsa`, `xqffi`, and `xquad` with the `xqffi` cdylib built
from the current Rust sources.

`uv sync` resolves the base dependencies only, so the optional solver backends
are absent from a fresh workspace. Local GPU or QPU work needs the matching
extra synced on top -- `uv sync --extra cuda`, `--extra metal`, `--extra dwave`
or `--extra quip`.

## Dependencies and Lockfiles

The workspace carries two lockfiles. Both are enforced, so a lock that has
drifted from its manifests fails a check rather than being silently
re-resolved:

- **`Cargo.lock`** -- every dependency-resolving invocation carries
  `--locked`, so cargo fails if the lockfile would have to change instead of
  quietly resolving a different dependency set. That covers the wrappers as
  well as `cargo` itself: `cargo deny --locked check`, `maturin develop
  --locked` (including the one in `make deps-py`, so a drifted lock fails the
  bootstrap rather than being resolved around), the two `maturin build
  --locked` calls in `scripts/python-dists.sh` that produce the wheels
  `release:pypi` uploads, and `wasm-pack test`, which forwards trailing
  options through to its `cargo build`. `cargo fmt` is excluded because it
  resolves nothing, as is `cargo miri setup`, which prepares a toolchain
  rather than building the workspace.

  One resolving invocation is covered by ordering instead of by a flag:
  `maturin sdist`, at the end of `scripts/python-dists.sh`, re-resolves (it
  shells out to `cargo metadata`, which rewrites a stale lock in place) but
  has no `--locked` option. The script is `set -euo pipefail` and the two
  `maturin build --locked` calls run first, so a stale lock fails there and
  the run never reaches the sdist. Do not reorder those three calls.

  `fixtures/pallet-xqvm` is a standalone workspace with its own lock; it is
  covered too, by `make test-substrate-fixture`'s `cargo test --locked`.
- **`uv.lock`** -- `make check-uv-lock` runs `uv lock --check`, a read-only
  resolver pass that fails when `uv.lock` is stale against any
  `pyproject.toml`, rather than the `uv sync` behaviour of quietly rewriting
  it. It runs in `make preflight-py` and in CI's `verify:python`.

If either check fails, regenerate the lock (`cargo check`, `uv lock`) and
commit the result with the change that caused it.

Tool-version pins live in the Makefile rather than in CI configuration.
`RUFF_VERSION` and `UV_VERSION` are each read out of the Makefile with `sed`,
by `.githooks/pre-commit` and by `.gitlab/ci/setup.yml` respectively, rather
than carrying a second copy of the pin. The two bind differently, though.
`RUFF_VERSION` is installed by both sides -- local dev gets it through `uvx
ruff@<pin>` in `make lint-py` / `make fmt-check-py` and in the pre-commit
hook -- so local and CI run the same ruff. `UV_VERSION` is installed by CI
only: nothing can install uv through uv, so your `uv` is whatever is on your
PATH. `make check-uv-lock` warns (it does not fail) when that differs from
the pin, because a resolver difference between two uv versions otherwise
shows up as `uv.lock` looking stale locally while CI is green, or the
reverse. `PYYAML_VERSION` pins the isolated `uv run`
environment the documentation generator uses and is consumed only within the
Makefile. Cargo-installed tool versions (`taplo`, `cargo-deny`,
`cargo-nextest`, `mdbook`, `git-cliff`, `wasm-pack`, `cargo-zigbuild`) are
pinned separately in `scripts/cargo-tools.lock`, read directly by
`scripts/install-cargo-tools.sh` and by CI's tool cache key.

## Development Workflow

All checks must pass before a merge request is accepted. `make preflight`
runs the full local mirror of what CI enforces, grouped by phase so a
single-language MR can run just its half:

```sh
make preflight          # everything below, in one shot
make preflight-rs       # fmt, taplo, clippy, rustdoc, deny, unit/integration/doc tests
make preflight-py       # taplo, ruff format + lint, pytest, uv.lock freshness
make preflight-parity   # opcode parity, conformance, example smoke
make preflight-docs     # generated-doc freshness, docs drift, README length, prose (needs vale)
make preflight-policy   # changelog render, release-notes scoping, atomic spec-MR and commit-message guards
```

`make preflight-release` (crate packaging dry-run plus the five Python
distributions) needs `maturin`, `twine`, and `uv` on `PATH`, so it is kept out
of plain `make preflight`; see [RELEASING.md](RELEASING.md).

`make all` (`fmt` + `lint` + `test`) is a convenience for reformatting the
tree and running everything locally, but it is not what CI runs -- CI invokes
the `preflight-*` targets above, one per pipeline phase. Match those before
pushing.

Miri is not part of `preflight-rs`; run it separately with `make test-miri`
before submitting changes that touch `unsafe` code, dependencies, or
procedural macros -- see [Undefined Behaviour](#undefined-behaviour) below.

For the individual leaf targets behind each `preflight-*` aggregate
(`fmt-rs`, `lint-clippy`, `test-unit-rs`, and so on), see the
Quick-Reference Commands in `AGENTS.md` rather than a second list here that
can drift from it.

## Documentation Layout

Contributor-facing guides live under `docs/guide/`. Design notes and AI-workflow artifacts that are useful to retain in git live under `docs/design/`.

New local or generated workflow artifacts should stay under the gitignored `docs/superpowers/` tree unless they are intentionally promoted.

## Commits

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
- Separate subject from body with a blank line
- Body: wrap at 72 characters; explain *what* and *why*, not *how*
- Footer: reference the tracking issue (e.g. `Fixes #123`) to auto-link and close it on merge
- Breaking changes: append `!` after type/scope (e.g. `feat(xqvm)!: remove deprecated API`) or add a `BREAKING CHANGE:` footer
- Each commit is a logical cohesive change, which should pass tests and lints

A `commit-msg` hook validates the format automatically. Install hooks with:

```sh
git config core.hooksPath .githooks
```

Example:

```
fix(xqasm): handle forward label references in nested loops

The two-pass label resolver was not accounting for label offsets
inside nested RANGE blocks, causing incorrect jump targets when
a forward reference crossed a loop boundary.

Fixes #456
```

## Semver Compliance

Public API changes must be semver-compatible. Breaking changes require a major version bump.

## Undefined Behaviour

Miri is not required to pass before merging, but running it locally is highly
recommended before submitting changes that touch `unsafe` code, dependencies,
or procedural macros. It detects undefined behaviour and unsound code that the
compiler and standard tests cannot catch.

Run Miri locally on nightly:

```sh
make deps-miri   # one-time setup
make test-miri
```

## Code Style

- All public items must be documented (`missing-docs` is enforced).
- Follow standard Rust naming conventions (`nonstandard-style = "deny"`).
- Run `make fmt` before committing -- formatting is checked in CI.

## Licensing

By submitting a contribution, you agree that your work will be licensed under [AGPL-3.0-or-later](https://www.gnu.org/licenses/agpl-3.0.html), the same license as this project.

Every new source file must include the AGPL license header at the top:

```rust
// Copyright (C) <year> Postquant Labs Incorporated
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.
//
// SPDX-License-Identifier: AGPL-3.0-or-later
```

## Contributor License Agreement

By submitting a contribution to this project, you:

1. Certify that you wrote the contribution or have the right to submit it under the AGPL-3.0 license
2. Agree that your contribution will be licensed under AGPL-3.0-or-later
3. Grant a patent license as specified in the AGPL-3.0 license
4. Acknowledge that your contribution is public and may be redistributed under the AGPL-3.0 license

## Sign-off Procedure

Add a Signed-off-by line to your commit messages:

```sh
git commit -s -m "Your commit message"
```

This adds:
```
Signed-off-by: Your Name <your.email@example.com>
```

## AI Assistants

`AGENTS.md` is the canonical, committed source of project conventions for AI
coding assistants and human contributors. It is the only agent-config file
tracked in git; keep it up to date when project conventions change.

Tools that read `AGENTS.md` natively (e.g. OpenAI Codex, Cursor) pick it up on
clone. Tools that do not (e.g. Claude Code) opt in locally with a gitignored
entry-point file whose first line imports it: for Claude Code, add a gitignored
`CLAUDE.md` (or `CLAUDE.local.md`) whose first line is `@AGENTS.md`.

Personal, repository-specific configuration that is not a repo-wide convention
goes in a gitignored `AGENTS.local.md`, imported at the end of `AGENTS.md` and
silently skipped for contributors who do not have one.

## Merge Requests

- Keep changes focused and minimal.
- Reference any related issues in the MR description.
- Ensure all CI pipeline stages pass. Use the checklist in the template.
- The MR **title** must follow Conventional Commits, exactly as a commit
  subject does -- squash-on-merge makes the title the subject of the
  squash commit that lands on `main`. CI enforces this in `verify:policy`
  (`scripts/check-mr-title.sh`, which reuses the same grammar as the
  `commit-msg` hook). GitLab's three draft prefixes
  (`[Draft]`, `Draft:`, `(Draft)`) are stripped before the check, so a
  draft MR is not failed for being a draft. Nothing else is stripped --
  `WIP:` has not been a draft marker since GitLab 14.0 and is judged as
  ordinary title text.

  GitLab starts pipelines on push, not on title edits, so a title changed
  after your last push is not rechecked. Retitle before pushing rather
  than after. A title that slips through that way is caught after the
  merge instead: `verify:policy` on `main` finds an empty commit range
  and checks what the push landed there, which for a merge is the
  squash commit carrying the title
  (`scripts/check-commit-messages.sh`, "Landed mode"). That is
  detection only -- the subject is on `main` by then, and a subject
  that failed the grammar has already been dropped from the release
  notes.

  To try a title before pushing:
  `bash scripts/check-mr-title.sh 'feat(xqvm): add an opcode'`

### Atomic Spec-MR Rule

Any MR that changes VM semantics must touch **all four** of these layers in the same MR:

1. `spec/xqvm/SPEC.md` -- the normative specification
2. `xqvm/src/**/*.rs` -- the Rust production implementation
3. `xqvm_py/{executor,opcodes,xqmx,state,vector,tracer,errors}.py` -- the Python reference implementation
4. `conformance/vectors/**` or `conformance/opcodes.yaml` -- cross-impl parity coverage

CI enforces this via `verify:policy` (`scripts/check-atomic-spec-mr.sh`). MRs touching 0 or all 4 layers pass; partial changes fail.

**Exemptions:** For deliberately one-sided changes (e.g. aligning one impl to existing behaviour), add an `Atomic-Spec-Exempt:` trailer to a commit message. It goes in the message's last paragraph at column 0, beside the sign-off, with the whole reason and the ticket on that one line. git reads trailers out of the last paragraph only, and a wrapped reason is silently truncated, so the guard rejects either rather than bypassing on a trailer nobody can read:

```
Fixes QUI-453
Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change
Signed-off-by: You <you@example.com>
```

A bare-word `Fixes QUI-NNN` footer may share that paragraph, as above, but not the line directly below the trailer -- there it cannot be told apart from a wrapped reason.

See [docs/guide/development-workflow.md](docs/guide/development-workflow.md) for the full rationale and exempt cases.

### Opcode Addition Gate

Adding a row to the `opcodes!` table in `xqvm/src/bytecode/types/table.rs` is a change to VM semantics. The MR that adds it argues in its description that the new opcode clears all six clauses of the on-chain admissibility bar: no floating point, no host I/O or ambient state, no nondeterministic iteration order, bounded allocation, bounded per-instruction work, and behaviour specified in `spec/xqvm/` with a conformance vector covering it. An opcode that cannot clear all six does not ship; the operation belongs in `xqcp`, `xqsa`, the `xquad` API or a helper library instead.

There is deliberately no CI guard for this one. The gate asks for a correctness argument a reviewer weighs, not a string a script can find.

Reading [docs/guide/development-workflow.md](docs/guide/development-workflow.md) is required before changing VM semantics, not optional background: it carries the six clauses in full and the reasoning behind both this gate and the atomic spec-MR rule above.

---

**License**: This document is licensed under AGPL-3.0-or-later
**Copyright**: (C) 2026 Postquant Labs Incorporated
