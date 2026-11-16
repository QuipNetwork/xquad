# XQVM Bytecode Verifier

A bytecode verifier performs static analysis over a program before execution.
It catches structural and semantic errors without running the program, enabling
early rejection at load time or at the blockchain submission boundary.

The Rust implementation is `xqvm::verifier` (exposed via `xqffi.verifier` and
`xquad.verifier` Python bindings).

---

## Architecture

Verification is structured as composable [`Phase`] implementations. Each phase
performs one focused pass over a [`Program`] and maps failures to a
[`VerifierError`]. A [`Verifier`] runs phases sequentially, stopping at the
first failure.

Verification bounds what a program can do structurally -- reachable jump
targets, well-typed registers, a balanced stack -- it does not predict what
a run will cost. A program's step count, like its memory allocation, is
data-dependent: it depends on values that only exist once calldata is bound
and the program actually runs (loop trip counts, vector lengths, the sizes
of models a program builds for itself), none of which the verifier's static
analysis has access to. A program can therefore pass verification and still
exhaust the step budget (`StepLimitExceeded`, see `spec/xqvm/METERING.md`)
or the allocation budget (`MemoryLimitExceeded`) at runtime; catching either
is the step limit's and the memory limit's job, not the verifier's.

The `scan` function is the shared kernel for Phase 1: it makes one linear pass
over the instruction bytes, builds the jump table, and returns the first
structural, jump-target, or loop-nesting violation. `Program::new` calls it to
populate the jump table (ignoring any error); `Verifier::default` calls it once
per invocation rather than three separate stream passes.

Phases 2--4 each build a control-flow graph (CFG) of basic blocks and run a
forward worklist analysis to track per-block state. The CFG correctly handles
unreachable code, conditional branches, and loop back-edges, allowing errors
that a linear scan would miss to be detected.

### Default pipeline (`Verifier::default`)

| Order | Phase | Struct | What it checks |
|-------|-------|--------|----------------|
| 1a | Structural | `StructuralPhase` | Truncated bytes, unknown opcodes |
| 1b | Jump target | `JumpTargetPhase` | Jump label >= target count |
| 1c | Loop nesting | `LoopNestingPhase` | Loop open/close balance, loop-context reads |
| 2 | Register type-state | `RegisterTypePhase` | CFG-based read-before-write, register type mismatches |
| 3 | Must-init | `UninitRegisterPhase` | CFG AND-meet: registers uninit on at least one path |
| 4 | Stack depth | `StackDepthPhase` | CFG-based stack underflow/overflow, loop balance, join-point depth mismatch |

Phases 1a--1c share the `scan` kernel and run as one combined pass. Custom
pipelines are supported via `Verifier::new().with_phase(p)`.

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
verifier tracks nesting depth and rejects:

- `NEXT`, `LVAL`, or `LIDX` reached with no open loop frame.
- Any loop frame still open at the end of the program, reported at the byte
  offset of the outermost unmatched opener.

Errors: `NoActiveLoop`, `UnmatchedLoop`.

---

Basic-block transfer and replay stop immediately after the first terminator
(`JUMP`, `JUMPI`, `RANGE`, `ITER`, `NEXT` or `HALT`), or at the next block
leader if it comes first. Bytes after a terminator do not become part of the
preceding block merely because a dead leader has no CFG edge. Dead writes
cannot initialize a live read, and dead reads cannot reject a live halt.

## Phase 2 -- Register Type-State (`RegisterTypePhase`)

A CFG-based forward analysis over `[RegType; 256]` (one slot per register).
Each register starts as `Unset`. Instructions that write a register advance its
slot to a concrete type (`Int`, `VecInt`, `VecXqmx`, `Model`, `Sample`) or
`Any`. A join can additionally produce `Grid`, `AnyVec` or `Conflict`, none of
which any instruction writes directly. Instructions that read a register are checked against a `RegTypeReq`
requirement.

At join points the per-register meet answers what both sides share, and never
more. `Any` satisfies every requirement, so answering a join with it hands out
capabilities neither branch has: `meet(Model, Sample) = Grid` and
`meet(VecInt, VecXqmx) = AnyVec` name the two pairs that do share a surface --
the grid surface and the vec surface -- and each satisfies exactly the
intersection of what its members satisfy, neither `Model`, `Sample` nor
`VecInt` on its own. Every other pair of types meets to `Conflict`, which
satisfies `NonUnset` and nothing else. Without these rows a program could
allocate a model on one branch and a sample on the other, join, and reach
`SETQUAD` with a sample while verifying clean (QUI-1160).

`Conflict` rather than `Any` for unrelated types is also what makes the meet
associative, so a join of three or more predecessors does not depend on the
order the CFG recorded their edges. With `meet(T, U) = Any` for unrelated
types, `meet(meet(Model, Sample), Int)` is `Any` while
`meet(Model, meet(Sample, Int))` is `Model`, and one branch of an unrelated
type reopens the bypass the pair rows close.

`Unset` is the absorbing bottom: `meet(Unset, T) = Unset`, including
when `T` is `Any`. It means possibly unwritten on at least one incoming
path. The descending cover relations over all ten values are:

- `Any` covers `Int`, `Model`, `Sample`, `VecInt` and `VecXqmx`.
- `Model` and `Sample` cover `Grid`.
- `VecInt` and `VecXqmx` cover `AnyVec`.
- `Int`, `Grid` and `AnyVec` cover `Conflict`.
- `Conflict` covers `Unset`.

The meet is the greatest lower bound in this order. Equal values stay
unchanged; `Any` is identity; `Unset` absorbs; the two related families
meet at their shared lower bound; all remaining initialized pairs share
only `Conflict`. These cases cover every pair. In particular, associativity
also holds for joins containing `Unset`.

An individual register can descend strictly at most four times. A transfer
replaces written coordinates with constants and carries all other coordinates
unchanged, so it is monotone in this order. Dead blocks always return top.

Register analysis could fail to terminate on some CFGs with the former
`meet(Unset, T) = Any` rule. The absorbing rule also makes the standalone
`RegisterTypePhase` stricter: a read after a partial write is rejected here.
In the default pipeline the must-init phase already rejected such reads.
Diagnostic phase, error variant and offset may change when a program has
multiple errors; they are not compatibility guarantees between releases.

Preconditions enforced:

- Reading an `Unset` register is rejected.
- Type mismatches (e.g., reading a `Model` register where `Int` is required)
  are rejected.
- The `DROP` instruction resets a register to `Unset` in the type-state model --
  a subsequent read is rejected as `ReadUnsetRegister`. At VM runtime `DROP`
  also writes `Unset`, and a subsequent `LOAD` or `OUTPUT` faults as
  `UnsetRegister`; the verifier models that behaviour faithfully.

Reachability is computed by traversing CFG edges from program entry. Dead
blocks contribute the meet identity without applying writes and are skipped
by instruction replay. A reachable block whose input happens to equal
`[Any; 256]` still applies its writes and checks its reads; lattice top does
not encode reachability.

Errors: `ReadUnsetRegister`, `RegisterTypeMismatch`.

---

## Phase 3 -- Must-Init Analysis (`UninitRegisterPhase`)

A CFG-based forward AND-meet analysis over a 256-bit bitmap (four `u64` words).
Bit `r` is set if register `r` is definitely initialized on **every** path to
the current program point.

At program entry all bits are clear (all registers unwritten). The boundary
value is `[0; 4]`; the lattice top is `[u64::MAX; 4]` (identity for AND-meet).

At join points: `meet(a, b) = a & b`. A register written on only one branch
has its bit cleared at the join, so subsequent reads are flagged.

The initialization projection of Phase 2 maps `Unset` to false and every
other type to true. It preserves meet (AND), entry, dead-block top, and
all writes: `DROP` clears its bit and every other register write sets it.
Both phases use the same CFG, reachability, block ranges and write effects.
This phase therefore agrees on possible missing writes and remains available
independently in custom pipelines.

The same CFG reachability rule applies here. Dead blocks contribute the
identity without applying writes and are skipped by replay. Reachable blocks
are checked even when all 256 registers are initialized, which equals `top`.
A dead `DROP` cannot make another dead block appear reachable.

Error: `ReadUnsetRegister`.

---

## Phase 4 -- Stack Depth (`StackDepthPhase`)

A CFG-based forward analysis over abstract stack depth. Each basic block is
characterised by a `BlockEffect` (minimum entry depth required and the output
depth or depth delta at exit). The worklist uses min-meet to propagate the
most conservative depth to successors. Both the main walk and the separate
loop-body walk must bound decreasing-cycle work by the number of CFG nodes,
rather than by the initial stack depth. A decreasing witness with at least
one edge per CFG node contains a negative cycle and propagates `Underflow`.
A fixed-depth effect such as `SCLR` starts a new witness: lowering a cycle's
depth once by clearing it does not imply eventual underflow.

The verifier also tracks the peak within every reachable basic block, not
only its entry and exit. Before the first `SCLR`, the peak is relative to
the incoming depth; after a clear it is absolute. Later pops or clears do
not erase either peak. A transient depth above 8,192 is rejected even if
the block exits empty; exactly 8,192 remains valid.
Using the min-meet entry for this peak is sound because the preceding
`StackDepthMismatch` check requires equal depths on all reachable incoming
edges, including loop-body joins and the implicit program-entry edge.

### Check order

1. **`LoopStackImbalance`** -- detected via a separate bounded walk over each
   loop body (ignoring its structured back-edge and skip-edge). If the exit depth of the `NEXT` block
   differs from the depth at the matching `RANGE`/`ITER` opener, the loop body
   has a non-zero net stack effect.

2. **`StackDepthMismatch`** -- after the worklist converges, every join block
   (2+ incoming edges) is examined. The arrival depths of all reachable
   incoming edges are collected and compared, program entry contributing
   depth 0. If any two differ, the stack is in
   an undefined state at the join point. Blocks are checked in program order
   (ascending byte offset) for deterministic error reporting.

   Program entry counts as an incoming edge of the entry block, arriving at
   depth 0. So a back-edge targeting the entry block makes it a join block with
   two incoming edges -- entry at depth 0, and the back-edge at whatever depth
   the loop body leaves -- even though it has only one predecessor *block*.
   Counting predecessor blocks alone would let `.0: PUSH 1 / JUMP .0 / HALT`
   verify clean and then overflow the stack at runtime.

3. **`StackUnderflow`** / **`StackOverflowRisk`** -- an instruction whose
   pops take the depth below 0 is flagged under the
   [Stack underflow rule](#stack-underflow-rule), and a block whose depth
   exceeds 8192 is flagged as `StackOverflowRisk`.

### Stack underflow rule

An instruction that pops `p` values needs at least `p` values on the stack
when it runs, whatever it pushes afterwards. The analysis applies each
instruction's pops, checks the running depth, and only then applies its
pushes, so a block's minimum entry depth is the lowest depth any of its
pops reaches. An opcode that both pops and pushes is held to its full pop
count: `PUSH 1 / SWAP / HALT`, `COPY / HALT` and `PUSH 1 / PUSH 2 / IDXGRID
/ HALT` are all rejected with `StackUnderflow`, although `SWAP` leaves the
depth unchanged, `COPY` raises it, and the running net depth of the third
never goes negative.

An underflow stands once it has happened. A later `SCLR` resets the depth
for the instructions after it but does not clear an underflow before it,
because at run time that instruction has already faulted: `SCLR / POP /
SCLR / HALT` and `SCLR / PUSH 1 / SWAP / SCLR / HALT` are rejected.

The pop and push counts are the `Pops` and `Pushes` columns of the table
under [Per-Opcode Stack Effects](#per-opcode-stack-effects).

### Loop handling

`RANGE`/`ITER` emit two CFG edges: a fall-through to the loop body and a
skip-edge to the block after `NEXT` (empty-loop path). `NEXT` emits a
back-edge to the loop body start and a fall-through to the post-loop block.

`LoopStackImbalance` is reported before `StackDepthMismatch`, so if a loop is
imbalanced the depth-mismatch check at the loop header is never reached.

### `SCLR` semantics

`SCLR` resets the abstract depth unconditionally to 0 (`StackEffect::Reset`).
If the entry depth was N > 0 and the exit depth is 0, this is reported as
`LoopStackImbalance` if inside a loop, or detected by `StackDepthMismatch` at a
downstream join if the reset creates a discrepancy.

Errors: `LoopStackImbalance`, `StackDepthMismatch`, `StackUnderflow`,
`StackOverflowRisk`.

---

## Deterministic analysis budgets

CFG edges preserve insertion order. A duplicate edge changes neither the
successor nor predecessor list. Insertion checks the source's successor
list once and appends both directions only for a new edge; it does not
search the destination's growing predecessor list. Bytecode blocks have
at most two successors, so edge deduplication takes at most two comparisons
per insertion, including graphs where every block targets the same join.

The generic Rust `dataflow::solve(analysis, cfg, max_visits)` returns
`Result<DataFlowResult<...>, SolveError>`. Every output starts at lattice
top, including the boundary node. The boundary value seeds only that node's
input before its transfer runs. This applies in both flow directions.

For N nodes, E edges and at most H strict descents per output, the FIFO
worklist visits at most `N + H * E` nodes: N initial visits, then at most H
enqueue attempts per edge. Duplicate pending nodes are suppressed. Register
analysis uses H = 4 * 256 = 1024; must-init uses H = 256. All bound arithmetic
is checked. Bytecode CFGs have at most two outgoing edges per node, so at
N = 2048 their respective limits are 4,196,352 and 1,050,624 visits.

Each stack-depth walk has its own `N * (3 * N + 3)` visit limit, independent
of initial depth. There is one main walk plus one walk per structured loop;
these costs must be added. The depth algorithm resets witnesses at fixed
outputs, accelerates decreasing cycles and preserves absorbing underflow.

A visit includes transfer work and incoming-edge folds; materializing the
result also folds incoming edges. These are visit bounds, not linear-time
or wall-clock guarantees. Register operations process 256 coordinates, and
loop-body walks can make total depth work larger than a single walk.

Exhaustion returns `VerifierError::AnalysisBudgetExceeded` without partial
facts. It indicates an analysis contract or bound defect, not a valid verdict
about the program; callers must refuse admission and investigate. No retry
through an unbounded solver is provided. Custom analyses must supply their
own justified bound; a transfer callback itself must also terminate.

The Rust API change is breaking: callers add the visit limit and handle
`SolveError`; exhaustive matches on `VerifierError` add the new variant.
Neither `Analysis` nor `Lattice` requires a new method. `RegType` has no new
variant, but its `Unset` meet and standalone phase behavior change as above.

---

## Error Variants

| Variant | Phase | Description |
|---------|-------|-------------|
| `AnalysisBudgetExceeded` | 2--4 | Derived analysis work bound exhausted; no byte offset |
| `TruncatedInstruction` | 1a | Instruction stream ends mid-operand |
| `BadOpcode` | 1a | Opcode byte does not map to a known instruction |
| `UndefinedJumpTarget` | 1b | Jump references label id >= target count |
| `NoActiveLoop` | 1c | `NEXT`, `LVAL`, or `LIDX` with no open loop frame |
| `UnmatchedLoop` | 1c | Loop frame still open at end of program |
| `ReadUnsetRegister` | 2 or 3 | Register read before any write (or before write on all paths) |
| `RegisterTypeMismatch` | 2 | Register holds wrong type for the instruction |
| `LoopStackImbalance` | 4 | Loop body entry depth != exit depth |
| `StackDepthMismatch` | 4 | Two paths arrive at a join with different stack depths |
| `StackUnderflow` | 4 | Stack depth would go negative at a reachable instruction |
| `StackOverflowRisk` | 4 | Stack depth would exceed 8192 |

---

## Per-Opcode Stack Effects

The stack-effect column of the `opcodes!` x-macro is the single source of
truth, checked at build time against `stack_pop` and `stack_push` in
`xqvm/opcodes.yaml`. The table below is derived from it. `Pops` is checked
against the depth before `Pushes` is applied, per the [stack underflow
rule](#stack-underflow-rule). `Reset` means `StackEffect::Reset` -- depth is
set to 0 unconditionally, with no fixed pop or push count.

| Mnemonic | Pops | Pushes | Notes |
|----------|------|--------|-------|
| `TARGET` | 0 | 0 | |
| `JUMP1` | 0 | 0 | |
| `JUMPI1` | 1 | 0 | Pops condition |
| `JUMP2` | 0 | 0 | |
| `JUMPI2` | 1 | 0 | Pops condition |
| `LIDX` | 0 | 0 | |
| `LVAL` | 0 | 0 | |
| `NEXT` | 0 | 0 | |
| `RANGE` | 2 | 0 | Pops start, count |
| `ITER` | 2 | 0 | Pops start_idx, end_idx |
| `LOAD` | 0 | 1 | |
| `STOW` | 1 | 0 | |
| `DROP` | 0 | 0 | |
| `INPUT` | 1 | 0 | Pops slot index |
| `OUTPUT` | 1 | 0 | Pops slot index |
| `POP` | 1 | 0 | |
| `PUSH1`..`PUSH8` | 0 | 1 | |
| `SCLR` | Reset | Reset | depth -> 0 |
| `SWAP` | 2 | 2 | |
| `COPY` | 1 | 2 | |
| `ADD` | 2 | 1 | |
| `SUB` | 2 | 1 | |
| `MUL` | 2 | 1 | |
| `DIV` | 2 | 1 | |
| `MOD` | 2 | 1 | |
| `SQR` | 1 | 1 | |
| `ABS` | 1 | 1 | |
| `NEG` | 1 | 1 | |
| `MIN` | 2 | 1 | |
| `MAX` | 2 | 1 | |
| `INC` | 1 | 1 | |
| `DEC` | 1 | 1 | |
| `BITLEN` | 1 | 1 | |
| `EQ` | 2 | 1 | |
| `LT` | 2 | 1 | |
| `GT` | 2 | 1 | |
| `LTE` | 2 | 1 | |
| `GTE` | 2 | 1 | |
| `NOT` | 1 | 1 | |
| `AND` | 2 | 1 | |
| `OR` | 2 | 1 | |
| `XOR` | 2 | 1 | |
| `BAND` | 2 | 1 | |
| `BOR` | 2 | 1 | |
| `BXOR` | 2 | 1 | |
| `BNOT` | 1 | 1 | |
| `SHL` | 2 | 1 | |
| `SHR` | 2 | 1 | |
| `BQMX` | 1 | 0 | Pops size |
| `SQMX` | 1 | 0 | Pops size |
| `XQMX` | 2 | 0 | Pops size, k |
| `BSMX` | 1 | 0 | Pops size |
| `SSMX` | 1 | 0 | Pops size |
| `XSMX` | 2 | 0 | Pops size, k |
| `VEC` | 0 | 0 | |
| `VECI` | 0 | 0 | |
| `VECX` | 0 | 0 | |
| `VECPUSH` | 1 | 0 | |
| `VECGET` | 1 | 1 | Pops index, pushes element |
| `VECSET` | 2 | 0 | Pops value, index |
| `VECLEN` | 0 | 1 | |
| `SLACK` | 2 | 0 | Pops capacity, start_index |
| `IDXGRID` | 3 | 1 | Pops cols, col, row; pushes index |
| `IDXTRIU` | 2 | 1 | Pops j, i; pushes index |
| `GETLINE` | 1 | 1 | Pops i, pushes value |
| `SETLINE` | 2 | 0 | Pops value, i |
| `ADDLINE` | 2 | 0 | Pops delta, i |
| `GETQUAD` | 2 | 1 | Pops j, i; pushes value |
| `SETQUAD` | 3 | 0 | Pops value, j, i |
| `ADDQUAD` | 3 | 0 | Pops delta, j, i |
| `RESIZE` | 2 | 0 | Pops cols, rows |
| `ROWFIND` | 2 | 1 | Pops value, row; pushes col |
| `COLFIND` | 2 | 1 | Pops value, col; pushes row |
| `ROWSUM` | 1 | 1 | Pops row, pushes sum |
| `COLSUM` | 1 | 1 | Pops col, pushes sum |
| `ONEHOTR` | 2 | 0 | Pops penalty, row |
| `ONEHOTC` | 2 | 0 | Pops penalty, col |
| `EXCLUDE` | 3 | 0 | Pops penalty, j, i |
| `IMPLIES` | 3 | 0 | Pops penalty, j, i |
| `EQUALITY` | 2 | 0 | Pops penalty, target |
| `ATLEAST` | 2 | 0 | Pops penalty, k |
| `ATLEASTW` | 2 | 0 | Pops penalty, k |
| `REDUCE` | 3 | 1 | Pops P_aux, var_b, var_a; pushes aux index |
| `ENERGY` | 0 | 1 | Reads model and sample registers; pushes energy |
| `NOP` | 0 | 0 | |
| `HALT` | 0 | 0 | |