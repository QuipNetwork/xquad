# XQVM Technical Specification

## X-Quadratic Virtual Machine

A specialized virtual machine for encoding, verifying, and decoding quantum optimization problems. Provides a unified instruction set for manipulating quadratic models across variable domains (binary, spin, discrete).

## Three-Program Architecture

Each optimization problem is defined by three independent programs sharing the same instruction set:

- **Encoder** — Transforms problem-specific input into an XQMX model suitable for quantum optimization.
- **Verifier** — Validates solution quality and constraint satisfaction.
- **Decoder** — Transforms quantum solution back into problem-specific output.

Programs execute independently with no shared state. Communication occurs only through calldata (`input`) and results (`output`).

---

## Machine State

```python
{
    "pc": 0,                  # Program counter
    "stack": [],              # Integer-only stack

    "registers": {            # slot (0-255) → value
        0: 42,                # int
        1: [1, 2, 3, 4],      # vec
        2: {                  # xqmx
            "mode": "model",          # "model" | "sample"
            "domain": [0, 1],         # {0,1} | {-1,+1} | {0,...,k-1}
            "size": 9,
            "rows": None,
            "cols": None,
            "linear": {},             # sparse: var_index → value
            "quadratic": {}           # sparse: (i,j) → value, i <= j
        }
    },

    "jc": {
        "targets": {},        # target_id → pc
        "loop_stack": []      # loop state stack (LIFO)
    },

    "input": {},              # calldata: slot → value
    "output": {}              # results: slot → value
}
```

---

## Determinism

An instruction's result is a function of the program, the calldata, and the machine state defined above -- and of nothing else. No instruction reads host state that the calldata did not carry: not a clock, not a random source, not an environment variable, not a filesystem or a network, and not any counter the host advances outside the program's own execution. The instruction set contains no opcode that can, and this is a property of the instruction set rather than a configuration a host selects; an operation that would need one does not become an opcode.

The rule is normative and has no implementation-defined alternative: two conforming implementations running the same program against the same calldata produce the same outputs, the same step and allocation charges, and the same fault identity if the program faults. Without it a program's result would stop being a function of its inputs, and every other guarantee stated here rests on that one. A host that replays a submitted program on more than one machine and compares the results -- which is what a chain does -- would find that an instruction reading anything the machines do not share splits them rather than merely complicating a debugger.

One host configuration currently escapes the fault-identity half of that rule, and exactly one: an allocation budget large enough to pay for an allocator size past a 32-bit target's address space. [Allocation budget](#allocation-budget) states the bound, what happens above it, and the ticket that closes it. No default reaches it, and the rule above is what that gap is measured against rather than something the gap qualifies.

Three properties this rule leans on are specified in their own sections rather than restated here: checked arithmetic, so that a result leaving the value range faults instead of becoming target-dependent, in [Type System](#type-system); a fixed iteration order for anything that walks a collection, in [HLF.md](HLF.md#accumulation-order); and fault identity, so that the same program fails by the same name everywhere, in [Faults](#faults), which states that rule and records the one identity not yet settled under it. The step and allocation schedules in [METERING.md](METERING.md) are observable for the same reason, and are derived from the program and its data alone rather than from the executing target's pointer width or measured speed.

---

## Type System

### Stack

- Integer only. All primitive operations work on integers.
- **Value type:** signed 64-bit integer. Valid range `[-2^63, 2^63 - 1]`.
- **Overflow:** **every operation on i64 values the VM performs on a program's behalf** raises `ArithmeticOverflow` (same class as `DivisionByZero`, `StackOverflow`) when its result would fall outside the i64 range. The rule is stated over operations rather than over a list of opcodes because a list has never been the boundary. It covers the arithmetic, shift, and `INC`/`DEC`/`NEG`/`ABS`/`SQR` opcodes, model coefficients and the reductions over them, and integer values entering the VM through `INPUT`; it covers equally the arithmetic that no opcode names -- loop control (a `RANGE` bound, a loop index advance, the index sequence `SLACK` appends), index construction (`IDXGRID`, `IDXTRIU`), and grid address computation. The rule is normative and has no implementation-defined alternative: an implementation backed by fixed-width 64-bit integers performs checked arithmetic and raises rather than wrapping, and an implementation backed by unbounded integers range-checks every intermediate rather than only the value it finally produces.
  - The test is on the **result**, not on the intermediate hardware operation. `i64::MIN / -1` raises, because the quotient 2^63 is not representable; `i64::MIN % -1` yields `0`, which is, even though a fixed-width machine computes it through the same overflowing division.
  - Where a value is built from several operations -- a constraint expansion's coefficient, an energy accumulation -- **each** step is checked, so a computation that leaves the range on the way to an in-range answer raises rather than silently recovering. This is why the accumulation order in [HLF.md](HLF.md#accumulation-order) is normative.
- Maximum depth: 8192 (2^13)

### Registers (`r0`–`r255`)

- 8-bit slot ID (0-255)
- Types: `int` | `vec` | `xqmx`
- No pointers, no type coercion
- Only `int` registers exchange with stack via `LOAD`/`STOW`
- `vec` and `xqmx` accessed only through specialized opcodes

### `vec`

- Homogeneous dynamic array: `vec<int>`, `vec<xqmx>` with support for nesting (`vec<vec<int>>`)
- Element type inferred and locked on first push, or explicit via `VECI`/`VECX` opcodes. Type validation on mutate operations
- Tracks length and capacity

### `xqmx`

- Sparse x-quadratic matrix
- **Mode:** `model` (linear & quadratic are hamiltonian coefficients) or `sample` (linear are variable assignments, quadratic is nil)
- **Domain:** `{0, 1}` binary, `{-1, +1}` spin, `{0, ..., k-1}` discrete, where `k` is the number of values and not a half-width, so a discrete variable is a case index (`spec/xqsa/DOMAINS.md` states the same domains)
- **Sample writes are domain-checked.** `SETLINE` and `ADDLINE` raise `SampleOutOfDomain` when the value they would store is not a member of the register's domain. `ADDLINE` checks the result of the addition rather than the delta, so a write that leaves the domain raises while one that returns to it succeeds. `IndexOutOfBounds` and `ArithmeticOverflow` both take precedence
- **Model coefficients are unbounded.** A model's `linear` entry is a bias and not an assignment, so its magnitude has nothing to do with the values the variable may take. The check above applies in sample mode only
- **The invariant is on the write, not on the register.** A host that installs a sample through calldata bypasses both opcodes, and `GETLINE` then reads whatever it installed. The reference implementations' host bindings validate at that boundary, but the guarantee this specification makes is about `SETLINE` and `ADDLINE`, not about what a sample register can hold
- **Dimension:** `size` (total linear variables), optional `rows`/`cols` for grid layout
- **Storage:** Sparse tables for `linear` and `quadratic`
- Constraint opcodes (ONEHOTR, ONEHOTC, EXCLUDE, IMPLIES, EQUALITY, ATLEAST, ATLEASTW, REDUCE) are only valid in model mode
- ENERGY computes the Hamiltonian energy of a sample against a model
- **Reductions over the sparse tables visit terms in sorted key order**, never in insertion order; see [HLF.md](HLF.md#accumulation-order)

---

## Runtime Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Stack depth | 8192 (2^13) | `StackOverflow` if exceeded. |
| Stack value range | `[-2^63, 2^63 - 1]` | Signed 64-bit. `ArithmeticOverflow` if exceeded. |
| Register slots | 256 (r0–r255) | 8-bit addressing. |
| Target IDs | 0–65535 | `u8` via `JUMP1`/`JUMPI1`, `u16` big-endian via `JUMP2`/`JUMPI2`. Sequential assignment during pre-scan. |
| Loop nesting | 8192 (2^13) | `LoopStackOverflow` if exceeded. A `RANGE` or `ITER` that would push a frame past the cap raises and pushes nothing. The bound mirrors the stack-depth cap: a loop frame is a per-run allocation the program controls, so leaving it uncapped leaves one growth path outside every budget. |
| Step budget | Host-supplied | Bounds the work one run may do, counted in steps rather than instructions; `StepLimitExceeded` when exhausted. See [Step budget](#step-budget) and [METERING.md](METERING.md). |
| Allocation budget | Host-supplied | Bounds the bytes one run may ask the host to allocate; `MemoryLimitExceeded` when exhausted. See [Allocation budget](#allocation-budget). |
| XQMX size | Bounded by the allocation budget | No fixed spec limit. A negative size raises `InvalidAllocation`; a non-negative size that does not fit the remaining budget raises `MemoryLimitExceeded`. See [Allocator validation order](#allocation-budget) for the one case in which a non-negative size raises `InvalidAllocation` instead. |
| Program length | Implementation-defined | No spec limit. |

### Step budget

Execution is bounded by a **step budget**: a maximum number of steps one run may consume, fixed by the host before the run starts. A host may leave it unbounded; how that is spelled is an interface question rather than a semantic one.

The budget is normative rather than an implementation convenience. An embedder that meters execution -- the Substrate pallet pre-charges a caller for the requested limit, refunds the difference, and publishes the number consumed -- prices a quantity that has to be defined here, or two conforming implementations charge a caller differently for the same program.

- **A step is a unit of work, not an instruction.** Every instruction charges a base cost before dispatch, and the opcodes whose work scales with data the program controls charge additional units before doing that work. What each opcode charges, and when, is specified in [METERING.md](METERING.md), which is normative and is the only place the cost model is stated.
- **The budget is tested before the charge lands.** An instruction whose charge would carry the counter past the limit raises `StepLimitExceeded` and does not execute -- so a run can be refused while its consumed count is still below the limit, whenever the refused charge is larger than the remaining budget. `NOP`, `TARGET` and `HALT` charge the base cost like any other instruction; an instruction reached by a jump or a loop back-edge charges each time it is reached; and an instruction that faults has been charged before it faults.
- **Reaching the end of the instruction stream is not a step.** The fetch that finds no instruction ends the run before anything is charged, so a program that runs off the end having spent exactly `limit` steps succeeds rather than raising. A program with no instructions therefore runs successfully under a budget of `0`; a program with one instruction does not.
- **The counter is per run.** It is zero at the start of every run and holds the number of steps consumed when the run ends, whether it ended by `HALT`, by end of stream, or by a fault.

The step counter is distinct from the instruction count an implementation may expose for tracing. Steps are the metered quantity an embedder prices; the instruction count is the dispatch ordinal. They are equal only for a program whose instructions all charge exactly the base cost.

Error: `StepLimitExceeded`, naming the limit, the charge that was refused, and the steps already consumed.

### Allocation budget

Execution is bounded by an **allocation budget**: a maximum number of bytes one run may ask the host to allocate, fixed by the host before the run starts. Unlike the step budget it has no unbounded spelling -- a host that wants no practical bound passes a large value.

**Charge before allocating.** An opcode that is about to allocate charges the budget first and allocates only if the charge succeeds. Letting the allocator fail instead is not equivalent: inside a Wasm runtime a failed allocation traps the whole execution rather than returning a fault the host can report, and that is the case the budget exists to prevent.

**The budget counts cumulatively, not live.** Every charge adds and nothing is ever refunded. Overwriting a register, clearing one, or leaving a loop returns no bytes. The budget bounds what a run asks for over its whole lifetime rather than what it holds at any instant, so `MemoryLimitExceeded` means "this run has asked for too much", not "this run is holding too much".

**The counter is per run**, reset to zero at the start of every run alongside the step counter.

**Expansions are charged their worst case, before they expand.** A constraint expansion over `n` terms is charged for `n` linear entries and one quadratic entry per unordered pair, whether or not it writes that many -- repeated indices collide on one key and cancelling coefficients are removed again. Charging the worst case up front is what makes the charge a bound on the work rather than a measurement taken after it.

Charging points and counting units:

| Opcode | Charged for | Unit |
|--------|-------------|------|
| `BQMX`, `SQMX`, `XQMX`, `BSMX`, `SSMX`, `XSMX` | the allocated `size` | variable |
| `SETLINE`, `ADDLINE` on a model register | one entry | linear entry |
| `SETQUAD`, `ADDQUAD` on a model register | one entry | quadratic entry |
| `VECPUSH` | one element | vec element |
| `SLACK` | two entries per slack variable it appends | vec element |
| `ITER` | the elements copied into the loop frame | see below |
| `INPUT`, `OUTPUT` | the whole register value copied across the host boundary | see below |
| `ONEHOTR`, `ONEHOTC` | the expansion over the row's or column's variables | expansion worst case |
| `EQUALITY`, `ATLEAST`, `ATLEASTW` | the expansion, plus the variables the model grows by | expansion worst case, variable |
| `EXCLUDE`, `IMPLIES` | the entries they write | linear entry, quadratic entry |
| `REDUCE` | its four enforcement entries, plus one new variable | linear entry, quadratic entry, variable |

Coefficient writes are charged only on a model register. A sample's assignments live in the buffer that was charged when the sample was allocated, so writing one costs nothing further. A coefficient write that lands on an entry that already exists is charged too: the alternative is measuring the map before and after every write, and the step budget already bounds how many of these a run can perform.

`ITER` charges for the copy it makes into the loop frame, because a frame is popped only by `NEXT` and a back-edge that re-enters an `ITER` without reaching its `NEXT` piles up one copy per execution. The charge is over the vec's element type, not a flat per-element rate: an element of a `vec<int>` is charged at the variable rate, and an element of a `vec<xqmx>` is charged the whole-model copy rate below.

`INPUT` and `OUTPUT` charge for the copy they make across the host boundary. `OUTPUT` writes into a slot the host keeps after the run, so the copy outlives the register it came from and a slot bound alone does not bound it: one allocation that spends the budget exactly, copied into every slot, hands the host an unbounded multiple of the budget. `INPUT` copies the other way, from a calldata slot into a register. Both are charged over the value's type, on the same terms as `ITER`, and neither refunds the slot or register it overwrites -- the budget counts cumulatively.

**One model copy has one price.** Copying a model costs its declared `size` at the variable rate, plus one linear entry per live linear coefficient and one quadratic entry per live quadratic coefficient -- exactly what the allocator and the coefficient writes charged to build it. That number is the charge wherever the copy happens, whether the model is an element of a `vec<xqmx>` an `ITER` copies into a loop frame or a whole register copied across the host boundary. Measuring the copy the same way the original was measured is what keeps a copy from being cheaper than the thing it copies, and pricing it the same in every copying opcode is what keeps a program from choosing the cheap route to the same duplication.

**Byte rates.** The rates are a versioned schedule, and every schedule entry is a fixed number:

| Unit | Bytes | Schedule version |
|------|-------|------------------|
| Variable | 8 | 1 |
| Vec element | 16 | 1 |
| Linear entry | 32 | 1 |
| Quadratic entry | 48 | 1 |

There is no separate model-header rate. A model's fixed overhead is not priced: what a copy is charged is its declared variables and its live entries, and nothing else. A schedule constant with no counting unit in the table above is a rate one implementation can carry and the other silently omit, which is what a model header was.

A rate must not be derived from the executing target's pointer width, or from any implementation's in-memory layout. A program's charge, and so whether it faults, has to be the same on every target: a 32-bit host that charges less than a 64-bit one for the same model splits consensus between two nodes running the same bytecode. The numbers above bound real heap use from above on a 64-bit host; they are a schedule rather than a measurement, and an implementation must not substitute a measurement for them.

**Allocator validation order.** An allocator resolves its `size` operand in a fixed order, so that a bad program gets the same fault on every target:

1. Reject a negative `size` with `InvalidAllocation`. For the discrete allocators the domain width `k` is validated before the size.
2. Charge the budget for that size, computed from the operand as the i64 the program pushed. `MemoryLimitExceeded` if it does not fit.
3. Only then convert the size to the target's native width and allocate. A size the target cannot address raises `InvalidAllocation` here, in the one case the bound below admits.

Charging off the i64 rather than off the converted value is the point of the ordering: a negative size raises `InvalidAllocation` on every target, and a size beyond one target's address space raises `MemoryLimitExceeded` on every target under the bound stated next, instead of silently becoming a zero-sized allocation that charges nothing on the narrower one.

**The bound on that guarantee.** Step 3 is decided by the executing target's pointer width, so the ordering makes the identity target-independent only while step 2 is certain to refuse first. That holds for every allocation budget below the bytes `2^32` variables cost -- 32 GiB at the variable rate above -- which is every budget a host sets today. Above it the guarantee lapses: a size past a 32-bit target's address space is charged successfully, and then raises `InvalidAllocation` on that target while a 64-bit one accepts it, as does an implementation whose integers are unbounded and which therefore has no step 3 at all. This is a recorded gap rather than a licence: an implementation may not read it as permission to diverge, and closing it means giving `size` a maximum that does not mention pointer width. It is tracked as QUI-1315. Until it closes, a host that wants the identity guarantee keeps its allocation budget below that bound.

**Error precedence.** Within one instruction, operand pops happen first (`StackUnderflow`), then the allocation charge, then type and range validation. An instruction can therefore raise `MemoryLimitExceeded` for work it would never have done, because the register it names holds the wrong type or the index it was given is out of range. That ordering is deliberate: the charge is what makes the later work safe to attempt, so it cannot be made conditional on that work succeeding.

Range validation itself is ordered. On `SETLINE` and `ADDLINE` the index is checked before the value: a write that addresses no variable has no domain to be measured against, so `IndexOutOfBounds` precedes `SampleOutOfDomain`. On `ADDLINE` the addition is checked before its result, so `ArithmeticOverflow` precedes `SampleOutOfDomain` too -- a sum that leaves the i64 range produces no value for the domain check to read.

---

## Faults

A fault aborts the run. Every fault has an **identity** -- a name from the list below -- and the identity is part of this specification: two conforming implementations running the same program must raise the same identity, not merely both fail. An implementation is free to spell an identity however its language prefers (a Rust enum variant, a Python exception class) and to attach whatever context it can; the identity is what conformance compares.

| Identity | Raised when |
|----------|-------------|
| `StackUnderflow` | An opcode pops from an empty value stack. |
| `StackOverflow` | A push would take the value stack past its depth limit. |
| `LoopStackOverflow` | A `RANGE` or `ITER` would push a loop frame past the nesting limit. |
| `TypeMismatch` | A register holds a value of the wrong kind for the opcode, or a value is incompatible with a vec's element type. A model-only opcode applied to a sample register, or a sample-only one to a model, raises this identity: an implementation that distinguishes the two by a mode flag rather than by type must still report `TypeMismatch`. |
| `UnsetRegister` | An opcode reads a register that was never written, or that has been `DROP`ped. |
| `DivisionByZero` | `DIV` or `MOD` with a zero divisor. |
| `ArithmeticOverflow` | An i64 operation leaves the i64 range (see [Stack](#stack)). |
| `IndexOutOfBounds` | An index falls outside the vec, model, sample, or grid axis it addresses. |
| `NoActiveLoop` | `NEXT`, `LIDX` or `LVAL` with no frame on the loop stack. |
| `UnmatchedLoop` | The empty-loop skip reaches the end of the stream without finding its matching `NEXT`. |
| `BadJumpTarget` | A jump names a target the pre-scan did not register. |
| `InvalidLabel` | A jump's label is absent from the jump table. |
| `BadOpcode` | A byte in opcode position is reserved or unassigned. |
| `TruncatedInstruction` | The stream ends inside an instruction's operands. |
| `CallDataIndex` | `INPUT` names a slot outside the calldata the host supplied. |
| `OutputIndex` | `OUTPUT` names a slot outside the output slots the host reserved. |
| `SizeMismatch` | `ENERGY`'s model and sample have different sizes. |
| `VecLengthMismatch` | Two vec operands required to have equal length do not. |
| `StepLimitExceeded` | The step budget is exhausted (see [Step budget](#step-budget)). |
| `MemoryLimitExceeded` | The allocation budget cannot pay a charge (see [Allocation budget](#allocation-budget)). |
| `InvalidAllocation` | An allocator is given a negative size. A non-negative size the executing target cannot address also raises it, in the one case [Allocator validation order](#allocation-budget) bounds. |
| `InvalidShift` | `SHL` or `SHR` with a shift amount outside `[0, 63]`. |
| `InvalidGridDimensions` | A grid extent is not positive, does not fit the register's variables, or is required by the opcode and absent. |
| `InvalidDiscreteK` | `XQMX` or `XSMX` with `k < 2`. |
| `SampleOutOfDomain` | `SETLINE` or `ADDLINE` writes a value outside the domain a sample's allocator declared. Sample registers only: model coefficients are unbounded. `ADDLINE` checks the result of the addition, not the delta. |

The list is closed for program faults. A host-side failure that is not the program's doing -- a tracer callback that errors, an I/O failure in the embedder -- is outside it and is not compared. Every identity above has a counterpart in both reference implementations.

[ISA.md](ISA.md) names faults by these identities. Where a row records that the two current implementations raise different identities for the same program, that is a recorded divergence and not a licence: the identity is normative, and the divergence is a defect in whichever implementation is wrong.

---

## Instruction Set Architecture

The XQVM defines 93 opcodes organised into 13 categories: control flow, register I/O, stack manipulation, arithmetic, comparison, logical boolean, bitwise, allocators, vector operations, index math, XQMX coefficient access, XQMX grid, and XQMX high-level functions. Each opcode specifies its stack effect, register access mode, and error conditions. Opcodes not assigned to an instruction are reserved and must be rejected by the decoder.

Full opcode tables, semantic notes, and the reserved opcode list: **[ISA.md](ISA.md)**

## High-Level Functions

A subset of opcodes (ONEHOTR, ONEHOTC, EXCLUDE, IMPLIES, EQUALITY, ATLEAST, ATLEASTW, REDUCE, ENERGY) implement high-level combinatorial constraint patterns. These opcodes expand into linear and quadratic coefficient deltas on an XQMX model, injecting QUBO penalty terms automatically. Some (ATLEAST, ATLEASTW, REDUCE) allocate auxiliary variables during execution, growing the model size.

Expansion formulas, derivations, and the ENERGY computation: **[HLF.md](HLF.md)**

## Encoding and File Formats

Programs exist in two representations. `.xqasm` is the human-readable text assembly format with line comments, register names (`r0`–`r255`), target labels (`.N`), and syntactic sugar for `PUSH` and `JUMP`/`JUMPI` families. `.xqb` is the binary bytecode format -- a 15-byte XQBC header (magic, version, slot counts, payload length, CRC-32) followed by a raw instruction stream where each instruction is encoded as an opcode byte followed by zero to eight operand bytes. Instruction length is determined solely by the opcode.

File format definitions, assembly syntax, and binary encoding rules: **[ENCODING.md](ENCODING.md)**

## Bytecode Verification

Bytecode verification phases, error semantics, composable architecture, and per-opcode stack effects: **[VERIFIER.md](VERIFIER.md)**

## Related Specifications

- **[../xqsa/SPEC.md](../xqsa/SPEC.md)** -- solver adapter interface. Defines how external solvers plug into the pipeline between encoder and verifier execution.
- **[../xqcp/SPEC.md](../xqcp/SPEC.md)** -- constraint-programming DSL. Compiles high-level problem descriptions into the three XQVM programs (encoder, verifier, decoder).
- **[METERING.md](METERING.md)** -- step metering. Defines the step budget's cost units, the per-opcode charges, and the constants a conforming implementation must share with the Rust and Python VMs.
