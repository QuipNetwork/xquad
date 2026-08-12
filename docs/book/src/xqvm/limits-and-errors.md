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
| Grid dimensions | rows and cols must be > 0 | VM: `InvalidGridDimensions` |
| Discrete domain size (`XQMX`/`XSMX`) | `k >= 2` | VM: `InvalidDiscreteK` |

## Configurable Limits

| Limit | Library default | Method | VM error when exceeded |
|---|---|---|---|
| Step count | 10,000,000 | `Vm::set_step_limit(n)` | `StepLimitExceeded` |
| Calldata slots | 0 | `Vm::set_calldata(vec)` | `CallDataIndex` |
| Output slots | 0 | `Vm::set_output_slots(n)` | `OutputIndex` |

The library defaults above apply to `Vm::new()` directly; the `xquad run`
CLI sets its own defaults before handing control to the VM -- 16 output
slots unless `--outputs` overrides it. See [xquad run](cli/run.md) for the
CLI's own default table.

Calling `set_step_limit(0)` sets the limit to `u64::MAX` (effectively
unlimited), not to zero steps.

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
| `IndexOutOfBounds` | Vector access with an invalid index |
| `NoActiveLoop` | `NEXT`, `LVAL`, or `LIDX` with no active loop |
| `BadJumpTarget` | Jump target lands outside the bytecode buffer |
| `InvalidLabel` | `JUMP`/`JUMPI` references a label id the id-to-offset scan never resolved |
| `BadOpcode` | Unrecognised opcode byte |
| `TruncatedInstruction` | Bytecode ends mid-instruction |
| `CallDataIndex` | `INPUT` index out of range |
| `OutputIndex` | `OUTPUT` index out of range |
| `SizeMismatch` | `ENERGY` sample length does not match model size |
| `VecLengthMismatch` | Two parallel vectors used together (for example `EQUALITY`'s indices and coefficients) have different lengths |
| `StepLimitExceeded` | Execution exceeded the configured step limit |
| `InvalidShift` | `SHL`/`SHR` shift amount outside `[0, 64)` |
| `InvalidGridDimensions` | `RESIZE` with rows or cols <= 0 |
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
[Embedding Overview](../embedding/README.md)) adds two additional bounds on
top of the ones above, enforced by the runtime's `Config` trait rather than
the VM:

| Limit | Config item | Purpose |
|---|---|---|
| Program size | `MaxProgramSize` | Maximum bytecode byte length accepted by `submit_program` |
| Calldata and output count | `MaxCalldata` | Shared bound on both the calldata vector and the output-slot vector |

The fixture applies no step-limit override, so the VM's default of
10,000,000 steps applies inside it.
