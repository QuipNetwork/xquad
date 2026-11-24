# Checker

The checker validates a program's structure before it runs, so a malformed
program is caught at load time rather than partway through execution. It
runs as a fixed pipeline of phases sharing one linear pass over the
instruction bytes, and the pipeline is fail-fast, stopping at the first
violation any phase finds. A program with two independent defects reports
only the first one encountered.

The Rust implementation is `xqvm::checker`, exposed to Python via
`xqffi.checker` and re-exported as `xquad.checker`.

This page is the reference: what each phase checks and what each error
means. For why a specific program was rejected and how to change it, see
[Checking](../running/checking.md).

## What passing the check guarantees

`Checker::default()` builds the standard pipeline described below. Calling
its `run` method against a program returns `Ok(())` if every phase passes,
or the first `CheckerError` encountered otherwise. A program that passes is
guaranteed structurally sound: every opcode decodes, every jump lands on a
real `TARGET`, and every loop opened by `RANGE` or `ITER` is closed by a
`NEXT` in program order, with `NEXT`, `LVAL` and `LIDX` only inside an open
loop.

The checker reads the shape of the bytecode only. It does not follow
control flow, and it does not track what a register holds, how deep the
stack is, or any value the program computes. Every such fault is the VM's
to raise as the program runs: `UnsetRegister`, `RegisterType`,
`StackUnderflow`, `StackOverflow`, `InvalidAllocation`,
`InvalidGridDimensions`, `InvalidIntegerK`, `InvalidShift`,
`ArithmeticOverflow`, `IndexOutOfBounds`, `SampleOutOfDomain`,
`NonBinaryModel`, `LoopStackOverflow` and the two budget faults. That is a
boundary rather than a gap: an embedder gets its bound from the step and
allocation budgets it sets, not from a checker pass. See [Limits and
Errors](limits-and-errors.md#vm-runtime-errors).

## Pipeline

| Order | Phase | Checks |
|---|---|---|
| 1a | Structural | Truncated instruction bytes, unknown opcodes |
| 1b | Jump target | A jump label id at or past the program's target count |
| 1c | Loop nesting | `RANGE`/`ITER`/`NEXT` balance, loop-only instructions used outside a loop |

Phases 1a-1c share one linear scan over the instruction bytes -- the same
scan that produces the program's id-to-offset lookup for `TARGET` labels
(see [Bytecode Format](bytecode-format.md)).

## Phase 1 -- structural, jump target, loop nesting

**1a, structural.** Every byte in the instruction stream must decode to a
known opcode with the correct number of operand bytes. An unrecognised
opcode byte or a truncated operand sequence is rejected immediately.
Errors: `TruncatedInstruction`, `BadOpcode`.

**1b, jump target.** Every `JUMP1`, `JUMPI1`, `JUMP2`, and `JUMPI2`
instruction must reference a label id produced by the `TARGET` pre-scan; an
id greater than or equal to the program's target count is invalid.
Error: `UndefinedJumpTarget`.

**1c, loop nesting.** `RANGE` and `ITER` each open a loop frame; `NEXT`
closes one. The checker rejects `NEXT`, `LVAL`, or `LIDX` reached with no
open loop frame, and any loop frame still open at the end of the program
(reported at the byte offset of the outermost unmatched opener).
Errors: `NoActiveLoop`, `UnmatchedLoop`.

## Error reference

| Error | Phase | Meaning |
|---|---|---|
| `TruncatedInstruction` | 1a | The instruction stream ends mid-operand |
| `BadOpcode` | 1a | An opcode byte does not map to a known instruction |
| `UndefinedJumpTarget` | 1b | A jump references a label id at or past the program's target count |
| `NoActiveLoop` | 1c | `NEXT`, `LVAL`, or `LIDX` reached with no open loop frame |
| `UnmatchedLoop` | 1c | A loop frame is still open at the end of the program |

Every `CheckerError` variant carries the byte offset of the offending
instruction; `UndefinedJumpTarget` also carries the label id and the
program's target count, so a diagnostic can point at the exact spot in the
bytecode.

## When a program fails

A checker failure is a load-time rejection: nothing in the program has
executed. `xquad vm check` runs the pipeline from the command line without
running the program; see [CLI](../cli/vm/). For what to change in your
source when a specific error fires, see
[Checking](../running/checking.md), which covers the same errors
from the side of fixing them rather than defining them.

## Sources

- [`spec/xqvm/CHECKER.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/CHECKER.md) is the normative reference.
- [`xqvm/src/checker/phase.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/checker/phase.rs) defines the `Phase` trait and the default pipeline (`CombinedPhase`, which runs 1a-1c).
- The individual phase modules live alongside it in [`xqvm/src/checker/`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm/src/checker).
- [`xqvm/src/checker/error.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/checker/error.rs) defines `CheckerError` and its display strings, reproduced in the table above.
