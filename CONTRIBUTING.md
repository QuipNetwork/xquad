<!--
Copyright (C) 2026 Postquant Labs Incorporated
SPDX-License-Identifier: AGPL-3.0-or-later
-->
# Contributing to Aglais XQVM

Thank you for your interest in contributing. This document covers the development workflow and requirements for getting changes merged.

## Conduct

Be respectful in all project spaces, including issues, merge requests, and code review.

## Prerequisites

- Rust (stable, latest recommended)
- Dev tools — install everything in one step:

```sh
make deps
```

This installs: `clippy`, `rustfmt`, `taplo-cli`, `cargo-deny`, `cargo-nextest`.

- Miri interpreter — optional, but highly recommended; see [Undefined Behaviour](#undefined-behaviour):

```sh
make deps-miri
```

## Development Workflow

All checks must pass before a merge request is accepted. Run them locally before pushing:

```sh
make all          # lint + test (what CI runs)
```

Individual targets:

```sh
# Formatting
make fmt              # apply all formatting (Rust + TOML)
make fmt-rs           # cargo fmt --all
make fmt-toml         # taplo fmt

make fmt-check        # check formatting without modifying files
make fmt-check-rs
make fmt-check-toml

# Lints
make lint             # all lints + format check
make lint-clippy      # cargo clippy --workspace --all-targets --all-features -- -D warnings
make lint-doc         # RUSTDOCFLAGS="-D warnings" cargo doc
make lint-deny-rs     # cargo deny check

# Tests
make test             # unit + integration
make test-unit-rs     # cargo nextest --lib
make test-integ-rs    # cargo nextest --test '*'
make test-miri        # cargo +nightly miri test (requires make deps-miri)
```

## Commits

All commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <subject>

[optional body]

[optional footer(s)]
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`

**Scope** is optional. When used, it should be the crate or package name (e.g. `xqvm`, `xqcp`, `conformance`).

**Rules:**

- Subject line: imperative mood, lowercase start, no trailing period, max 72 characters
- Separate subject from body with a blank line
- Body: wrap at 72 characters; explain *what* and *why*, not *how*
- Footer: `Fixes QUI-NNN` or `Implements QUI-NNN` to link Linear tickets
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

Fixes QUI-456
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
- Run `make fmt` before committing — formatting is checked in CI.

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

The repository ships configuration for AI coding assistants so they understand
the project's conventions out of the box.

| File | Tool |
|---|---|
| `AGENTS.md` | Claude Code, OpenAI Codex, Cursor (primary source of truth) |

`AGENTS.md` is the canonical source of project conventions for AI assistants.
When updating project conventions, keep this file up to date.

## Merge Requests

- Keep changes focused and minimal.
- Reference any related issues in the MR description.
- Ensure all CI pipeline stages pass. Use the checklist in the template.

### Atomic Spec-MR Rule

Any MR that changes VM semantics must touch **all four** of these layers in the same MR:

1. `spec/xqvm/SPEC.md` — the normative specification
2. `xqvm/src/**/*.rs` — the Rust production implementation
3. `xqvm_py/{executor,opcodes,xqmx,state,vector,tracer,errors}.py` — the Python reference implementation
4. `conformance/vectors/**` or `conformance/opcodes.yaml` — cross-impl parity coverage

CI enforces this via `lint:atomic-spec-mr` (`scripts/check-atomic-spec-mr.sh`). MRs touching 0 or all 4 layers pass; partial changes fail.

**Exemptions:** For deliberately one-sided changes (e.g. aligning one impl to existing behaviour), add a `Atomic-Spec-Exempt: <reason>` trailer to a commit message:

```
Atomic-Spec-Exempt: Python-only fix bringing impl in line with existing Rust behaviour
```

See [docs/xquad-development-workflow.md](docs/xquad-development-workflow.md) for the full rationale and exempt cases.

---

**License**: This document is licensed under AGPL-3.0-or-later
**Copyright**: (C) 2026 Postquant Labs Incorporated
