> Editor's note: the domain this plan calls "discrete" was renamed to "integer" in QUI-1345. The text below is left as written on 2026-03-24.

# XQVM Spec Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the Rust XQVM implementation (68 -> 76 opcodes) with `XQVM_SPEC.md` via a layer-by-layer migration that keeps `make lint && make test` green after each layer.

**Architecture:** Four independent crates updated in dependency order: `bytecode` (opcode table + builder) -> `disasm` (recompile-only) -> `asm` (assembler sugar updates + TSP examples) -> `vm` (new instruction handlers + stack depth enforcement). JUMP/JUMPI keep `i16` relative byte offsets; loop semantics stay lazy.

**Tech Stack:** Rust 2024 edition, `pastey` (paste! macro), `pest` (grammar -- no changes), `thiserror`, `miette`, `cargo nextest`.

**Spec:** `docs/design/specs/2026-03-24-xqvm-spec-migration-design.md`

---

## File Map

| File | Change |
|---|---|
| `crates/bytecode/src/types/table.rs` | Rewrite: all recodings + removal + 9 new opcodes |
| `crates/bytecode/src/builder.rs` | Update `minimal_pushc()` to use new variant names; update doc comments referencing `PUSHC_N`; auto-generated methods (`copy`, `one_hot_r`, `drop`, `sclr`, etc.) come for free from the macro |
| `crates/bytecode/src/codec.rs` | Update doc examples and tests that hard-code old variant names (`PushC0`, `PushC1`, etc.) or opcode bytes (`0x10`, `0x0F`) |
| `crates/bytecode/src/stream.rs` | Update doc examples and unit tests referencing old variant names or opcode bytes |
| `crates/bytecode/src/lib.rs` | Update doc examples referencing `Instruction::PushC0`, `Opcode::PushC0`, byte `0x10` |
| `crates/disasm/` | No code changes -- recompile from updated table; update any snapshot tests |
| `crates/asm/src/assembler.rs` | Update `PUSH`/`PUSHC` special-case to also accept `PUSH1..PUSH8`; update doc comment opcode references |
| `crates/vm/src/error.rs` | Add `StackOverflow` variant |
| `crates/vm/src/vm.rs` | Introduce module-level `STACK_LIMIT` const; introduce `push_stack` helper (replaces `push_val`); rename exec handlers; add 9 new `exec_*` handlers |
| `crates/vm/examples/tsp/encoder.xqasm` | `DUPL`->`COPY`, `ONEHOT`->`ONEHOTR` |
| `crates/vm/examples/tsp/decoder.xqasm` | Inspect and update if affected |
| `crates/vm/examples/tsp/verifier.xqasm` | Inspect and update if affected |

---

## Layer 1: `crates/bytecode`

### Task 1: Rewrite the opcode table

**Files:**
- Modify: `crates/bytecode/src/types/table.rs`

Apply all recodings, removal, and new opcodes in a **single edit** to avoid transient code collisions.

- [ ] **Step 1: Write a failing compile check**

Run `cargo check -p aglais-xqvm-bytecode` to confirm it currently passes.

```sh
cargo check -p aglais-xqvm-bytecode
```

Expected: exits 0 (green baseline).

- [ ] **Step 2: Replace the full opcode table**

Replace the contents of `crates/bytecode/src/types/table.rs` with the version below. The diff is: (1) update the doc comment count from 76 to 76 post-migration, (2) apply all opcode code/variant/mnemonic changes, (3) remove `PushC0`, (4) add 9 new entries.

Complete new table:

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.
//
// SPDX-License-Identifier: AGPL-3.0-or-later

/// Invoke `$mac!` with the complete XQVM opcode table.
///
/// The callback macro receives the full comma-separated list of 76 opcode
/// entries. Each entry has the form:
///
/// ```text
/// (code, Variant, "MNEMONIC", "doc", {field_name: FieldType, ...})
/// ```
///
/// | Position | Type | Description |
/// |---|---|---|
/// | `code` | `u8` literal | Wire-encoding byte |
/// | `Variant` | `ident` | `PascalCase` Rust enum variant name |
/// | `"MNEMONIC"` | `str` literal | Uppercase assembly mnemonic |
/// | `"doc"` | `str` literal | Single-sentence description |
/// | `{field: Type, ...}` | named-field list | Zero or more named operand fields |
///
/// # Examples
///
/// ```rust
/// macro_rules! collect_mnemonics {
///     ( $( ($code:literal, $var:ident, $mnem:literal, $doc:literal, {$($f:tt)*}) ),* $(,)? ) => {
///         &[ $( $mnem ),* ]
///     }
/// }
///
/// let mnemonics: &[&str] = aglais_xqvm_bytecode::opcodes!(collect_mnemonics);
/// assert!(mnemonics.contains(&"HALT"));
/// assert!(mnemonics.contains(&"ENERGY"));
/// ```
#[macro_export]
macro_rules! opcodes {
    ($mac:ident) => {
        $mac! {
            // ---------------------------------------------------------------
            // Control Flow
            // ---------------------------------------------------------------
            (0x00, Nop,      "NOP",      "No operation.",
             {}),
            (0x01, Target,   "TARGET",   "Mark a valid jump destination.",
             {}),
            (0x02, Jump,     "JUMP",     "Unconditionally jump by a signed 16-bit PC offset.",
             {offset: i16}),
            (0x03, JumpI,    "JUMPI",    "Jump by a signed 16-bit PC offset if the top of the stack is non-zero.",
             {offset: i16}),
            (0x04, Next,     "NEXT",     "Advance the loop index; jump back or exit the current loop.",
             {}),
            (0x05, LVal,     "LVAL",     "Copy the current loop value into a register.",
             {reg: $crate::Register}),
            (0x06, Range,    "RANGE",    "Start a range loop over [start, start + count).",
             {}),
            (0x07, Iter,     "ITER",     "Start a vec iteration over a slice of a register's vec.",
             {reg: $crate::Register}),
            (0x09, Halt,     "HALT",     "Stop execution.",
             {}),
            // ---------------------------------------------------------------
            // Register Manipulation
            // ---------------------------------------------------------------
            (0x0A, Load,     "LOAD",     "Push the value of an int register onto the stack.",
             {reg: $crate::Register}),
            (0x0B, Stow,     "STOW",     "Pop the top of the stack into an int register.",
             {reg: $crate::Register}),
            (0x0C, Drop,     "DROP",     "Reset a register to Int(0).",
             {reg: $crate::Register}),
            (0x0E, Input,    "INPUT",    "Pop a calldata slot index and load that slot into a register.",
             {reg: $crate::Register}),
            (0x0F, Output,   "OUTPUT",   "Pop an output slot index and write the register to it.",
             {reg: $crate::Register}),
            // ---------------------------------------------------------------
            // Stack Manipulation
            // ---------------------------------------------------------------
            (0x10, Pop,      "POP",      "Discard the top of the stack.",
             {}),
            (0x11, Push1,    "PUSH1",    "Push a 1-byte big-endian signed constant, sign-extended to i64.",
             {val: [u8; 1]}),
            (0x12, Push2,    "PUSH2",    "Push a 2-byte big-endian signed constant, sign-extended to i64.",
             {val: [u8; 2]}),
            (0x13, Push3,    "PUSH3",    "Push a 3-byte big-endian signed constant, sign-extended to i64.",
             {val: [u8; 3]}),
            (0x14, Push4,    "PUSH4",    "Push a 4-byte big-endian signed constant, sign-extended to i64.",
             {val: [u8; 4]}),
            (0x15, Push5,    "PUSH5",    "Push a 5-byte big-endian signed constant, sign-extended to i64.",
             {val: [u8; 5]}),
            (0x16, Push6,    "PUSH6",    "Push a 6-byte big-endian signed constant, sign-extended to i64.",
             {val: [u8; 6]}),
            (0x17, Push7,    "PUSH7",    "Push a 7-byte big-endian signed constant, sign-extended to i64.",
             {val: [u8; 7]}),
            (0x18, Push8,    "PUSH8",    "Push a full 8-byte big-endian signed constant (i64).",
             {val: [u8; 8]}),
            (0x1A, Sclr,     "SCLR",     "Empty the entire stack.",
             {}),
            (0x1B, Swap,     "SWAP",     "Swap the top two stack elements.",
             {}),
            (0x1C, Copy,     "COPY",     "Duplicate the top of the stack.",
             {}),
            // ---------------------------------------------------------------
            // Arithmetic
            // ---------------------------------------------------------------
            (0x20, Add,      "ADD",      "Pop b and a; push a + b.",
             {}),
            (0x21, Sub,      "SUB",      "Pop b and a; push a - b.",
             {}),
            (0x22, Mul,      "MUL",      "Pop b and a; push a * b.",
             {}),
            (0x23, Div,      "DIV",      "Pop b and a; push a / b (truncating integer division).",
             {}),
            (0x24, Modulo,   "MOD",      "Pop b and a; push a % b.",
             {}),
            (0x25, Sqr,      "SQR",      "Pop a; push a * a.",
             {}),
            (0x26, Abs,      "ABS",      "Pop a; push |a|.",
             {}),
            (0x27, Neg,      "NEG",      "Pop a; push -a.",
             {}),
            (0x28, Min,      "MIN",      "Pop b and a; push min(a, b).",
             {}),
            (0x29, Max,      "MAX",      "Pop b and a; push max(a, b).",
             {}),
            (0x2A, Inc,      "INC",      "Pop a; push a + 1.",
             {}),
            (0x2B, Dec,      "DEC",      "Pop a; push a - 1.",
             {}),
            // ---------------------------------------------------------------
            // Comparison  (result: 1 if true, 0 if false)
            // ---------------------------------------------------------------
            (0x30, Eq,       "EQ",       "Pop b and a; push 1 if a == b, else 0.",
             {}),
            (0x31, Lt,       "LT",       "Pop b and a; push 1 if a < b, else 0.",
             {}),
            (0x32, Gt,       "GT",       "Pop b and a; push 1 if a > b, else 0.",
             {}),
            (0x33, Lte,      "LTE",      "Pop b and a; push 1 if a <= b, else 0.",
             {}),
            (0x34, Gte,      "GTE",      "Pop b and a; push 1 if a >= b, else 0.",
             {}),
            // ---------------------------------------------------------------
            // Logical Boolean
            // ---------------------------------------------------------------
            (0x36, Not,      "NOT",      "Pop a; push 1 if a == 0, else 0.",
             {}),
            (0x37, And,      "AND",      "Pop b and a; push 1 if both are non-zero, else 0.",
             {}),
            (0x38, Or,       "OR",       "Pop b and a; push 1 if either is non-zero, else 0.",
             {}),
            (0x39, Xor,      "XOR",      "Pop b and a; push 1 if exactly one is non-zero, else 0.",
             {}),
            // ---------------------------------------------------------------
            // Bitwise
            // ---------------------------------------------------------------
            (0x3A, BAnd,     "BAND",     "Pop b and a; push a & b.",
             {}),
            (0x3B, BOr,      "BOR",      "Pop b and a; push a | b.",
             {}),
            (0x3C, BXor,     "BXOR",     "Pop b and a; push a ^ b.",
             {}),
            (0x3D, BNot,     "BNOT",     "Pop a; push ~a.",
             {}),
            (0x3E, Shl,      "SHL",      "Pop b and a; push a << b.",
             {}),
            (0x3F, Shr,      "SHR",      "Pop b and a; push a >> b (logical right shift).",
             {}),
            // ---------------------------------------------------------------
            // Allocators
            // ---------------------------------------------------------------
            (0x40, Bqmx,     "BQMX",     "Pop size; allocate a binary QUBO model ([0, 1] domain) into a register.",
             {reg: $crate::Register}),
            (0x41, Sqmx,     "SQMX",     "Pop size; allocate a spin Ising model ([-1, 1] domain) into a register.",
             {reg: $crate::Register}),
            (0x42, Xqmx,     "XQMX",     "Pop k and size; allocate a discrete model ([0, k-1] domain) into a register.",
             {reg: $crate::Register}),
            (0x43, Bsmx,     "BSMX",     "Pop size; allocate a binary sample ([0, 1] domain) into a register.",
             {reg: $crate::Register}),
            (0x44, Ssmx,     "SSMX",     "Pop size; allocate a spin sample ([-1, 1] domain) into a register.",
             {reg: $crate::Register}),
            (0x45, Xsmx,     "XSMX",     "Pop k and size; allocate a discrete sample ([0, k-1] domain) into a register.",
             {reg: $crate::Register}),
            // ---------------------------------------------------------------
            // Vec Allocators
            // ---------------------------------------------------------------
            (0x4A, Vec,      "VEC",      "Create an empty vec (element type inferred on first push) in a register.",
             {reg: $crate::Register}),
            (0x4B, VecI,     "VECI",     "Create an empty `vec<int>` in a register.",
             {reg: $crate::Register}),
            (0x4C, VecX,     "VECX",     "Create an empty `vec<xqmx>` in a register.",
             {reg: $crate::Register}),
            // ---------------------------------------------------------------
            // Vector Access
            // ---------------------------------------------------------------
            (0x50, VecPush,  "VECPUSH",  "Pop a value; append it to the register's vec.",
             {reg: $crate::Register}),
            (0x51, VecGet,   "VECGET",   "Pop index; push `vec[index]` from the register's vec.",
             {reg: $crate::Register}),
            (0x52, VecSet,   "VECSET",   "Pop value and index; set `vec[index]` in the register's vec.",
             {reg: $crate::Register}),
            (0x53, VecLen,   "VECLEN",   "Push the length of the register's vec onto the stack.",
             {reg: $crate::Register}),
            // ---------------------------------------------------------------
            // Index Math
            // ---------------------------------------------------------------
            (0x5A, IdxGrid,  "IDXGRID",  "Pop cols, col, row; push the flat grid index row * cols + col.",
             {}),
            (0x5B, IdxTriu,  "IDXTRIU",  "Pop j and i (i <= j); push the upper-triangular index for (i, j).",
             {}),
            // ---------------------------------------------------------------
            // XQMX Coefficient Access
            // ---------------------------------------------------------------
            (0x60, GetLine,  "GETLINE",  "Pop i; push `linear[i]` from the register's model (0 if absent).",
             {reg: $crate::Register}),
            (0x61, SetLine,  "SETLINE",  "Pop value and i; set `linear[i]` in the register's model.",
             {reg: $crate::Register}),
            (0x62, AddLine,  "ADDLINE",  "Pop delta and i; add delta to `linear[i]` in the register's model.",
             {reg: $crate::Register}),
            (0x63, GetQuad,  "GETQUAD",  "Pop j and i; push `quadratic[i, j]` from the register's model (0 if absent).",
             {reg: $crate::Register}),
            (0x64, SetQuad,  "SETQUAD",  "Pop value, j, and i; set `quadratic[i, j]` in the register's model.",
             {reg: $crate::Register}),
            (0x65, AddQuad,  "ADDQUAD",  "Pop delta, j, and i; add delta to `quadratic[i, j]` in the register's model.",
             {reg: $crate::Register}),
            // ---------------------------------------------------------------
            // XQMX Grid
            // ---------------------------------------------------------------
            (0x66, Resize,   "RESIZE",   "Pop cols and rows; set the grid dimensions of the register's model.",
             {reg: $crate::Register}),
            (0x67, RowFind,  "ROWFIND",  "Pop value and row; push the first column where the value matches, or -1.",
             {reg: $crate::Register}),
            (0x68, ColFind,  "COLFIND",  "Pop value and col; push the first row where the value matches, or -1.",
             {reg: $crate::Register}),
            (0x69, RowSum,   "ROWSUM",   "Pop row; push the sum of all linear values in that grid row.",
             {reg: $crate::Register}),
            (0x6A, ColSum,   "COLSUM",   "Pop col; push the sum of all linear values in that grid column.",
             {reg: $crate::Register}),
            // ---------------------------------------------------------------
            // XQMX High-Level Constraints
            // ---------------------------------------------------------------
            (0x70, OneHotR,  "ONEHOTR",  "Pop penalty and row; add a one-hot constraint over the grid row.",
             {reg: $crate::Register}),
            (0x71, OneHotC,  "ONEHOTC",  "Pop penalty and col; add a one-hot constraint over the grid column.",
             {reg: $crate::Register}),
            (0x72, Exclude,  "EXCLUDE",  "Pop penalty, j, and i; add a mutual-exclusion constraint between variables i and j.",
             {reg: $crate::Register}),
            (0x73, Implies,  "IMPLIES",  "Pop penalty, j, and i; add an implication constraint from variable i to variable j.",
             {reg: $crate::Register}),
            (0x7F, Energy,   "ENERGY",   "Compute the Hamiltonian energy of a sample against a model; push the result.",
             {model: $crate::Register, sample: $crate::Register}),
        }
    };
}
```

- [ ] **Step 3: Run compile check**

```sh
cargo check -p aglais-xqvm-bytecode 2>&1 | head -40
```

Expected: compilation errors in crates that depend on `bytecode` (the downstream crates reference old variant names). The `bytecode` crate itself should compile. If `bytecode` fails, fix it before proceeding.

---

### Task 2: Update `builder.rs`

**Files:**
- Modify: `crates/bytecode/src/builder.rs`

The `impl_builder_methods!` macro auto-generates methods by converting `PascalCase` variant names to `snake_case` (via `pastey::paste!`). The new variants produce these auto-generated names:
- `Drop` -> `fn drop(&mut self, reg: Register)` -- `drop` is not a Rust keyword; it is safe as a method name
- `Sclr` -> `fn sclr(&mut self)`
- `Sqr` -> `fn sqr(&mut self)`
- `Abs` -> `fn abs(&mut self)`
- `Min` -> `fn min(&mut self)`
- `Max` -> `fn max(&mut self)`
- `Inc` -> `fn inc(&mut self)`
- `Dec` -> `fn dec(&mut self)`
- `OneHotC` -> `fn one_hot_c(&mut self, reg: Register)`
- `OneHotR` -> `fn one_hot_r(&mut self, reg: Register)` (renamed from `one_hot`)
- `Copy` -> `fn copy(&mut self)` (renamed from `dupl`)

> **Grammar note:** The pest grammar rule `mnemonic = @{ ASCII_ALPHA ~ (ASCII_ALPHA | ASCII_DIGIT)* }` already matches `PUSH1..PUSH8` and all other new/renamed mnemonics (they are all alphanumeric). No changes to `grammar.pest` are needed.

The macro handles all of the above automatically. The only manual changes needed in `builder.rs` are:

1. **Update the `push()` method** -- it currently references `PushC1..PushC8`; update to `Push1..Push8`.
2. **Update the `impl_builder_methods!` skip arm for push variants** -- currently matches `{val: ...}`; remains correct since the field name is still `val`.
3. **Update doc comments** -- find and replace `PUSHC_N` references in doc strings with `PUSH_N`.
4. **Remove or update any method that was hand-written for old variants** -- search for `fn dupl`, `fn onehot`, `fn push_c` in the file and update.

- [ ] **Step 1: Find all hand-written references to old variant names in builder.rs**

```sh
grep -n "dupl\|push_c\|one_hot\b\|PushC\|PUSHC" crates/bytecode/src/builder.rs
```

- [ ] **Step 2: Update `minimal_pushc` and doc comments in `builder.rs`**

The `push()` method delegates to `minimal_pushc(val: i64) -> Instruction` (a private helper near the bottom of the file). Update `minimal_pushc` to use the new variant names. The logic (sign-extension range checks) is unchanged; only the `Instruction::PushCN` names change to `Instruction::PushN`:

```rust
fn minimal_pushc(val: i64) -> Instruction {
    let be = val.to_be_bytes();
    for n in 1usize..=7 {
        let bits = (n * 8) as u32;
        let shift = 64 - bits;
        if (val << shift) >> shift == val {
            return match n {
                1 => Instruction::Push1 { val: [be[7]] },
                2 => Instruction::Push2 { val: [be[6], be[7]] },
                3 => Instruction::Push3 { val: [be[5], be[6], be[7]] },
                4 => Instruction::Push4 { val: [be[4], be[5], be[6], be[7]] },
                5 => Instruction::Push5 { val: [be[3], be[4], be[5], be[6], be[7]] },
                6 => Instruction::Push6 { val: [be[2], be[3], be[4], be[5], be[6], be[7]] },
                7 => Instruction::Push7 {
                    val: [be[1], be[2], be[3], be[4], be[5], be[6], be[7]],
                },
                _ => unreachable!(),
            };
        }
    }
    Instruction::Push8 { val: be }
}
```

Note: the old function had a special case `if val == 0 { return Instruction::PushC0 {} }` at the top. Remove that branch -- `val == 0` now encodes as `Push1 { val: [0x00] }` (the loop handles it since 0 fits in 1 byte).

Also update all doc comments in `builder.rs` that reference `PUSHC_N`:
- Struct-level doc comment: "Use push() to emit the smallest `PUSHC_N` instruction" -> "Use push() to emit the smallest `PUSH1..PUSH8` instruction"
- Any `/// PUSHC_0`, `/// PUSHC_1..PUSHC_8` references in examples

Search with: `grep -n "PUSHC\|PushC" crates/bytecode/src/builder.rs`

- [ ] **Step 3: Update codec.rs, stream.rs, and lib.rs doc examples and tests**

These files contain doc examples and unit tests that hard-code old variant names and opcode bytes. Find them all:

```sh
grep -rn "PushC\|0x10\b\|0x0F\b\|0x14\b\|0x15\b\|0x16\b\|0x17\b" \
  crates/bytecode/src/codec.rs \
  crates/bytecode/src/stream.rs \
  crates/bytecode/src/lib.rs
```

For each hit:
- `Instruction::PushC0 {}` -> `Instruction::Push1 { val: [0x00] }`
- `Instruction::PushCN { val: [...] }` -> `Instruction::PushN { val: [...] }` (same bytes, new name)
- `Opcode::PushC0` -> `Opcode::Push1`
- `0x10 // PUSHC_0` -> `0x11 // PUSH1`
- `0x0F // HALT` -> `0x09 // HALT`
- `0x14 // LOAD` -> `0x0A // LOAD`
- etc.

- [ ] **Step 4: Verify the builder compiles and tests pass**

```sh
cargo test -p aglais-xqvm-bytecode 2>&1 | tail -20
```

Expected: all pass. If tests fail on hard-coded byte values, fix them as described in Step 3.

- [ ] **Step 5: Run full lint on bytecode crate**

```sh
cargo clippy -p aglais-xqvm-bytecode --all-targets --all-features -- -D warnings 2>&1 | head -40
RUSTDOCFLAGS="-D warnings" cargo doc -p aglais-xqvm-bytecode 2>&1 | head -20
```

Expected: zero warnings from both clippy and rustdoc.

---

### Task 3: Commit Layer 1

- [ ] **Step 1: Run full bytecode test suite**

```sh
make test-unit 2>&1 | tail -30
```

- [ ] **Step 2: Commit**

```sh
git add crates/bytecode/
git commit -s -m "Recode and extend XQVM opcode table to match spec (68 -> 76 opcodes)"
```

---

## Layer 2: `crates/disasm`

### Task 4: Verify disasm

The disasm crate is X-macro driven and should recompile cleanly from the updated table. No source changes are expected.

**Files:**
- Verify: `crates/disasm/src/` (no edits expected)

- [ ] **Step 1: Build disasm**

```sh
cargo build -p aglais-xqvm-disasm 2>&1 | head -20
```

Expected: compiles cleanly. If there are errors, they are due to hard-coded opcode references -- fix them.

- [ ] **Step 2: Run disasm tests**

```sh
cargo test -p aglais-xqvm-disasm 2>&1 | tail -20
```

If snapshot tests exist, update them: `INSTA_UPDATE=new cargo test -p aglais-xqvm-disasm` or equivalent.

- [ ] **Step 3: Spot-check output format**

First check the actual disassembler binary name in `crates/disasm/Cargo.toml`:
```sh
grep 'name' crates/disasm/Cargo.toml | head -5
```

Then run a round-trip:
```sh
printf 'PUSH 0\nHALT\n' | cargo run --bin xqasm -- /dev/stdin \
  | cargo run --bin <disasm-bin-name> -- /dev/stdin 2>/dev/null || true
```

Verify rendered mnemonics use `PUSH1`, not `PUSHC_1`, and `HALT` appears.

- [ ] **Step 4: Commit**

```sh
git add crates/disasm/
git commit -s -m "Verify disasm recompiles cleanly against updated opcode table"
```

---

## Layer 3: `crates/asm`

### Task 5: Update assembler special-case handling

**Files:**
- Modify: `crates/asm/src/assembler.rs`

The `build_instr` x-macro match already handles all renamed opcodes automatically (it matches on `$mnem` string literals from the table). The only manual changes needed are:

1. The `"PUSH" | "PUSHC"` special case -- update the match arm to also accept explicit `PUSH1..PUSH8`.
2. Update the doc comment in `assemble()` that references `PUSHC_0`.
3. Update the doc-test assertion `program.code()[0] == 0x10 // PUSHC_0` to reflect the new encoding (`PUSH1` = `0x11`).

- [ ] **Step 1: Locate and update the push special case**

Find the arm:
```rust
"PUSH" | "PUSHC" => {
    assemble_push(instr, &mut b, src)?;
}
```

Replace with:
```rust
"PUSH" | "PUSHC" | "PUSH1" | "PUSH2" | "PUSH3" | "PUSH4"
| "PUSH5" | "PUSH6" | "PUSH7" | "PUSH8" => {
    assemble_push(instr, &mut b, src)?;
}
```

This makes `PUSH1 42`, `PUSH2 256`, etc. valid in assembly. All of these accept a **single integer operand** and route through `assemble_push`, which calls `b.push(value)` and lets the builder pick the minimal encoding. The explicit width suffix is a readability hint only -- `PUSH2 42` emits `PUSH1` because 42 fits in 1 byte.

> **Not supported:** The multi-byte literal form `PUSH2 0x01 0x00` (two separate hex operands) is not valid in this assembler. `check_operand_count(instr, 1, src)?` will reject it with `WrongOperandCount`. Document this limitation in the function's doc comment.

- [ ] **Step 2: Update doc comments**

In `assembler.rs`, find all references to `PUSHC_0`, `PUSHC_N`, `0x10 // PUSHC_0`, `0x0F // HALT` and update:
- `PUSHC_0` -> `PUSH1` (sugar for value 0 uses `PUSH1 0x00`)
- `0x10 // PUSHC_0` -> `0x11 // PUSH1` (PUSH1 opcode = 0x11)
- `0x0F // HALT` -> `0x09 // HALT`

- [ ] **Step 3: Build and test asm crate**

```sh
cargo test -p aglais-xqvm-asm 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 4: Lint**

```sh
cargo clippy -p aglais-xqvm-asm --all-targets --all-features -- -D warnings 2>&1 | head -20
```

---

### Task 6: Update TSP example `.xqasm` files

**Files:**
- Modify: `crates/vm/examples/tsp/encoder.xqasm`
- Check: `crates/vm/examples/tsp/decoder.xqasm`
- Check: `crates/vm/examples/tsp/verifier.xqasm`

`grep` found two usages in `encoder.xqasm`: `DUPL` (line 29) and `ONEHOT r4` (line 106).

- [ ] **Step 1: Update `encoder.xqasm`**

```sh
sed -i 's/\bDUPL\b/COPY/g; s/\bONEHOT\b/ONEHOTR/g' crates/vm/examples/tsp/encoder.xqasm
```

Verify with:
```sh
grep -n "DUPL\|ONEHOT\b\|PUSHC" crates/vm/examples/tsp/encoder.xqasm
```

Expected: no matches.

- [ ] **Step 2: Check decoder and verifier**

```sh
grep -n "DUPL\|ONEHOT\b\|PUSHC" crates/vm/examples/tsp/decoder.xqasm crates/vm/examples/tsp/verifier.xqasm
```

Apply same replacements if any matches are found.

- [ ] **Step 3: Commit Layer 3**

```sh
git add crates/asm/ crates/vm/examples/tsp/
git commit -s -m "Update assembler sugar and TSP examples for renamed opcodes"
```

---

## Layer 4: `crates/vm`

### Task 7: Add `StackOverflow` error and `push_stack` helper

**Files:**
- Modify: `crates/vm/src/error.rs`
- Modify: `crates/vm/src/vm.rs`

- [ ] **Step 1: Write a failing test for stack overflow**

Add to `crates/vm/src/vm.rs` in the `#[cfg(test)]` block:

```rust
#[test]
fn stack_overflow_is_reported() {
    // RANGE loop with count=8193: pushes 1 value per iteration without
    // popping. After 8192 successful pushes (stack len = 8192), the 8193rd
    // push triggers `stack.len() >= STACK_LIMIT` (8192 >= 8192) -> overflow.
    let mut b = InstructionBuilder::new();
    b.push(0).push(8193).range();
    b.push(1);
    b.next();
    b.halt();
    let program = b.build().unwrap();

    let mut vm = Vm::new();
    let err = vm.run(&program).unwrap_err();
    assert!(
        matches!(err, Error::StackOverflow { .. }),
        "expected StackOverflow, got {err:?}"
    );
}
```

Run to confirm it fails (StackOverflow variant does not exist yet):
```sh
cargo test -p aglais-xqvm-vm stack_overflow 2>&1 | tail -10
```

- [ ] **Step 2: Add `StackOverflow` to `error.rs`**

In `crates/vm/src/error.rs`, add after the `StepLimitExceeded` variant:

```rust
/// The value stack exceeded the maximum allowed depth of 8192.
#[error("stack overflow at byte {pos:#06x}: depth exceeded 8192")]
StackOverflow { pos: usize },
```

Also add `pos` to the `byte_pos()` match arm:
```rust
Self::StackOverflow { pos } => Some(*pos),
```

- [ ] **Step 3: Introduce `STACK_LIMIT` const and `push_stack` helper in `vm.rs`**

Add a module-level const immediately after `DEFAULT_STEP_LIMIT` (line ~111):

```rust
/// Maximum stack depth enforced by [`Vm`].
const STACK_LIMIT: usize = 8192;
```

Replace the existing `push_val` helper:

```rust
fn push_val(&mut self, v: i64) {
    self.stack.push(v);
}
```

With (note: module-level const, not `Self::STACK_LIMIT`):

```rust
fn push_stack(&mut self, v: i64, pos: usize) -> Result<(), Error> {
    if self.stack.len() >= STACK_LIMIT {
        return Err(Error::StackOverflow { pos });
    }
    self.stack.push(v);
    Ok(())
}
```

- [ ] **Step 4: Update all call sites of `push_val` -> `push_stack`**

Every `self.push_val(v)` call must become `self.push_stack(v, pos)?`. Use:

```sh
grep -n "push_val" crates/vm/src/vm.rs
```

Important: some handlers declare their `pos` parameter with a leading underscore (`_pos: usize`) to suppress the unused-variable lint. When these handlers are updated to call `push_stack(v, pos)`, the parameter must be renamed from `_pos` to `pos` -- otherwise `-D warnings` will fire for `unused_variables`. Check every handler that pushes. For example, `exec_push_c1` currently has `_pos: usize`; after renaming to `exec_push1` and calling `push_stack`, change the signature to `pos: usize`.

- [ ] **Step 5: Run the overflow test**

```sh
cargo test -p aglais-xqvm-vm stack_overflow 2>&1 | tail -10
```

Expected: PASS.

---

### Task 8: Rename changed VM match arms

**Files:**
- Modify: `crates/vm/src/vm.rs`

The `impl_dispatch!` macro generates calls to `self.exec_<variant_snake>()`. Renamed variants automatically call the new function name. We must rename the handler functions to match.

- [ ] **Step 1: Rename push handler functions**

Find and rename:
- `exec_push_c0` (delete -- `PushC0` variant is gone)
- `exec_push_c1` -> `exec_push1`
- `exec_push_c2` -> `exec_push2`
- ... through `exec_push_c8` -> `exec_push8`

Each handler body is identical (calls `sign_extend_be` and `push_stack`). Use a bulk rename:

```sh
sed -i \
  's/fn exec_push_c0/fn exec_push_c0_DELETED/g;
   s/fn exec_push_c1/fn exec_push1/g;
   s/fn exec_push_c2/fn exec_push2/g;
   s/fn exec_push_c3/fn exec_push3/g;
   s/fn exec_push_c4/fn exec_push4/g;
   s/fn exec_push_c5/fn exec_push5/g;
   s/fn exec_push_c6/fn exec_push6/g;
   s/fn exec_push_c7/fn exec_push7/g;
   s/fn exec_push_c8/fn exec_push8/g' \
  crates/vm/src/vm.rs
```

Then **immediately** remove the `exec_push_c0_DELETED` function body from the file before proceeding to Step 4 (the push_val -> push_stack migration). The deleted function calls `self.push_val(0)`; if it is still present when push_val is renamed, the file will fail to compile.

- [ ] **Step 2: Rename `exec_dupl` -> `exec_copy`**

```sh
sed -i 's/fn exec_dupl/fn exec_copy/g' crates/vm/src/vm.rs
```

- [ ] **Step 3: Rename `exec_one_hot` -> `exec_one_hot_r`**

```sh
sed -i 's/fn exec_one_hot\b/fn exec_one_hot_r/g' crates/vm/src/vm.rs
```

- [ ] **Step 4: Compile check**

```sh
cargo check -p aglais-xqvm-vm 2>&1 | head -30
```

Expected: errors only for missing `exec_drop`, `exec_sclr`, `exec_sqr`, `exec_abs`, `exec_min`, `exec_max`, `exec_inc`, `exec_dec`, `exec_one_hot_c` (not yet written). Fix any other errors first.

---

### Task 9: Add new instruction handlers

**Files:**
- Modify: `crates/vm/src/vm.rs`
- Modify: `crates/vm/src/model.rs`

- [ ] **Step 1: Add arithmetic handlers**

Add after `exec_neg`:

```rust
fn exec_sqr(&mut self, pos: usize) -> Result<StepResult, Error> {
    let a = self.pop(pos)?;
    self.push_stack(a.wrapping_mul(a), pos)?;
    Ok(StepResult::Continue)
}

fn exec_abs(&mut self, pos: usize) -> Result<StepResult, Error> {
    let a = self.pop(pos)?;
    self.push_stack(a.wrapping_abs(), pos)?;
    Ok(StepResult::Continue)
}

fn exec_min(&mut self, pos: usize) -> Result<StepResult, Error> {
    let b = self.pop(pos)?;
    let a = self.pop(pos)?;
    self.push_stack(a.min(b), pos)?;
    Ok(StepResult::Continue)
}

fn exec_max(&mut self, pos: usize) -> Result<StepResult, Error> {
    let b = self.pop(pos)?;
    let a = self.pop(pos)?;
    self.push_stack(a.max(b), pos)?;
    Ok(StepResult::Continue)
}

fn exec_inc(&mut self, pos: usize) -> Result<StepResult, Error> {
    let a = self.pop(pos)?;
    self.push_stack(a.wrapping_add(1), pos)?;
    Ok(StepResult::Continue)
}

fn exec_dec(&mut self, pos: usize) -> Result<StepResult, Error> {
    let a = self.pop(pos)?;
    self.push_stack(a.wrapping_sub(1), pos)?;
    Ok(StepResult::Continue)
}
```

- [ ] **Step 2: Add stack/register handlers**

```rust
fn exec_drop(&mut self, _pos: usize, reg: Register) -> Result<StepResult, Error> {
    *self.reg_mut(reg) = RegVal::Int(0);
    Ok(StepResult::Continue)
}

fn exec_sclr(&mut self, _pos: usize) -> Result<StepResult, Error> {
    self.stack.clear();
    Ok(StepResult::Continue)
}
```

- [ ] **Step 3: Add `exec_one_hot_c` handler**

The column one-hot mirrors `exec_one_hot_r` with row/col axes swapped. Add after `exec_one_hot_r`:

```rust
fn exec_one_hot_c(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
    let penalty = self.pop(pos)?;
    let col = self.pop(pos)?;
    let m = self
        .reg_mut(reg)
        .as_model_mut()
        .map_err(|got| Error::RegisterType {
            reg: reg.slot(),
            expected: "model",
            got,
        })?;
    let col_usize = col as usize;
    // H = penalty * (sum(x_i for i in col) - 1)^2
    // Linear: -penalty per variable in column
    // Quadratic: 2*penalty per pair in column
    for r in 0..m.rows {
        m.add_linear(r * m.cols + col_usize, -penalty);
    }
    for ri in 0..m.rows {
        for rj in (ri + 1)..m.rows {
            m.add_quad(ri * m.cols + col_usize, rj * m.cols + col_usize, 2 * penalty);
        }
    }
    Ok(StepResult::Continue)
}
```

- [ ] **Step 4: Write unit tests for new instructions**

Add to the `#[cfg(test)]` block:

```rust
#[test]
fn sqr_abs_min_max_inc_dec() {
    use aglais_xqvm_bytecode::InstructionBuilder;

    let cases: &[(&dyn Fn(&mut InstructionBuilder), i64)] = &[
        (&|b: &mut InstructionBuilder| { b.push(5).sqr(); }, 25),
        (&|b: &mut InstructionBuilder| { b.push(-3).abs(); }, 3),
        (&|b: &mut InstructionBuilder| { b.push(3).push(7).min(); }, 3),
        (&|b: &mut InstructionBuilder| { b.push(3).push(7).max(); }, 7),
        (&|b: &mut InstructionBuilder| { b.push(10).inc(); }, 11),
        (&|b: &mut InstructionBuilder| { b.push(10).dec(); }, 9),
    ];
    for (setup, expected) in cases {
        let mut b = InstructionBuilder::new();
        setup(&mut b);
        b.halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[*expected]);
    }
}

#[test]
fn drop_clears_register() {
    use aglais_xqvm_bytecode::{InstructionBuilder, Register};
    let mut b = InstructionBuilder::new();
    b.push(42).stow(Register(0)).drop(Register(0)).halt();
    let program = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&program).unwrap();
    assert_eq!(vm.register(0), &RegVal::Int(0));
}

#[test]
fn sclr_empties_stack() {
    use aglais_xqvm_bytecode::InstructionBuilder;
    let mut b = InstructionBuilder::new();
    b.push(1).push(2).push(3).sclr().halt();
    let program = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&program).unwrap();
    assert_eq!(vm.stack(), &[] as &[i64]);
}
```

- [ ] **Step 5: Run new tests**

```sh
cargo test -p aglais-xqvm-vm sqr_abs_min_max drop_clears sclr_empties 2>&1 | tail -15
```

Expected: all PASS.

---

### Task 10: Final validation and commit

- [ ] **Step 1: Run full workspace lint and test**

```sh
make lint && make test
```

Expected: zero warnings, all tests pass. If the TSP example is a test target, it must pass end-to-end.

- [ ] **Step 2: Commit Layer 4**

```sh
git add crates/vm/
git commit -s -m "Add 9 new VM instruction handlers and enforce 8192-element stack limit"
```

- [ ] **Step 3: Final integration check**

```sh
make all
```

Expected: exits 0.
