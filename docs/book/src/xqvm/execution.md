# Execution Model

This chapter describes how the VM fetches, decodes, and executes instructions.

## Fetch-Decode-Execute Cycle

The VM processes instructions in a loop:

```
1. Check step limit → error if exceeded
2. Increment step counter
3. Fetch next instruction from the instruction stream
4. Decode the opcode byte and operands
5. Dispatch to the handler for that instruction
6. Handle the control flow result:
   - Continue  → advance to next instruction
   - Halt      → stop execution
   - Jump(lbl) → seek to jump_table[lbl].start
   - Seek(off) → seek to byte offset (used by NEXT)
7. Repeat from step 1
```

## Instruction Stream

The instruction stream is a cursor over the program's raw bytecode. It decodes
one instruction at a time, advancing the cursor past the opcode byte and its
operands. The stream supports seeking to arbitrary byte offsets for jumps and
loop backs.

Each decoded instruction yields:
- **Byte offset** -- position in the bytecode buffer.
- **Optional label** -- if a jump table entry starts at this offset.
- **Instruction** -- the fully decoded instruction with typed operands.

## Step Counting

The VM maintains a step counter that increments after every instruction
dispatch. A configurable step limit (default: 10,000,000) prevents runaway
programs. When the limit is reached, execution stops with a
`StepLimitExceeded` error.

```rust
let mut vm = Vm::new();
vm.set_step_limit(1_000_000);  // custom limit
// set_step_limit(0) sets the limit to u64::MAX (effectively unlimited)
```

The step counter is accessible after execution via `vm.steps()`, which reports
the actual number of instructions executed. This is used by the pallet for
weight refunds.

## Control Flow Results

Each instruction handler returns a `StepResult` that tells the execution loop
what to do next:

| Result | Meaning |
|--------|---------|
| `Continue` | Advance to the next instruction in sequence. |
| `Halt` | Stop execution. Returned by `HALT`. |
| `Jump(label)` | Seek the instruction stream to `jump_table[label].start`. |
| `Seek(offset)` | Seek to a raw byte offset. Used by `NEXT` to loop back. |
| `StartLoop` | A loop frame was pushed; continue to the next instruction (which becomes the loop body start). |

## Tracing

The VM supports optional step-by-step tracing via the `Tracer` trait. When
tracing is enabled, the VM captures state before and after each instruction:

```rust
pub struct StepState<'a> {
    pub pos: usize,                     // byte offset
    pub step: u64,                      // step count
    pub instruction: &'a Instruction,   // decoded instruction
    pub stack: &'a [i64],               // current stack
    pub read_regs: &'a [(u8, RegVal)],  // registers read
    pub written_regs: &'a [(u8, RegVal)], // registers written
    pub loop_depth: usize,              // nesting level
}
```

Two built-in tracer implementations are provided:

- **`TextTracer`** -- human-readable aligned columns, written to any `Write`
  target.
- **`JsonTracer`** -- one JSON object per step (JSONL format).

When tracing is disabled (`NoopTracer`), the tracer code is eliminated by dead
code optimisation, adding zero overhead to execution.

## Error Handling

Runtime errors carry the byte offset (`pos`) of the faulting instruction,
enabling precise error reporting. When the `std` feature is enabled, errors can
be converted to `miette::Diagnostic` with a disassembled listing highlighting
the faulting instruction.
