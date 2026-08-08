# xquad development workflow

This document describes how changes move from authoring to `main` in
the xquad repository. The central idea — carried over from the
xq-rs ↔ xq-py merge (QUI-412) — is that two implementations of the
same VM must stay in lockstep. Everything else in the workflow is in
service of that invariant.

## Who maintains what

One repository, two implementations, one spec:

| Component | Owner(s) | Role |
|-----------|----------|------|
| [`spec/xqvm/SPEC.md`](../spec/xqvm/SPEC.md) | all | Normative description of VM behaviour. Every conformance vector derives from here. |
| [`xqvm/`](../xqvm/) | Rust track | Production interpreter. `no_std + alloc`. Used by the Substrate pallet, the `xquad` CLI, and the `xqffi` pyo3 extension. |
| [`xqvm_py/`](../xqvm_py/) | Python track | Reference interpreter. Pure Python. The conformance oracle. |
| [`conformance/`](../conformance/) | shared | Cross-impl parity harness. Every committed vector runs on both VMs in CI; disagreement fails the build. |
| [`xqffi/`](../xqffi/) | Rust track | PyO3 FFI layer. Rust crate compiled via maturin to a Python wheel — exposes `xqvm` and `xqasm` to the Python side. Not a pure-Python package. |
| [`xqcp/`](../xqcp/), [`xqsa/`](../xqsa/), [`xquad/`](../xquad/) | Python track | Python surface: DSL, solver adapters, and umbrella. Consume the VMs (via `xqffi`) rather than define their semantics. |

## Branches

- **`main`** — released state. Protected. Squash-merge from MRs only.
- **`experimentation`** — long-lived integration branch where
  exploratory changes accumulate. No protection beyond basic CI.
  Periodic promotion to `main` via atomic spec-MRs (see below).
- **`feature/qui-<id>[-tag]`** — short-lived branches for individual
  tickets. Merged directly into `main` when the change is scoped
  small enough to fit an atomic spec-MR, or into `experimentation`
  when it's part of a larger coordinated effort.

## The atomic spec-MR rule

**Any MR that changes VM semantics must touch all four of the
following in the same MR:**

1. `spec/xqvm/SPEC.md` — the change documented.
2. `xqvm/src/**/*.rs` — the Rust production impl updated.
3. `xqvm_py/{executor,opcodes,xqmx,state,vector,tracer,errors}.py` —
   the Python reference impl updated.
4. `conformance/vectors/**` or `conformance/opcodes.yaml` — a new
   or modified conformance vector that exercises the change.

### Why

- **No drift-tracking middle ground.** Before the merge, drift
  between `xq-rs` and `xq-py` was tracked as a running list. Ten
  drift points accumulated before we stopped accepting it and merged
  the repos (QUI-412). The atomic rule makes divergence impossible by
  construction: you can't commit a spec change without updating both
  impls in the same diff.
- **Reviewable as one story.** A reviewer sees the spec delta next
  to the two impl deltas next to the test that proves they agree.
  The mental model is contained in one MR.
- **CI coverage that scales.** The conformance harness runs every
  vector against both runtimes. Adding a vector at the same time as
  the semantics change means the harness is as up-to-date as the
  spec on the day of the merge.

### Enforcement

A CI guard — [`scripts/check-atomic-spec-mr.sh`](../scripts/check-atomic-spec-mr.sh)
— runs as `lint:atomic-spec-mr` on every merge request. It classifies
changed files into the four layers and fails the pipeline if an MR
touches **1-3 layers but not all four**. Touching **0 layers** (pure
docs / CI / tooling MRs) or **all 4** passes.

Run locally the same way CI does:

```sh
scripts/check-atomic-spec-mr.sh origin/main HEAD
```

### Exemptions

Some legitimate changes only touch one or two layers — the guard
would flag them as drift even though they're alignment fixes. The
exempt cases:

- **One-sided alignment fix.** One impl already matches the spec;
  the other impl is brought in line without changing the canonical
  behaviour. Example: QUI-453 changed only `xqvm_py/xqmx.py` to pre-
  populate a spin sample with `-1` per position, matching what Rust
  had always done — no Rust change, no opcode table change.
- **Spec clarification.** The spec text gains precision without
  changing the normative rules. No impl updates needed.
- **Conformance-only coverage addition.** A new vector exercises
  existing semantics. No spec / impl changes.

**To take an exemption, add a commit-message trailer of the form
`Atomic-Spec-Exempt: <reason>` on its own line.** The trailer format
mirrors `Signed-off-by:` / `Co-authored-by:` — unambiguous to the
guard, visible to reviewers, and impossible to trigger accidentally
from prose that happens to mention the token. Example:

```
Align Python SSMX default to Rust's [-1, -1, …] initialisation.

Python was shipping empty-dict samples where Rust used vec![-1; size];
this aligns the Python side. No Rust change needed.

Atomic-Spec-Exempt: one-sided Python fix bringing impl in line with
  existing Rust behaviour — no semantics change, no Rust diff, no
  new vector.
```

The guard scans every commit message in the MR range and bypasses
when it finds at least one trailer. Reviewers should see the
exemption and confirm the rationale holds; there is no approval
process beyond review.

## Promotion from experimentation to main

When the experimentation branch accumulates work that forms a
coherent change:

1. Open an MR from `experimentation` to `main`.
2. The MR must either respect the atomic spec-MR rule directly or
   carry an `Atomic-Spec-Exempt:` trailer with justification.
3. The MR body explicitly lists each ticket it consumes (QUI-*), so
   Linear status moves in lockstep with git state.
4. Squash-merge on green CI. `experimentation` then rebases onto
   the new `main` to stay current.

Avoid letting `experimentation` diverge by more than ~2 weeks from
`main`; promotion churn scales non-linearly with the size of the
delta.

## What does NOT trigger the rule

- **Consumer-side surface work** — the PyO3 bindings under
  `xqffi/src/*.rs` (Rust, but consumer glue — they expose the VM to
  Python, they don't define its semantics), plus `xquad/program.py`
  and the `xquad` / `xqcp` / `xqsa` Python packages. Changes here
  don't need spec or conformance updates.
- **Build glue and tooling** — `xqvm/build.rs`, `xqvm/Cargo.toml`,
  `xqvm_py/pyproject.toml`, `Makefile`, CI config, scripts. Not
  semantic.
- **Docs** — everything under `docs/`, READMEs, CHANGELOG. Not
  semantic.
- **Tests** — `xqvm/src/**/tests.rs`, `xqvm_py/tests/**`,
  `conformance/tests/**`. Tests exercise semantics but don't define
  them; the conformance *vectors* (under `conformance/vectors/`) are
  the authoritative cross-impl check and that's what the guard
  watches.

## Commit and review conventions

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for:

- Sign-off (DCO) requirements.
- Commit message format.
- Review turnaround expectations.
- AGPL license header requirements on new files.
