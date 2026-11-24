# XQVM Bytecode Checker

A bytecode checker validates a program's structure before execution: every
instruction decodes, every jump names a defined `TARGET`, and every loop is
opened and closed in order. It runs in one linear pass over the instruction
bytes, enabling early rejection at load time or at the blockchain submission
boundary.

The Rust implementation is `xqvm::checker` (exposed via `xqffi.checker` and
`xquad.checker` Python bindings).

---

## Architecture

Checking is structured as composable [`Phase`] implementations. Each phase
performs one focused pass over a [`Program`] and maps failures to a
[`CheckerError`]. A [`Checker`] runs phases sequentially, stopping at the
first failure.

The checker reads the shape of the bytecode only. It does not follow control
flow or track values, so it says nothing about what a register holds when an
instruction reads it, how deep the stack is, or what a run will cost. Those
are enforced by the VM as the program runs: `UnsetRegister`,
`RegisterType`, `StackUnderflow`, `StackOverflow`, `StepLimitExceeded` (see
`spec/xqvm/METERING.md`), `MemoryLimitExceeded` and the other runtime
faults in `SPEC.md`. A program that passes the check can raise any of them.

The `scan` function is the shared kernel: it makes one linear pass over the
instruction bytes, builds the jump table, and returns the first structural,
jump-target, or loop-nesting violation. `Program::new` calls it to populate
the jump table (ignoring any error); `Checker::default` calls it once per
invocation rather than three separate stream passes.

### Default pipeline (`Checker::default`)

| Order | Phase | Struct | What it checks |
|-------|-------|--------|----------------|
| 1a | Structural | `StructuralPhase` | Truncated bytes, unknown opcodes |
| 1b | Jump target | `JumpTargetPhase` | Jump label >= target count |
| 1c | Loop nesting | `LoopNestingPhase` | Loop open/close balance, loop-context reads |

Phases 1a--1c share the `scan` kernel and run as one combined pass. Custom
pipelines are supported via `Checker::new().with_phase(p)`.

---

## Phase 1 -- Structural, Jump-Target, Loop-Nesting

### 1a: Structural

Every byte in the instruction stream must decode to a known opcode with the
correct number of operand bytes. Unrecognised opcode bytes and truncated operand
sequences are rejected immediately.

Errors: `TruncatedInstruction`, `BadOpcode`.

### 1b: Jump target

Every `JUMP1`, `JUMPI1`, `JUMP2`, and `JUMPI2` instruction must reference a
label id that is present in the jump table (i.e., a `TARGET` with that
sequential id exists in the program). Label ids are assigned sequentially in
program order during the `TARGET` pre-scan; any label id >= target count is
invalid.

Error: `UndefinedJumpTarget`.

### 1c: Loop nesting

`RANGE` and `ITER` instructions each open a loop frame; `NEXT` closes one. The
checker tracks nesting depth and rejects:

- `NEXT`, `LVAL`, or `LIDX` reached with no open loop frame.
- Any loop frame still open at the end of the program, reported at the byte
  offset of the outermost unmatched opener.

Errors: `NoActiveLoop`, `UnmatchedLoop`.

---

## Error Variants

| Variant | Phase | Description |
|---------|-------|-------------|
| `TruncatedInstruction` | 1a | Instruction stream ends mid-operand |
| `BadOpcode` | 1a | Opcode byte does not map to a known instruction |
| `UndefinedJumpTarget` | 1b | Jump references label id >= target count |
| `NoActiveLoop` | 1c | `NEXT`, `LVAL`, or `LIDX` with no open loop frame |
| `UnmatchedLoop` | 1c | Loop frame still open at end of program |

Every variant carries the byte offset of the offending instruction.
