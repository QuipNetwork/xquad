# On-Chain Admissibility

A runtime that embeds `xqvm` runs bytecode it did not write, submitted by
an account it does not trust, inside a block whose weight it has to declare
in advance. The question this page answers is which of the VM's 93 opcodes
such a runtime may admit.

The answer is all of them, and not as a concession each runtime makes
separately. On-chain verifiable deterministic execution is what XQVM is
for, so chain compatibility is a property of the instruction set rather
than a per-embedder admission decision. An operation that cannot be made to
meet the bar below is not an opcode that needs gating: it is functionality
that belongs in `xqcp`, `xqsa`, the `xquad` API or a helper library, and it
never enters the ISA at all.

This page is the reader-facing summary of that rule. All six clauses are
specified where a third implementation reads them: clause 1 in
`spec/xqvm/SPEC.md`'s Type System, which makes checked arithmetic normative
with no implementation-defined alternative; clause 2 in the same file's
Determinism section, which states that an instruction's result is a
function of the program, the calldata and the VM's own state, and that no
instruction reads host state the calldata did not carry; clause 3 in
`HLF.md`'s ordering rules, covering delta application, sparse-table
accumulation and the grid fold; clause 4 in `SPEC.md`'s Allocation budget
section, which charges every allocation before it happens; clause 5 in
`METERING.md`, which fixes the step schedule, its charging points and its
counting units; and clause 6 in `SPEC.md`'s Faults section, which gives
every fault a normative identity that `ISA.md` is bound to name faults by,
together with the conformance vectors that pin the behaviour itself. One
row of that section records an identity that is not yet settled; the
section on [what is not enforced](#what-is-not-enforced-today) says which.

The gate that holds a proposed opcode against all six belongs to the
contribution process rather than to the specification. It is stated in
`docs/guide/development-workflow.md` under "The opcode-addition gate":
adding a row to the opcode table requires the merge request to argue each
of the six clauses in its description, and an opcode that cannot clear all
six does not ship. There is deliberately no CI guard for it, because the
gate asks for a correctness argument a reviewer weighs rather than a string
a script can find.

## The bar

An XQVM opcode has to satisfy all of the following.

1. **No floating point.** Integer arithmetic only, with every operation
   range-checked so that it faults rather than wrapping.
2. **No host I/O, wall-clock, or ambient state.** An instruction's result
   is a function of the program, the calldata and the VM's own state.
3. **No nondeterministic iteration order.** Anything that walks a
   collection walks it in an order the spec fixes.
4. **Bounded allocation.** Every allocation is charged against a budget
   before it happens, and the budget is not escapable by a value the
   submitting account controls.
5. **Bounded per-instruction work.** The work one instruction performs is
   charged against the step budget before it happens, at a rate that scales
   with the data the program controls, so that a step is a unit of cost
   rather than a unit of dispatch.
6. **Specified behaviour, pinned by a vector.** The result and every fault
   the opcode can raise are specified normatively in `spec/xqvm/`, and a
   conformance vector covers the behaviour, failure paths included.

Rules 4 and 5 are the two halves of bounded work, and rule 5 is the one
that is easy to lose: an allocation budget bounds how much an instruction
*keeps*, not how long it takes. Rule 6 is the one that is easy to skip. A
chain does not need determinism against a single implementation; it needs
behaviour that a reader of the specification can predict, because a
divergence between the specification and an implementation is unspecified
behaviour with a plausible result, and no memory budget makes that safe.
Today that check is implemented as agreement between the Rust VM and the
Python reference interpreter, checked by the conformance suite; after
QUI-1082 removes the Python interpreter, `spec/xqvm/` and the vectors
derived from it remain the whole of rule 6.

Because the bar is a property of the instruction set, the denied set is
empty by construction. A proposed opcode that cannot clear it does not
ship, so no embedder inherits a per-opcode decision.

## Why not an allowlist

An earlier framing of this question asked which opcode families to admit
and which to gate behind a later runtime upgrade, on the theory that the
integer core is cheap and the model-building surface is not. That framing
is no longer the right one: the model-building opcodes are exactly the
reason to run XQVM on chain at all, and the two properties that made them
unsafe -- unbounded allocation and Rust-versus-Python divergence -- were
defects with owners rather than inherent properties of the family.

So a runtime should not carry a static allowlist scanned over the decoded
instruction stream. With the bar above there is no denied set for such a
scan to find, and an allowlist would be code that has to be revisited on
every addition to the instruction set, silently rejecting valid programs
until someone remembers to update it. The static half of enforcement is the
verifier, which checks properties of the program rather than membership of
its opcodes.

## Where the bar is enforced

| Layer | Runs | Enforces |
|---|---|---|
| Static | `xqvm::verifier::verify` at program submission | Structural integrity, jump targets, loop balance, register type-state, must-init, stack depth |
| Runtime | `Vm::set_memory_limit` before execution | The allocation budget, charged before each allocation |
| Runtime | `Vm::set_step_limit` before execution | The step budget, charged before each unit of work |
| Weight | The extrinsic's declared and refunded weight | The block's share of the above |

The static layer is what makes a rejection cheap: a program that cannot run
correctly is refused before it consumes execution weight. The runtime layer
is what makes admission safe: the verifier does not bound allocation or
running time, and cannot, because both depend on values computed at
runtime. Neither layer substitutes for the other.

The budgets belong in the runtime's configuration rather than in the VM
defaults. The library defaults documented in
[Limits and Errors](../xqvm/limits-and-errors.md) -- 10,000,000 steps and a
1 GiB allocation budget -- are sized for an off-chain host, and a 1 GiB
budget admits a sample of 134 million variables. A chain has to set both
from its own `Config`, sized so that the worst case a submitted program can
reach still fits the block.

## Family by family

The table below uses the same fourteen categories as the
[Instruction Set Reference](../xqvm/instructions/), so the two can be
read side by side. `Cost` is what an instruction charges against the
allocation budget; the full schedule, with rates, is in
[Limits and Errors](../xqvm/limits-and-errors.md#the-allocation-budget).
Several categories charge different rates for different opcodes, and the
cells say which. The step budget is a separate schedule with its own
rates, specified in
[`spec/xqvm/METERING.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/METERING.md);
an opcode free against one budget is not necessarily free against the
other. `ENERGY` and the four grid readers are the clearest cases: each
allocates nothing and each charges the step budget per unit of work it
does.

| Family | Count | Cost | Notes |
|---|---|---|---|
| [Control flow](../xqvm/instructions/control-flow.md) | 12 | `ITER` charges for the slice it copies -- 8 bytes per element of a `vec<int>`, a whole-model copy per element of a `vec<xqmx>`; the rest are free | Loop balance is checked statically; `RANGE` and `ITER` skip an empty body rather than running it once. The loop frame itself is uncharged but capped: the loop stack holds 8,192 frames and a program that grows past that faults with `LoopStackOverflow`. `LVAL` copies the current loop value without charging the allocation budget -- see [below](#what-is-not-enforced-today). `NOP` and `HALT` live here |
| [Register I/O](../xqvm/instructions/register-io.md) | 5 | `INPUT` and `OUTPUT` charge for the value they copy across the host boundary, at the schedule's own rates; the rest are free | Both are also bounded by the calldata and output-slot counts the runtime sets. A model copies at the whole-model rate `ITER` pays, but a `vec<int>` costs 16 bytes per element here against `ITER`'s 8: the copy rate prices a `Vec` that grew by doubling, which is what `VECPUSH` charged for, while `ITER` allocates its slice at exact capacity |
| [Stack manipulation](../xqvm/instructions/stack-manipulation.md) | 12 | Free | Depth is bounded at 8,192 items, checked statically and at runtime |
| [Arithmetic](../xqvm/instructions/arithmetic.md) | 13 | Free | Integer only; every operation is range-checked and faults rather than wrapping |
| [Comparison](../xqvm/instructions/comparison.md) | 5 | Free | |
| [Logical](../xqvm/instructions/logical.md) | 4 | Free | |
| [Bitwise](../xqvm/instructions/bitwise.md) | 6 | Free | Shift amounts outside `[0, 64)` fault |
| [Allocators](../xqvm/instructions/allocators.md) | 9 | `BQMX`, `SQMX`, `XQMX`: 8 bytes per declared variable. `BSMX`, `SSMX`, `XSMX`: 8 bytes per variable. `VEC`, `VECI`, `VECX`: free | The three charges are not the same kind. A model stores coefficients sparsely, so its charge is a proxy for what every consumer of the model has to materialise; a sample allocates a dense buffer at that rate, so its charge buys real bytes. An empty vec allocates nothing and its storage is charged as it grows |
| [Vector operations](../xqvm/instructions/vector-ops.md) | 5 | `VECPUSH`: 16 bytes per element. `SLACK`: 16 bytes per element appended, two per entry. `VECGET`, `VECSET`, `VECLEN`: free | `SLACK` appends to two vecs and is charged for both, so an entry costs 32 bytes; the three access forms neither grow nor allocate |
| [Index math](../xqvm/instructions/index-math.md) | 2 | Free | Pure index arithmetic on the stack |
| [Coefficient access](../xqvm/instructions/coefficient-access.md) | 6 | `SETLINE`, `ADDLINE`: 32 bytes per linear coefficient. `SETQUAD`, `ADDQUAD`: 48 bytes per quadratic | Reads are free, and so are writes into a sample, whose buffer was charged when it was allocated. Both rates are literals rather than derived from the host's pointer width, so a wasm32 runtime charges what the reference VM and the book state |
| [Grid operations](../xqvm/instructions/grid.md) | 5 | Free | A grid reinterprets variables the program already declared and paid for: `RESIZE` rejects extents whose product exceeds the model's size, which bounds memory. The step budget bounds the work: `ROWSUM`, `COLSUM`, `ROWFIND` and `COLFIND` charge `GRID_CELL_STEPS` per cell of the axis they scan, after validating the operand and before walking it, whether or not the scan short-circuits |
| [High-level constraints](../xqvm/instructions/constraints.md) | 8 | `EXCLUDE`: one quadratic term. `IMPLIES`: one linear and one quadratic. `REDUCE`: one variable, three quadratic terms and one linear. `ONEHOTR`, `ONEHOTC`, `EQUALITY`, `ATLEAST`, `ATLEASTW`: worst-case expansion, one linear term per variable and one quadratic term per pair | Only the last five expand; see below |
| [Energy](../xqvm/instructions/energy.md) | 1 | Free | Allocates nothing, and charges the step budget for the sample it copies and every model term it accumulates |

Ninety-three opcodes across the fourteen categories, all of them
integer-only by construction: the ISA has no floating-point type, no
floating-point opcode and no way to produce one. What the conformance
vectors pin is the stronger property that makes this useful for consensus
-- the checked-arithmetic rule, under which Rust's `i64` and Python's
unbounded integers agree on every result and fault the same way on every
overflow.

### The expanding constraints deserve a second look

Three sets are worth separating, because
[Limits and Errors](../xqvm/limits-and-errors.md#the-allocation-budget)
separates them and a weight model needs each:

- **Quadratic in a stack-controlled operand.** `ONEHOTR`, `ONEHOTC`,
  `EQUALITY`, `ATLEAST` and `ATLEASTW` write a number of coefficients that
  grows with the square of a term count the caller chooses.
- **Allocating variables.** `EQUALITY`, `ATLEAST`, `ATLEASTW` and `REDUCE`
  add variables to the model. `REDUCE` adds exactly one.
- **Both at once, from a single operand.** `EQUALITY`, `ATLEAST` and
  `ATLEASTW`.

`EXCLUDE`, `IMPLIES` and `REDUCE` charge constant amounts and do not
expand. Every opcode in the first set is charged for its worst case before
it writes anything, so the aggregate budget does bound them. A runtime that
wants a submitted program to fail early and cheaply rather than after
spending most of its budget in one instruction should consider a
per-instruction cap on top of the aggregate one. That is a pricing
decision, not an admissibility one.

## What is not enforced today

The bar above is what the instruction set is designed to, and most of it is
now backed by code. The v0.4.0 hardening pass bounded grid extents to the
allocation a program already paid for, made the coefficient rates
target-independent literals, charged the `INPUT`/`OUTPUT` copy across the
host boundary, capped the loop stack, and closed the three
Rust-versus-Python divergences in the model-building surface. Step
metering then made a step a unit of cost rather than a unit of dispatch:
every instruction charges a base cost before dispatch, the opcodes whose
work scales with caller-controlled data charge for that work before doing
it, and the forward scan of an empty-loop skip charges for each
instruction it consumes. The four grid scans charge `GRID_CELL_STEPS` per
cell of the axis they walk, which was the last place a memory bound stood in
for a work bound: `RESIZE` bounds the extent by an allocation the program
paid for, and that bounds what the grid *holds*, not what a scan over it
costs. An embedder pricing `WeightPerStep * steps` is pricing work rather
than dispatches.

The same pass closed the sample value domain, which had been documented as
a producer convention rather than a rule: `SETLINE` and `ADDLINE` now raise
`SampleOutOfDomain` for a write outside the domain the register's allocator
declared. It is a bounded per-instruction check on a value already on the
stack, so it costs nothing against the bar above, and it removes a way for
a program to hand a solver an assignment that is not an assignment.

What is left is narrow.

**A tracer copies registers outside the step budget.** The Rust VM clones
every register an instruction reads and writes around each dispatch when a
tracer is attached -- O(model) per instruction, charged nothing, because the
observable step count must not depend on whether a tracer is attached. This
is a constraint on the embedder rather than a hole in the meter: a chain runs
`NoopTracer` and the branch compiles out. A runtime that attaches a real
tracer meters untrusted programs untraced, or accounts for the tracing copy
outside the step budget. Stated normatively in `spec/xqvm/METERING.md` under
Conformance.

**`LVAL` copies without charging the allocation budget.** The step budget
now prices the copy -- `LVAL` charges for the coefficients it clones out of
a loop frame -- but the bytes that copy occupies are not charged, so a
`vec<xqmx>` iterated with `LVAL` in the body holds one unaccounted model
copy per live register beyond what `ITER` paid for. Bounded by the 256-slot
register file, so a constant factor rather than an unbounded obligation,
but one a memory bound has to include.

**The reference integration does not verify.** The fixture takes both
budgets from the caller and bounds them with `MaxStepLimit` and
`MaxMemoryLimit`, which is the shape a real pallet needs, so the runtime
layer of the boundary is now exercised end to end. The static layer is not:
it never calls `xqvm::verifier::verify` -- it decodes and runs, so every
static check is skipped and the faults those checks would have caught
surface at runtime as `ExecutionFailed`. Running the verifier on chain is
QUI-1055; pricing the budgets into a benchmarked weight rather than the
fixed placeholder is QUI-1012.

Cross-implementation agreement, meanwhile, is close to where the bar
wants it.
`make opcode-parity` holds the two opcode tables to each other,
`make conformance` runs every vector on both interpreters, and the metering
constants are mirrored value for value in `xqvm_py/metering.py` under their
own parity check. The vectors now cover the model-building surface that the
arithmetic-only suite once missed, including failure paths: constraints
without a grid, invalid grid dimensions, integer allocation, index-math
operand ordering, and accumulation overflow in energy and grid sums.

Operand validation order is part of that agreement and is now normative:
`spec/xqvm/METERING.md` fixes it under Conformance, and both VMs validate
each register completely before looking at the next. What the specification
has not yet settled is the *identity* of a mode fault. `xqvm::Error` has no
mode variant -- a `RegVal` is either a model or a sample, so the Rust VM
reports a sample in a model slot as a register-type error, where `xqvm_py`,
whose `XQMX` carries a mode flag, reports a mode error. That difference
spans every mode check rather than one opcode, predates step metering and is
tracked separately. Unsettled is not the same as permitted: `SPEC.md`'s
Faults section holds every identity normative and records this row as the
one still unresolved, so one of the two implementations is wrong and a third
must not read either spelling as settled. A runtime that surfaces fault
identity to submitters should know it is not yet uniform. See
[Conformance](conformance.md) for how the suite is structured and what
adding a vector involves.
