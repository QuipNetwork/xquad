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
            "domain": [0, 1],         # [0,1] | [-1,1] | [-k,...,k-1]
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
- **Domain:** `[0,1]` binary, `[-1,1]` spin, `[-k, ..., k-1]` discrete (signed and centred, so the sample default `0` is always in-domain; `spec/xqsa/DOMAINS.md` states the same range)
- **The domain is not enforced at runtime.** Neither implementation range-checks an assignment written through `SETLINE`/`ADDLINE` against the variable's domain, and both accept an out-of-domain value. The domain constrains what a solver may return and what an encoder should write; it is not a per-write invariant the VM maintains. Enforcing it would put a check on every sample write, whose cost and placement belong with the bytecode verifier rather than the interpreter
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
| Step budget | Host-supplied | Bounds the instructions one run may execute; `StepLimitExceeded` when exhausted. See [Step budget](#step-budget). |
| Allocation budget | Host-supplied | Bounds the bytes one run may ask the host to allocate; `MemoryLimitExceeded` when exhausted. See [Allocation budget](#allocation-budget). |
| XQMX size | Bounded by the allocation budget | No fixed spec limit. A size that is not an allocation -- negative, or larger than the executing target can address -- raises `InvalidAllocation`; a size that is an allocation but does not fit the remaining budget raises `MemoryLimitExceeded`. |
| Program length | Implementation-defined | No spec limit. |

### Step budget

Execution is bounded by a **step budget**: a maximum number of instructions one run may execute, fixed by the host before the run starts. A host may leave it unbounded; how that is spelled is an interface question rather than a semantic one.

The budget is normative rather than an implementation convenience. An embedder that meters execution -- the Substrate pallet pre-charges a caller for the requested limit, refunds the difference, and publishes the number consumed -- prices a quantity that has to be defined here, or two conforming implementations charge a caller differently for the same program.

- **One step is one instruction fetched from the instruction stream and dispatched.** Every instruction costs one step and none costs more: `NOP`, `TARGET` and `HALT` are steps, an instruction reached by a jump or a loop back-edge is a step each time it is reached, and an instruction that faults has been charged before it faults.
- **The budget is tested after the fetch and before the dispatch.** If the counter has already reached the limit, the run raises `StepLimitExceeded` and the fetched instruction does not execute.
- **Reaching the end of the instruction stream is not a step.** The fetch that finds no instruction ends the run before the budget is consulted, so a program that runs off the end having executed exactly `limit` instructions succeeds rather than raising. A program with no instructions therefore runs successfully under a budget of `0`; a program with one instruction does not.
- **The forward scan of the empty-loop skip is not metered.** The instructions the scan passes over (see [Empty-loop skip](ISA.md#control-flow)) are not fetched for execution and are not steps. That scan is bounded by the length of the program rather than by the budget.
- **The counter is per run.** It is zero at the start of every run and holds the number of steps executed when the run ends, whether it ended by `HALT`, by end of stream, or by a fault.

Error: `StepLimitExceeded`, naming the limit that was reached.

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

1. Reject a `size` that is not an allocation -- negative, or larger than the executing target can address -- with `InvalidAllocation`. For the discrete allocators the domain width `k` is validated before the size.
2. Charge the budget for that size, computed from the operand as the i64 the program pushed. `MemoryLimitExceeded` if it does not fit.
3. Only then convert the size to the target's native width and allocate.

Charging off the i64 rather than off the converted value is the point of the ordering: a negative size raises `InvalidAllocation` on every target, and a size beyond one target's address space raises `MemoryLimitExceeded` on every target, instead of silently becoming a zero-sized allocation that charges nothing on the narrower one.

**Error precedence.** Within one instruction, operand pops happen first (`StackUnderflow`), then the allocation charge, then type and range validation. An instruction can therefore raise `MemoryLimitExceeded` for work it would never have done, because the register it names holds the wrong type or the index it was given is out of range. That ordering is deliberate: the charge is what makes the later work safe to attempt, so it cannot be made conditional on that work succeeding.

---

## Faults

A fault aborts the run. Every fault has an **identity** -- a name from the list below -- and the identity is part of this specification: two conforming implementations running the same program must raise the same identity, not merely both fail. An implementation is free to spell an identity however its language prefers (a Rust enum variant, a Python exception class) and to attach whatever context it can; the identity is what conformance compares.

| Identity | Raised when |
|----------|-------------|
| `StackUnderflow` | An opcode pops from an empty value stack. |
| `StackOverflow` | A push would take the value stack past its depth limit. |
| `LoopStackOverflow` | A `RANGE` or `ITER` would push a loop frame past the nesting limit. |
| `TypeMismatch` | A register holds a value of the wrong kind for the opcode, or a value is incompatible with a vec's element type. |
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
| `InvalidAllocation` | An allocator is given a size that is not an allocation: negative, or larger than the executing target can address. |
| `InvalidShift` | `SHL` or `SHR` with a shift amount outside `[0, 63]`. |
| `InvalidGridDimensions` | A grid extent is not positive, does not fit the register's variables, or is required by the opcode and absent. |
| `InvalidDiscreteK` | `XQMX` or `XSMX` with `k < 2`. |
| `XqmxMode` | A model-only opcode is applied to a sample register, or a sample-only one to a model. **Unresolved between the two reference implementations, and the only row here that is.** `xqvm_py` raises this identity; the Rust `xqvm` VM raises `TypeMismatch` for the same programs, and no `xqvm::Error` variant maps to `XqmxMode` at all. Both reject the affected programs, so only the name is in question, not admissibility. A third implementation must not read this row as settled: until it is, either identity is defensible and neither is safe to write a conformance vector against. |

The list is closed for program faults. A host-side failure that is not the program's doing -- a tracer callback that errors, an I/O failure in the embedder -- is outside it and is not compared. Every other identity above has a counterpart in the Rust reference implementation.

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
