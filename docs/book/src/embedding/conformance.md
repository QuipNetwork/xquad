# Conformance

`spec/xqvm/` says what an XQVM program must do, and the Rust `xqvm` crate
this book documents implements it. The specification vectors --
[`xqvm/tests/vectors/`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm/tests/vectors) -- are the mechanical
check that the crate does what the spec says. This page explains what
their result means for you: whether you are embedding `xqvm` or deciding
how much to trust it.

## What a Vector Is

A conformance vector is a directory holding a fixed test case: a canonical
`.xqasm` program, the calldata it runs with, and the outputs and residual
stack it is expected to produce. The runner assembles the program, runs
it on the VM, and asserts the observed result matches the recorded
expectation.

Each vector directory holds three files:

- **`program.xqasm`** -- the canonical, human-readable source. The
  assembler is authoritative: whatever `xqasm::assemble_source()` produces
  from this file *is* the canonical bytecode, assembled in-process on
  every run. No pre-assembled bytecode artifact is committed.
- **`inputs.json`** -- calldata, output slot count and the two budgets the
  run is given, for example `{"calldata": [6, 7], "output_slots": 16}`.
  Every key but `calldata` is optional: `output_slots` defaults to 16,
  `step_limit` to 10000000 and `memory_limit` to 1073741824, each matching
  `xquad run`. The runner resolves the defaults itself, so a budget is a
  property of the vector rather than of whichever default the runner
  carries. Set one only for a vector that is about that budget.
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
`xqvm/tests/vectors/`: `arithmetic`, `constraints`, `control-flow`,
`energy`, `index-math`, `metering`, `vector-ops`, and `xqmx-grid`. These
names are not the same vocabulary as
[`opcodes.yaml`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/opcodes.yaml)'s
own `category` field, which has 15 values and matches `spec/xqvm/SPEC.md`'s
section names, not the vector directories. Only five names happen to
coincide (`arithmetic`, `control-flow`, `index-math`, `vector-ops`,
`xqmx-grid`);
`constraints`, `energy` and `metering` have no matching YAML category at
all -- the constraint opcodes are catalogued under `xqmx-high-level`, and so
is `ENERGY` itself, while a metering vector is about a budget rather than
about any one opcode. Don't expect a vector directory to line up with an
`opcodes.yaml` category by name.

## Running the Vectors Locally

```sh
# Every vector
cargo test -p xqvm --test vectors

# One category
cargo test -p xqvm --test vectors -- arithmetic

# One vector
cargo test -p xqvm --test vectors -- --exact vector::arithmetic::add_basic
```

The runner assembles each program with `xqasm`, which the published `xqvm`
tarball does not carry, so the vectors run from a checkout of the
repository rather than from the crate downloaded from crates.io.

## What Passing Means

Every vector runs as its own named test in the `xqvm` crate's `vectors`
test target, which CI runs with the rest of the Rust integration tests. A
passing vector means: for that specific program and that specific
calldata, the VM produces every output value, the final stack and the step
count the vector records. It says nothing about programs the vector does
not cover.

Two more guarantees apply alongside the vectors, each checked separately:

- **The opcode table itself** -- a build-time assertion ties the Rust
  `opcodes!` macro to `opcodes.yaml`, so the VM cannot silently diverge from
  the published table on which opcodes exist or how many operands they
  take.
- **Bytecode encoding** -- owned by the `xqasm` crate's own test suite.
  The vectors trust the assembler to produce correct bytecode from
  `.xqasm` source; they do not re-check the encoding independently, though
  each vector does run a second time after an encode/decode round trip and
  must produce the same result.

For what the VM actually does with a program, see the [XQVM
Reference](../xqvm/).

## Coverage Is Per-Opcode, and Incomplete

A vector exists only where someone wrote one, and `xqvm/tests/vectors/`
holds far more vectors than it covers distinct opcodes: `bitlen_small`
and `bitlen_negative` both cover `BITLEN`; three separate `slack_*`
vectors all cover `SLACK`; the `xqmx-grid` directory alone spends
seventeen vectors on six opcodes. Where a vector is missing, the suite
makes no claim about that opcode at all. Passing CI does not mean every
opcode has been checked against the spec -- only that every opcode a
vector currently exercises has been.

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
[`xqvm/tests/vector_suite/coverage.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/tests/vector_suite/coverage.rs)
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

Treat a green vector run as evidence for the programs it actually tests,
not as a blanket guarantee that the VM matches the spec on every opcode in
every configuration.
