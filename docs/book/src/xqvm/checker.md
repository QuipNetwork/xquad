# Checker

The checker performs static analysis over a program before it runs, so
structural and semantic errors are caught at load time rather than partway
through execution. It runs as a fixed pipeline of phases; each phase makes
one pass over the program and the pipeline is fail-fast, stopping at the
first violation any phase finds. A program with two independent defects
reports only the first one encountered.

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
real `TARGET`, every loop is balanced, and every register is read only after
it is written on every reachable path. For the value stack, phase 4 found no
underflow, no join-point depth disagreement, and no basic block whose depth
exceeds the 8,192-item limit. Every instruction on every reachable path has
at least as many values on the stack as it pops, counted before any of its
pushes, so a program that passes cannot underflow the value stack.

Every phase reasons about types, control flow and depths. None reasons
about values, so no phase can decide a question whose answer the program
computes at run time. An allocator size, a grid extent, a loop bound, a
shift amount, a calldata or output index and every arithmetic operand are
all ordinary popped stack values, which is why `InvalidAllocation`,
`InvalidGridDimensions`, `InvalidIntegerK`, `InvalidShift`,
`ArithmeticOverflow`, `IndexOutOfBounds`, `SampleOutOfDomain`,
`LoopStackOverflow` and the two budget faults exist only at runtime and
have no checker counterpart. `SampleOutOfDomain` is the clearest of them:
`SETLINE` on a sample is well-typed whatever it writes, and only the value
the program computes decides whether the write is in domain.
That is a boundary rather than a gap: an embedder gets its bound from the
step and allocation budgets it sets, not from a checker pass.

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
closes one. The checker rejects `NEXT`, `LVAL`, or `LIDX` reached with no
open loop frame, and any loop frame still open at the end of the program
(reported at the byte offset of the outermost unmatched opener).
Errors: `NoActiveLoop`, `UnmatchedLoop`.

## Phase 2 -- register type-state

A control-flow-based forward analysis over 256 register slots. Each
register starts `Unset`. An instruction that writes a register advances its
slot to a concrete type (`Int`, `VecInt`, `VecXqmx`, `Model`, `Sample`) or to
`Any`; an instruction that reads a register is checked against what that
instruction requires.

At a join point, a register unwritten on any incoming path remains
possibly unset. Reading it is rejected. `Any` is the meet identity, while
`Unset` is absorbing. This makes register propagation monotone and bounded.
The standalone type-state phase now also catches partial writes across
branches; the default pipeline already rejected them in the must-init phase.

Joining a model with a sample gives `model or sample`, and
joining the two vector types gives `vec<int> or vec<xqmx>`; each satisfies
only what its two members have in common -- the grid operations and the
linear coefficient trio for the first, `ITER` and `VECLEN` for the second.
Any other pair of types gives `conflicting types`, which satisfies nothing
but a read that accepts any set register, such as `OUTPUT`. A program that
allocates a model on one branch and a sample on the other and then writes a
quadratic coefficient is rejected, which is the point: it would fault at run
time on one of its two paths. Answering unrelated pairs this way is also
what keeps the result the same however many branches meet, and whatever
order they are in.

`DROP` is a special case worth knowing. At runtime it resets the register to
`Unset`, and the checker models that faithfully:
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
conditional is flagged as possibly-unset at any read after the join, including
when used independently of Phase 2.
Unreachable blocks are skipped.

Error: `ReadUnsetRegister`.

## Migrating custom data-flow analyses

`dataflow::solve` now takes a third argument, `max_visits`, and returns
`Result<DataFlowResult<...>, SolveError>`. Supply a justified bound using
checked arithmetic and handle `SolveError::BudgetExceeded`; do not retry
without a bound. `N + H * E` visits suffice for N nodes, E edges and at most
H strict output descents per node under a monotone transfer.

All outputs start at top. The boundary value supplies the boundary input;
its output is the result of its transfer. Backward results follow program
order even when entry and exit refer to the same node.

Exhaustive matches on `CheckerError` must handle `AnalysisBudgetExceeded`.
Built-in analyses derive their bounds internally. Exhaustion reports an
analysis defect and provides no partial facts or byte offset. The default
pipeline can report a missing write earlier than before, changing diagnostic
variant or offset on an invalid program. Standalone `RegisterTypePhase` now
rejects reads after partial writes across branches.

See the normative [analysis budget contract](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/CHECKER.md#deterministic-analysis-budgets)
for the register order, bounds and cost limitations.

## Phase 4 -- stack depth

A control-flow-based forward analysis over the abstract depth of the value
stack. Each basic block is characterised by the minimum depth it needs on
entry and its effect on exit. The analysis assigns one exact height per
reachable block and checks every incoming edge for agreement.

Checks run in this order:

1. **Local stack faults.** FIFO from program entry checks each block once:
   minimum input and post-reset underflow first, then entry height, exit
   height and transient peak against the 8,192 limit.
2. **Edge consistency.** Successors retain CFG insertion order (jump target
   before fall-through; loop body before skip; NEXT back-edge before exit).
   An unseen successor receives its height and enters the queue once. A
   different incoming height reports `StackDepthMismatch`; a matching NEXT
   back-edge reports `LoopStackImbalance`. Back-edges to program entry are
   compared with the implicit zero-height entry edge.

The first witnessed fault is reported. This replaces the former global
error-class priority, so rejected programs can change diagnostic variant or
offset. Accepted programs are unchanged. Arbitrary loop escapes and re-entry
remain ordinary CFG edges; there are no separate body walks or new nesting
limits. Depth work is at most N block transfers and E edge checks after
indexing the graph. See the normative checker specification for the
acceptance-equivalence proof and historical minimum-walk oracle.

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

Every `CheckerError` variant carries the byte offset of the offending
instruction; several (`RegisterTypeMismatch`, `UndefinedJumpTarget`, and
others) also carry the register number or label id involved, so a
diagnostic can point at the exact spot in the bytecode.

## When a program fails

A checker failure is a load-time rejection: nothing in the program has
executed. `xquad vm check` runs the pipeline from the command line without
running the program; see [CLI](../cli/vm/). For what to change in your
source when a specific error fires, see
[Checking](../running/checking.md), which covers the same errors
from the side of fixing them rather than defining them.

## Sources

- [`spec/xqvm/CHECKER.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/CHECKER.md) is the normative reference, including the full per-opcode stack-effect table.
- [`xqvm/src/checker/phase.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/checker/phase.rs) defines the `Phase` trait and the default pipeline (`CombinedPhase` for 1a-1c, `AllCfgPhases` for 2-4).
- The individual phase modules live alongside it in [`xqvm/src/checker/`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm/src/checker).
- [`xqvm/src/checker/error.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/checker/error.rs) defines `CheckerError` and its display strings, reproduced in the table above.
