# xquad development workflow

This document describes how changes move from authoring to `main` and
`dev` in the xquad repository. The central idea -- carried over from the
xq-rs <-> xq-py merge (QUI-412) -- is that two implementations of the
same VM must stay in lockstep. Everything else in the workflow is in
service of that invariant.

## Who maintains what

One repository, two implementations, one spec:

| Component | Owner(s) | Role |
|-----------|----------|------|
| [`spec/xqvm/SPEC.md`](../../spec/xqvm/SPEC.md) | all | Normative description of VM behaviour. Every conformance vector derives from here. |
| [`xqvm/`](../../xqvm/) | Rust track | Production interpreter. `no_std + alloc`. Used by the Substrate pallet, the `xquad` CLI, and the `xqffi` pyo3 extension. |
| [`xqvm_py/`](../../xqvm_py/) | Python track | Reference interpreter. Pure Python. The conformance oracle. |
| [`xqvm/tests/vectors/`](../../xqvm/tests/vectors/) | shared | Specification vectors. Every committed vector runs on the Rust VM in CI; a mismatch fails the build. |
| [`xqffi/`](../../xqffi/) | Rust track | PyO3 FFI layer. Rust crate compiled via maturin to a Python wheel -- exposes `xqvm` and `xqasm` to the Python side. Not a pure-Python package. |
| [`xqcp/`](../../xqcp/), [`xqsa/`](../../xqsa/), [`xquad/`](../../xquad/) | Python track | Python surface: DSL, solver adapters, and umbrella. Consume the VMs (via `xqffi`) rather than define their semantics. |

## Branches

Two long-lived branches. Before 1.0, `main` is the non-breaking line
and `dev` is the breaking one; which a change targets is decided by
whether it breaks, not by whether it is a fix.
[`gitflow-protocol.md`](gitflow-protocol.md) is the normative
description -- routing, releases, betas and the back-merge -- and what
follows is only the naming.

- **`main`** -- the non-breaking line, at the next patch's `-dev`
  version. Protected: everything lands by merge request except the
  version bump that reopens it after a release, which the code owners push
  directly and CI holds to a version-only change. A merge lands as a
  real merge commit with a GitLab-generated `merge: branch '<source>'
  into 'main'` subject. Merges are not squashed.
- **`dev`** -- the breaking line, at the next minor's `-dev` version.
  Protected; Maintainers may push to it, for back-merges and version
  bumps only. It contains `main` at all times, and CI marks `dev` red
  while a back-merge is outstanding.
- **`feature/qui-<id>[-tag]`** -- short-lived branches for individual
  tickets, cut from and merged into whichever line the change belongs
  on. One branch may carry several tickets when they touch the same
  files. The MR body lists each ticket it consumes (QUI-*), so Linear
  status moves in lockstep with git state.
- **`release/v<version>`** -- prepares a release: cut from `main` for a
  patch or from `dev` for a minor, carries the version bump, and merges
  into `main` unsquashed. `release:auto-tag` cuts the tag from the
  merge.
- **`chore/<tag>`** -- repository maintenance belonging to no ticket.

## The atomic spec-MR rule

**Any MR that changes VM semantics must touch all four of the
following in the same MR:**

1. `spec/xqvm/*.md` -- any normative spec file: the change documented.
   `SPEC.md`, `ISA.md`, `HLF.md`, `ENCODING.md` and `VERIFIER.md` all
   satisfy this layer; the guard matches the directory, not one file.
2. `xqvm/src/**/*.rs` -- the Rust production impl updated.
3. `xqvm_py/{executor,opcodes,xqmx,state,vector,tracer,errors}.py` --
   the Python reference impl updated.
4. `xqvm/tests/vectors/**` or `xqvm/opcodes.yaml` -- a new
   or modified vector that exercises the change.

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

A CI guard -- [`scripts/check-atomic-spec-mr.sh`](../../scripts/check-atomic-spec-mr.sh)
-- runs as part of `verify:policy` on every merge request. It classifies
changed files into the four layers and fails the pipeline if an MR
touches **1-3 layers but not all four**. Touching **0 layers** (pure
docs / CI / tooling MRs) or **all 4** passes.

Run locally the same way CI does:

```sh
scripts/check-atomic-spec-mr.sh origin/main HEAD
```

### Exemptions

Some legitimate changes only touch one or two layers -- the guard
would flag them as drift even though they're alignment fixes. The
exempt cases:

- **One-sided alignment fix.** One impl already matches the spec;
  the other impl is brought in line without changing the canonical
  behaviour. Example: QUI-453 changed only `xqvm_py/xqmx.py` to pre-
  populate a spin sample with `-1` per position, matching what Rust
  had always done -- no Rust change, no opcode table change.
- **Spec clarification.** The spec text gains precision without
  changing the normative rules. No impl updates needed.
- **Conformance-only coverage addition.** A new vector exercises
  existing semantics. No spec / impl changes.
- **Cross-implementation alignment with no normative change.** Both
  impls move, and sometimes the harness with them, to converge on
  behaviour the spec already states -- so the spec layer has nothing to
  add and the guard's four-layer test cannot be satisfied honestly.
  This is the case the v0.4.0 series used most: QUI-1147's step-budget
  export touched `xqvm/src/` and `xqvm_py/` together, and QUI-1032's
  opcode-signature check touched those two plus `conformance/`, neither
  of them changing what the spec says.

**To take an exemption, add a commit-message trailer of the form
`Atomic-Spec-Exempt: QUI-<id> <reason>`.** It goes in the message's last
paragraph, at column 0, beside the sign-off:

```
Align Python SSMX default to Rust's [-1, -1, ...] initialisation.

Python was shipping empty-dict samples where Rust used vec![-1; size];
this aligns the Python side. No Rust change needed.

Fixes QUI-453
Atomic-Spec-Exempt: QUI-453 one-sided Python fix, no semantics change
Signed-off-by: You <you@example.com>
```

The layout is not stylistic. git reads trailers out of the **last
paragraph only**, and a paragraph break above the trailer hides it from
`git log --format='%(trailers)'`, from GitLab's commit view, and from
everything else built on git's trailer parsing. Every exempt trailer
written before QUI-1030 sat in a paragraph of its own above the ticket
footer, so not one of them parses -- which is how eleven exemptions were
taken without any of them being findable.

Two more rules follow from how git treats that paragraph:

- **The whole reason goes on the trailer line.** A continuation line is
  not a trailer, and git either drops it -- silently truncating the
  reason to its first line -- or, with no sign-off in the paragraph,
  stops reading the paragraph altogether.
- **A `Fixes QUI-NNN` footer may share the paragraph, but not the line
  directly below the trailer.** It has no colon, so it is not a trailer
  either; the mandatory sign-off is what makes git tolerate it. Directly
  below the trailer it is indistinguishable from a wrapped reason, so the
  guard rejects that position rather than guess. Put it above the
  trailer, or below the sign-off.

A trailer that breaks any of this fails the guard rather than being
ignored by it, because an exemption nobody can find is worse than no
exemption at all. Column 0 is git's rule too, which is why an indented
example in a commit body -- like the ones in this repository's own
documentation commits -- is not mistaken for a real exemption.

The guard scans every commit message in the MR range and bypasses when
it finds at least one well-formed trailer. Reviewers should see the
exemption and confirm the rationale holds; there is no approval process
beyond review.

## What does NOT trigger the rule

- **Consumer-side surface work** -- the PyO3 bindings under
  `xqffi/src/*.rs` (Rust, but consumer glue -- they expose the VM to
  Python, they don't define its semantics), plus `xquad/program.py`
  and the `xquad` / `xqcp` / `xqsa` Python packages. Changes here
  don't need spec or conformance updates.
- **Build glue and tooling** -- `xqvm/build.rs`, `xqvm/Cargo.toml`,
  `xqvm_py/pyproject.toml`, `Makefile`, CI config, scripts. Not
  semantic.
- **Docs** -- everything under `docs/`, READMEs, CHANGELOG. Not
  semantic.
- **Tests** -- `xqvm/src/**/tests.rs`, `xqvm/tests/*.rs`,
  `xqvm/tests/vector_suite/**`, `xqvm_py/tests/**`. Tests exercise
  semantics but don't define them; the *vectors* (under
  `xqvm/tests/vectors/`) are the authoritative check and that's what
  the guard watches.

## The opcode-addition gate

**Adding a row to the `opcodes!` table in
`xqvm/src/bytecode/types/table.rs` is a change to VM semantics, and the
merge request that adds it argues in its description that the new opcode
clears all six clauses of the on-chain admissibility bar:**

1. **No floating point.** Integer arithmetic only, with every operation
   range-checked so that it faults rather than wrapping.
2. **No host I/O, wall-clock, or ambient state.** An instruction's
   result is a function of the program, the calldata and the VM's own
   state. Stated normatively in `spec/xqvm/SPEC.md` under Determinism.
3. **No nondeterministic iteration order.** Anything that walks a
   collection walks it in an order the spec fixes.
4. **Bounded allocation.** Every allocation is charged against a budget
   before it happens, and the budget is not escapable by a value the
   submitting account controls.
5. **Bounded per-instruction work.** The work one instruction performs
   is charged against the step budget before it happens, at a rate that
   scales with the data the program controls, so that a step is a unit
   of cost rather than a unit of dispatch.
6. **Specified behaviour, pinned by a vector.** The result and every
   fault the opcode can raise are specified normatively in
   `spec/xqvm/`, and a conformance vector covers the behaviour, failure
   paths included.

An opcode that cannot clear all six does not ship. The operation belongs
in `xqcp`, `xqsa`, the `xquad` API or a helper library, where it is
ordinary code rather than something every embedder has to trust.

### Why

The bar is a property of the instruction set rather than a decision each
embedder makes, and that is only true while every row on the table has
been held to it. The denied set is empty by construction, so an embedder
has nothing to gate -- but "by construction" names a construction
somebody has to carry out. Each of the six clauses was argued for every
opcode shipped so far, and none of those arguments was recorded where
the next proposal's reviewer would find it. An unwritten rule holds
nothing.

Clause 2 is the one most easily lost, because no test can fail it. An
opcode that read a clock would pass every conformance vector on the
machine that ran them, and split two hosts replaying the same program.
Clauses 4 and 5 are the ones most often half-satisfied: a charge levied
after the work rather than before it, or one that scales with something
other than the data the caller controls, reads as bounded until somebody
submits the worst case.

### Enforcement

There is none, deliberately. Every comparable process rule here ships a
`scripts/check-*.sh` guard that `verify:policy` runs -- the atomic
spec-MR rule, the commit-message grammar, the merge-request title, the
release notes. This one does not, and the absence is a decision rather
than an oversight.

The reason is what the gate asks for. A guard can prove that a trailer
exists, or that a file was touched. It cannot prove that the argument
the trailer claims was made is sound, and soundness is the whole of what
a reviewer is weighing here. Wiring in a grep would convert a
correctness argument into a compliance ritual, and the ritual is the
half that can be satisfied without doing the work.

So the gate lives in review. A reviewer who cannot find the six-clause
argument in the merge request description asks for it before approving,
the same way they would ask for a test.

## Commit and review conventions

See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) for:

- Sign-off (DCO) requirements.
- Commit message format.
- Review turnaround expectations.
- AGPL license header requirements on new files.
