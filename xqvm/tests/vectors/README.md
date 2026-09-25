# XQVM Specification Vectors

Each directory under here is one small program, with its inputs and the
result `spec/xqvm/` requires of it. The `vectors` test target
(`xqvm/tests/vector_suite/` in the source tree) runs every vector on the
VM and compares the result, so `cargo test -p xqvm` checks the VM against
the spec one vector at a time.

The vectors are data files rather than Rust tests so they stay reviewable
as spec artefacts and replayable by other harnesses, such as the chain's
metering check. They ship in the crate tarball; the runner does not,
because it assembles each `program.xqasm` with `xqasm`, a path-only
dev-dependency the tarball drops.

## Layout

```
xqvm/
├── opcodes.yaml              machine-readable source for the 93-opcode table
└── tests/
    ├── vector_suite/         the runner, the file format and the coverage ratchet
    └── vectors/
        ├── arithmetic/<name>/
        │   ├── program.xqasm       assembly source
        │   ├── inputs.json         {"calldata": [i64, ...], "output_slots": N}
        │   └── expected.json       {"outputs": [...], "final_stack": [...], "steps": N} or {"error": FAULT}
        ├── constraints/<name>/
        ├── control-flow/<name>/
        ├── energy/<name>/
        ├── index-math/<name>/
        ├── metering/<name>/
        ├── vector-ops/<name>/
        └── xqmx-grid/<name>/
```

A directory missing any of the three files is skipped rather than
reported, so a half-authored vector does not run.

## File format

### `program.xqasm`

Human-readable source, assembled in-process on every run. Prefer explicit
`PUSH1`/`PUSH2`/... forms when the constant width matters for the vector;
use the `PUSH` sugar where it does not.

The runner executes the program twice, as assembled and after an
encode/decode round trip through the bytecode format, and fails the vector
if the two runs disagree.

### `inputs.json`

```json
{
  "calldata": [6, 7],
  "output_slots": 16,
  "step_limit": 50,
  "memory_limit": 1048576
}
```

The `calldata` array is exposed to the program in slot order: slot 0 holds
the first value, slot 1 the second, and so on. `null` is a slot the host
fixed but left unset: in range for `INPUT`, and copied into the register as
unset.

`output_slots` is optional and defaults to 16, matching `xquad run`.
`step_limit` is optional and defaults to `xqvm::DEFAULT_STEP_LIMIT`
(10000000); set it only for a vector that exercises the step budget.
`memory_limit` is optional and defaults to `xqvm::DEFAULT_MEMORY_LIMIT`
(1073741824); it is what makes the allocation-charge half of the
fault-ordering rule below assertable, since those faults are only
reachable against a budget the charge exhausts.

### `expected.json`

A vector asserts **either** an outcome **or** a fault. Supplying both, or
neither, is rejected as a vector-authoring mistake rather than silently
preferring one half.

```json
{
  "outputs": [42],
  "final_stack": [],
  "steps": 11
}
```

`outputs` is a sparse map, per the state model in `spec/xqvm/SPEC.md`:
each entry is either an `i64` (the value written by `OUTPUT`) or `null`
(the slot was reserved but never written, and a later slot was). Trailing
unset slots are omitted, so a program that writes slot 0 out of 16
reserved slots produces `[42]`, not `[42, null, ...]`. `final_stack` is
the residual stack at `HALT`, bottom to top.

`steps` is required on every successful vector: the exact metered cost of
the run, per `spec/xqvm/METERING.md`. It is the quantity the chain prices
execution by, so a vector that pinned the result without the cost would
let a metering change through unnoticed. A successful vector without
`steps`, or a fault vector with `steps` or `final_stack`, is rejected:
the runner compares neither for a faulting run, so the value would go
unchecked.

A vector that expects the program to fault writes the fault's identity
instead:

```json
{
  "error": "DIVISION_BY_ZERO"
}
```

### Fault identities

`expected.json` names faults in its own vocabulary rather than by
`xqvm::Error` variant. Byte position is deliberately not part of the
identity, so vectors do not turn brittle against unrelated codegen
changes, and two variants that describe the same fault share one name.

| `expected.json` | `xqvm::Error` |
| --- | --- |
| `STACK_UNDERFLOW` | `StackUnderflow` |
| `STACK_OVERFLOW` | `StackOverflow` |
| `TYPE_MISMATCH` | `RegisterType`, `IncompatibleType` |
| `UNSET_REGISTER` | `UnsetRegister` |
| `DIVISION_BY_ZERO` | `DivisionByZero` |
| `ARITHMETIC_OVERFLOW` | `ArithmeticOverflow` |
| `INDEX_OUT_OF_BOUNDS` | `IndexOutOfBounds` |
| `NO_ACTIVE_LOOP` | `NoActiveLoop` |
| `UNMATCHED_LOOP` | `UnmatchedLoop` |
| `BAD_JUMP_TARGET` | `BadJumpTarget` |
| `INVALID_LABEL` | `InvalidLabel` |
| `BAD_OPCODE` | `BadOpcode` |
| `TRUNCATED_INSTRUCTION` | `TruncatedInstruction` |
| `CALL_DATA_INDEX` | `CallDataIndex` |
| `OUTPUT_INDEX` | `OutputIndex` |
| `SIZE_MISMATCH` | `SizeMismatch` |
| `VEC_LENGTH_MISMATCH` | `VecLengthMismatch` |
| `STEP_LIMIT_EXCEEDED` | `StepLimitExceeded` |
| `MEMORY_LIMIT_EXCEEDED` | `MemoryLimitExceeded` |
| `INVALID_SHIFT` | `InvalidShift` |
| `INVALID_GRID_DIMENSIONS` | `InvalidGridDimensions` |
| `INVALID_INTEGER_K` | `InvalidIntegerK` |
| `SAMPLE_OUT_OF_DOMAIN` | `SampleOutOfDomain` |
| `TRACE_FAILED` | `TraceFailed` |
| `INVALID_ALLOCATION` | `InvalidAllocation` |
| `LOOP_STACK_OVERFLOW` | `LoopStackOverflow` |

The mapping is an exhaustive `match`, so adding a variant to `xqvm::Error`
without extending this table fails to compile rather than silently
degrading to an "unknown" fault.

### Fault ordering (QUI-1178)

`spec/xqvm/SPEC.md` fixes the order of work within one instruction: pops,
then the allocation charge, then validation. A fault that the order raises
ahead of a register read or an index check is consensus-visible, and these
vectors pin it:

| Opcode(s) | Fault raised ahead of the register read | Pinned by |
|---|---|---|
| `RESIZE` | `InvalidGridDimensions`, unconditional | `xqmx-grid/resize_type_after_dimension_check` |
| `VECPUSH` | `MemoryLimitExceeded`, only against a near-exhausted budget | `metering/vecpush_charge_before_type_check` |
| `ATLEAST` | `IndexOutOfBounds` on `k`, unconditional | `constraints/atleast_k_range_before_model_type` |
| `ATLEASTW` | `VecLengthMismatch`, unconditional | `constraints/atleastw_length_before_model_type` |
| `EQUALITY` | `VecLengthMismatch`, unconditional -- the vec lengths are compared before the model register is discriminated, and the charge follows both vec reads | `constraints/equality_length_mismatch_beats_model_type` |
| `SETLINE`, `ADDLINE`, `SETQUAD`, `ADDQUAD`, `EXCLUDE`, `IMPLIES`, `REDUCE` | `MemoryLimitExceeded` ahead of the index check, on a model register against an exhausted budget | `metering/<opcode>_charge_before_index_check`, one per opcode |
| `ONEHOTR`, `ONEHOTC` | none -- a sample register is charged nothing, so the type check decides (QUI-1202) | `xqvm_py/tests/test_executor.py` alone (`test_onehot_does_not_charge_for_a_sample`); see below |

The seven coefficient-write rows used to be pinned by `xqvm_py`'s unit
tests alone. Coefficient writes are charged only on a model register, so
an int or vec register in their place is a `TypeMismatch` at any budget;
the observable ordering is the charge against the index check, which is
what their vectors pin.

The `ONEHOTR`/`ONEHOTC` row has no vector because the spec does not yet
say what it pins. Both VMs size the charge from a model-only peek, so a
sample is charged nothing and the run reaches the type check. The
charging table in `spec/xqvm/SPEC.md` charges ONEHOT "the expansion over
the row's or column's variables" with no exception for a sample, and its
error-precedence rule puts that charge ahead of type validation, which
read literally gives `MemoryLimitExceeded` against a budget with no
headroom. A vector written from the spec would contradict both VMs, and
one written from the VMs would not be a spec vector. The spec has to
decide first. Until it does, the `xqvm_py` test is the only pin and
QUI-1481 must replace it before deleting the package.

## Authoring a new vector

1. Write `program.xqasm` with a minimal scenario that exercises the
   behaviour you care about.
2. Pick the matching category directory (or create a new one). These
   directory names are a separate, coarser vocabulary from `opcodes.yaml`'s
   own `category` field; the two do not need to match, and mostly don't.
3. Write `inputs.json` with the calldata the program reads.
4. Write `expected.json` from what `spec/xqvm/` says the program must
   produce, not from what the VM happens to return. If the VM disagrees,
   either the VM or the spec is wrong: file a ticket and fix whichever one
   it is. A vector copied from the VM's output checks nothing.

## What is checked

- **Opcode table** -- `xqvm/build.rs` asserts `opcodes.yaml` against the
  `opcodes!` x-macro at compile time: the wire byte, the mnemonic, the pop
  and push counts (or `SCLR`'s reset) and each operand's name and encoded
  byte width.
- **Observable behaviour** -- every vector's outputs, residual stack and
  step count, or its fault identity, against `expected.json`.
- **Coverage** -- `xqvm/tests/vector_suite/coverage.rs` computes
  which opcodes the vectors cover. It does not require completeness; it
  holds the current numbers as floors so coverage cannot regress
  unremarked, and CI prints the report on every pipeline.

## Running locally

```sh
# Every vector, plus the format self-tests and the coverage ratchet
cargo test -p xqvm --test vectors

# One category, or one vector
cargo test -p xqvm --test vectors -- arithmetic
cargo test -p xqvm --test vectors -- --exact vector::arithmetic::add_basic

# Which opcodes no vector covers
make conformance-coverage
```

The coverage report gives two numbers. **Present** counts opcodes
appearing in a vector's assembled program; **reached** counts those a
vector executes to completion. Reached is always the smaller: an opcode
behind an untaken branch is present but not reached, and so is the
instruction an error vector exists to fault on, since a faulting
instruction never completes a step. An opcode missing from both lists is
a genuine hole; one that is present but never reached has an error vector
and no success-path vector.
