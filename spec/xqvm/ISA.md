# XQVM Instruction Set (93 opcodes)

## Notation

The following conventions are used throughout this section to describe opcode behaviour. They are documentation shorthand only and do not prescribe implementation details.

- **Stack diagrams** — `[..., a, b] → [..., r]`, rightmost = **top**. `b` is popped first.
- **`reg`** — the register operand encoded in the instruction.
- **Register effect** column uses three access modes:
  - **`read`** — register contents are inspected but not changed.
  - **`write`** — register is replaced wholesale with a new value.
  - **`mutate`** — register's existing value is modified in-place (e.g. appending to a vec, incrementing a coefficient).
- Assignments use `←` (register write) and `→` (stack push).
- **Boolean representation** — `0` is false, any non-zero value is true. Boolean-producing opcodes always push `0` or `1`.
- **Error names** are fault identities from [SPEC.md](SPEC.md#faults). An implementation spells them in its own language; the identity is what conformance compares. Where a row records that the two current implementations raise different identities for the same program, that is a recorded divergence rather than a licence.
- **Step cost** is not a per-row property of this table. Every opcode charges at least the base dispatch cost; opcodes whose additional cost scales with program-controlled data are listed, with their formulas, in [METERING.md](METERING.md).

## Control Flow

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x00` | `TARGET` | — | `[...] → [...]` | — | Mark a valid jump destination. No operand in bytecode; during pre-scan, each TARGET is assigned a sequential ID (0, 1, 2, ...) in program order and registered at the current PC. No-op at runtime. In assembly source, `TARGET .N` is syntactic sugar — the `.N` label is resolved by the assembler and not encoded. |
| `0x01` | `JUMP1` | `.N` | `[...] → [...]` | — | Set PC to the instruction at `targets[N]`, where N is the sequential target ID (u8). Unconditional. Error: `TargetNotFound` if N is undefined. |
| `0x02` | `JUMPI1` | `.N` | `[..., cond] → [...]` | — | Pop `cond`. If `cond != 0`, set PC to `targets[N]` (u8); otherwise fall through. Error: `TargetNotFound` if N is undefined and condition is non-zero. |
| `0x03` | `JUMP2` | `hi`, `lo` | `[...] → [...]` | — | Set PC to the instruction at `targets[N]`, where N is the sequential target ID encoded as u16 big-endian (`N = hi << 8 \| lo`). Unconditional. Error: `TargetNotFound` if N is undefined. |
| `0x04` | `JUMPI2` | `hi`, `lo` | `[..., cond] → [...]` | — | Pop `cond`. If `cond != 0`, set PC to `targets[N]` (u16 big-endian); otherwise fall through. Error: `TargetNotFound` if N is undefined and condition is non-zero. |
| `0x05` | `LIDX` | `reg` | `[...] → [...]` | `write` — `reg ← index + start_offset` | Copy the current loop index (offset-adjusted) into `reg`. For RANGE loops: equivalent to LVAL (values are indices). For ITER loops: returns the original vec index (`frame.index + start_idx`). Error: `NoActiveLoop` if no active loop. |
| `0x06` | `LVAL` | `reg` | `[...] → [...]` | `write` — `reg ← values[index]` | Copy the current loop value into `reg`. For RANGE loops: `reg ← int`. For ITER loops: `reg ← vec element` (type preserved: int or xqmx). Error: `NoActiveLoop` if no active loop. |
| `0x07` | `NEXT` | — | `[...] → [...]` | — | Advance the active loop frame index. If more values remain, set PC to `frame.target` (loop body start). Otherwise pop the frame and fall through. Error: `NoActiveLoop` if no loop frame is active. |
| `0x08` | `RANGE` | — | `[..., start, count] → [...]` | — | Pop `count`, then `start`. Generate values `[start, start+1, ..., start+count-1]`. Push a loop frame with `target = PC+1` and `start_offset = start`. If `count <= 0`, the loop body is skipped entirely (see **Empty-loop skip** below). |
| `0x09` | `ITER` | `reg` | `[..., start_idx, end_idx] → [...]` | `read` — validates `reg` holds `vec` | Pop `end_idx`, then `start_idx`. Read vec from `reg`. Copy elements `vec[start_idx:end_idx]` into a loop frame with `target = PC+1` and `start_offset = start_idx`. If `start_idx >= end_idx`, the loop body is skipped -- the condition is `>=`, so an inverted range is empty rather than an error, and no bounds check applies to a slice that is never taken. Elements are copied for immutability. Errors: `UnsetRegister` if `reg` is unset; `TypeMismatch` if `reg` holds a value that is not a vec; `IndexOutOfBounds` if the slice is non-empty and either bound falls outside `[0, len]`. The first two are distinct identities, so a register that was never written is not a type mismatch. |

The `0xF_` range is reserved for control flow utilities. The two currently assigned opcodes:

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0xF0` | `NOP` | — | `[...] → [...]` | — | No operation. |
| `0xFF` | `HALT` | — | `[...] → [...]` | — | Stop execution immediately. |

**JUMP sugar:** In assembly source, `JUMP .N` and `JUMPI .N` are syntactic sugar. The assembler resolves the label and selects `JUMP1`/`JUMPI1` (u8) or `JUMP2`/`JUMPI2` (u16) based on the target ID value, mirroring the [`PUSH` sugar](ENCODING.md#push-sugar) pattern.

**Empty-loop skip:** When `RANGE` is executed with `count <= 0` (or `ITER` with `start_idx >= end_idx`), the VM must not push a loop frame or execute the loop body. Instead, the VM scans forward from the next instruction, maintaining a nesting-depth counter initialised to 1. Each `RANGE` or `ITER` encountered increments the counter; each `NEXT` decrements it. When the counter reaches 0, execution resumes at the instruction immediately after that matching `NEXT`. If the instruction stream ends before a matching `NEXT` is found, the VM faults with an unmatched-loop error.

**Why an inverted range is not an error:** Making `ITER` fault when `start_idx > end_idx` was considered and declined. What settles it is the order of the two tests: emptiness is decided before the bounds are. An empty slice reads no element, so it cannot name a missing one; a non-empty slice whose bounds fall outside the vec names elements that do not exist, and raises `IndexOutOfBounds`. An inverted range is well-formed and denotes the empty sequence rather than a malformed one, which is the reading `RANGE` already gives every non-positive `count` -- making the two openers differ here would split a family that is otherwise uniform. The cost is that a program whose bounds come out backwards loops zero times and reports nothing, so a caller that needs to tell an inverted range from an empty one compares the bounds before the opener. The condition has read `>=` since before the two openers were reconciled with each other; this records why it stays that way rather than changing it.

---

## Register I/O

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x0A` | `LOAD` | `reg` | `[...] → [..., v]` | `read` — `reg` must hold `int` | Push the integer value from `reg` onto the stack. Error: `TypeMismatch` if `reg` holds vec or xqmx. Error: `UnsetRegister` if `reg` is unset. |
| `0x0B` | `STOW` | `reg` | `[..., v] → [...]` | `write` — `reg ← int(v)` | Pop `v`. Write `reg ← v` as an integer. |
| `0x0C` | `DROP` | `reg` | `[...] → [...]` | `write` — `reg ← unset` | Clear the register, releasing any value it held. The register becomes unset. No error if already unset. |
| `0x0E` | `INPUT` | `reg` | `[..., s] → [...]` | `write` — `reg ← input[s]` | Pop `s` (slot index). Copy `input[s]` into `reg`. Any value type is transferable (int, vec, or xqmx). The host fixes the calldata before the run; `s` outside `[0, slots)` is a program error and raises `CallDataIndex`. Calldata is a dense sequence of slots, and the `"input": {}` map in [SPEC.md](SPEC.md#machine-state)'s machine-state sketch is a notation for that sequence rather than a sparse map whose largest key fixes the count. The entries of that sequence need not all be set: an in-range slot the host left unset is not out of range, and copying it leaves `reg` unset rather than storing a null, so the next instruction to read `reg` raises `UnsetRegister`. Error: `CallDataIndex` if `s` is out of range. |
| `0x0F` | `OUTPUT` | `reg` | `[..., s] → [...]` | `read` — `reg` value copied to `output[s]` | Pop `s` (slot index). Copy `reg`'s value into `output[s]`. Any value type is transferable. The host fixes the slot count before the run; `s` outside `[0, slots)` is a program error and raises `OutputIndex` rather than growing the output map. Errors, in this order: the copy is charged first, so a budget too small for it raises `MemoryLimitExceeded` even when the write would not have happened; then `UnsetRegister` if `reg` is unset; then `OutputIndex` if `s` is out of range. This is the general precedence in [SPEC.md](SPEC.md#allocation-budget) applied to `OUTPUT`, and an unset register is charged 0, so it still faults. |

---

## Stack Manipulation

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x10` | `POP` | — | `[..., a] → [...]` | — | Discard the top of the stack. Error: `StackUnderflow` if empty. |
| `0x11` | `PUSH1` | `val: 1 × <int>` | `[...] → [..., v]` | — | Interpret `val` as a 1-byte big-endian signed two's complement integer, push `v`. |
| `0x12` | `PUSH2` | `val: 2 × <int>` | `[...] → [..., v]` | — | Same, 2 bytes. |
| `0x13` | `PUSH3` | `val: 3 × <int>` | `[...] → [..., v]` | — | Same, 3 bytes. |
| `0x14` | `PUSH4` | `val: 4 × <int>` | `[...] → [..., v]` | — | Same, 4 bytes. |
| `0x15` | `PUSH5` | `val: 5 × <int>` | `[...] → [..., v]` | — | Same, 5 bytes. |
| `0x16` | `PUSH6` | `val: 6 × <int>` | `[...] → [..., v]` | — | Same, 6 bytes. |
| `0x17` | `PUSH7` | `val: 7 × <int>` | `[...] → [..., v]` | — | Same, 7 bytes. |
| `0x18` | `PUSH8` | `val: 8 × <int>` | `[...] → [..., v]` | — | Same, 8 bytes (full-width signed integer). |
| `0x1A` | `SCLR` | — | `[...] → []` | — | Clear the entire value stack. Stack depth becomes 0. |
| `0x1B` | `SWAP` | — | `[..., a, b] → [..., b, a]` | — | Swap the top two elements. Error: `StackUnderflow` if depth < 2. |
| `0x1C` | `COPY` | — | `[..., a] → [..., a, a]` | — | Duplicate the top of the stack without consuming it. Error: `StackUnderflow` if empty. |

Operand bytes are concatenated in big-endian order (most significant byte first) and interpreted as a signed two's complement integer. For example, `PUSH2 0xFF 0xFE` pushes −2.

---

## Arithmetic

| Code | Mnemonic | Stack effect | Interpretation |
|------|----------|--------------|----------------|
| `0x20` | `ADD` | `[..., a, b] → [..., a+b]` | Addition. |
| `0x21` | `SUB` | `[..., a, b] → [..., a-b]` | Subtraction. |
| `0x22` | `MUL` | `[..., a, b] → [..., a*b]` | Multiplication. |
| `0x23` | `DIV` | `[..., a, b] → [..., a/b]` | Floor division (rounds toward negative infinity, matching Python `//`). Errors: `DivisionByZero` if `b == 0`; `ArithmeticOverflow` for `i64::MIN / -1`, whose quotient is not representable. |
| `0x24` | `MOD` | `[..., a, b] → [..., a%b]` | Modulo (result has same sign as divisor). Error: `DivisionByZero` if `b == 0`. `i64::MIN % -1` yields `0` and does not raise -- the remainder is representable even though the underlying division overflows. |
| `0x25` | `SQR` | `[..., a] → [..., a*a]` | Square. |
| `0x26` | `ABS` | `[..., a] → [..., \|a\|]` | Absolute value. |
| `0x27` | `NEG` | `[..., a] → [..., -a]` | Negation. |
| `0x28` | `MIN` | `[..., a, b] → [..., min(a,b)]` | Signed minimum. |
| `0x29` | `MAX` | `[..., a, b] → [..., max(a,b)]` | Signed maximum. |
| `0x2A` | `INC` | `[..., a] → [..., a+1]` | Increment. |
| `0x2B` | `DEC` | `[..., a] → [..., a-1]` | Decrement. |
| `0x2C` | `BITLEN` | `[..., a] → [..., floor(log2(a))+1]` | Bit length. Push the number of bits needed to represent `a` in binary. If `a <= 0`, push `0`. |

Binary operations pop the second operand first: `PUSH a; PUSH b; SUB` → `a - b`. Top of stack is always the second operand.

---

## Comparison

Results are `1` (true) or `0` (false). All comparisons are signed.

| Code | Mnemonic | Stack effect | Interpretation |
|------|----------|--------------|----------------|
| `0x30` | `EQ` | `[..., a, b] → [..., a==b ? 1 : 0]` | Equality. |
| `0x31` | `LT` | `[..., a, b] → [..., a<b ? 1 : 0]` | Less-than. |
| `0x32` | `GT` | `[..., a, b] → [..., a>b ? 1 : 0]` | Greater-than. |
| `0x33` | `LTE` | `[..., a, b] → [..., a<=b ? 1 : 0]` | Less-or-equal. |
| `0x34` | `GTE` | `[..., a, b] → [..., a>=b ? 1 : 0]` | Greater-or-equal. |

---

## Logical Boolean

Operands are treated as booleans: `0` is false, any non-zero value is true. Results are `1` or `0`.

| Code | Mnemonic | Stack effect | Interpretation |
|------|----------|--------------|----------------|
| `0x36` | `NOT` | `[..., a] → [..., a==0 ? 1 : 0]` | Logical NOT. |
| `0x37` | `AND` | `[..., a, b] → [..., (a!=0 && b!=0) ? 1 : 0]` | Logical AND. |
| `0x38` | `OR` | `[..., a, b] → [..., (a!=0 \|\| b!=0) ? 1 : 0]` | Logical OR. |
| `0x39` | `XOR` | `[..., a, b] → [..., ((a!=0) ^ (b!=0)) ? 1 : 0]` | Logical XOR. True iff exactly one operand is non-zero. |

---

## Bitwise

Operate on raw integer bit patterns.

| Code | Mnemonic | Stack effect | Interpretation |
|------|----------|--------------|----------------|
| `0x3A` | `BAND` | `[..., a, b] → [..., a & b]` | Bitwise AND. |
| `0x3B` | `BOR` | `[..., a, b] → [..., a \| b]` | Bitwise OR. |
| `0x3C` | `BXOR` | `[..., a, b] → [..., a ^ b]` | Bitwise XOR. |
| `0x3D` | `BNOT` | `[..., a] → [..., ~a]` | Bitwise NOT (one's complement). |
| `0x3E` | `SHL` | `[..., a, b] → [..., a << b]` | Left shift. Raises `InvalidShift` unless `0 <= b < 64`, and `ArithmeticOverflow` when the shift discards significant bits. |
| `0x3F` | `SHR` | `[..., a, b] → [..., a >> b]` | Right shift (arithmetic, sign-extending). Raises `InvalidShift` unless `0 <= b < 64`. |

> **Design note — shift opcodes.**
> `SHR` is arithmetic (sign-extending), not logical (zero-filling).
> Because the VM's type system is entirely signed integers, arithmetic
> right shift is the natural default — it preserves sign and is equivalent
> to integer division by 2^b. A separate logical right shift opcode is
> intentionally omitted: there is no unsigned integer type, no fixed bit
> width to zero-fill from, and no current workload that requires unsigned
> bit-pattern manipulation. Where logical right shift semantics are
> needed, they can be emulated with a mask (`BAND`) followed by `SHR`.

---

## Register Allocators

These instructions allocate typed objects into registers.

**Size precondition.** Every allocator that takes a `size` from the stack -- `BQMX`, `SQMX`, `XQMX`, `BSMX`, `SSMX`, `XSMX` -- requires that size to be non-negative; a negative size raises `InvalidAllocation` and leaves the target register alone. A non-negative size is then charged against the [allocation budget](SPEC.md#allocation-budget) before anything is allocated, and raises `MemoryLimitExceeded` if it does not fit. The order is normative (see [Allocator validation order](SPEC.md#allocation-budget)): validating and charging off the operand as the program pushed it, rather than off a value already narrowed to the executing target's native width, is what makes a five-billion-variable request raise the same fault on a 32-bit target as on a 64-bit one, instead of quietly becoming an empty model that costs nothing -- and it does so for every allocation budget below the bound [SPEC.md](SPEC.md#allocation-budget) records, which is every budget a host sets today. The rows below name only what is theirs.

### Model Allocators

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x40` | `BQMX` | `reg` | `[..., size] → [...]` | `write` — `reg ← xqmx(model, binary, size)` | Pop `size`. Create a binary `[0,1]` model XQMX with `size` variables and empty linear/quadratic tables. Write to `reg`. |
| `0x41` | `SQMX` | `reg` | `[..., size] → [...]` | `write` — `reg ← xqmx(model, spin, size)` | Pop `size`. Create a spin `[-1,+1]` model XQMX. Write to `reg`. |
| `0x42` | `XQMX` | `reg` | `[..., size, k] → [...]` | `write` — `reg ← xqmx(model, discrete(k), size)` | Pop `k`, then `size`. Create a discrete `[-k,...,k-1]` model XQMX. Error if `k < 2`. Write to `reg`. |

### Sample Allocators

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x43` | `BSMX` | `reg` | `[..., size] → [...]` | `write` — `reg ← xqmx(sample, binary, size)` | Pop `size`. Create a binary `[0,1]` sample XQMX with `size` variables; every position initialised to `0`. Linear table stores variable assignments. Write to `reg`. |
| `0x44` | `SSMX` | `reg` | `[..., size] → [...]` | `write` — `reg ← xqmx(sample, spin, size)` | Pop `size`. Create a spin `[-1,+1]` sample XQMX; every position initialised to `-1` (a valid spin state). Write to `reg`. |
| `0x45` | `XSMX` | `reg` | `[..., size, k] → [...]` | `write` — `reg ← xqmx(sample, discrete(k), size)` | Pop `k`, then `size`. Create a discrete `[-k,...,k-1]` sample XQMX; every position initialised to `0`. Error if `k < 2`. Write to `reg`. |

Sample allocation is dense: after `BSMX`/`SSMX`/`XSMX` every position `i` in `[0, size)` holds its domain-default value. Reads via `GETLINE` see that default until a matching write overrides it. This mirrors the Rust runtime's `vec![default; size]` storage; the Python reference VM pre-populates the equivalent sparse entries (QUI-453).

### Vec Allocators

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x4A` | `VEC` | `reg` | `[...] → [...]` | `write` — `reg ← vec<int>` | Create an empty `vec<int>`, identical to VECI. Write to `reg`. |
| `0x4B` | `VECI` | `reg` | `[...] → [...]` | `write` — `reg ← vec<int>` | Create an empty `vec<int>`. Write to `reg`. |
| `0x4C` | `VECX` | `reg` | `[...] → [...]` | `write` — `reg ← vec<xqmx>` | Create an empty `vec<xqmx>`. Write to `reg`. |

---

## Vector Operations

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x50` | `VECPUSH` | `reg` | `[..., v] → [...]` | `mutate` — appends `v` to `reg`'s vec | Pop `v`. `reg` must hold a vec. Append `v`. If vec type is unset, infer from `v`. Otherwise validate type compatibility. Error: `TypeMismatch` if `reg` is not a vec or if `v` has incompatible type. |
| `0x51` | `VECGET` | `reg` | `[..., idx] → [..., v]` | `read` — `reg` must hold `vec<int>` | Pop `idx`. `reg` must hold a vec. Bounds-check: `0 ≤ idx < len`. Push `vec[idx]`. Error: `IndexOutOfBounds` if out of bounds. Error: `TypeMismatch` if element is not int (cannot push non-int to stack). |
| `0x52` | `VECSET` | `reg` | `[..., idx, v] → [...]` | `mutate` — sets `reg.vec[idx] ← v` | Pop `v`, then `idx`. `reg` must hold a vec. Bounds-check: `0 ≤ idx < len`. Set `vec[idx] ← v`. Validates type compatibility. |
| `0x53` | `VECLEN` | `reg` | `[...] → [..., n]` | `read` — `reg` must hold a vec | `reg` must hold a vec. Push `len(vec)` as integer. Error: `TypeMismatch` if `reg` is not a vec. |
| `0x54` | `SLACK` | `indices coeffs` | `[..., start_index, capacity] → [...]` | `mutate` — appends elements to both `indices` and `coeffs` vecs | Pop `capacity`, then `start_index`. Compute S = floor(log2(capacity)) + 1 slack variable entries. Append indices `[start_index, start_index+1, ..., start_index+S-1]` and coefficients `[1, 2, 4, ..., 2^(S-1)]` to the register vecs. Both registers are read and must hold `vec<int>` before anything is appended, and before the `capacity` test: `SLACK` carries a `mutate` effect on both that the verifier's static type system depends on, so a non-appending `SLACK` still resolves them, and neither register is mutated by an instruction that goes on to fault on the other. If `capacity <= 0`, no elements are appended. Errors: `UnsetRegister` if either register is unset; `TypeMismatch` if either holds a value that is not a `vec<int>`. See derivation below. |

### `SLACK` Derivation

Converts an inequality constraint `Σ(w_i × x_i) ≤ W` into equality by generating slack variable entries suitable for appending to an `EQUALITY` call.

Given `capacity` W (popped from stack) and `start_index` (popped from stack):

1. Compute S = floor(log2(W)) + 1 slack variables (S binary digits can represent 0 to 2^S − 1 ≥ W)
2. Append to the `indices` register: `[start_index, start_index+1, ..., start_index+S-1]`
3. Append to the `coeffs` register: `[1, 2, 4, ..., 2^(S-1)]`

The slack variables represent the gap `W − Σ(w_i × x_i)` in binary. When combined with the original item indices/weights and passed to `EQUALITY` with target = W, this enforces:

```
Σ(w_i × x_i) + 1×s_0 + 2×s_1 + 4×s_2 + ... + 2^(S-1)×s_{S-1} = W
```

Which is satisfied iff `Σ(w_i × x_i) ≤ W`.

`SLACK` appends rather than overwrites so that item variables and slack variables can be built into the same vec pair in sequence.

Steps 2 and 3 are two passes and not one interleaved pass: the whole index sequence is appended before the first coefficient. The order is only observable when `indices` and `coeffs` name the same register, in which case the resulting vec is `[start_index, ..., start_index+S-1, 1, ..., 2^(S-1)]`. Aliasing the two operands is a legal program, so the order is normative rather than an implementation detail.

---

## Index Math

Utilities for mapping 2-D coordinates to flat array indices.

| Code | Mnemonic | Stack effect | Interpretation |
|------|----------|--------------|----------------|
| `0x5A` | `IDXGRID` | `[..., row, col, cols] → [..., row*cols+col]` | Row-major flat index. Pop `cols`, then `col`, then `row`. Push `row * cols + col`. |
| `0x5B` | `IDXTRIU` | `[..., i, j] → [..., j*(j-1)/2+i]` | Upper-triangular index for the pair `(i, j)`. Pop `j`, then `i`. If `i > j`, swap them, so `(i, j)` and `(j, i)` address the same cell. Push `j * (j - 1) / 2 + i`. The division is exact: `j * (j - 1)` is a product of consecutive integers, hence non-negative and even for every operand, so truncating and flooring division agree and no rounding rule needs stating. The operands need not be non-negative and are not range-checked here -- `IDXTRIU` computes an index, and the opcode that consumes it bounds-checks it against the structure it addresses. Every intermediate of the computation is range-checked (SPEC.md overflow rule), so a pair whose index is representable but whose product is not raises `ArithmeticOverflow` rather than pushing a wrapped result. |

---

## XQMX Coefficient Access

Read and write the linear (bias) and quadratic (coupling) coefficients of an XQMX register. Writes create the entry on first call. Zero values are removed from sparse storage to maintain sparsity. `reg` must hold an XQMX.

**Index precondition.** Every index in this section is bounded against the register's declared size, and every opcode here raises `IndexOutOfBounds` for one outside `[0, size)` -- reads as well as writes. Within that range a missing entry reads as `0`; outside it there is no entry to be missing, because the variable is not one the allocator declared. The bound is the declared size and not the extent of the sparse map, so a read of an absent in-range coefficient and a read past the end are different outcomes rather than the same `0`. An unbounded write is the case this rules out: the sparse map would accept the key and the model would carry a coefficient over a variable that does not exist, leaving a constraint that constrains nothing on a model that still solves cleanly.

The linear opcodes (`GETLINE`, `SETLINE`, `ADDLINE`) accept either MODEL or SAMPLE mode — sample values are stored densely in `values[i]`, model biases sparsely in `linear[i]`. The quadratic opcodes (`GETQUAD`, `SETQUAD`, `ADDQUAD`) require MODEL mode: samples carry no quadratic storage, and `reg` must hold an XQMX in MODEL mode. A sample register raises `XqmxMode` on `xqvm_py` and `TypeMismatch` on the Rust `xqvm` VM, whose error type has no mode-specific variant.

### Linear Coefficients

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x60` | `GETLINE` | `reg` | `[..., i] → [..., linear[i]]` | `read` — `reg.xqmx.linear[i]` | Pop `i`. Push `linear[i]` (0 if absent). Error: `IndexOutOfBounds` if `i` out of range `[0, size)`. |
| `0x61` | `SETLINE` | `reg` | `[..., i, v] → [...]` | `mutate` — `reg.xqmx.linear[i] ← v` | Pop `v`, then `i`. Set `linear[i] ← v`. Error: `IndexOutOfBounds` if `i` out of range `[0, size)`. |
| `0x62` | `ADDLINE` | `reg` | `[..., i, δ] → [...]` | `mutate` — `reg.xqmx.linear[i] += δ` | Pop `δ`, then `i`. `linear[i] += δ`. Error: `IndexOutOfBounds` if `i` out of range. |

### Quadratic Coefficients

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x63` | `GETQUAD` | `reg` | `[..., i, j] → [..., quad[i,j]]` | `read` — `reg.xqmx.quad[i,j]` | Pop `j`, then `i`. If `i > j`, swap. Push `quad[i,j]` (0 if absent). Error: `IndexOutOfBounds` if either index out of range `[0, size)`; both are checked before the swap, so the reported index is the operand the program supplied. |
| `0x64` | `SETQUAD` | `reg` | `[..., i, j, v] → [...]` | `mutate` — `reg.xqmx.quad[i,j] ← v` | Pop `v`, then `j`, then `i`. If `i > j`, swap. Set `quad[i,j] ← v`. Error: `IndexOutOfBounds` if indices out of range `[0, size)`. |
| `0x65` | `ADDQUAD` | `reg` | `[..., i, j, δ] → [...]` | `mutate` — `reg.xqmx.quad[i,j] += δ` | Pop `δ`, then `j`, then `i`. If `i > j`, swap. `quad[i,j] += δ`. Error: `IndexOutOfBounds` if indices out of range. |

---

## XQMX Grid

An XQMX register (model or sample) can optionally be given 2-D grid dimensions so that variables are addressed as `(row, col)` with flat index `row * cols + col`. `reg` must hold an XQMX. These opcodes accept either MODEL or SAMPLE mode — row/column reads scan the register's `linear` surface (models: sparse `linear[idx]`; samples: dense `values[idx]`).

**Grid precondition.** Every grid-addressed opcode other than `RESIZE` requires two things of its operands, stated here once rather than repeated per row. The register must carry a grid: a register with none raises `InvalidGridDimensions`, because an absent grid is a precondition the program never established, and answering with nothing written or a sum of zeroes would answer a question that was never well posed. And the index must lie in the grid's extent along the axis the opcode addresses: outside `[0, rows)` for a row opcode, or `[0, cols)` for a column opcode, raises `IndexOutOfBounds`. The rows below name only what is theirs.

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x66` | `RESIZE` | `reg` | `[..., rows, cols] → [...]` | `mutate` — `reg.xqmx.rows ← rows; reg.xqmx.cols ← cols` | Pop `cols`, then `rows`. Set grid dimensions on the XQMX. Both extents must be positive: `rows <= 0` or `cols <= 0` raises `InvalidGridDimensions` and leaves the grid unchanged, since a degenerate grid is not a grid the grid-addressed opcodes can act on. The grid must also fit the register's variables: `rows * cols` may not exceed the model or sample's `size`, and a pair that does not raises `InvalidGridDimensions` and leaves the grid unchanged. A grid is a reinterpretation of an array the program has already declared and already paid for at allocation, so it cannot name cells that do not exist; stating the bound in the register's own terms makes it hold identically on every target. The bound is `<=` rather than `==` because `EQUALITY`, `ATLEAST`, `ATLEASTW` and `REDUCE` append variables past the grid, and they only ever grow `size`, so a grid accepted at `RESIZE` stays inside the bound afterwards. |
| `0x67` | `ROWFIND` | `reg` | `[..., row, value] → [..., col]` | `read` — scans `reg.xqmx.linear` across row | Pop `value`, then `row`. Scan `linear[row*cols + c]` for `c` in `0..cols`. Push the column index of the first entry equal to `value`, or `-1` if not found. Subject to the **Grid precondition** above. |
| `0x68` | `COLFIND` | `reg` | `[..., col, value] → [..., row]` | `read` — scans `reg.xqmx.linear` down column | Pop `value`, then `col`. Scan `linear[r*cols + col]` for `r` in `0..rows`. Push the row index of the first match, or `-1` if not found. Subject to the **Grid precondition** above. |
| `0x69` | `ROWSUM` | `reg` | `[..., row] → [..., sum]` | `read` — sums `reg.xqmx.linear` across row | Pop `row`. Push `Σ linear[row*cols + c]` for `c` in `0..cols`. The sum is accumulated in column order with every partial sum range-checked; overflow raises `ArithmeticOverflow` (SPEC.md overflow rule). Subject to the **Grid precondition** above. |
| `0x6A` | `COLSUM` | `reg` | `[..., col] → [..., sum]` | `read` — sums `reg.xqmx.linear` down column | Pop `col`. Push `Σ linear[r*cols + col]` for `r` in `0..rows`. The sum is accumulated in row order with every partial sum range-checked; overflow raises `ArithmeticOverflow` (SPEC.md overflow rule). Subject to the **Grid precondition** above. |

---

## XQMX High-Level Functions

These instructions inject QUBO penalty terms for common combinatorial constraints, expanding into linear and quadratic coefficient deltas automatically. `reg` must hold an XQMX in MODEL mode. A SAMPLE-mode XQMX raises `XqmxMode` on `xqvm_py` and `TypeMismatch` on the Rust `xqvm` VM, whose error type has no mode-specific variant.

| Code | Mnemonic | Arguments | Stack effect | Register effect | Interpretation |
|------|----------|-----------|--------------|-----------------|----------------|
| `0x70` | `ONEHOTR` | `reg` | `[..., row, penalty] → [...]` | `mutate` — adds to linear and quadratic for row variables | Pop `penalty`, then `row`. Apply one-hot constraint over all variables in grid row `row`. Subject to the **[Grid precondition](#xqmx-grid)** in both halves: with no grid there is no row to constrain and the instruction raises `InvalidGridDimensions` rather than writing nothing, and a `row` outside `[0, rows)` raises `IndexOutOfBounds` rather than applying the constraint to variables the grid does not address. See [expansion](HLF.md#onehotr--onehotc-expansion). |
| `0x71` | `ONEHOTC` | `reg` | `[..., col, penalty] → [...]` | `mutate` — adds to linear and quadratic for column variables | Pop `penalty`, then `col`. Apply one-hot constraint over all variables in grid column `col`. Subject to the **[Grid precondition](#xqmx-grid)** in both halves: with no grid there is no column to constrain and the instruction raises `InvalidGridDimensions` rather than writing nothing, and a `col` outside `[0, cols)` raises `IndexOutOfBounds` rather than applying the constraint to variables the grid does not address. See [expansion](HLF.md#onehotr--onehotc-expansion). |
| `0x72` | `EXCLUDE` | `reg` | `[..., i, j, penalty] → [...]` | `mutate` — `reg.xqmx.quad[i,j] += penalty` | Pop `penalty`, then `j`, then `i`. Add mutual-exclusion penalty. Error: `IndexOutOfBounds` if either index out of range `[0, model.size)`, checked in the order the operands were supplied. See [expansion](HLF.md#exclude-expansion). |
| `0x73` | `IMPLIES` | `reg` | `[..., i, j, penalty] → [...]` | `mutate` — modifies linear and quadratic | Pop `penalty`, then `j`, then `i`. Add implication constraint `i → j`. Error: `IndexOutOfBounds` if either index out of range `[0, model.size)`, checked in the order the operands were supplied. See [expansion](HLF.md#implies-expansion). |
| `0x74` | `EQUALITY` | `model indices coeffs` | `[..., target, penalty] → [...]` | `read` indices, coeffs; `mutate` model | Pop `penalty`, then `target`. Read variable indices from `indices` (`vec<int>`) and coefficients from `coeffs` (`vec<int>`). Expand weighted equality penalty `P × (Σ(a_k × x_k) − b)²` into QUBO terms on `model`. Lengths of `indices` and `coeffs` must match; both implementations raise `VecLengthMismatch` otherwise. See [expansion](HLF.md#equality-expansion). |
| `0x75` | `ATLEAST` | `model indices` | `[..., k, penalty] → [...]` | `read` indices; `mutate` model (grows size) | Pop `penalty`, then `k`. Read variable indices from `indices` (`vec<int>`). Allocate slack variables at `model.size`. Apply `EQUALITY` expansion with negative slack coefficients and target = k. Error: `IndexOutOfBounds` if `k <= 0` or `k > len(indices)`. See [derivation](HLF.md#atleast-derivation). |
| `0x76` | `ATLEASTW` | `model indices coeffs` | `[..., k, penalty] → [...]` | `read` indices, coeffs; `mutate` model (grows size) | Pop `penalty`, then `k`. Read variable indices from `indices` (`vec<int>`) and weights from `coeffs` (`vec<int>`). Allocate slack variables at `model.size`. Apply `EQUALITY` expansion with weighted variables, negative slack coefficients, and target = k. Lengths of `indices` and `coeffs` must match; both implementations raise `VecLengthMismatch` otherwise. Error: `IndexOutOfBounds` if `k <= 0`. The weight sum and its excess over `k` are accumulated in index order with every partial value range-checked; overflow raises `ArithmeticOverflow`. See [derivation](HLF.md#atleastw-derivation). |
| `0x77` | `REDUCE` | `model` | `[..., var_a, var_b, P_aux] → [..., aux]` | `mutate` model (grows size) | Pop `P_aux`, then `var_b`, then `var_a`. Allocate auxiliary variable w at `model.size` (size grows by 1). Add enforcement terms constraining `w = x_a × x_b`. Push the index of w. Error: `IndexOutOfBounds` if `var_a` or `var_b` out of range `[0, model.size)` before allocation. See [derivation](HLF.md#reduce-derivation). |
| `0x7F` | `ENERGY` | `model sample` | `[...] → [..., E]` | `read` — both `model` and `sample` registers are read-only | The `model` register must hold an XQMX in MODEL mode. The `sample` register must hold an XQMX in SAMPLE mode. Sizes must match. Compute and push the Hamiltonian energy. See [formula](HLF.md#energy-computation). |

---

## Reserved Opcodes

The following byte values within `0x00`–`0x7F` are unassigned and reserved for future use. A decoder encountering any of these in opcode position must reject the program:

| Category | Reserved bytes |
|----------|---------------|
| Register I/O | `0x0D` |
| Stack Manipulation | `0x19`, `0x1D`, `0x1E`, `0x1F` |
| Arithmetic | `0x2D`, `0x2E`, `0x2F` |
| Comparison | `0x35` |
| Register Allocators | `0x46`, `0x47`, `0x48`, `0x49`, `0x4D`, `0x4E`, `0x4F` |
| Vector Operations | `0x55`, `0x56`, `0x57`, `0x58`, `0x59` |
| XQMX | `0x6B`, `0x6C`, `0x6D`, `0x6E`, `0x6F` |
| XQMX High-Level Functions | `0x78`, `0x79`, `0x7A`, `0x7B`, `0x7C`, `0x7D`, `0x7E` |
| Unassigned | `0x5C`, `0x5D`, `0x5E`, `0x5F` |

All byte values in `0x80`–`0xFF` are likewise reserved and illegal, except `0xF0` (`NOP`) and `0xFF` (`HALT`) which are assigned to the [Control Flow](#control-flow) category.
