<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `conformance/opcodes.yaml` by
  `scripts/gen-bytecode-docs.py`.
  Edit the YAML (and the opcodes! x-macro in
  xqvm/src/bytecode/types/table.rs, which is checked against the YAML at
  compile time), then run `make regen-docs`.

  For the long-form human-readable semantics of each instruction see
  `docs/book/src/xqvm/instructions/*.md` or `spec/xqvm/SPEC.md`.
-->

# XQVM Bytecode Semantics

Concise reference table for every opcode in the XQVM bytecode format.
Derived directly from [`conformance/opcodes.yaml`](opcodes.yaml),
which is kept in sync with the Rust `opcodes!` x-macro (enforced at
compile time by `xqvm/build.rs`) and the Python `Opcode` enum (enforced
by `scripts/check-opcode-parity.py`).

Columns:
- **Code** -- wire-encoding byte.
- **Mnemonic** -- uppercase assembly name.
- **Operands** -- post-opcode operand layout; empty for no-operand instructions.
- **Stack** -- stack effect as `pop → push`; `0 → 1` means one value produced.
  `any → 0` marks an instruction that empties the stack outright rather than
  applying a fixed net effect.
- **Description** -- single-sentence semantic summary.

Reserved wire bytes (rejected by the decoder as illegal): `0x0D`, `0x19`, `0x1D`-`0x1F`, `0x2D`-`0x2F`, `0x35`, `0x46`-`0x49`, `0x4D`-`0x4F`, `0x55`-`0x59`, `0x5C`-`0x5F`, `0x6B`-`0x6F`, `0x78`-`0x7E`, `0x80`-`0xEF`, `0xF1`-`0xFE`.

Total: **93 opcodes**.

---

## Control Flow

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x00` | `TARGET` | -- | `0 → 0` | Mark a valid jump destination. |
| `0x01` | `JUMP1` | `label: u8` | `0 → 0` | Unconditionally jump to a basic block by u8 label index (narrow form). |
| `0x02` | `JUMPI1` | `label: u8` | `1 → 0` | Jump to a basic block by u8 label index if the top of the stack is non-zero (narrow form). |
| `0x03` | `JUMP2` | `label: u16` | `0 → 0` | Unconditionally jump to a basic block by u16 label index (wide form). |
| `0x04` | `JUMPI2` | `label: u16` | `1 → 0` | Jump to a basic block by u16 label index if the top of the stack is non-zero (wide form). |
| `0x05` | `LIDX` | `reg: Register` | `0 → 0` | Copy the current loop index (offset-adjusted) into a register. |
| `0x06` | `LVAL` | `reg: Register` | `0 → 0` | Copy the current loop value into a register. |
| `0x07` | `NEXT` | -- | `0 → 0` | Advance the loop index; jump back or exit the current loop. |
| `0x08` | `RANGE` | -- | `2 → 0` | Start a range loop over [start, start + count). |
| `0x09` | `ITER` | `reg: Register` | `2 → 0` | Start a vec iteration over a slice of a register's vec. |

---

## Register I/O

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x0A` | `LOAD` | `reg: Register` | `0 → 1` | Push the value of an int register onto the stack. |
| `0x0B` | `STOW` | `reg: Register` | `1 → 0` | Pop the top of the stack into an int register. |
| `0x0C` | `DROP` | `reg: Register` | `0 → 0` | Reset a register to Unset, releasing any value it held. |
| `0x0E` | `INPUT` | `reg: Register` | `1 → 0` | Pop a calldata slot index and load that slot into a register. |
| `0x0F` | `OUTPUT` | `reg: Register` | `1 → 0` | Pop an output slot index and write the register to it. |

---

## Stack Manipulation

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x10` | `POP` | -- | `1 → 0` | Discard the top of the stack. |
| `0x11` | `PUSH1` | `val: [u8; 1]` | `0 → 1` | Push a 1-byte big-endian signed constant, sign-extended to i64. |
| `0x12` | `PUSH2` | `val: [u8; 2]` | `0 → 1` | Push a 2-byte big-endian signed constant, sign-extended to i64. |
| `0x13` | `PUSH3` | `val: [u8; 3]` | `0 → 1` | Push a 3-byte big-endian signed constant, sign-extended to i64. |
| `0x14` | `PUSH4` | `val: [u8; 4]` | `0 → 1` | Push a 4-byte big-endian signed constant, sign-extended to i64. |
| `0x15` | `PUSH5` | `val: [u8; 5]` | `0 → 1` | Push a 5-byte big-endian signed constant, sign-extended to i64. |
| `0x16` | `PUSH6` | `val: [u8; 6]` | `0 → 1` | Push a 6-byte big-endian signed constant, sign-extended to i64. |
| `0x17` | `PUSH7` | `val: [u8; 7]` | `0 → 1` | Push a 7-byte big-endian signed constant, sign-extended to i64. |
| `0x18` | `PUSH8` | `val: [u8; 8]` | `0 → 1` | Push a full 8-byte big-endian signed constant (i64). |
| `0x1A` | `SCLR` | -- | `any → 0` | Clear the entire value stack. |
| `0x1B` | `SWAP` | -- | `2 → 2` | Swap the top two stack elements. |
| `0x1C` | `COPY` | -- | `1 → 2` | Duplicate the top of the stack. |

---

## Arithmetic

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x20` | `ADD` | -- | `2 → 1` | Pop b and a; push a + b. |
| `0x21` | `SUB` | -- | `2 → 1` | Pop b and a; push a - b. |
| `0x22` | `MUL` | -- | `2 → 1` | Pop b and a; push a * b. |
| `0x23` | `DIV` | -- | `2 → 1` | Pop b and a; push a / b (floor division, rounds toward negative infinity). |
| `0x24` | `MOD` | -- | `2 → 1` | Pop b and a; push a % b. |
| `0x25` | `SQR` | -- | `1 → 1` | Pop a; push a * a. |
| `0x26` | `ABS` | -- | `1 → 1` | Pop a; push \|a\|. |
| `0x27` | `NEG` | -- | `1 → 1` | Pop a; push -a. |
| `0x28` | `MIN` | -- | `2 → 1` | Pop b and a; push min(a, b). |
| `0x29` | `MAX` | -- | `2 → 1` | Pop b and a; push max(a, b). |
| `0x2A` | `INC` | -- | `1 → 1` | Pop a; push a + 1. |
| `0x2B` | `DEC` | -- | `1 → 1` | Pop a; push a - 1. |
| `0x2C` | `BITLEN` | -- | `1 → 1` | Pop a; push floor(log2(a))+1. If a <= 0, push 0. |

---

## Comparison

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x30` | `EQ` | -- | `2 → 1` | Pop b and a; push 1 if a == b, else 0. |
| `0x31` | `LT` | -- | `2 → 1` | Pop b and a; push 1 if a < b, else 0. |
| `0x32` | `GT` | -- | `2 → 1` | Pop b and a; push 1 if a > b, else 0. |
| `0x33` | `LTE` | -- | `2 → 1` | Pop b and a; push 1 if a <= b, else 0. |
| `0x34` | `GTE` | -- | `2 → 1` | Pop b and a; push 1 if a >= b, else 0. |

---

## Logical Boolean

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x36` | `NOT` | -- | `1 → 1` | Pop a; push 1 if a == 0, else 0. |
| `0x37` | `AND` | -- | `2 → 1` | Pop b and a; push 1 if both are non-zero, else 0. |
| `0x38` | `OR` | -- | `2 → 1` | Pop b and a; push 1 if either is non-zero, else 0. |
| `0x39` | `XOR` | -- | `2 → 1` | Pop b and a; push 1 if exactly one is non-zero, else 0. |

---

## Bitwise

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x3A` | `BAND` | -- | `2 → 1` | Pop b and a; push a & b. |
| `0x3B` | `BOR` | -- | `2 → 1` | Pop b and a; push a \| b. |
| `0x3C` | `BXOR` | -- | `2 → 1` | Pop b and a; push a ^ b. |
| `0x3D` | `BNOT` | -- | `1 → 1` | Pop a; push ~a. |
| `0x3E` | `SHL` | -- | `2 → 1` | Pop b and a; push a << b. |
| `0x3F` | `SHR` | -- | `2 → 1` | Pop b and a; push a >> b (arithmetic right shift, sign-preserving). |

---

## Allocators

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x40` | `BQMX` | `reg: Register` | `1 → 0` | Pop size; allocate a binary QUBO model ({0, 1} domain) into a register. |
| `0x41` | `SQMX` | `reg: Register` | `1 → 0` | Pop size; allocate a spin Ising model ({-1, +1} domain) into a register. |
| `0x42` | `XQMX` | `reg: Register` | `2 → 0` | Pop k then size; allocate a discrete model with domain {0, ..., k-1} into a register. Errors when k < 2. |
| `0x43` | `BSMX` | `reg: Register` | `1 → 0` | Pop size; allocate a binary sample ({0, 1} domain) into a register. |
| `0x44` | `SSMX` | `reg: Register` | `1 → 0` | Pop size; allocate a spin sample ({-1, +1} domain) into a register. |
| `0x45` | `XSMX` | `reg: Register` | `2 → 0` | Pop k then size; allocate a discrete sample with domain {0, ..., k-1} into a register. Errors when k < 2. |
| `0x4A` | `VEC` | `reg: Register` | `0 → 0` | Create an empty `vec<int>` in a register, identical to VECI. |
| `0x4B` | `VECI` | `reg: Register` | `0 → 0` | Create an empty `vec<int>` in a register. |
| `0x4C` | `VECX` | `reg: Register` | `0 → 0` | Create an empty `vec<xqmx>` in a register. |

---

## Vector Operations

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x50` | `VECPUSH` | `reg: Register` | `1 → 0` | Pop a value; append it to the register's vec. |
| `0x51` | `VECGET` | `reg: Register` | `1 → 1` | Pop index; push `vec[index]` from the register's vec. |
| `0x52` | `VECSET` | `reg: Register` | `2 → 0` | Pop value and index; set `vec[index]` in the register's vec. |
| `0x53` | `VECLEN` | `reg: Register` | `0 → 1` | Push the length of the register's vec onto the stack. |
| `0x54` | `SLACK` | `indices: Register`, `coeffs: Register` | `2 → 0` | Pop capacity and start_index; append slack variable indices and power-of-two coefficients to two register vecs. |

---

## Index Math

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x5A` | `IDXGRID` | -- | `3 → 1` | Pop cols, col, row; push the flat grid index row * cols + col. |
| `0x5B` | `IDXTRIU` | -- | `2 → 1` | Pop j and i; push the upper-triangular index for the unordered pair (i, j). |

---

## XQMX Coefficient Access

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x60` | `GETLINE` | `reg: Register` | `1 → 1` | Pop i; push `linear[i]` from the register's model, or a sample's assignment at i; raises IndexOutOfBounds outside [0, size). |
| `0x61` | `SETLINE` | `reg: Register` | `2 → 0` | Pop value and i; set `linear[i]` in the register's model, or a sample's assignment at i; a sample value outside its domain raises `SampleOutOfDomain`. |
| `0x62` | `ADDLINE` | `reg: Register` | `2 → 0` | Pop delta and i; add delta to `linear[i]` in the register's model, or a sample's assignment at i; a sample result outside its domain raises `SampleOutOfDomain`. |
| `0x63` | `GETQUAD` | `reg: Register` | `2 → 1` | Pop j and i; push `quadratic[i, j]` from the register's model; requires MODEL mode -- a sample register raises `TypeMismatch`; raises IndexOutOfBounds outside [0, size). |
| `0x64` | `SETQUAD` | `reg: Register` | `3 → 0` | Pop value, j, and i; set `quadratic[i, j]` in the register's model; requires MODEL mode -- a sample register raises `TypeMismatch`; raises IndexOutOfBounds outside [0, size). |
| `0x65` | `ADDQUAD` | `reg: Register` | `3 → 0` | Pop delta, j, and i; add delta to `quadratic[i, j]` in the register's model; requires MODEL mode -- a sample register raises `TypeMismatch`; raises IndexOutOfBounds outside [0, size). |

---

## XQMX Grid

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x66` | `RESIZE` | `reg: Register` | `2 → 0` | Pop cols and rows; set the grid dimensions of the register's model or sample. |
| `0x67` | `ROWFIND` | `reg: Register` | `2 → 1` | Pop value and row; push the first column where the value matches, or -1. |
| `0x68` | `COLFIND` | `reg: Register` | `2 → 1` | Pop value and col; push the first row where the value matches, or -1. |
| `0x69` | `ROWSUM` | `reg: Register` | `1 → 1` | Pop row; push the sum of all linear values in that grid row. |
| `0x6A` | `COLSUM` | `reg: Register` | `1 → 1` | Pop col; push the sum of all linear values in that grid column. |

---

## XQMX High-Level Constraints

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0x70` | `ONEHOTR` | `reg: Register` | `2 → 0` | Pop penalty and row; add a one-hot constraint over the grid row. |
| `0x71` | `ONEHOTC` | `reg: Register` | `2 → 0` | Pop penalty and col; add a one-hot constraint over the grid column. |
| `0x72` | `EXCLUDE` | `reg: Register` | `3 → 0` | Pop penalty, j, and i; add a mutual-exclusion constraint between variables i and j; raises IndexOutOfBounds outside [0, size). |
| `0x73` | `IMPLIES` | `reg: Register` | `3 → 0` | Pop penalty, j, and i; add an implication constraint from variable i to variable j; raises IndexOutOfBounds outside [0, size). |
| `0x74` | `EQUALITY` | `model: Register`, `indices: Register`, `coeffs: Register` | `2 → 0` | Pop penalty and target; expand weighted equality constraint into QUBO terms on a model. |
| `0x75` | `ATLEAST` | `model: Register`, `indices: Register` | `2 → 0` | Pop penalty and k; allocate slack variables and apply at-least-k constraint. |
| `0x76` | `ATLEASTW` | `model: Register`, `indices: Register`, `coeffs: Register` | `2 → 0` | Pop penalty and k; allocate slack variables and apply weighted at-least-k constraint. |
| `0x77` | `REDUCE` | `model: Register` | `3 → 1` | Pop P_aux, var_b, var_a; allocate auxiliary variable and add Rosenberg enforcement terms; push aux index. |
| `0x7F` | `ENERGY` | `model: Register`, `sample: Register` | `0 → 1` | Compute the Hamiltonian energy of a sample against a model; push the result. |

---

## Special

| Code | Mnemonic | Operands | Stack | Description |
|------|----------|----------|-------|-------------|
| `0xF0` | `NOP` | -- | `0 → 0` | No operation. |
| `0xFF` | `HALT` | -- | `0 → 0` | Stop execution. |
