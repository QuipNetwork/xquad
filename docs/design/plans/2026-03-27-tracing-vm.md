# Tracing VM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add zero-cost execution tracing to the XQVM interpreter with text and JSON output formats.

**Architecture:** Generic `Tracer` trait injected via type parameter into `Vm::run_trace`. Monomorphization with `NoopTracer` produces identical code to the current `run`. Concrete `TextTracer` / `JsonTracer` formatters live behind `#[cfg(feature = "std")]`. CLI gets `--trace`, `--trace-format`, `--trace-file` flags.

**Tech Stack:** Rust, existing workspace (`aglais-xqvm-bytecode`, `aglais-xqvm-vm`), clap, no new dependencies.

**Spec:** `docs/design/specs/2026-03-27-tracing-vm-design.md`

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `crates/bytecode/src/types/register_effect.rs` | `RegisterEffect` type + `Instruction::read_registers` / `written_registers` methods |
| `crates/vm/src/tracer/mod.rs` | `Tracer` trait, `StepState`, `NoopTracer`, re-exports |
| `crates/vm/src/tracer/text.rs` | `TextTracer<W>` (std-only) |
| `crates/vm/src/tracer/json.rs` | `JsonTracer<W>` (std-only) |

### Modified files

| File | Change |
|---|---|
| `crates/bytecode/src/types/mod.rs` | Add `mod register_effect;` + re-export `RegisterEffect` |
| `crates/bytecode/src/types/instruction.rs` | Add `Display` impl, `read_registers`, `written_registers` |
| `crates/bytecode/src/lib.rs` | Re-export `RegisterEffect` |
| `crates/vm/src/lib.rs` | Add `mod tracer;` + re-exports |
| `crates/vm/src/error.rs` | Add `TraceFailed` variant + update `byte_pos()` |
| `crates/vm/src/vm.rs` | Add `run_trace`, refactor `run` to delegate |
| `crates/vm/src/bin/xqvm.rs` | Add `--trace`, `--trace-format`, `--trace-file` CLI flags |

---

## Task 1: Register Effect Type (bytecode crate)

**Files:**
- Create: `crates/bytecode/src/types/register_effect.rs`
- Modify: `crates/bytecode/src/types/mod.rs`
- Modify: `crates/bytecode/src/lib.rs`

- [ ] **Step 1: Write the failing test**

Add to the bottom of `crates/bytecode/src/types/register_effect.rs` (we create the file with the test first):

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

//! Register effect metadata for instructions.

/// A compact set of register indices (0-2 elements, stack-allocated).
///
/// Used by [`Instruction::read_registers`] and [`Instruction::written_registers`]
/// to report which registers an instruction accesses without heap allocation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RegisterEffect {
    buf: [u8; 2],
    len: u8,
}

impl RegisterEffect {
    /// Empty set (no registers).
    pub const EMPTY: Self = Self { buf: [0; 2], len: 0 };

    /// Single-register set.
    pub const fn one(r: u8) -> Self {
        Self { buf: [r, 0], len: 1 }
    }

    /// Two-register set.
    pub const fn two(a: u8, b: u8) -> Self {
        Self { buf: [a, b], len: 2 }
    }

    /// View as a slice.
    pub fn as_slice(&self) -> &[u8] {
        &self.buf[..self.len as usize]
    }

    /// Number of registers in the set.
    pub fn len(&self) -> usize {
        self.len as usize
    }

    /// Whether the set is empty.
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_register_effect() {
        let e = RegisterEffect::EMPTY;
        assert!(e.is_empty());
        assert_eq!(e.len(), 0);
        assert_eq!(e.as_slice(), &[]);
    }

    #[test]
    fn one_register_effect() {
        let e = RegisterEffect::one(5);
        assert!(!e.is_empty());
        assert_eq!(e.len(), 1);
        assert_eq!(e.as_slice(), &[5]);
    }

    #[test]
    fn two_register_effect() {
        let e = RegisterEffect::two(3, 7);
        assert_eq!(e.len(), 2);
        assert_eq!(e.as_slice(), &[3, 7]);
    }
}
```

- [ ] **Step 2: Wire up the module**

In `crates/bytecode/src/types/mod.rs`, add after `mod operand;`:
```rust
mod register_effect;
```
And update the re-export block:
```rust
pub use self::{
    instruction::Instruction,
    opcode::{DecodeError, Opcode},
    operand::Register,
    register_effect::RegisterEffect,
};
```

In `crates/bytecode/src/lib.rs`, add to the re-export line:
```rust
pub use types::{Instruction, Opcode, Register, RegisterEffect};
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cargo nextest run -p aglais-xqvm-bytecode --lib -- register_effect`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```
Add RegisterEffect type for register introspection

Implements QUI-314
```

---

## Task 2: Register Introspection Methods (bytecode crate)

**Files:**
- Modify: `crates/bytecode/src/types/instruction.rs`

- [ ] **Step 1: Write the failing tests**

Add tests to the existing `#[cfg(test)] mod tests` block in `crates/bytecode/src/types/instruction.rs`:

```rust
    #[test]
    fn read_registers_stack_only() {
        // Stack-only instructions read no registers.
        let add = Instruction::Add {};
        assert!(add.read_registers().is_empty());
        assert!(add.written_registers().is_empty());
    }

    #[test]
    fn read_registers_load() {
        // LOAD r5 reads r5.
        let load = Instruction::Load { reg: Register(5) };
        assert_eq!(load.read_registers().as_slice(), &[5]);
        assert!(load.written_registers().is_empty());
    }

    #[test]
    fn written_registers_stow() {
        // STOW r3 writes r3.
        let stow = Instruction::Stow { reg: Register(3) };
        assert!(stow.read_registers().is_empty());
        assert_eq!(stow.written_registers().as_slice(), &[3]);
    }

    #[test]
    fn read_write_registers_vec_push() {
        // VECPUSH r0 reads and writes r0.
        let vp = Instruction::VecPush { reg: Register(0) };
        assert_eq!(vp.read_registers().as_slice(), &[0]);
        assert_eq!(vp.written_registers().as_slice(), &[0]);
    }

    #[test]
    fn read_registers_energy() {
        // ENERGY r1 r2 reads both.
        let e = Instruction::Energy {
            model: Register(1),
            sample: Register(2),
        };
        assert_eq!(e.read_registers().as_slice(), &[1, 2]);
        assert!(e.written_registers().is_empty());
    }

    #[test]
    fn all_variants_covered_by_read_and_written() {
        // Ensure no panic for any instruction variant.
        for (instr, _, _) in opcodes!(all_instruction_opcode_pairs) {
            let _ = instr.read_registers();
            let _ = instr.written_registers();
        }
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo nextest run -p aglais-xqvm-bytecode --lib -- read_registers`
Expected: FAIL — method `read_registers` not found

- [ ] **Step 3: Implement `read_registers` and `written_registers`**

Add a new `impl Instruction` block in `crates/bytecode/src/types/instruction.rs`, before `#[cfg(test)]`:

```rust
use super::RegisterEffect;

impl Instruction {
    /// Register indices this instruction reads from.
    ///
    /// Returns the set of register slots whose current values are consumed
    /// by this instruction. Stack-only and control-flow-only instructions
    /// return an empty set.
    pub fn read_registers(&self) -> RegisterEffect {
        match self {
            // -- No register reads (48 variants) --
            // Control flow
            Self::Nop { .. }
            | Self::Target { .. }
            | Self::Jump { .. }
            | Self::JumpI { .. }
            | Self::Next { .. }
            | Self::Range { .. }
            | Self::Halt { .. }
            // Stack manipulation
            | Self::Pop { .. }
            | Self::Push1 { .. }
            | Self::Push2 { .. }
            | Self::Push3 { .. }
            | Self::Push4 { .. }
            | Self::Push5 { .. }
            | Self::Push6 { .. }
            | Self::Push7 { .. }
            | Self::Push8 { .. }
            | Self::Sclr { .. }
            | Self::Swap { .. }
            | Self::Copy { .. }
            // Arithmetic
            | Self::Add { .. }
            | Self::Sub { .. }
            | Self::Mul { .. }
            | Self::Div { .. }
            | Self::Modulo { .. }
            | Self::Sqr { .. }
            | Self::Abs { .. }
            | Self::Neg { .. }
            | Self::Min { .. }
            | Self::Max { .. }
            | Self::Inc { .. }
            | Self::Dec { .. }
            // Comparison
            | Self::Eq { .. }
            | Self::Lt { .. }
            | Self::Gt { .. }
            | Self::Lte { .. }
            | Self::Gte { .. }
            // Logical
            | Self::Not { .. }
            | Self::And { .. }
            | Self::Or { .. }
            | Self::Xor { .. }
            // Bitwise
            | Self::BAnd { .. }
            | Self::BOr { .. }
            | Self::BXor { .. }
            | Self::BNot { .. }
            | Self::Shl { .. }
            | Self::Shr { .. }
            // Index math
            | Self::IdxGrid { .. }
            | Self::IdxTriu { .. }
            // Write-only: allocators
            | Self::Stow { .. }
            | Self::Drop { .. }
            | Self::Input { .. }
            | Self::LVal { .. }
            | Self::Bqmx { .. }
            | Self::Sqmx { .. }
            | Self::Xqmx { .. }
            | Self::Bsmx { .. }
            | Self::Ssmx { .. }
            | Self::Xsmx { .. }
            | Self::Vec { .. }
            | Self::VecI { .. }
            | Self::VecX { .. } => RegisterEffect::EMPTY,

            // -- Single register reads (22 variants) --
            // Read-only
            Self::Load { reg }
            | Self::Output { reg }
            | Self::Iter { reg }
            | Self::VecGet { reg }
            | Self::VecLen { reg }
            | Self::GetLine { reg }
            | Self::GetQuad { reg }
            | Self::RowFind { reg }
            | Self::ColFind { reg }
            | Self::RowSum { reg }
            | Self::ColSum { reg }
            // Read+write
            | Self::VecPush { reg }
            | Self::VecSet { reg }
            | Self::SetLine { reg }
            | Self::AddLine { reg }
            | Self::SetQuad { reg }
            | Self::AddQuad { reg }
            | Self::Resize { reg }
            | Self::OneHotR { reg }
            | Self::OneHotC { reg }
            | Self::Exclude { reg }
            | Self::Implies { reg } => RegisterEffect::one(reg.slot()),

            // -- Two register reads (1 variant) --
            Self::Energy { model, sample } => {
                RegisterEffect::two(model.slot(), sample.slot())
            }
        }
    }

    /// Register indices this instruction writes to.
    ///
    /// Returns the set of register slots whose values are modified by this
    /// instruction. Stack-only, control-flow-only, and read-only register
    /// instructions return an empty set.
    pub fn written_registers(&self) -> RegisterEffect {
        match self {
            // -- No register writes (60 variants) --
            // Control flow
            Self::Nop { .. }
            | Self::Target { .. }
            | Self::Jump { .. }
            | Self::JumpI { .. }
            | Self::Next { .. }
            | Self::Range { .. }
            | Self::Halt { .. }
            // Stack manipulation
            | Self::Pop { .. }
            | Self::Push1 { .. }
            | Self::Push2 { .. }
            | Self::Push3 { .. }
            | Self::Push4 { .. }
            | Self::Push5 { .. }
            | Self::Push6 { .. }
            | Self::Push7 { .. }
            | Self::Push8 { .. }
            | Self::Sclr { .. }
            | Self::Swap { .. }
            | Self::Copy { .. }
            // Arithmetic
            | Self::Add { .. }
            | Self::Sub { .. }
            | Self::Mul { .. }
            | Self::Div { .. }
            | Self::Modulo { .. }
            | Self::Sqr { .. }
            | Self::Abs { .. }
            | Self::Neg { .. }
            | Self::Min { .. }
            | Self::Max { .. }
            | Self::Inc { .. }
            | Self::Dec { .. }
            // Comparison
            | Self::Eq { .. }
            | Self::Lt { .. }
            | Self::Gt { .. }
            | Self::Lte { .. }
            | Self::Gte { .. }
            // Logical
            | Self::Not { .. }
            | Self::And { .. }
            | Self::Or { .. }
            | Self::Xor { .. }
            // Bitwise
            | Self::BAnd { .. }
            | Self::BOr { .. }
            | Self::BXor { .. }
            | Self::BNot { .. }
            | Self::Shl { .. }
            | Self::Shr { .. }
            // Index math
            | Self::IdxGrid { .. }
            | Self::IdxTriu { .. }
            // Read-only register ops
            | Self::Load { .. }
            | Self::Output { .. }
            | Self::Iter { .. }
            | Self::VecGet { .. }
            | Self::VecLen { .. }
            | Self::GetLine { .. }
            | Self::GetQuad { .. }
            | Self::RowFind { .. }
            | Self::ColFind { .. }
            | Self::RowSum { .. }
            | Self::ColSum { .. }
            // Energy: reads two registers, writes none
            | Self::Energy { .. } => RegisterEffect::EMPTY,

            // -- Single register writes (24 variants) --
            // Write-only
            Self::Stow { reg }
            | Self::Drop { reg }
            | Self::Input { reg }
            | Self::LVal { reg }
            | Self::Bqmx { reg }
            | Self::Sqmx { reg }
            | Self::Xqmx { reg }
            | Self::Bsmx { reg }
            | Self::Ssmx { reg }
            | Self::Xsmx { reg }
            | Self::Vec { reg }
            | Self::VecI { reg }
            | Self::VecX { reg }
            // Read+write
            | Self::VecPush { reg }
            | Self::VecSet { reg }
            | Self::SetLine { reg }
            | Self::AddLine { reg }
            | Self::SetQuad { reg }
            | Self::AddQuad { reg }
            | Self::Resize { reg }
            | Self::OneHotR { reg }
            | Self::OneHotC { reg }
            | Self::Exclude { reg }
            | Self::Implies { reg } => RegisterEffect::one(reg.slot()),
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo nextest run -p aglais-xqvm-bytecode --lib`
Expected: all PASS (existing + new)

- [ ] **Step 5: Commit**

```
Add register introspection to Instruction

Implements QUI-314
```

---

## Task 3: Display Impl for Instruction (bytecode crate)

**Files:**
- Modify: `crates/bytecode/src/types/instruction.rs`

- [ ] **Step 1: Write the failing test**

Add to the `#[cfg(test)] mod tests` in `crates/bytecode/src/types/instruction.rs`:

```rust
    use alloc::format;

    #[test]
    fn display_no_operands() {
        assert_eq!(format!("{}", Instruction::Add {}), "ADD");
        assert_eq!(format!("{}", Instruction::Halt {}), "HALT");
        assert_eq!(format!("{}", Instruction::Nop {}), "NOP");
    }

    #[test]
    fn display_register_operand() {
        assert_eq!(
            format!("{}", Instruction::Load { reg: Register(5) }),
            "LOAD r5",
        );
        assert_eq!(
            format!("{}", Instruction::Stow { reg: Register(0) }),
            "STOW r0",
        );
    }

    #[test]
    fn display_push() {
        assert_eq!(
            format!("{}", Instruction::Push8 { val: 42i64.to_be_bytes() }),
            "PUSH8 42",
        );
        assert_eq!(
            format!("{}", Instruction::Push1 { val: [0xFF] }),
            "PUSH1 -1",
        );
    }

    #[test]
    fn display_jump() {
        assert_eq!(
            format!("{}", Instruction::Jump { label: 3 }),
            "JUMP .3",
        );
    }

    #[test]
    fn display_energy() {
        assert_eq!(
            format!(
                "{}",
                Instruction::Energy { model: Register(0), sample: Register(1) }
            ),
            "ENERGY r0 r1",
        );
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo nextest run -p aglais-xqvm-bytecode --lib -- display`
Expected: FAIL — `Instruction doesn't implement fmt::Display`

- [ ] **Step 3: Implement Display**

Add to `crates/bytecode/src/types/instruction.rs`, before the `#[cfg(test)]` block. Use the existing `sign_extend_be`-style helper from `vm.rs` — but since this is the bytecode crate, define a local helper:

```rust
use core::fmt;

/// Sign-extend a big-endian byte slice (1..=8 bytes) to `i64`.
fn sign_extend_be(bytes: &[u8]) -> i64 {
    debug_assert!(!bytes.is_empty() && bytes.len() <= 8);
    let mut v = 0i64;
    for &b in bytes {
        v = (v << 8) | i64::from(b);
    }
    let shift = 64u32 - (bytes.len() * 8) as u32;
    (v << shift) >> shift
}

impl fmt::Display for Instruction {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            // Push variants: show decoded integer value.
            Self::Push1 { val } => write!(f, "PUSH1 {}", sign_extend_be(val)),
            Self::Push2 { val } => write!(f, "PUSH2 {}", sign_extend_be(val)),
            Self::Push3 { val } => write!(f, "PUSH3 {}", sign_extend_be(val)),
            Self::Push4 { val } => write!(f, "PUSH4 {}", sign_extend_be(val)),
            Self::Push5 { val } => write!(f, "PUSH5 {}", sign_extend_be(val)),
            Self::Push6 { val } => write!(f, "PUSH6 {}", sign_extend_be(val)),
            Self::Push7 { val } => write!(f, "PUSH7 {}", sign_extend_be(val)),
            Self::Push8 { val } => write!(f, "PUSH8 {}", sign_extend_be(val)),

            // Jump variants: show label index with dot prefix.
            Self::Jump { label } => write!(f, "JUMP .{label}"),
            Self::JumpI { label } => write!(f, "JUMPI .{label}"),

            // Energy: two register operands.
            Self::Energy { model, sample } => {
                write!(f, "ENERGY r{} r{}", model.slot(), sample.slot())
            }

            // All other single-register variants.
            Self::Load { reg }
            | Self::Stow { reg }
            | Self::Drop { reg }
            | Self::Input { reg }
            | Self::Output { reg }
            | Self::LVal { reg }
            | Self::Iter { reg }
            | Self::Bqmx { reg }
            | Self::Sqmx { reg }
            | Self::Xqmx { reg }
            | Self::Bsmx { reg }
            | Self::Ssmx { reg }
            | Self::Xsmx { reg }
            | Self::Vec { reg }
            | Self::VecI { reg }
            | Self::VecX { reg }
            | Self::VecPush { reg }
            | Self::VecGet { reg }
            | Self::VecSet { reg }
            | Self::VecLen { reg }
            | Self::GetLine { reg }
            | Self::SetLine { reg }
            | Self::AddLine { reg }
            | Self::GetQuad { reg }
            | Self::SetQuad { reg }
            | Self::AddQuad { reg }
            | Self::Resize { reg }
            | Self::RowFind { reg }
            | Self::ColFind { reg }
            | Self::RowSum { reg }
            | Self::ColSum { reg }
            | Self::OneHotR { reg }
            | Self::OneHotC { reg }
            | Self::Exclude { reg }
            | Self::Implies { reg } => write!(f, "{} r{}", self.mnemonic(), reg.slot()),

            // No-operand variants: just the mnemonic.
            _ => write!(f, "{}", self.mnemonic()),
        }
    }
}
```

Note: the final `_` catch-all covers `Nop`, `Target`, `Next`, `Range`, `Halt`, `Pop`, `Sclr`, `Swap`, `Copy`, and all arithmetic/comparison/logic/bitwise/index instructions (all zero-operand).

- [ ] **Step 4: Run tests**

Run: `cargo nextest run -p aglais-xqvm-bytecode --lib`
Expected: all PASS

- [ ] **Step 5: Commit**

```
Add Display impl for Instruction

Implements QUI-314
```

---

## Task 4: Tracer Trait and NoopTracer (VM crate)

**Files:**
- Create: `crates/vm/src/tracer/mod.rs`
- Modify: `crates/vm/src/lib.rs`
- Modify: `crates/vm/src/error.rs`

- [ ] **Step 1: Create the tracer module**

Create `crates/vm/src/tracer/mod.rs`:

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

//! Execution tracing for the XQVM interpreter.
//!
//! The [`Tracer`] trait is the observer interface injected into
//! [`Vm::run_trace`](crate::Vm::run_trace). A [`NoopTracer`] eliminates all
//! tracing overhead via monomorphization.
//!
//! Concrete formatters ([`TextTracer`], [`JsonTracer`]) are available behind
//! `#[cfg(feature = "std")]`.

#[cfg(feature = "std")]
mod json;
#[cfg(feature = "std")]
mod text;

#[cfg(feature = "std")]
pub use json::JsonTracer;
#[cfg(feature = "std")]
pub use text::TextTracer;

use aglais_xqvm_bytecode::Instruction;

use crate::value::RegVal;

/// Snapshot of VM state passed to the tracer after each instruction executes.
#[derive(Debug)]
pub struct StepState<'a> {
    /// Byte offset of the current instruction in the code section.
    pub pos: usize,
    /// Step number (1-based).
    pub step: u64,
    /// The instruction that just executed.
    pub instruction: &'a Instruction,
    /// Current stack contents (bottom-first).
    pub stack: &'a [i64],
    /// Registers read by this instruction (index, value at read time).
    pub read_regs: &'a [(u8, RegVal)],
    /// Registers written by this instruction (index, new value after exec).
    pub written_regs: &'a [(u8, RegVal)],
    /// Current loop nesting depth.
    pub loop_depth: usize,
}

/// Observer notified on every VM execution step.
///
/// Implement this trait to receive step-by-step execution state.
/// The associated [`ENABLED`](Tracer::ENABLED) constant allows the compiler
/// to eliminate all tracing code when set to `false`.
pub trait Tracer {
    /// Error type for when tracing I/O fails.
    type Error;

    /// Whether this tracer is active. When `false`, the VM skips all
    /// snapshotting and tracing calls, guaranteeing zero overhead.
    const ENABLED: bool = true;

    /// Called after each instruction executes, with the resulting state.
    fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error>;
}

/// A tracer that does nothing. Used as the default for [`Vm::run`](crate::Vm::run).
///
/// The compiler eliminates all tracing overhead via monomorphization and
/// dead-code elimination (DCE), producing identical code to a tracer-free
/// execution loop.
#[derive(Debug, Clone, Copy)]
pub struct NoopTracer;

impl Tracer for NoopTracer {
    type Error = core::convert::Infallible;

    const ENABLED: bool = false;

    #[inline(always)]
    fn on_step(&mut self, _state: &StepState<'_>) -> Result<(), Self::Error> {
        Ok(())
    }
}
```

- [ ] **Step 2: Add `TraceFailed` error variant**

In `crates/vm/src/error.rs`, add at the top of the file after the existing `#[cfg(feature = "std")]` imports:

```rust
#[cfg(not(feature = "std"))]
use alloc::string::String;
```

Add new variant to the `Error` enum, after `InvalidGridDimensions`:

```rust
    /// A tracer callback returned an error (e.g. I/O write failure).
    #[error("trace failed at byte {pos:#06x}: {message}")]
    TraceFailed {
        pos: usize,
        message: String,
    },
```

Update `byte_pos()` — add `Self::TraceFailed { pos, .. }` to the `Some(*pos)` arm:

```rust
            | Self::TraceFailed { pos, .. } => Some(*pos),
```

- [ ] **Step 3: Wire up in `lib.rs`**

In `crates/vm/src/lib.rs`, add `mod tracer;` after `mod vm;`:

```rust
mod vm;
pub mod tracer;
```

Add re-exports after the existing re-export block:

```rust
pub use tracer::{NoopTracer, StepState, Tracer};
#[cfg(feature = "std")]
pub use tracer::{JsonTracer, TextTracer};
```

- [ ] **Step 4: Create placeholder files for TextTracer and JsonTracer**

Create `crates/vm/src/tracer/text.rs` with the license header and a placeholder struct that compiles:

```rust
// [license header]

//! Human-readable text tracer.

use std::io::Write;

use crate::tracer::{StepState, Tracer};

/// Writes human-readable trace output with aligned columns.
#[derive(Debug)]
pub struct TextTracer<W: Write> {
    out: W,
}

impl<W: Write> Tracer for TextTracer<W> {
    type Error = std::io::Error;

    fn on_step(&mut self, _state: &StepState<'_>) -> Result<(), Self::Error> {
        todo!("implemented in Task 6")
    }
}
```

Create `crates/vm/src/tracer/json.rs` with same pattern:

```rust
// [license header]

//! JSONL tracer.

use std::io::Write;

use crate::tracer::{StepState, Tracer};

/// Writes one JSON object per line (JSONL format).
#[derive(Debug)]
pub struct JsonTracer<W: Write> {
    out: W,
}

impl<W: Write> Tracer for JsonTracer<W> {
    type Error = std::io::Error;

    fn on_step(&mut self, _state: &StepState<'_>) -> Result<(), Self::Error> {
        todo!("implemented in Task 7")
    }
}
```

- [ ] **Step 5: Verify compilation**

Run: `cargo check -p aglais-xqvm-vm --all-features`
Expected: compiles cleanly

Run: `cargo check -p aglais-xqvm-vm --no-default-features`
Expected: compiles cleanly (no_std)

- [ ] **Step 6: Commit**

```
Add Tracer trait, StepState, and NoopTracer

Implements QUI-314
```

---

## Task 5: VM Integration — `run_trace` Method

**Files:**
- Modify: `crates/vm/src/vm.rs`

- [ ] **Step 1: Write the failing test for RecordingTracer**

Add at the very bottom of `crates/vm/src/vm.rs`, a test module:

```rust
#[cfg(test)]
mod tests {
    extern crate alloc;
    use alloc::vec::Vec;
    use alloc::string::String;
    use alloc::format;

    use aglais_xqvm_bytecode::{InstructionBuilder, Instruction, Register};
    use crate::tracer::{Tracer, StepState, NoopTracer};
    use crate::value::RegVal;
    use crate::Vm;
    use crate::error::Error;

    /// Test tracer that records all step states.
    struct RecordingTracer {
        steps: Vec<RecordedStep>,
    }

    struct RecordedStep {
        pos: usize,
        step: u64,
        instruction: Instruction,
        stack: Vec<i64>,
        read_regs: Vec<(u8, RegVal)>,
        written_regs: Vec<(u8, RegVal)>,
        loop_depth: usize,
    }

    impl RecordingTracer {
        fn new() -> Self {
            Self { steps: Vec::new() }
        }
    }

    impl Tracer for RecordingTracer {
        type Error = core::convert::Infallible;

        fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error> {
            self.steps.push(RecordedStep {
                pos: state.pos,
                step: state.step,
                instruction: *state.instruction,
                stack: state.stack.to_vec(),
                read_regs: state.read_regs.to_vec(),
                written_regs: state.written_regs.to_vec(),
                loop_depth: state.loop_depth,
            });
            Ok(())
        }
    }

    /// Tracer that errors on a specific step.
    struct FailingTracer {
        fail_at: u64,
    }

    impl Tracer for FailingTracer {
        type Error = String;

        fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error> {
            if state.step == self.fail_at {
                Err(format!("intentional failure at step {}", self.fail_at))
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn run_delegates_to_run_trace() {
        // run() should produce the same result as run_trace with NoopTracer.
        let mut b = InstructionBuilder::new();
        b.push(3).push(4).add().halt();
        let program = b.build().unwrap();

        let mut vm1 = Vm::new();
        vm1.run(&program).unwrap();

        let mut vm2 = Vm::new();
        vm2.run_trace(&mut NoopTracer, &program).unwrap();

        assert_eq!(vm1.stack(), vm2.stack());
    }

    #[test]
    fn recording_tracer_captures_steps() {
        // PUSH 3, PUSH 4, ADD, STOW r0, HALT = 5 steps.
        let mut b = InstructionBuilder::new();
        b.push(3).push(4).add().stow(Register(0)).halt();
        let program = b.build().unwrap();

        let mut tracer = RecordingTracer::new();
        let mut vm = Vm::new();
        vm.run_trace(&mut tracer, &program).unwrap();

        assert_eq!(tracer.steps.len(), 5);

        // Step 1: PUSH 3 -> stack=[3], no regs
        assert_eq!(tracer.steps[0].step, 1);
        assert_eq!(tracer.steps[0].stack, &[3]);
        assert!(tracer.steps[0].read_regs.is_empty());
        assert!(tracer.steps[0].written_regs.is_empty());

        // Step 2: PUSH 4 -> stack=[3, 4], no regs
        assert_eq!(tracer.steps[1].stack, &[3, 4]);

        // Step 3: ADD -> stack=[7], no regs
        assert_eq!(tracer.steps[2].stack, &[7]);

        // Step 4: STOW r0 -> stack=[], writes r0=7
        assert_eq!(tracer.steps[3].stack, &[] as &[i64]);
        assert!(tracer.steps[3].read_regs.is_empty());
        assert_eq!(tracer.steps[3].written_regs.len(), 1);
        assert_eq!(tracer.steps[3].written_regs[0], (0, RegVal::Int(7)));

        // Step 5: HALT -> stack=[]
        assert_eq!(tracer.steps[4].stack, &[] as &[i64]);
    }

    #[test]
    fn recording_tracer_captures_read_regs() {
        // PUSH 42, STOW r0, LOAD r0, HALT
        let mut b = InstructionBuilder::new();
        b.push(42).stow(Register(0)).load(Register(0)).halt();
        let program = b.build().unwrap();

        let mut tracer = RecordingTracer::new();
        let mut vm = Vm::new();
        vm.run_trace(&mut tracer, &program).unwrap();

        // Step 3: LOAD r0 reads r0=42
        assert_eq!(tracer.steps[2].read_regs.len(), 1);
        assert_eq!(tracer.steps[2].read_regs[0], (0, RegVal::Int(42)));
    }

    #[test]
    fn failing_tracer_propagates_error() {
        let mut b = InstructionBuilder::new();
        b.push(1).push(2).add().halt();
        let program = b.build().unwrap();

        let mut tracer = FailingTracer { fail_at: 2 };
        let mut vm = Vm::new();
        let err = vm.run_trace(&mut tracer, &program).unwrap_err();

        match err {
            Error::TraceFailed { message, .. } => {
                assert!(message.contains("intentional failure at step 2"));
            }
            other => panic!("expected TraceFailed, got {other:?}"),
        }
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo nextest run -p aglais-xqvm-vm --lib -- vm::tests`
Expected: FAIL — method `run_trace` not found

- [ ] **Step 3: Implement `run_trace`**

In `crates/vm/src/vm.rs`, add imports at the top (after existing imports):

```rust
#[cfg(not(feature = "std"))]
use alloc::{format, vec::Vec};

use crate::tracer::{NoopTracer, StepState, Tracer};
```

Replace the existing `run` method (lines 268-304) with:

```rust
    /// Execute a [`Program`].
    ///
    /// Executes the instruction stream of `program`. Inline constants are
    /// encoded directly in `PUSH1`..`PUSH8` instructions -- no separate
    /// constant pool is needed.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] on any runtime fault (stack underflow, bad jump, etc.).
    pub fn run(&mut self, program: &Program) -> Result<(), Error> {
        self.run_trace(&mut NoopTracer, program)
    }

    /// Execute a [`Program`] with a [`Tracer`].
    ///
    /// Behaves identically to [`run`](Self::run) but invokes `tracer.on_step`
    /// after every instruction, providing a snapshot of the VM state.
    ///
    /// When `T` is [`NoopTracer`], the compiler eliminates all tracing
    /// overhead via dead-code elimination.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] on any runtime fault, or [`Error::TraceFailed`] if
    /// the tracer callback returns an error.
    pub fn run_trace<T: Tracer>(
        &mut self,
        tracer: &mut T,
        program: &Program,
    ) -> Result<(), Error>
    where
        T::Error: core::fmt::Display,
    {
        let mut stream = InstructionStream::from_program(program);
        let table = program.jump_table();
        let mut steps: u64 = 0;

        loop {
            if steps >= self.step_limit {
                return Err(Error::StepLimitExceeded {
                    limit: self.step_limit,
                });
            }
            steps += 1;

            let Some(item) = stream.next_instruction() else {
                break;
            };
            let (pos, _label, instr) = item.map_err(Error::from)?;

            let result = if T::ENABLED {
                // Snapshot read registers before dispatch.
                let read_slots = instr.read_registers();
                let read_regs: Vec<(u8, RegVal)> = read_slots
                    .as_slice()
                    .iter()
                    .map(|&i| (i, self.regs[usize::from(i)].clone()))
                    .collect();

                // Snapshot written register values before dispatch.
                let write_slots = instr.written_registers();
                let pre_write: Vec<RegVal> = write_slots
                    .as_slice()
                    .iter()
                    .map(|&i| self.regs[usize::from(i)].clone())
                    .collect();

                // Execute the instruction.
                let result = self.dispatch(pos, instr)?;

                // Collect registers that actually changed.
                let written_regs: Vec<(u8, RegVal)> = write_slots
                    .as_slice()
                    .iter()
                    .zip(pre_write.iter())
                    .filter(|(&i, pre)| self.regs[usize::from(i)] != *pre)
                    .map(|(&i, _)| (i, self.regs[usize::from(i)].clone()))
                    .collect();

                let state = StepState {
                    pos,
                    step: steps,
                    instruction: &instr,
                    stack: &self.stack,
                    read_regs: &read_regs,
                    written_regs: &written_regs,
                    loop_depth: self.loop_stack.len(),
                };

                tracer.on_step(&state).map_err(|e| Error::TraceFailed {
                    pos,
                    message: format!("{e}"),
                })?;

                result
            } else {
                self.dispatch(pos, instr)?
            };

            match result {
                StepResult::Continue => {}
                StepResult::Halt => break,
                StepResult::Jump(label) => {
                    let entry =
                        table.get(label).ok_or(Error::InvalidLabel { pos, label })?;
                    stream.seek(entry.start as usize).map_err(Error::from)?;
                }
                StepResult::Seek(target) => {
                    stream.seek(target).map_err(Error::from)?;
                }
                StepResult::StartLoop { kind } => {
                    let body_start = stream.pos();
                    self.loop_stack.push(LoopFrame { kind, body_start });
                }
            }
        }

        Ok(())
    }
```

- [ ] **Step 4: Run tests**

Run: `cargo nextest run -p aglais-xqvm-vm --lib`
Expected: all PASS

Run: `cargo nextest run -p aglais-xqvm-vm --test '*'`
Expected: all existing integration tests PASS (run delegates to run_trace)

- [ ] **Step 5: Commit**

```
Add run_trace method with register snapshotting

Implements QUI-314
```

---

## Task 6: TextTracer (VM crate, std-only)

**Files:**
- Modify: `crates/vm/src/tracer/text.rs`

This is a design and formatting task where your choices shape the output. See the spec for the desired column layout. The `RegVal` display formatting (compact representation for complex types) is a key decision.

- [ ] **Step 1: Write the integration test**

Create `crates/vm/tests/trace_text.rs`:

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
//
// [license header]
//
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Integration tests for TextTracer output.

use aglais_xqvm_bytecode::{InstructionBuilder, Register};
use aglais_xqvm_vm::{TextTracer, Vm};

#[test]
fn text_tracer_produces_header_and_rows() {
    let mut b = InstructionBuilder::new();
    b.push(3).push(4).add().stow(Register(0)).halt();
    let program = b.build().unwrap();

    let mut buf = Vec::new();
    let mut tracer = TextTracer::new(&mut buf);
    let mut vm = Vm::new();
    vm.run_trace(&mut tracer, &program).unwrap();

    let output = String::from_utf8(buf).unwrap();
    let lines: Vec<&str> = output.lines().collect();

    // First line is the column header.
    assert!(lines[0].contains("step"));
    assert!(lines[0].contains("instruction"));

    // We should have header + 5 data rows.
    assert_eq!(lines.len(), 6, "header + 5 steps, got:\n{output}");

    // Step 4 (STOW r0) should show a written register.
    assert!(
        lines[4].contains("r0=7"),
        "STOW r0 row should show r0=7, got: {}",
        lines[4]
    );
}

#[test]
fn text_tracer_large_stack_is_truncated() {
    // Push 12 elements, check that output truncates display to 8.
    let mut b = InstructionBuilder::new();
    for i in 0..12 {
        b.push(i);
    }
    b.halt();
    let program = b.build().unwrap();

    let mut buf = Vec::new();
    let mut tracer = TextTracer::new(&mut buf);
    let mut vm = Vm::new();
    vm.run_trace(&mut tracer, &program).unwrap();

    let output = String::from_utf8(buf).unwrap();
    // The last data row (HALT) should show the truncated stack.
    let last_data = output.lines().last().unwrap();
    assert!(
        last_data.contains("..."),
        "12-element stack should be truncated, got: {last_data}"
    );
}
```

- [ ] **Step 2: Implement TextTracer**

Replace the placeholder in `crates/vm/src/tracer/text.rs` with the full implementation. Key decisions:

- Fixed column widths: step(6), offset(8), instruction(22), stack(26), read-regs(18), written-regs(18).
- Write a header line first, then one row per step.
- `RegVal` compact display:
  - `Int(v)` → `"{v}"`
  - `VecInt(v)` → `"vec<int>(len={v.len()})"`
  - `VecXqmx(v)` → `"vec<model>(len={v.len()})"`
  - `Model(m)` → `"model({m.rows}x{m.cols})"`
  - `Sample(s)` → `"sample(len={s.values.len()})"`
- Stack display: show all if ≤8 elements, otherwise `...N more, top8`

The compact `RegVal` formatting, stack formatting, and column layout are design choices — implement them per the spec's example output.

- [ ] **Step 3: Run tests**

Run: `cargo nextest run -p aglais-xqvm-vm --test trace_text`
Expected: PASS

- [ ] **Step 4: Commit**

```
Add TextTracer for human-readable trace output

Implements QUI-314
```

---

## Task 7: JsonTracer (VM crate, std-only)

**Files:**
- Modify: `crates/vm/src/tracer/json.rs`

- [ ] **Step 1: Write the integration test**

Create `crates/vm/tests/trace_json.rs`:

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
//
// [license header]
//
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Integration tests for JsonTracer output.

use aglais_xqvm_bytecode::{InstructionBuilder, Register};
use aglais_xqvm_vm::{JsonTracer, Vm};

fn is_valid_json(s: &str) -> bool {
    // Minimal JSON validation: starts with '{', ends with '}'.
    let s = s.trim();
    s.starts_with('{') && s.ends_with('}')
}

#[test]
fn json_tracer_produces_valid_jsonl() {
    let mut b = InstructionBuilder::new();
    b.push(3).push(4).add().stow(Register(0)).halt();
    let program = b.build().unwrap();

    let mut buf = Vec::new();
    let mut tracer = JsonTracer::new(&mut buf);
    let mut vm = Vm::new();
    vm.run_trace(&mut tracer, &program).unwrap();

    let output = String::from_utf8(buf).unwrap();
    let lines: Vec<&str> = output.lines().collect();

    // 5 steps = 5 JSON lines.
    assert_eq!(lines.len(), 5, "expected 5 lines, got:\n{output}");

    // Every line is valid JSON.
    for (i, line) in lines.iter().enumerate() {
        assert!(is_valid_json(line), "line {i} is not valid JSON: {line}");
    }

    // Step numbers are sequential.
    assert!(lines[0].contains("\"step\":1"));
    assert!(lines[4].contains("\"step\":5"));

    // STOW r0 (step 4) should show written register.
    assert!(lines[3].contains("\"written_regs\""));
}

#[test]
fn json_tracer_each_line_has_required_fields() {
    let mut b = InstructionBuilder::new();
    b.push(42).halt();
    let program = b.build().unwrap();

    let mut buf = Vec::new();
    let mut tracer = JsonTracer::new(&mut buf);
    let mut vm = Vm::new();
    vm.run_trace(&mut tracer, &program).unwrap();

    let output = String::from_utf8(buf).unwrap();
    for line in output.lines() {
        assert!(line.contains("\"step\""), "missing step: {line}");
        assert!(line.contains("\"pos\""), "missing pos: {line}");
        assert!(line.contains("\"instruction\""), "missing instruction: {line}");
        assert!(line.contains("\"stack\""), "missing stack: {line}");
    }
}
```

- [ ] **Step 2: Implement JsonTracer**

Replace the placeholder in `crates/vm/src/tracer/json.rs`. Hand-write JSON serialization using `write!` calls — no serde dependency. One JSON object per line.

Format:
```json
{"step":1,"pos":0,"instruction":"PUSH1 3","stack":[3],"read_regs":{},"written_regs":{}}
```

Register objects use the register index as key:
```json
{"step":4,"pos":5,"instruction":"STOW r0","stack":[],"read_regs":{},"written_regs":{"0":{"type":"int","value":7}}}
```

Complex types:
```json
{"0":{"type":"model","rows":8,"cols":8}}
{"0":{"type":"vec<int>","len":12}}
{"0":{"type":"sample","len":5}}
```

- [ ] **Step 3: Run tests**

Run: `cargo nextest run -p aglais-xqvm-vm --test trace_json`
Expected: PASS

- [ ] **Step 4: Commit**

```
Add JsonTracer for JSONL trace output

Implements QUI-314
```

---

## Task 8: CLI Integration

**Files:**
- Modify: `crates/vm/src/bin/xqvm.rs`

- [ ] **Step 1: Add CLI flags**

In `crates/vm/src/bin/xqvm.rs`, add the new imports and types:

```rust
use std::fs::File;
use std::io::{BufWriter, Write};

use aglais_xqvm_vm::{TextTracer, JsonTracer};
```

Add the `TraceFormat` enum:

```rust
/// Trace output format.
#[derive(Clone, Debug, clap::ValueEnum)]
enum TraceFormat {
    /// Human-readable aligned columns.
    Text,
    /// One JSON object per line (JSONL).
    Json,
}
```

Add new fields to `Args`:

```rust
    /// Enable execution tracing.
    #[arg(long)]
    trace: bool,

    /// Trace output format.
    #[arg(long, default_value = "text", requires = "trace")]
    trace_format: TraceFormat,

    /// Write trace output to a file instead of stderr.
    #[arg(long, requires = "trace")]
    trace_file: Option<PathBuf>,
```

- [ ] **Step 2: Wire up trace dispatch**

Replace the `vm.run(&program)` call in `main()` (line 94-95) with:

```rust
    if args.trace {
        let writer: Box<dyn Write> = match &args.trace_file {
            Some(path) => {
                let file = File::create(path)
                    .into_diagnostic()
                    .wrap_err_with(|| format!("failed to create '{}'", path.display()))?;
                Box::new(BufWriter::new(file))
            }
            None => Box::new(BufWriter::new(std::io::stderr())),
        };
        match args.trace_format {
            TraceFormat::Text => {
                let mut tracer = TextTracer::new(writer);
                vm.run_trace(&mut tracer, &program)
                    .map_err(|e| e.into_diagnostic(&program, &args.file.to_string_lossy()))?;
            }
            TraceFormat::Json => {
                let mut tracer = JsonTracer::new(writer);
                vm.run_trace(&mut tracer, &program)
                    .map_err(|e| e.into_diagnostic(&program, &args.file.to_string_lossy()))?;
            }
        }
    } else {
        vm.run(&program)
            .map_err(|e| e.into_diagnostic(&program, &args.file.to_string_lossy()))?;
    };
```

- [ ] **Step 3: Verify compilation and help output**

Run: `cargo build -p aglais-xqvm-vm --bin xqvm`
Expected: compiles

Run: `cargo run -p aglais-xqvm-vm --bin xqvm -- --help`
Expected: output includes `--trace`, `--trace-format`, `--trace-file`

- [ ] **Step 4: Commit**

```
Add --trace, --trace-format, --trace-file CLI flags

Implements QUI-314
```

---

## Task 9: Final Verification

- [ ] **Step 1: Run full lints**

Run: `make lint`
Expected: 0 warnings, 0 errors

- [ ] **Step 2: Run all tests**

Run: `make test`
Expected: all PASS

- [ ] **Step 3: Run doc tests**

Run: `make lint-doc`
Expected: 0 warnings

- [ ] **Step 4: Fix any issues found above, then commit fixes**

- [ ] **Step 5: Mark QUI-314 as Done in Linear**
