# Limits and Errors

XQVM has two separate error surfaces. The [verifier](verifier.md) is static
analysis that runs before a program executes; `xquad verify` runs it without
running the program. The VM is runtime: `xquad run` does not verify
automatically, so a program that was never checked can reach the VM and
fault there instead. Several conditions -- an out-of-range jump label, in
particular -- have both a verifier error and a distinct VM runtime error for
this reason.

## Fixed Limits

| Limit | Value | Enforced by |
|---|---|---|
| Stack depth | 8,192 items | VM: `StackOverflow`. Verifier: `StackUnderflow`/`StackOverflowRisk` (Phase 4) |
| Register count | 256 slots (r0-r255), statically allocated | -- |
| Jump label range | 0-65,535 (`u16`), 65,536 labels max | Assembler: `TooManyTargets`. Verifier: `UndefinedJumpTarget`. VM: `InvalidLabel` |
| Shift amount | 0-63 bits | VM: `InvalidShift` |
| Grid dimensions | rows and cols must be > 0, and `rows * cols` must not exceed the register's declared size | VM: `InvalidGridDimensions` |
| Loop nesting | 8,192 frames | VM: `LoopStackOverflow` |
| Discrete domain size (`XQMX`/`XSMX`) | `k >= 2` | VM: `InvalidDiscreteK` |

## Configurable Limits

| Limit | Library default | Method | VM error when exceeded |
|---|---|---|---|
| Step budget | 10,000,000 | `Vm::set_step_limit(n)` / `Vm::set_unlimited_steps()` | `StepLimitExceeded` |
| Allocation budget | 1 GiB | `Vm::set_memory_limit(bytes)` | `MemoryLimitExceeded` |
| Calldata slots | 0 | `Vm::set_calldata(vec)` | `CallDataIndex` |
| Output slots | 0 | `Vm::set_output_slots(n)` | `OutputIndex` |

The library defaults above apply to `Vm::new()` directly; the `xquad run`
CLI sets its own defaults before handing control to the VM -- 16 output
slots unless `--outputs` overrides it. See [xquad run](cli/run.md) for the
CLI's own default table.

The step limit is exact: `set_step_limit(0)` permits no instructions at
all, not unlimited ones. To remove the bound, call
`Vm::set_unlimited_steps()`. Nothing else removes it, and nothing removes
it by default. Until 0.4.0 `0` was the sentinel for "unlimited", which
made a zero budget the most dangerous value a caller could pass rather
than the safest.

## The step budget

A step is a unit of metered execution cost, not an instruction: every
instruction charges a base cost before dispatch, and opcodes whose work
scales with data the program controls -- evaluating a model, expanding a
constraint, copying a register that holds a model -- charge more before
they do that work. An instruction that cannot pay the charge fails with
`StepLimitExceeded` and does none of the work it would have charged for.
`Vm::steps()` reports the metered total; `Vm::instructions()` reports the
plain dispatch count, which is always less than or equal to `steps()`. See
[`spec/xqvm/METERING.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/METERING.md)
for the full cost model, including the per-opcode charges and the
constants they use.

## The allocation budget

Every instruction that allocates a sample buffer, declares model variables,
grows a vector, or expands constraint coefficients is charged against the
budget *before* it allocates. An instruction that cannot pay fails with
`MemoryLimitExceeded` and allocates nothing, leaving its target register
untouched. `Vm::memory_used()` reports what a run spent.

Charges are cumulative rather than a high-water mark of live memory: bytes are
charged when they are allocated and are never refunded, so a loop that
allocates and discards cannot spend more than the budget in total. Each `run()`
starts from zero. There is no sentinel for "unlimited" -- pass `u64::MAX`.

| Charged | Rate |
|---------|------|
| `BQMX`, `SQMX`, `XQMX` (declared model size) | 8 bytes per variable |
| `BSMX`, `SSMX`, `XSMX` (sample buffer) | 8 bytes per variable |
| `VECPUSH`, `SLACK` (vector growth) | 16 bytes per element |
| `SETLINE`, `ADDLINE` on a model | 32 bytes per coefficient |
| `SETQUAD`, `ADDQUAD`, `EXCLUDE`, `IMPLIES` | 48 bytes per coefficient |
| `ONEHOTR`, `ONEHOTC`, `EQUALITY`, `ATLEAST`, `ATLEASTW` | worst-case expansion: one linear term per variable and one quadratic term per pair |
| `REDUCE` | one auxiliary variable, three quadratic terms, one linear term |
| `ITER` on a `vec<int>` (slice copied into the loop frame) | 8 bytes per element |
| `ITER` on a `vec<xqmx>` (slice copied into the loop frame) | one whole-model copy per element -- see below |

Models store their coefficients sparsely, so a declared model size costs
nothing immediately; it is charged because every consumer of the model -- the
sample needed to evaluate it, each solver backend -- has to materialise it.
The expanding constraint opcodes are charged for their worst case, so an
expansion whose indices collide can be charged more than it ultimately stores.

`VEC`, `VECI` and `VECX` install an empty vector and allocate nothing; their
storage is charged as `VECPUSH` and `SLACK` create it.

`ITER` copies the slice it iterates into its loop frame, and a loop frame is
released only by `NEXT`. A back-edge that re-enters an `ITER` without reaching
its `NEXT` therefore accumulates copies, which is why the copy is charged.
The loop frame itself is not charged, but it is bounded: the loop stack is
capped at 8,192 frames and a program that grows it past that fails with
`LoopStackOverflow`.

An element of a `vec<xqmx>` is charged the whole-model copy rate, because
cloning a model clones its coefficient maps: 8 bytes per declared variable
plus 32 per live linear coefficient and 48 per live quadratic one. That is
exactly what the allocator and the coefficient writes charged to build the
model in the first place, and it is the same number wherever the copy
happens -- through an `ITER`, or through `INPUT`/`OUTPUT` copying a whole
register across the host boundary. A model does not get cheaper by being
duplicated through one opcode rather than another.

The charge schedule is defined over program-visible quantities -- variables
declared, elements appended, coefficients written -- rather than over either
interpreter's internal representation. The Python reference interpreter
charges the same rates via `Executor.execute(..., memory_limit=...)`,
raising `xqvm_py.errors.MemoryLimitExceeded`, so both implementations reject
the same programs at the same instruction having charged the same bytes.
`xquad.vm.VM.set_memory_limit()` sets it on either backend and
`VM.memory_used()` reads the result back.

## VM runtime errors

Produced by `xqvm::Error`
([`xqvm/src/error.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/error.rs))
while a program is executing. Every variant that carries a byte position
(`pos`) can be turned into a `RuntimeDiagnostic` via `Error::into_diagnostic`,
which disassembles the program and points at the failing instruction.

| Error | Cause |
|---|---|
| `StackUnderflow` | Popping from an empty or too-shallow stack |
| `StackOverflow` | Pushing when the stack is already at 8,192 items |
| `RegisterType` | Instruction expects a different `RegVal` variant than the register holds |
| `IncompatibleType` | Type mismatch reported without register context |
| `UnsetRegister` | `LOAD` or `OUTPUT` on a register that was never written, or was `DROP`ped |
| `DivisionByZero` | `DIV` or `MOD` with divisor 0 |
| `ArithmeticOverflow` | An `i64` operation left the signed 64-bit range. Every operation the VM performs on a program's behalf is checked, intermediates included, so a computation whose mathematical answer is representable still faults when a partial result is not |
| `IndexOutOfBounds` | An index operand outside what it addresses: a vec index, a coefficient index against the model's declared size, or a grid row or column against the extent `RESIZE` declared |
| `NoActiveLoop` | `NEXT`, `LVAL`, or `LIDX` with no active loop |
| `BadJumpTarget` | Jump target lands outside the bytecode buffer |
| `InvalidLabel` | `JUMP`/`JUMPI` references a label id the id-to-offset scan never resolved |
| `BadOpcode` | Unrecognised opcode byte |
| `TruncatedInstruction` | Bytecode ends mid-instruction |
| `CallDataIndex` | `INPUT` index out of range |
| `OutputIndex` | `OUTPUT` index out of range |
| `SizeMismatch` | `ENERGY` sample length does not match model size |
| `VecLengthMismatch` | Two parallel vectors used together (for example `EQUALITY`'s indices and coefficients) have different lengths |
| `StepLimitExceeded` | A step charge could not be paid: `"step charge of {requested} exceeds the step limit of {limit} ({used} steps already charged)"`, where `requested` is `1` for the base per-instruction cost or the larger charge an opcode asked for before doing data-scaled work |
| `MemoryLimitExceeded` | An allocating instruction exceeded the configured allocation budget |
| `InvalidShift` | `SHL`/`SHR` shift amount outside `[0, 64)` |
| `InvalidGridDimensions` | `RESIZE` with rows or cols <= 0, `RESIZE` with `rows * cols` past the register's declared size, or a grid-reading opcode on a register with no grid |
| `InvalidAllocation` | An allocator given a negative size, or -- only above the budget bound `spec/xqvm/SPEC.md` records -- one too large for the executing target to address |
| `LoopStackOverflow` | `RANGE`/`ITER` nesting past 8,192 frames |
| `InvalidDiscreteK` | `XQMX`/`XSMX` called with `k < 2` -- at `k = 1` the signed `[-k, k-1]` domain is `{-1, 0}`, which degenerates to a binary choice `BQMX` already covers |
| `UnmatchedLoop` | A `RANGE`/`ITER` skip-forward scan reached the end of the stream without a matching `NEXT` |
| `TraceFailed` | A tracer callback returned an error (for example an I/O write failure) |

## Verifier errors

Produced by `xqvm::verifier::VerifierError`
([`xqvm/src/verifier/error.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/verifier/error.rs))
before a program runs, by the four-phase pipeline described in
[Verifier](verifier.md): structural, jump-target and loop-nesting checks;
register type-state; must-init analysis; and stack depth.

The eleven variants, which phase raises each one, and what each one means
are catalogued in [Verifier's error reference](verifier.md#error-reference)
rather than repeated here, since that page also explains the phase that
produces each one. See [Verification](../running/verification.md) for how
to fix a rejected program.

## Substrate pallet fixture limits

`fixtures/pallet-xqvm` (excluded from the main Cargo workspace build; see
[Embedding Overview](../embedding/)) adds two additional bounds on
top of the ones above, enforced by the runtime's `Config` trait rather than
the VM:

| Limit | Config item | Purpose |
|---|---|---|
| Program size | `MaxProgramSize` | Maximum bytecode byte length accepted by `submit_program` |
| Calldata and output count | `MaxCalldata` | Shared bound on both the calldata vector and the output-slot vector |

`submit_program` takes the step budget as an extrinsic argument rather
than reading a pallet constant, so a caller names the bound the VM may
spend. The bound is exact, and a `step_limit` of `0` fails rather than
succeeding vacuously. The fixture sets no allocation budget, so the VM's
1 GiB default applies -- far too generous for a runtime that has to price
what it admits. See [Substrate Pallet](../embedding/pallet.md).
