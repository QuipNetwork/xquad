# XQVM Spec Migration Design

**Date:** 2026-03-24
**Status:** Approved

## Overview

Align the Rust XQVM implementation with the canonical Python reference spec
(`XQVM_SPEC.md`). Changes touch all four crates: `bytecode`, `asm`, `disasm`,
`vm`. Migration follows a layer-by-layer strategy so each crate compiles and
passes `make lint && make test` before moving to the next.

## Agreed Constraints

- **JUMP/JUMPI encoding:** Keep `i16` relative byte offsets (assembler resolves,
  VM jumps directly). The spec's symbolic target-ID approach is not adopted at
  the binary level.
- **Loop semantics:** Keep current lazy evaluation (`Range` tracks `current/end`,
  `Iter` holds a register reference and index). No eager value pre-materialization.
- **`PUSHC_0` removal:** The zero-byte push constant has no equivalent in the
  spec. `PUSH1 0x00` is the replacement.
- **Stack depth:** Enforce a maximum depth of 8192 elements.

---

## Layer 1: `crates/bytecode`

### Opcode table (`types/table.rs`)

Total opcodes: 68 current -- 1 removed + 9 added = **76**.

> Note: apply all recodings and the removal first in a single edit to `table.rs`,
> then insert the new opcodes. This avoids transient code collisions (e.g.
> `0x1A` is occupied by `PushC3` until the push-family recodings vacate it, after
> which `SCLR` takes it).

#### Unassigned codes (intentional gaps)

Codes `0x08` and `0x0D` are unassigned post-migration. They are reserved for
future use and must be treated as illegal opcodes by the decoder and VM.

#### Removed

| Old code | Old variant | Reason |
|---|---|---|
| `0x10` | `PushC0` / `PUSHC_0` | No spec equivalent; `PUSH1 0x00` replaces it |

#### Recodings

Control flow / register I/O:

| Old code | New code | Variant | Mnemonic |
|---|---|---|---|
| `0x0F` | `0x09` | `Halt` | `HALT` |
| `0x14` | `0x0A` | `Load` | `LOAD` |
| `0x15` | `0x0B` | `Stow` | `STOW` |
| `0x16` | `0x0E` | `Input` | `INPUT` |
| `0x17` | `0x0F` | `Output` | `OUTPUT` |

Stack manipulation:

| Old code | New code | Old variant / mnemonic | New variant / mnemonic |
|---|---|---|---|
| `0x11` | `0x10` | `Pop` / `POP` | same |
| `0x13` | `0x1B` | `Swap` / `SWAP` | same |
| `0x12` | `0x1C` | `Dupl` / `DUPL` | `Copy` / `COPY` |
| `0x18--0x1F` | `0x11--0x18` | `PushC1..PushC8` / `PUSHC_1..PUSHC_8` | `Push1..Push8` / `PUSH1..PUSH8` |

Arithmetic:

| Old code | New code | Variant | Mnemonic |
|---|---|---|---|
| `0x25` | `0x27` | `Neg` / `NEG` | same |

Comparison (all shift up by `0x0A`):

| Old code | New code | Variant | Mnemonic |
|---|---|---|---|
| `0x26` | `0x30` | `Eq` | `EQ` |
| `0x27` | `0x31` | `Lt` | `LT` |
| `0x28` | `0x32` | `Gt` | `GT` |
| `0x29` | `0x33` | `Lte` | `LTE` |
| `0x2A` | `0x34` | `Gte` | `GTE` |

Boolean (all shift up by `0x06`):

| Old code | New code | Variant | Mnemonic |
|---|---|---|---|
| `0x30` | `0x36` | `Not` | `NOT` |
| `0x31` | `0x37` | `And` | `AND` |
| `0x32` | `0x38` | `Or` | `OR` |
| `0x33` | `0x39` | `Xor` | `XOR` |

Bitwise (all shift up by `0x06`):

| Old code | New code | Variant | Mnemonic |
|---|---|---|---|
| `0x34` | `0x3A` | `BAnd` | `BAND` |
| `0x35` | `0x3B` | `BOr` | `BOR` |
| `0x36` | `0x3C` | `BXor` | `BXOR` |
| `0x37` | `0x3D` | `BNot` | `BNOT` |
| `0x38` | `0x3E` | `Shl` | `SHL` |
| `0x39` | `0x3F` | `Shr` | `SHR` |

High-level constraints:

| Old code | New code | Old variant / mnemonic | New variant / mnemonic |
|---|---|---|---|
| `0x70` | `0x70` | `OneHot` / `ONEHOT` | `OneHotR` / `ONEHOTR` |
| `0x71` | `0x72` | `Exclude` / `EXCLUDE` | same |
| `0x72` | `0x73` | `Implies` / `IMPLIES` | same |

#### New opcodes

| Code | Variant | Mnemonic | Operands | Stack | Description |
|---|---|---|---|---|---|
| `0x0C` | `Drop` | `DROP` | `<reg>` | -- | Reset register to `Int(0)` (the `RegVal` default) |
| `0x1A` | `Sclr` | `SCLR` | -- | clear all | Empty entire stack |
| `0x25` | `Sqr` | `SQR` | -- | pop a -> push a*a | Square |
| `0x26` | `Abs` | `ABS` | -- | pop a -> push \|a\| | Absolute value |
| `0x28` | `Min` | `MIN` | -- | pop b, a -> push min(a,b) | Minimum |
| `0x29` | `Max` | `MAX` | -- | pop b, a -> push max(a,b) | Maximum |
| `0x2A` | `Inc` | `INC` | -- | pop a -> push a+1 | Increment |
| `0x2B` | `Dec` | `DEC` | -- | pop a -> push a-1 | Decrement |
| `0x71` | `OneHotC` | `ONEHOTC` | `<reg>` | pop penalty (i64), col (i64) | One-hot constraint over column; `col` is a stack integer (grid column index), mirroring how `ONEHOTR` takes `row` |

### Builder (`builder.rs`)

- Rename `dupl()` -> `copy()`
- Rename `onehot()` -> `onehotr()`
- Internal push helper selects `Push1..Push8` (replaces `PushC1..PushC8`); public
  `push(i64)` sugar interface unchanged
- Add builder methods: `drop_reg(reg)`, `sclr()`, `sqr()`, `abs()`, `min()`,
  `max()`, `inc()`, `dec()`, `onehotc(reg)`
- JUMP/JUMPI two-pass fixup and `i16` relative offset encoding: **no change**

### Codec / stream / program / error

No structural changes. Codec is X-macro driven; renamed/recoded opcodes propagate
automatically from `table.rs`. `[u8; N]` payload sizes for the push family are
unchanged (1--8 bytes).

---

## Layer 2: `crates/disasm`

X-macro driven. Renames and additions propagate automatically from `table.rs`.
Manual verification: confirm `PUSH1..PUSH8` display format matches spec assembly
syntax (no underscore prefix).

---

## Layer 3: `crates/asm`

### Grammar (`grammar.pest`)

- Remove `PUSHC_0` keyword rule
- Rename keyword rules: `PUSHC_1..PUSHC_8` -> `PUSH1..PUSH8`, `DUPL` -> `COPY`,
  `ONEHOT` -> `ONEHOTR`
- Add keyword rules: `DROP`, `SCLR`, `SQR`, `ABS`, `MIN`, `MAX`, `INC`, `DEC`,
  `ONEHOTC`

### AST (`ast.rs`)

Rename and add instruction node variants to match the updated mnemonic set. One-to-one
correspondence with table changes.

### Assembler (`assembler.rs`)

- PUSH sugar: `push(v)` selects smallest fitting `Push1..Push8` variant (logic
  identical to current `PushC1..PushC8` selection)
- `JUMP`/`JUMPI` continue resolving to relative `i16` byte offsets via the
  existing two-pass label system -- no semantic change
- `TARGET` remains a binary no-op marker; `.N`-style label syntax unchanged
- Wire all new instructions through to `InstructionBuilder`

### TSP examples (`crates/vm/examples/tsp/`)

Update `encoder.xqasm`, `decoder.xqasm`, `verifier.xqasm` to use renamed mnemonics.
Also review `main.rs` in the same directory for any hardcoded mnemonic strings or
opcode references that need updating.

---

## Layer 4: `crates/vm`

### Interpreter (`vm.rs`)

**Stack depth:** All operations that grow the stack (`PUSHn`, `COPY`, `LVAL`,
`LOAD`, `VECGET`, `GETLINE`, `GETQUAD`, `ENERGY`, `IDXGRID`, `IDXTRIU`,
`ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM`, `VECLEN`, and arithmetic/comparison/
logical results) must route through a single private `push_stack` helper that
enforces `stack.len() < 8192` before pushing, returning `Error::StackOverflow`
if the limit is exceeded.

**Renamed match arms (behavior unchanged):**
- `Dupl` -> `Copy`
- `OneHot` -> `OneHotR`

**New instruction handlers:**

| Instruction | Semantics |
|---|---|
| `DROP` | `registers[reg] = RegVal::Int(0)` (explicit, not `default()`, to match spec "reset to unset" = integer zero) |
| `SCLR` | `self.stack.clear()` |
| `SQR` | pop a; push `a.wrapping_mul(a)` |
| `ABS` | pop a; push `a.wrapping_abs()` |
| `MIN` | pop b, a; push `a.min(b)` |
| `MAX` | pop b, a; push `a.max(b)` |
| `INC` | pop a; push `a.wrapping_add(1)` |
| `DEC` | pop a; push `a.wrapping_sub(1)` |
| `ONEHOTC` | one-hot constraint along a grid column (mirror of `ONEHOTR` with row/col axes swapped) |

**Loop logic (`Range`, `Iter`, `LVal`, `Next`):** no changes.

### Error (`error.rs`)

Add `StackOverflow` variant.

### Value (`value.rs`)

No changes.

### Model (`model.rs`)

`OneHotC` column implementation: walks the column index of the grid model,
mirroring how `OneHotR` walks a row. No new types required.

---

## Testing Strategy

Each layer must pass `make lint && make test` before proceeding to the next.

- **Layer 1:** Existing bytecode unit tests cover codec round-trips and builder
  fluent API. Add unit tests for each new opcode variant in the builder.
- **Layer 2:** Disasm snapshot tests verify rendered output for new/renamed mnemonics.
- **Layer 3:** Assembler round-trip tests: assemble -> encode -> decode ->
  disassemble and compare.
- **Layer 4:** VM execution tests for each new instruction. TSP example must
  run end-to-end after mnemonic updates.
