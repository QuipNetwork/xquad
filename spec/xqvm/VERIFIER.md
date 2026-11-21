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
| 1d | Jump span | `JumpSpanPhase` | Jump target in a different loop body from the jump |
| 2 | Register type-state | `RegisterTypePhase` | CFG-based read-before-write, register type mismatches |
| 3 | Must-init | `UninitRegisterPhase` | CFG AND-meet: registers uninit on at least one path |
| 4 | Stack depth | `StackDepthPhase` | CFG-based stack underflow/overflow, loop balance, join-point depth mismatch |

Phases 1a--1c share the `scan` kernel and run as one combined pass; 1d is a
second lexical pass over the same bytes. Custom pipelines are supported via
`Verifier::new().with_phase(p)`.

---

## Phase 1 -- Structural, Jump-Target, Loop-Nesting, Jump-Span

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

### 1d: Jump span

Every program position has a *body*: the innermost lexical loop body that
encloses it, by `RANGE`/`ITER`..`NEXT` pairing. A `RANGE` or `ITER`
instruction belongs to the *enclosing* body; its matching `NEXT` belongs to
the *inner* body it closes. A `JUMP1`, `JUMPI1`, `JUMP2`, or `JUMPI2` whose
target lies in a different body from the jump itself is rejected -- whether
it jumps into a loop, out of one, or into a sibling loop.

Why this is enough: with no cross-body jumps, the runtime loop stack equals
the lexical nesting at every pc (a skipped loop resynchronizes by a lexical
scan), so a jump that stays within one body cannot change the active loop
set between source and target. A cross-body jump would let the VM carry a
loop stack the lexical structure does not predict, which the other phases
assume it does. The rule deliberately also rejects break/continue style
exits; the toolchain (`xqcp`) never emits them as raw cross-body jumps, and
its "if inside a loop" and "loop inside an if" output stays within one body.
Reachability is not consulted.

The phase runs as its own lexical pass after the combined Phase 1 scan.
Its memory is proportional to the number of loop boundaries and jumps, not
to the number of instructions.

Error: `JumpAcrossLoopBoundary` (carries the jump's and the target's byte
offsets).

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

Each reachable block has one exact entry height. A `BlockEffect` records
its minimum required input and its exit delta or fixed output. The verifier
assigns heights on first reach, processes each block once and compares every
incoming edge with the assigned height. It never iterates minimum depths or
runs separate loop-body walks.

The verifier tracks the peak within every reachable basic block, not only
its entry and exit. Before the first `SCLR`, the peak is relative to the
incoming height; after a clear it is absolute. Later pops or clears do not
erase either peak. A transient height above 8,192 is rejected even if the
block exits empty; exactly 8,192 remains valid.

### Deterministic check order

The traversal is FIFO, seeded with program entry at height zero. Dense node
indices follow CFG node insertion order. Successors are examined in CFG
edge insertion order: conditional jump target before fall-through,
`RANGE`/`ITER` body before skip, and `NEXT` back-edge before fall-through.
Neither hash-map iteration nor the order of predecessor sets controls a
verdict. A node enters the queue only on its first height assignment.

For each dequeued block:

1. Apply its effect; a failed minimum-input check or an intrinsic post-reset
   underflow reports `StackUnderflow` at the block start.
2. Check entry height, exit height, then intra-block peak in that order;
   the first value above 8,192 reports `StackOverflowRisk` at the block start.
3. Examine outgoing edges in the order above. Assign an unseen successor's
   height and enqueue it. A different height at an assigned successor
   reports `StackDepthMismatch`, with the assigned height as `depth_a` and
   the incoming height as `depth_b`. A conflicting matching `NEXT` back-edge
   instead reports `LoopStackImbalance`, with its opener offset and the
   assigned/incoming heights as entry/exit.

Stop at the first witnessed fault or edge conflict. This replaces the old
whole-program error-class priority; rejected programs can change diagnostic
variant or offset. The accepted program set is unchanged.

The implicit program-entry edge always has height zero. Back-edges to entry
are compared with it, never ignored. A single-block CFG receives the same
check. Dead blocks, including unreachable leaders and loop openers, are not
processed. A `SCLR` output is fixed, but its incoming edge heights must still
agree. Underflow before a reset remains an error.

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

All these edges participate in exact-height consistency, including the
post-loop `after_next` block. Arbitrary jumps leaving or re-entering a body
remain ordinary CFG edges; no lexical confinement rule is added. A matching
`NEXT` back-edge must restore the body's entry height, which enforces zero
net effect without a separate body simulation.

### `SCLR` semantics

`SCLR` resets the abstract depth to zero (`StackEffect::Reset`). It neither
erases a previous underflow nor exempts a join from equal incoming heights.
A reset inside a loop can therefore produce a `LoopStackImbalance` when the
matching `NEXT` edge disagrees with the body's assigned entry height.

### Acceptance equivalence

If the former minimum-depth verifier accepts, every reachable value is
finite, every reachable incoming edge agrees (including the implicit entry
edge), and every minimum-input and transient-peak bound holds. These values
form an exact solution. Induction over first discovery shows that the new
traversal assigns those same values, including `Fixed`/`SCLR` outputs, so it
accepts.

Conversely, accepted exact heights satisfy every old min-meet equation.
Induction over the old worklist shows that no propagated finite value can
differ, and no negative cycle can produce underflow. For an old body walk
with a reachable opener, its entry equals the full-CFG body height. Every
edge it follows preserves the same solution, including escapes and re-entry.
Removing its root back-edge and `after_next` only removes paths. Any reached
predecessor of the body start has the body-entry height at its output;
unreached predecessors contribute `Top`. Thus its loop-effect check cannot
fail. An unreachable opener seeds only `Top`, and its body cannot fail that
check either. Exact-height acceptance also preserves the old minimum-input,
entry/exit bound and O7 peak checks. No assumption about nesting depth or
loop count is needed in either direction.

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
enqueue attempts per edge. Duplicate pending nodes are suppressed. This is
the public generic solver's scheduling bound, not the built-in sparse
algorithm's whole-work bound. All bound arithmetic is checked.

### Sparse built-in register and must-init propagation

Each block's register transfer independently overwrites some coordinates
with constants and leaves the rest unchanged. Decode the writes once and
retain the last write to each coordinate for transfer purposes; replay still
checks every original instruction in order. Reachability is computed
separately, and dead blocks never apply writes. A reachable all-top block
still applies its constant writes.

The built-ins cache incoming facts and propagate only changed output
coordinates. `meet(old contribution, new contribution) = new contribution`
when outputs descend, so meeting a new contribution into the cached input
is exactly the same as folding all current predecessors. The boundary's
incoming facts remain constant; incoming back-edges never overwrite them.
A written coordinate has a fixed output and needs no transfer on later
input changes. An unwritten coordinate copies its changed input with
constant scalar work. The generic `Analysis`, `Lattice` and `solve` APIs and
their backward behavior are unchanged.

Dense indices follow CFG insertion order. Initialise reachable nodes in that
order and coordinates from 0 through 255. Outputs start at top and only
strict descents enter a FIFO of `(node, coordinate)` pairs. At most one pair
is pending for a given coordinate; a pending pair reads the latest output.
Forward changed values to successors in CFG insertion order. Final error
replay remains in ascending byte offset and instruction order.

A register coordinate descends at most four times; an initialization bit
clears at most once. With R = 256, register propagation performs at most
`4 * R * N` constant-work coordinate events and `4 * R * E` coordinate edge
updates. Must-init uses at most `R * N` events and `R * E` bit updates; its
AND meet is applied only to a changed bit. These bounds include initial
output descents and hold for coalesced pending updates as well. Separate
checked budgets enforce event and edge-update ceilings. There is no full
256-coordinate transfer or predecessor refold per event.

At N = 2048 and E = 4096, the register ceilings are 2,097,152 events and
4,194,304 edge updates; must-init ceilings are 524,288 events and 1,048,576
bit updates. Initialising and materialising facts adds `O(R * N)` work,
write extraction/replay adds `O(B)`, and dense graph indexing adds
`O((N + E) log N)`. No term multiplies edge updates by R again or rescans a
long block on each incoming change. Dictionary accesses in CFG/context
construction remain separate from these exact scalar-operation counts;
these are not reference-machine weight or wall-clock guarantees.

### Exact-height depth work

The exact-height checker has a node budget of N and an edge budget of E.
It assigns each reachable node once and checks each outgoing edge once, so
these ceilings follow directly from the algorithm. Indexing offsets into
dense arrays takes `O((N + E) log N)` comparisons; the depth traversal takes
`O(N + E)` work and space. With N = 2048 and E <= 4096, there are at most
2048 block transfers and 4096 edge checks, independent of initial height,
loop count and nesting depth. Budgets detect internal invariant failures;
they are not a numerical cutoff for rejecting otherwise valid programs.

Upstream verification adds no nesting cap. Loop pairing uses one explicit
heap stack and one linear instruction scan. Nesting depth D is at most the
instruction count; its storage is `O(D)`, not a factor multiplying graph
work. Hosts may impose their own smaller depth limit.

Generic-solver visits still include transfer work and incoming-edge folds;
materialising its result also folds incoming edges. Its visit bound alone
is not a whole-program wall-clock guarantee. A callback must terminate.

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

## Historical minimum-walk oracle

The former algorithm is retained only in tests for equivalence and mutation
controls. It is not used by default verification or a public depth phase.
Its per-walk termination proof below does not provide an adequate aggregate
cost bound: L body walks could cost `(L + 1) * N * (3 * N + 3)` visits plus
`O(L * N)` setup. The exact-height checker removes those walks entirely.

Partition a FIFO walk into rounds. A round drains exactly the nodes queued
at its start; later arrivals belong to the next round. Queue membership
suppresses duplicates, so each round visits at most N nodes. A newly
improved node is processed no later than the following round.

Treat the boundary output and every reachable `Fixed(k)` output as sources.
A non-top value reaches every reachable node within N rounds along a simple
path. A fixed output is then either `Known(k)` or `Underflow`; it can never
produce a different finite source value. An input below its minimum or an
already underflowed input makes it absorbing `Underflow`, even for a reset.

For finite propagation, the witness counts edges since the last fixed
source. A fixed effect resets this count, not an underflow. After N further
rounds, all walks of fewer than N edges from all activated sources have
contributed. Competing paths cannot delay this: the domain is totally
ordered, transfers preserve the minimum, and retaining a smaller input
cannot hide a smaller output. An accelerated underflow is smaller still.
This argument includes short walks with repeated nodes, not just simple
paths; the length limit bounds how many rounds they need.

A later strict finite improvement therefore cannot have a witness shorter
than N edges. The algorithm accelerates such an improvement to `Underflow`
before propagation. Underflow then spreads in at most N rounds and cannot
be reset or replaced by a shorter finite witness. Three additional rounds
cover processing the endpoint of a witness and draining pending nodes,
giving `N * (3 * N + 3)` visits.

The boundary input is constant and incoming back-edges to it are ignored.
A loop-body walk also skips its `after_next` node. Both restrictions remove
walks from this argument; they cannot add delayed sources or improvements.
All bound arithmetic is checked, and exhaustion returns the dedicated error.

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
| `JumpAcrossLoopBoundary` | 1d | Jump target lies in a different loop body from the jump |
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