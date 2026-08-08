# Runtime Limits

This page summarises the fixed and configurable limits of the XQVM runtime.

## Fixed Limits

| Limit | Value | Error |
|-------|-------|-------|
| Stack depth | 8,192 items | `StackOverflow` |
| Register count | 256 slots (r0--r255) | -- (statically allocated) |
| Jump label range | 0--65,535 (`u16`) | `InvalidLabel` |
| Shift amount | 0--63 bits | `InvalidShift` |
| Grid dimensions | Must be > 0 | `InvalidGridDimensions` |

## Configurable Limits

| Limit | Default | Method | Error |
|-------|---------|--------|-------|
| Step count | 10,000,000 | `Vm::set_step_limit(n)` | `StepLimitExceeded` |
| Calldata slots | 0 | `Vm::set_calldata(vec)` | `CallDataIndex` |
| Output slots | 0 | `Vm::set_output_slots(n)` | `OutputIndex` |

Setting the step limit to `0` sets it to `u64::MAX` (effectively unlimited).

## Pallet Limits

When running in the Substrate pallet, additional limits apply:

| Limit | Configuration | Purpose |
|-------|---------------|---------|
| Program size | `MaxProgramSize` | Maximum bytecode bytes. |
| Calldata entries | `MaxCallDataLen` | Maximum input integers. |
| Output slots | `MaxOutputSlots` | Maximum output slots. |
| Step limit | `MaxStepLimit` | Cap on per-execution steps. |

## Error Types

All runtime errors include the byte offset (`pos`) of the faulting instruction
for precise diagnostics. The full error enum is defined in
`crates/vm/src/error.rs`.

| Error | Cause |
|-------|-------|
| `StackUnderflow` | Popping from an empty or too-shallow stack. |
| `StackOverflow` | Pushing when stack is at 8,192 items. |
| `RegisterType` | Instruction expects a different `RegVal` variant. |
| `DivisionByZero` | `DIV` or `MOD` with divisor 0. |
| `IndexOutOfBounds` | Vec access with invalid index. |
| `NoActiveLoop` | `NEXT` or `LVAL` with no loop frame. |
| `InvalidLabel` | Jump to a non-existent label. |
| `BadJumpTarget` | Jump target is not a `TARGET` instruction. |
| `BadOpcode` | Unknown opcode byte. |
| `TruncatedInstruction` | Bytecode ends mid-instruction. |
| `CallDataIndex` | `INPUT` index out of range. |
| `OutputIndex` | `OUTPUT` index out of range. |
| `SizeMismatch` | `ENERGY` sample length ≠ model size. |
| `StepLimitExceeded` | Execution exceeded configured limit. |
| `InvalidGridDimensions` | `RESIZE` with rows or cols ≤ 0. |
| `InvalidShift` | `SHL`/`SHR` shift amount outside [0, 64). |
| `InvalidDiscreteK` | `XQMX`/`XSMX` called with `k < 2` (the signed `[-k, k-1]` domain requires at least two values). |
