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
- **`inputs.json`** -- calldata, output slot count and the two budgets the
  run is given, for example `{"calldata": [6, 7], "output_slots": 16}`.
  Every key but `calldata` is optional: `output_slots` defaults to 16,
  `step_limit` to 10000000 and `memory_limit` to 1073741824, each matching
  `xquad run`. The harness resolves the defaults itself and passes them to
  both VMs, so a budget is a property of the vector rather than of whichever
  default each runner carries. Set one only for a vector that is about that
  budget.
- **`expected.json`** -- the recorded result, for example
  `{"outputs": [42], "final_stack": [], "steps": 11}`. `outputs` is a
  sparse map: each entry is an `i64` written by `OUTPUT`, or `null` for a
  slot that was reserved but never written; trailing unset slots are
  omitted entirely, so a program that writes slot 0 of 16 reserved slots
  produces `[42]`, not `[42, null, ...]`. Explicitly-written zeroes are
  preserved -- only slots `OUTPUT` never touched disappear. `final_stack`
  is the residual stack at `HALT`, bottom to top. `steps` is the run's
  metered cost, per `spec/xqvm/METERING.md`, and every successful vector
  must assert it: it is the quantity the chain prices execution by. A
  vector that expects a fault writes `{"error": "DIVISION_BY_ZERO"}`
  instead, with neither `steps` nor `final_stack`.

Vectors are grouped, for human navigation, into eight directories under
`conformance/vectors/`: `arithmetic`, `constraints`, `control-flow`,
`energy`, `index-math`, `metering`, `vector-ops`, and `xqmx-grid`. These
names are not the same vocabulary as
[`opcodes.yaml`](https://gitlab.com/quip.network/xquad/-/blob/main/conformance/opcodes.yaml)'s
own `category` field, which has 15 values and matches `spec/xqvm/SPEC.md`'s
section names, not the vector directories. Only five names happen to
coincide (`arithmetic`, `control-flow`, `index-math`, `vector-ops`,
`xqmx-grid`);
`constraints`, `energy` and `metering` have no matching YAML category at
all -- the constraint opcodes are catalogued under `xqmx-high-level`, and so
is `ENERGY` itself, while a metering vector is about a budget rather than
about any one opcode. Don't expect a vector directory to line up with an
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

A vector exists only where someone wrote one, and `conformance/vectors/`
holds far more vectors than it covers distinct opcodes: `bitlen_small`
and `bitlen_negative` both cover `BITLEN`; three separate `slack_*`
vectors all cover `SLACK`; the `xqmx-grid` directory alone spends
seventeen vectors on six opcodes. Where a vector is missing, the harness
makes no claim about that opcode at all. Passing CI does not mean every
opcode has been checked for cross-implementation agreement -- only that
every opcode a vector currently exercises has been.

Which opcodes those are is now computed rather than guessed. CI prints
the report on every pipeline, as the last step of `make check-parity`,
and it runs locally with:

```sh
make conformance-coverage
```

The report gives two numbers, because one is not enough. **Present**
counts opcodes appearing in some vector's assembled program; **reached**
counts those a vector executes to completion. Reached is the stronger
measure and always the smaller one: an opcode behind an untaken branch is
present but not reached, and so is the instruction an error vector exists
to make fault -- a faulting instruction never completes a step. Reporting
only "reached" would call `DIV` uncovered despite
`arithmetic/div_by_zero`; reporting only "present" would credit an opcode
sitting in dead code.

Both numbers sit below the size of the opcode table, and the report names
the shortfall rather than only counting it. Run `make conformance-coverage`
for the current figures; this page does not repeat them, because they move
whenever a vector lands. The opcodes in no vector at all are the real
holes. Those present but never reached are a weaker signal worth knowing:
each has an error vector pinning how it fails, and no vector pinning what
it does when it succeeds.

Coverage reports; it does not gate on completeness, which would fail
today. It does gate on regression --
[`conformance/tests/coverage.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/conformance/tests/coverage.rs)
holds both numbers as floors that a merge request may raise and may not
lower without saying why.

`IDXTRIU` is the worked example of what that costs. It had no vector, and
the two implementations disagreed on it in two separate ways: on operand
order, and on whether an intermediate that leaves `i64` range faults. Both
were found by reading the implementations side by side rather than by any
mechanical check, and both are now closed, with
`index-math/idxtriu_intermediate_overflow` and
`index-math/idxgrid_intermediate_overflow` pinning the second. Neither
gap was exotic; both were simply in the part of the opcode table nothing
had written a vector for -- which is the hole the coverage report exists
to make visible before someone has to find it by reading.

Treat a green conformance run as evidence for the programs it actually
tests, not as a blanket guarantee that the two implementations agree on
every opcode in every configuration.
