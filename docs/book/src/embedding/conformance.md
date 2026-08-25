# Conformance

XQuad ships two independent VM implementations: the Rust `xqvm` crate this
book otherwise documents, and a pure-Python reference VM, `xqvm_py`. A
program compiled once to XQVM bytecode is meant to produce the same result
on either. The conformance harness --
[`xquad-conformance`](https://gitlab.com/quip.network/xquad/-/tree/main/conformance)
-- is the mechanical check of that claim. This page explains what its
result means for you: whether you are embedding `xqvm`, writing against
`xqvm_py`, or deciding how much to trust either one.

## What a Vector Is

A conformance vector is a directory holding a fixed test case: a canonical
`.xqasm` program, the calldata it runs with, and the outputs and residual
stack it is expected to produce. The harness assembles the program, runs
it on both VMs, and asserts each one's observed result matches the
recorded expectation.

Each vector directory holds three files:

- **`program.xqasm`** -- the canonical, human-readable source. The
  assembler is authoritative: whatever `xqasm::assemble_source()` produces
  from this file *is* the canonical bytecode, assembled in-process on
  every run. No pre-assembled bytecode artifact is committed.
- **`inputs.json`** -- calldata and output slot count, for example
  `{"calldata": [6, 7], "output_slots": 16}`. `output_slots` is optional
  and defaults to 16, matching `xquad run`.
- **`expected.json`** -- the recorded result, for example
  `{"outputs": [42], "final_stack": []}`. `outputs` is a sparse map: each
  entry is an `i64` written by `OUTPUT`, or `null` for a slot that was
  reserved but never written; trailing unset slots are omitted entirely,
  so a program that writes slot 0 of 16 reserved slots produces `[42]`,
  not `[42, null, ...]`. Explicitly-written zeroes are preserved -- only
  slots `OUTPUT` never touched disappear. `final_stack` is the residual
  stack at `HALT`, bottom to top.

Vectors are grouped, for human navigation, into six directories under
`conformance/vectors/`: `arithmetic`, `constraints`, `control-flow`,
`energy`, `vector-ops`, and `xqmx-grid`. These names are not the same
vocabulary as
[`opcodes.yaml`](https://gitlab.com/quip.network/xquad/-/blob/main/conformance/opcodes.yaml)'s
own `category` field, which has 14 values and matches `spec/xqvm/SPEC.md`'s
section names, not the vector directories. Only four names happen to
coincide (`arithmetic`, `control-flow`, `vector-ops`, `xqmx-grid`);
`constraints` and `energy` have no matching YAML category at all -- the
constraint opcodes are catalogued under `xqmx-high-level`, and so is
`ENERGY` itself. Don't expect a vector directory to line up with an
`opcodes.yaml` category by name.

## Running the Harness Locally

```sh
# Full matrix: both runtimes, every vector
cargo test -p xquad-conformance

# Rust only
cargo test -p xquad-conformance --no-default-features --features rust

# Python only (needs uv and a synced workspace venv)
cargo test -p xquad-conformance --no-default-features --features python

# Triage a single vector on both runtimes
cargo run -p xquad-conformance -- --filter arithmetic/add_basic --impl both
```

The Python runs shell out through `uv run python -m xqvm_py`, so the
workspace venv is picked up without a manual activation step.
`XQUAD_CONFORMANCE_PYTHON` overrides the `uv` wrapper command, not the
interpreter behind it.

## What Passing Means

Every vector runs as two separate tests, one per runtime, generated at build
time so a regression in one implementation cannot be masked by the other
passing. CI runs those two test suites within the `verify:parity` job's
`make -k check-parity`, which keeps running every target after one fails so a
Rust-side failure cannot suppress a Python-side one (or vice versa). A vector
that passes on both VMs means: for that specific program and that specific
calldata, both implementations agree on every output value and the final
stack. It says nothing about programs the vector does not cover.

Two more guarantees apply alongside vector agreement, each checked
separately:

- **The opcode table itself** -- a build-time assertion ties the Rust
  `opcodes!` macro to `opcodes.yaml`, and a CI script ties `opcodes.yaml` to
  `xqvm_py`'s own opcode table, so the two implementations cannot silently
  diverge on which opcodes exist or how many operands they take.
- **Bytecode encoding** -- owned by the `xqasm` crate's own test suite, not
  by this harness. Conformance vectors trust the assembler to produce
  correct bytecode from `.xqasm` source; they do not re-check the encoding
  independently.

For the architectural reason two implementations exist at all --
independent verification of a solver's answer, not just parity between
runtimes -- see [Three Programs](../concepts/three-programs.md). For what
each VM actually does with a program, see the [XQVM
Reference](../xqvm/).

## Coverage Is Per-Opcode, and Incomplete

A vector exists only where someone wrote one. Nothing tracks coverage:
there is no script, manifest, or report that computes which opcodes have
a vector and which don't. `conformance/vectors/` currently holds 87
vectors across seven directories, against 93 opcodes, and many vectors
exercise the same opcode -- `bitlen_small` and `bitlen_negative` both
cover `BITLEN`; three separate `slack_*` vectors all cover `SLACK`; the
`xqmx-grid` directory alone spends seventeen vectors on six opcodes --
so the number of distinct opcodes actually checked is well under 87.
Where a vector is missing, the harness makes no claim about that opcode
at all. Passing CI does not mean every opcode has been checked for
cross-implementation agreement -- only that every opcode a vector
currently exercises has been.

`IDXTRIU` is the worked example of what that costs. It had no vector, and
the two implementations disagreed on it in two separate ways: on operand
order, and on whether an intermediate that leaves `i64` range faults. Both
were found by reading the implementations side by side rather than by any
mechanical check, and both are now closed, with
`index-math/idxtriu_intermediate_overflow` and
`index-math/idxgrid_intermediate_overflow` pinning the second. Neither
gap was exotic; both were simply in the part of the opcode table nothing
had written a vector for.

Treat a green conformance run as evidence for the programs it actually
tests, not as a blanket guarantee that the two implementations agree on
every opcode in every configuration.
