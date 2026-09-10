# Verifier

The verifier performs static analysis over a program before it runs, so
structural and semantic errors are caught at load time rather than partway
through execution. It runs as a fixed pipeline of phases; each phase makes
one pass over the program and the pipeline is fail-fast, stopping at the
first violation any phase finds. A program with two independent defects
reports only the first one encountered.

The Rust implementation is `xqvm::verifier`, exposed to Python via
`xqffi.verifier` and re-exported as `xquad.verifier`. There is one
implementation; `xqvm_py`, the pure-Python reference VM, does not verify.

This page is the reference: what each phase checks and what each error
means. For why a specific program was rejected and how to change it, see
[Verification](../running/verification.md).

## What passing verification guarantees

`Verifier::default()` builds the standard pipeline described below. Calling
its `run` method against a program returns `Ok(())` if every phase passes,
or the first `VerifierError` encountered otherwise. A program that passes is
guaranteed structurally sound: every opcode decodes, every jump lands on a
real `TARGET`, every loop is balanced, and every register is read only after
it is written on every reachable path. For the value stack, phase 4 found no
underflow, no join-point depth disagreement, and no basic block whose depth
exceeds the 8,192-item limit. That analysis models each basic block as a
*net* stack delta, so an instruction that pops more operands than it pushes
has its pop requirement absorbed whenever the running depth stays
non-negative -- a stack underflow caused by an operand-ordering error can
still pass verification.

Every phase reasons about types, control flow and depths. None reasons
about values, so no phase can decide a question whose answer the program
computes at run time. An allocator size, a grid extent, a loop bound, a
shift amount, a calldata or output index and every arithmetic operand are
all ordinary popped stack values, which is why `InvalidAllocation`,
`InvalidGridDimensions`, `InvalidDiscreteK`, `InvalidShift`,
`ArithmeticOverflow`, `IndexOutOfBounds`, `SampleOutOfDomain`,
`LoopStackOverflow` and the two budget faults exist only at runtime and
have no verifier counterpart. `SampleOutOfDomain` is the clearest of them:
`SETLINE` on a sample is well-typed whatever it writes, and only the value
the program computes decides whether the write is in domain.
That is a boundary rather than a gap: an embedder gets its bound from the
step and allocation budgets it sets, not from a verification pass.

## Pipeline

| Order | Phase | Checks |
|---|---|---|
| 1a | Structural | Truncated instruction bytes, unknown opcodes |
| 1b | Jump target | A jump label id at or past the program's target count |
| 1c | Loop nesting | `RANGE`/`ITER`/`NEXT` balance, loop-only instructions used outside a loop |
| 2 | Register type-state | Control-flow-based read-before-write and register type checks |
| 3 | Must-init | Control-flow AND-meet analysis: a register read that is unset on at least one incoming path |
| 4 | Stack depth | Control-flow-based stack underflow, overflow risk, and join-point depth mismatches |

Phases 1a-1c share one linear scan over the instruction bytes -- the same
scan that produces the program's id-to-offset lookup for `TARGET` labels
(see [Bytecode Format](bytecode-format.md)). Phases 2 through 4 share one
control-flow graph of basic blocks, built once, and each runs its own
forward worklist analysis over it. The control-flow graph correctly accounts for unreachable code,
conditional branches, and loop back-edges, catching errors that a purely
linear scan would miss.

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
closes one. The verifier rejects `NEXT`, `LVAL`, or `LIDX` reached with no
open loop frame, and any loop frame still open at the end of the program
(reported at the byte offset of the outermost unmatched opener).
Errors: `NoActiveLoop`, `UnmatchedLoop`.

## Phase 2 -- register type-state

A control-flow-based forward analysis over 256 register slots. Each
register starts `Unset`. An instruction that writes a register advances its
slot to a concrete type (`Int`, `VecInt`, `VecXqmx`, `Model`, `Sample`) or to
`Any`; an instruction that reads a register is checked against what that
instruction requires.

At a join point -- two or more incoming control-flow paths -- the
per-register meet is permissive: disagreeing types, including a register
unset on one path and `Int` on the other, resolve to `Any` rather than an
immediate error. That permissiveness avoids false positives for a register
written on only one branch of a conditional; Phase 3 is what actually
catches that case. Reading an `Unset` register is rejected, and so is a type
mismatch, for example reading a `Model` register where an instruction
requires `Int`.

`DROP` is a special case worth knowing. At runtime it resets the register to
`Unset`, and the verifier models that faithfully:
`DROP` resets the register's tracked type to `Unset`, so a read after
`DROP` is rejected as `ReadUnsetRegister` and would also fault at runtime as
`UnsetRegister`. Unreachable blocks are skipped, so dead code after an
unconditional jump never produces a false positive.

Errors: `ReadUnsetRegister`, `RegisterTypeMismatch`.

## Phase 3 -- must-init analysis

A control-flow-based forward analysis using an AND-meet over a bitmap of
which registers are definitely initialised. At program entry every register
is un-written. At a join point, a register's bit is cleared unless every
incoming path wrote it -- so a register written on only one branch of a
conditional is flagged as possibly-unset at any read after the join, even
though Phase 2's permissive meet let the same register pass its type check.
Unreachable blocks are skipped.

Error: `ReadUnsetRegister`.

## Phase 4 -- stack depth

A control-flow-based forward analysis over the abstract depth of the value
stack. Each basic block is characterised by the minimum depth it needs on
entry and its effect on exit; the analysis propagates the most conservative
depth to each successor.

Checks run in this order:

1. **Loop stack imbalance.** A separate pass walks each loop body, ignoring
   the back-edge, and compares the depth at `NEXT` against the depth at the
   matching `RANGE`/`ITER` opener. A non-zero net effect is an imbalance,
   reported before anything else in that loop is checked.
2. **Stack depth mismatch.** After the pass converges, every join block --
   two or more incoming edges -- has the arrival depths of all its reachable
   incoming edges compared; any two that differ mean the stack state at that
   point is not well-defined.

   Program entry counts as an incoming edge of the entry block, arriving at
   depth 0. So a back-edge targeting the entry block makes it a join block
   even though it has only one predecessor *block*, and the loop body's exit
   depth is compared against entry's depth 0.
3. **Underflow and overflow risk.** A block whose converged depth would
   fall below zero, or exceed 8,192, is flagged. The block's entry
   requirement is a running minimum that rises only when the depth within
   the block goes negative: a pop requirement absorbed by pushes earlier
   in the same block never makes the running depth negative, so it is
   invisible to this check.

`SCLR` resets the abstract depth unconditionally to zero, which the analysis
treats as a reset rather than a delta. If the entry depth was `N > 0` and the
exit depth is `0`, that reset is itself the imbalance: inside a loop it is
reported directly as `LoopStackImbalance` by check 1, ahead of everything
else in that loop. Outside a loop, the same reset is only a problem if it
creates a discrepancy between incoming edges at a join, in which case check 2
catches it as `StackDepthMismatch`.

Errors: `LoopStackImbalance`, `StackDepthMismatch`, `StackUnderflow`,
`StackOverflowRisk`.

## Error reference

| Error | Phase | Meaning |
|---|---|---|
| `TruncatedInstruction` | 1a | The instruction stream ends mid-operand |
| `BadOpcode` | 1a | An opcode byte does not map to a known instruction |
| `UndefinedJumpTarget` | 1b | A jump references a label id at or past the program's target count |
| `NoActiveLoop` | 1c | `NEXT`, `LVAL`, or `LIDX` reached with no open loop frame |
| `UnmatchedLoop` | 1c | A loop frame is still open at the end of the program |
| `ReadUnsetRegister` | 2 or 3 | A register is read before it is written, or before it is written on every path |
| `RegisterTypeMismatch` | 2 | A register holds a different `RegVal` variant than the instruction expects |
| `LoopStackImbalance` | 4 | A loop body's entry and exit stack depths differ |
| `StackDepthMismatch` | 4 | Two control-flow paths reach a join point with different stack depths |
| `StackUnderflow` | 4 | Stack depth would go negative at a reachable instruction |
| `StackOverflowRisk` | 4 | Stack depth would exceed 8,192 at a reachable instruction |

Every `VerifierError` variant carries the byte offset of the offending
instruction; several (`RegisterTypeMismatch`, `UndefinedJumpTarget`, and
others) also carry the register number or label id involved, so a
diagnostic can point at the exact spot in the bytecode.

## When a program fails

A verifier failure is a load-time rejection: nothing in the program has
executed. `xquad verify` runs the pipeline from the command line without
running the program; see [CLI](cli/). For what to change in your
source when a specific error fires, see
[Verification](../running/verification.md), which covers the same errors
from the side of fixing them rather than defining them.

## Sources

- [`spec/xqvm/VERIFIER.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/VERIFIER.md) is the normative reference, including the full per-opcode stack-effect table.
- [`xqvm/src/verifier/phase.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/verifier/phase.rs) defines the `Phase` trait and the default pipeline (`CombinedPhase` for 1a-1c, `AllCfgPhases` for 2-4).
- The individual phase modules live alongside it in [`xqvm/src/verifier/`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm/src/verifier).
- [`xqvm/src/verifier/error.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/verifier/error.rs) defines `VerifierError` and its display strings, reproduced in the table above.
