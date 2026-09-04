# XQVM Conformance Harness

Mechanical cross-implementation check that the Rust production VM
([`xqvm`](../xqvm/)) and the Python reference VM
([`xqvm_py`](../xqvm_py/)) agree on every observable behaviour defined
by [`spec/xqvm/SPEC.md`](../spec/xqvm/SPEC.md).

The harness is a Rust test crate (`xquad-conformance`). Each vector
becomes two `#[test]` functions -- one per runtime -- generated at
`cargo build` time by [`build.rs`](build.rs). CI runs both through the
`verify:parity` job's `make -k check-parity`, which keeps running
every target after one fails so a Python-side regression cannot mask a
Rust-side pass (or vice versa).

## Layout

```
conformance/
├── opcodes.yaml          single machine-readable source for the 93-opcode table
├── vectors/
│   ├── arithmetic/<name>/
│   │   ├── program.xqasm       canonical assembly source
│   │   ├── inputs.json         {"calldata": [i64, ...], "output_slots": N}
│   │   └── expected.json       {"outputs": [...], "final_stack": [...]} or {"error": FAULT}
│   ├── control-flow/<name>/
│   ├── energy/<name>/
│   ├── constraints/<name>/
│   ├── vector-ops/<name>/
│   └── xqmx-grid/<name>/
├── src/                  Rust library + manual CLI binary
├── tests/rust.rs         generated in-process Rust runner tests
└── tests/python.rs       generated subprocess Python runner tests
```

## File format

### `program.xqasm`

Canonical human-readable source. The assembler is authoritative: whatever
`xqasm::assemble_source()` produces becomes the canonical bytecode.
Prefer explicit `PUSH1`/`PUSH2`/... forms when the constant width matters
for the vector; use the `PUSH` sugar where it doesn't.

The harness assembles `program.xqasm` in-process on every run; encoding
correctness is owned by the `xqasm` crate's own test suite, so no
pre-assembled bytecode artifact is committed.

### `inputs.json`

```json
{
  "calldata": [6, 7],
  "output_slots": 16,
  "step_limit": 50
}
```

`output_slots` is optional (defaults to 16, matching `xquad run`). The
`calldata` array is exposed to the program in slot order: slot 0 holds
the first value, slot 1 the second, and so on. `step_limit` is optional
and defaults to 10000000, matching `xquad run`; the harness resolves the
default itself and passes it to both runners, because the two
implementations disagree on what an omitted budget means and a vector
that left it out used to run bounded on Rust and unbounded on Python.
Set it only for a vector that is specifically exercising the budget.

### `expected.json`

A vector asserts **either** an outcome **or** a fault. Supplying both, or
neither, is rejected as a vector-authoring mistake rather than silently
preferring one half.

```json
{
  "outputs": [42],
  "final_stack": []
}
```

`outputs` is a sparse map, per the state model in `spec/xqvm/SPEC.md` --
each entry is either an `i64` (the value written by `OUTPUT`) or `null`
(the slot was reserved but explicitly zeroed by a peer entry). Trailing
unset slots are omitted entirely, so a program that writes slot 0 out of
16 reserved slots produces `[42]`, not `[42, null, ...]`.
Explicitly-written zeroes are preserved; only slots that `OUTPUT` never
touched disappear. `final_stack` is the residual stack at `HALT` (bottom
to top); an empty stack is typical.

A vector that expects the program to fault writes the fault's identity
instead:

```json
{
  "error": "DIVISION_BY_ZERO"
}
```

### Fault identities

Neither implementation's spelling can serve as the identity: Rust raises
enum variants, Python raises exception classes, and the two vocabularies
do not line up one-to-one. `expected.json` therefore names a third,
implementation-neutral vocabulary that both sides map onto.

Byte position is deliberately **not** part of the identity. Only the
fault itself is compared, so vectors do not turn brittle against
unrelated codegen changes. Position assertions can be added later if a
bug ever motivates them.

| `expected.json` | Rust `xqvm::Error` | Python `xqvm_py.errors` |
| --- | --- | --- |
| `STACK_UNDERFLOW` | `StackUnderflow` | `StackUnderflow` |
| `STACK_OVERFLOW` | `StackOverflow` | `StackOverflow` |
| `TYPE_MISMATCH` | `RegisterType`, `IncompatibleType` | `TypeMismatch` |
| `UNSET_REGISTER` | `UnsetRegister` | `RegisterNotFound` |
| `DIVISION_BY_ZERO` | `DivisionByZero` | `DivisionByZero` |
| `ARITHMETIC_OVERFLOW` | `ArithmeticOverflow` | `ArithmeticOverflow` |
| `INDEX_OUT_OF_BOUNDS` | `IndexOutOfBounds` | `IndexOutOfBounds` |
| `NO_ACTIVE_LOOP` | `NoActiveLoop` | -- (see below) |
| `UNMATCHED_LOOP` | `UnmatchedLoop` | -- (see below) |
| `BAD_JUMP_TARGET` | `BadJumpTarget` | `TargetNotFound` |
| `INVALID_LABEL` | `InvalidLabel` | -- |
| `BAD_OPCODE` | `BadOpcode` | `InvalidOpcode` |
| `TRUNCATED_INSTRUCTION` | `TruncatedInstruction` | `TruncatedInstruction` |
| `CALL_DATA_INDEX` | `CallDataIndex` | -- |
| `OUTPUT_INDEX` | `OutputIndex` | `OutputIndex` |
| `SIZE_MISMATCH` | `SizeMismatch` | `SizeMismatch` |
| `VEC_LENGTH_MISMATCH` | `VecLengthMismatch` | `VecLengthMismatch` |
| `STEP_LIMIT_EXCEEDED` | `StepLimitExceeded` | `StepLimitExceeded` |
| `MEMORY_LIMIT_EXCEEDED` | `MemoryLimitExceeded` | `MemoryLimitExceeded` |
| `INVALID_SHIFT` | `InvalidShift` | `InvalidShift` |
| `INVALID_GRID_DIMENSIONS` | `InvalidGridDimensions` | `InvalidGridDimensions` |
| `INVALID_DISCRETE_K` | `InvalidDiscreteK` | `InvalidDiscreteK` |
| `XQMX_MODE` | -- | `XQMXModeError` |
| `TRACE_FAILED` | `TraceFailed` | -- |
| `INVALID_ALLOCATION` | `InvalidAllocation` | `InvalidAllocation` |
| `LOOP_STACK_OVERFLOW` | `LoopStackOverflow` | `LoopStackOverflow` |

The Rust mapping is an exhaustive `match`, so adding a variant to
`xqvm::Error` without extending this table fails to compile rather than
silently degrading to an "unknown" fault. The Python mapping is by class
name and is necessarily partial: a `--` above means that implementation
has no distinct class for the fault yet, and a vector asserting it will
fail loudly on the Python runner until one exists.

`LoopError` is the notable gap: Python raises the same class for both
`NO_ACTIVE_LOOP` and `UNMATCHED_LOOP`, so it is left unmapped rather than
resolved arbitrarily to one of them. Splitting it is a prerequisite for
any loop-fault vector.

### Fault ordering (QUI-1178)

`spec/xqvm/SPEC.md` fixes the order of work within one instruction: pops,
then the allocation charge, then validation. Where a register read once
preceded the pops in `xqvm_py`, the divergence was reachable two ways --
a short stack (which the verifier rejects) and an operand fault raised
ahead of the register read (which it admits). Only the second is
consensus-visible.

Of the 22 opcodes reordered, the second route reaches:

| Opcode(s) | Fault raised ahead of the register read | Pinned by |
|---|---|---|
| `RESIZE` | `InvalidGridDimensions`, unconditional | `vectors/xqmx-grid/resize_type_after_dimension_check` |
| `VECPUSH`, `SETLINE`, `ADDLINE`, `SETQUAD`, `ADDQUAD`, `EXCLUDE`, `IMPLIES`, `REDUCE` | `MemoryLimitExceeded`, only against a near-exhausted budget | `xqvm_py/tests/test_executor.py` -- `Inputs` carries no `memory_limit`, so a vector cannot express these |
| `ATLEAST` | `IndexOutOfBounds` on `k`, unconditional | `vectors/constraints/atleast_k_range_before_model_type` |
| `ATLEASTW` | `VecLengthMismatch`, unconditional | `vectors/constraints/atleastw_length_before_model_type` |
| `EQUALITY` | `VecLengthMismatch`, unconditional -- the vec lengths are compared before the model register is discriminated, and the charge follows both vec reads | `vectors/constraints/equality_length_mismatch_beats_model_type` |
| `VECGET`, `VECSET`, `GETLINE`, `GETQUAD`, `ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM` | none -- Rust type-checks immediately after its pops | short stack only, not verifier-clean |
| `ONEHOTR`, `ONEHOTC` | none, since QUI-1202 -- the charge is sized from a model-only peek on both VMs, so a sample sizes it at zero | `xqvm_py/tests/test_executor.py` -- `test_onehot_does_not_charge_for_a_sample`; `Inputs` carries no `memory_limit`, so a vector cannot express it |

The `ONEHOTR`/`ONEHOTC` row read "none" before QUI-1202 as well, on a
justification that did not hold. `exec_one_hot_r` sizes its charge from a
peek that yields `cols` only for `RegVal::Model` and `0` for everything
else, then resolves with `as_model_mut`; neither Python runner peeked at
all -- both went pop, `_get_register_as_xqmx` (which accepts a sample),
dimensions, charge -- so a sample's real extent sized the charge and a
tight budget raised `MemoryLimitExceeded` where the Rust VM charged
nothing. `_peek_model_grid` closes it: the peek is `is_model()`-gated on
both VMs now, and the budget can no longer decide the fault.

That mattered because the register reaches `ONEHOTR` as a sample without
the verifier objecting. `check_reads` requires `R::Model` there, and a
register the verifier knows is a `Sample` is rejected statically -- but it
does not always know. Two routes get past it:

- **Host calldata.** `Vm::set_calldata` takes any `RegVal`, samples
  included ("This allows passing models, samples, and vectors between
  programs"), and `INPUT` clones the entry into the register. `INPUT`
  writes `RegType::Any`, which satisfies every requirement, so
  `PUSH 0 / INPUT r0 / PUSH 0 / PUSH 1 / ONEHOTR r0` is verifier-clean and
  arrives holding whatever the embedder supplied. The `--calldata` CLI flag
  parses i64s and cannot express this; the embedding API is the reachable
  surface, and on a chain runtime it is the host that fills those slots.
- **A branch join.** `meet_reg` merges `Model` and `Sample` to
  `RegType::Any` (`xqvm/src/dataflow/register.rs:95`), so a program that
  writes `BQMX` on one branch and `BSMX` on the other, joins, and calls
  `ONEHOTR` verifies clean using nothing but its own instructions.

What survives is the `XqmxMode` carve-out, which is not this table's
business: `xqvm_py` rejects the sample with `XQMXModeError` and the Rust
VM with `RegisterType`. `spec/xqvm/SPEC.md:211` records that row as the
one unresolved entry in the fault table and says neither identity is safe
to write a vector against until it is settled, which is why the test above
pins the charge rather than the name.

## Authoring a new vector

1. Write `program.xqasm` with a minimal scenario that exercises the
   behaviour you care about.
2. Pick the matching vector directory under `vectors/` (or create a new
   one). These directory names are a separate, coarser vocabulary from
   `opcodes.yaml`'s own `category` field -- the two do not need to match,
   and mostly don't.
3. Write `inputs.json` with the calldata the program reads.
4. Produce `expected.json` by running one of the two impls and
   inspecting the result. The harness then asserts the *other* impl
   agrees. If they disagree, you've found spec drift -- file a bug
   ticket, and either fix the divergent impl or exclude the vector
   with a comment referencing the ticket.

## Drift policy

There is no `DRIFT.md`. Either every vector passes on both runtimes or
the build is broken. Concrete enforcement:

- **Opcode table** -- `xqvm/build.rs` asserts `opcodes.yaml` against the
  `opcodes!` x-macro at compile time; `scripts/check-opcode-parity.py`
  asserts `opcodes.yaml` against `xqvm_py/opcodes.py` in CI. The Rust
  check compares the wire byte, the mnemonic, the net stack effect and
  each operand's name and encoded byte width. It compares the net effect
  rather than the `stack_pop`/`stack_push` pair, because the x-macro
  stores only the net delta; the pair is compared on the Python side.
- **Bytecode encoding** -- owned by the `xqasm` crate's own test suite
  (`xqasm/tests/integration.rs` plus assembler unit tests).
- **Observable behaviour** -- [`check_vector`](src/lib.rs) asserts the
  observed `{outputs, final_stack}`, or the observed fault identity,
  matches `expected.json` for both runtimes.

## Running locally

```sh
# Full matrix (both runtimes, all vectors)
cargo test -p xquad-conformance

# Rust only
cargo test -p xquad-conformance --no-default-features --features rust

# Python only (needs python3 + xqvm_py importable from xqvm_py/)
cargo test -p xquad-conformance --no-default-features --features python

# Manual CLI for authoring / triaging a single vector
cargo run -p xquad-conformance -- --filter arithmetic/add_basic --impl both
```

Override the Python interpreter with `XQUAD_CONFORMANCE_PYTHON=...`.
