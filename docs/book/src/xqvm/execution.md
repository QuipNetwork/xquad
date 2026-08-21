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
   - Continue   → advance to next instruction
   - Halt       → stop execution
   - Jump(id)   → seek to the byte offset recorded for that TARGET id
   - Seek(off)  → seek to byte offset (used by NEXT)
   - StartLoop  → a loop frame was pushed; continue to the next instruction
   - SkipLoop   → loop count was zero or negative; scan past the matching NEXT
7. Repeat from step 1
```

## Instruction Stream

The instruction stream is a cursor over the program's raw bytecode. It decodes
one instruction at a time, advancing the cursor past the opcode byte and its
operands. The stream supports seeking to arbitrary byte offsets for jumps and
loop backs.

Before execution begins, the raw bytecode is scanned once for `TARGET`
opcodes: each is assigned the next sequential id (0, 1, 2, ...) in program
order, and its byte offset is recorded against that id. A `Jump` result
carries one of these ids; the run loop resolves it to the recorded offset and
seeks the stream there.

Each decoded instruction yields:
- **Byte offset** -- position in the bytecode buffer.
- **Optional label** -- the sequential `TARGET` id recorded at this offset, if any.
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

The step counter is accessible after execution via `vm.steps()`. For a
`HALT`-terminated program it reports the exact number of instructions
executed, since the loop breaks right after dispatching `HALT`. A program
that runs off the end of the instruction stream without a `HALT` gets one
extra count: the counter increments before the next instruction is fetched,
so the fetch that finds nothing and breaks the loop has already been
counted. `xqvm_py`'s executor increments after checking for more
instructions, so the two interpreters can disagree by one on the same
non-`HALT`-terminated program.

## Allocation Accounting

Steps bound how long a program runs, not how much memory it asks for: a
three-instruction program can name a sample of any size the value stack can
hold. A second counter tracks bytes. Every allocating instruction is charged
against a configurable budget (default: 1 GiB) *before* it allocates, and one
that cannot pay stops execution with a `MemoryLimitExceeded` error without
allocating anything.

```rust
let mut vm = Vm::new();
vm.set_memory_limit(16 * 1024 * 1024);  // 16 MiB
```

`vm.memory_used()` reports what the run spent. See
[Runtime Limits](limits-and-errors.md) for the per-opcode charges.

## Control Flow Results

Each instruction handler returns a `StepResult` that tells the execution loop
what to do next:

| Result | Meaning |
|--------|---------|
| `Continue` | Advance to the next instruction in sequence. |
| `Halt` | Stop execution. Returned by `HALT`. |
| `Jump(label)` | Seek the instruction stream to the byte offset recorded for that `TARGET` id. |
| `Seek(offset)` | Seek to a raw byte offset. Used by `NEXT` to loop back. |
| `StartLoop` | A loop frame was pushed; continue to the next instruction (which becomes the loop body start). |
| `SkipLoop` | The loop count was zero or negative; scan forward past the matching `NEXT` without pushing a frame. |

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
