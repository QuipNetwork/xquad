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

//! Fluent bytecode assembler with first-class label support.
//!
//! [`InstructionBuilder`] lets you emit XQVM instructions one at a time
//! using a chained, builder-style API. Labels are opaque handles created
//! with [`label`](InstructionBuilder::label) and anchored to a byte offset
//! with [`place`](InstructionBuilder::place). `JUMP`/`JUMPI` instructions
//! record a fixup instead of a raw label index;
//! [`build`](InstructionBuilder::build) resolves all fixups, constructs a
//! [`JumpTable`](crate::JumpTable), and returns the final program.
//!
//! Both forward and backward references work: you may call
//! [`emit_jump`](InstructionBuilder::emit_jump) before or after
//! [`place`](InstructionBuilder::place) on the same label.
//!
//! # Examples
//!
//! ```rust
//! use xqvm::InstructionBuilder;
//!
//! // Counted loop: push 3, decrement until zero.
//! let mut b = InstructionBuilder::new();
//! let loop_top = b.label();
//!
//! b.emit_push(3);
//! b.place(loop_top).unwrap();
//! b.emit_push(-1);
//! b.emit_add();
//! b.emit_copy();
//! b.emit_jump_if(loop_top);  // backward reference
//! b.emit_pop();
//! b.emit_halt();
//!
//! let program = b.build().unwrap();
//! assert!(!program.code().is_empty());
//! ```
//!
//! Forward references work equally well:
//!
//! ```rust
//! use xqvm::InstructionBuilder;
//!
//! let mut b = InstructionBuilder::new();
//! let done = b.label();
//!
//! b.emit_push(0);
//! b.emit_jump_if(done);  // forward reference -- target not yet placed
//! b.emit_push(42);
//! b.place(done).unwrap();    // anchor here
//! b.emit_halt();
//!
//! let program = b.build().unwrap();
//! assert!(!program.code().is_empty());
//! ```

#[cfg(not(feature = "std"))]
use alloc::{vec, vec::Vec};

use thiserror::Error;

use super::codec;
use super::program::Program;
use super::types::{Instruction, Register};

// ---------------------------------------------------------------------------
// Error and Result
// ---------------------------------------------------------------------------

/// Wire-format cap on placed labels: sequential `TARGET` ids are encoded as
/// `u16`, so a program may place `u16::MAX + 1` of them.
/// Needs no allow-list entry: arithmetic in a const initialiser is evaluated
/// at compile time, so an overflow here is a build failure rather than a wrap.
const MAX_TARGETS: usize = u16::MAX as usize + 1;

/// Error returned by [`InstructionBuilder::build`].
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum Error {
    /// A program contains more than `u16::MAX + 1` placed labels, exceeding
    /// the wire-format limit on sequential `TARGET` ids.
    #[error("too many TARGETs: {count} (max {MAX_TARGETS})")]
    TooManyTargets {
        /// Total number of placed labels in the program.
        count: usize,
    },

    /// A label was used in a `JUMP`/`JUMPI` but never placed.
    #[error("label {id} was used but never placed")]
    UnplacedLabel {
        /// Index of the unplaced label.
        id: usize,
    },

    /// A label was placed but never referenced by any jump instruction.
    #[error("label {id} was placed but never referenced by a jump")]
    UnusedLabel {
        /// Index of the unused label.
        id: usize,
    },

    /// An internal fixup site is outside the assembled buffer, indicating a
    /// bug in the builder itself.
    #[error("fixup site {site:#06x} out of buffer bounds (buf len {buf_len})")]
    FixupOutOfBounds {
        /// Byte offset of the fixup site.
        site: usize,
        /// Length of the assembled buffer.
        buf_len: usize,
    },

    /// A label was placed more than once.
    #[error("label {id} placed more than once")]
    DuplicateLabel {
        /// Index of the label that was placed again.
        id: usize,
    },

    /// A label was not created by this builder.
    #[error("label {id} was not created by this builder")]
    ForeignLabel {
        /// Index of the unknown label.
        id: usize,
    },
}

type Result<T> = core::result::Result<T, Error>;

// ---------------------------------------------------------------------------
// LabelId
// ---------------------------------------------------------------------------

/// An opaque handle to a bytecode label created by
/// [`InstructionBuilder::label`].
///
/// Labels are allocated in the order they are created. Pass a `LabelId` to
/// [`InstructionBuilder::place`] to anchor the label at a byte offset, and
/// to [`InstructionBuilder::emit_jump`] / [`InstructionBuilder::emit_jump_if`] to
/// emit a jump that targets the label.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct LabelId(usize);

// ---------------------------------------------------------------------------
// InstructionBuilder
// ---------------------------------------------------------------------------

/// Whether a [`Fixup`] targets a `JUMP` or `JUMPI` instruction.
#[derive(Debug, Clone, Copy)]
enum JumpKind {
    Jump,
    JumpIf,
}

/// Pending jump fixup: byte offset of the `JUMP`/`JUMPI` instruction and the
/// label it should resolve to.
#[derive(Debug)]
struct Fixup {
    /// Byte offset of the `JUMP`/`JUMPI` instruction (opcode byte).
    site: usize,
    kind: JumpKind,
    label: LabelId,
}

/// Fluent XQVM bytecode assembler.
///
/// Create a builder with [`new`](Self::new), allocate labels with
/// [`label`](Self::label), emit instructions through the named methods or
/// [`emit`](Self::emit), anchor labels with [`place`](Self::place), and
/// finalise the buffer with [`build`](Self::build).
///
/// Use [`emit_push`](Self::emit_push) to emit the smallest `PushN` instruction that
/// faithfully represents the given `i64` value.
///
/// # Examples
///
/// ```rust
/// use xqvm::InstructionBuilder;
/// use xqvm::{Instruction, Register};
///
/// let mut b = InstructionBuilder::new();
/// let skip = b.label();
///
/// b.emit_push(1)
///  .emit_push(2)
///  .emit_gt()
///  .emit_jump_if(skip)
///  .emit_push(99)
///  .place(skip).unwrap()
///  .emit_halt();
///
/// let program = b.build().unwrap();
/// assert!(!program.code().is_empty());
/// ```
#[derive(Debug, Default)]
pub struct InstructionBuilder {
    buf: Vec<u8>,
    /// `None` means allocated but not yet placed.
    label_positions: Vec<Option<usize>>,
    fixups: Vec<Fixup>,
}

// ---------------------------------------------------------------------------
// tt-muncher: generate no-argument and single-Register emit wrappers from
// the opcode table.  Entries with `label`, `model`, or `val` fields are
// skipped because they have dedicated hand-written methods.
//
// All generated methods are prefixed with `emit_` to avoid conflicts with
// standard trait methods (e.g. `Iterator::next`, `core::mem::drop`).
// ---------------------------------------------------------------------------

macro_rules! impl_builder_methods {
    // Base case.
    () => {};

    // Skip JUMP / JUMPI  -- `{label: ...}`
    ( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
       $_stack:expr, {label: $($rest_f:tt)*}), $($rest:tt)* ) => {
        impl_builder_methods!($($rest)*);
    };

    // Skip ENERGY / multi-register opcodes -- `{model: ...}`
    ( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
       $_stack:expr, {model: $($rest_f:tt)*}), $($rest:tt)* ) => {
        impl_builder_methods!($($rest)*);
    };

    // Skip SLACK -- `{indices: ...}`
    ( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
       $_stack:expr, {indices: $($rest_f:tt)*}), $($rest:tt)* ) => {
        impl_builder_methods!($($rest)*);
    };

    // Skip PUSH1..PUSH8 -- `{val: ...}` -- dedicated emit_push() handles them.
    ( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
       $_stack:expr, {val: $($rest_f:tt)*}), $($rest:tt)* ) => {
        impl_builder_methods!($($rest)*);
    };

    // No-argument variants -- empty field list `{}`
    ( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $_stack:expr, {}),
      $($rest:tt)* ) => {
        ::pastey::paste! {
            #[doc = $doc]
            pub fn [<emit_ $variant:snake>](&mut self) -> &mut Self {
                self.emit(Instruction::$variant {})
            }
        }
        impl_builder_methods!($($rest)*);
    };

    // Single-register variants -- `{reg: <type>}`
    ( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
       $_stack:expr, {reg: $($ftype:tt)*}), $($rest:tt)* ) => {
        ::pastey::paste! {
            #[doc = $doc]
            pub fn [<emit_ $variant:snake>](&mut self, reg: Register) -> &mut Self {
                self.emit(Instruction::$variant { reg })
            }
        }
        impl_builder_methods!($($rest)*);
    };
}

impl InstructionBuilder {
    /// Create an empty builder.
    pub fn new() -> Self {
        Self::default()
    }

    // -----------------------------------------------------------------------
    // Label API
    // -----------------------------------------------------------------------

    /// Allocate a new, unplaced label.
    ///
    /// The returned [`LabelId`] can be passed to [`place`](Self::place),
    /// [`emit_jump`](Self::emit_jump), and [`emit_jump_if`](Self::emit_jump_if)
    /// in any order.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::InstructionBuilder;
    ///
    /// let mut b = InstructionBuilder::new();
    /// let top = b.label();
    /// b.place(top).unwrap().emit_nop().emit_jump(top);
    /// let program = b.build().unwrap();
    /// assert!(!program.code().is_empty());
    /// ```
    pub fn label(&mut self) -> LabelId {
        let id = self.label_positions.len();
        self.label_positions.push(None);
        LabelId(id)
    }

    /// Anchor `label` to the current write position and emit an inline
    /// `TARGET` opcode there.
    ///
    /// Per `spec/xqvm/SPEC.md`, every jump destination must contain a `TARGET`
    /// instruction (a no-op at runtime, but required as a validation marker
    /// so jumps cannot land in the middle of a multi-byte instruction). The
    /// recorded label position is the byte offset of the `TARGET` opcode
    /// itself, so `JUMP .N` seeks to the `TARGET` and then falls through to
    /// the user code that follows.
    ///
    /// `place` is the only place that emits `TARGET` automatically; if you
    /// need a bare `TARGET` (e.g. for direct opcode emission via
    /// [`emit`](Self::emit)), use `b.emit_target()` instead.
    ///
    /// # Errors
    ///
    /// - [`Error::DuplicateLabel`] -- `label` was already placed.
    /// - [`Error::ForeignLabel`] -- `label` was not created by this builder.
    pub fn place(&mut self, label: LabelId) -> Result<&mut Self> {
        let slot = self
            .label_positions
            .get_mut(label.0)
            .ok_or(Error::ForeignLabel { id: label.0 })?;
        if slot.is_some() {
            return Err(Error::DuplicateLabel { id: label.0 });
        }
        *slot = Some(self.buf.len());
        Ok(self.emit(Instruction::Target {}))
    }

    // -----------------------------------------------------------------------
    // General emit
    // -----------------------------------------------------------------------

    /// Encode and append any instruction to the buffer.
    ///
    /// Prefer the named convenience methods; use this for instructions that
    /// do not have a dedicated method.
    pub fn emit(&mut self, instr: Instruction) -> &mut Self {
        self.buf.extend_from_slice(&codec::encode(&instr));
        self
    }

    // -----------------------------------------------------------------------
    // Control flow (jump instructions use labels, not raw indices)
    // -----------------------------------------------------------------------

    /// Emit a `JUMP` instruction targeting `label`.
    ///
    /// Internally the builder records a wide `Jump2` placeholder; during
    /// [`build`](Self::build) the placeholder is narrowed to `Jump1` (2 bytes)
    /// when the sequential id fits in `u8`, or left as `Jump2` (3 bytes)
    /// otherwise.
    ///
    /// The label byte is patched during [`build`](Self::build) with the
    /// label's *sequential* id (its `TARGET`'s position in stream order),
    /// not the allocation-order id returned by [`label`](Self::label).
    pub fn emit_jump(&mut self, label: LabelId) -> &mut Self {
        self.emit_with_fixup(
            Instruction::Jump2 { label: u16::MAX },
            JumpKind::Jump,
            label,
        )
    }

    /// Emit a `JUMPI` conditional jump targeting `label`.
    ///
    /// Pops the top of the stack and jumps if the value is non-zero.
    /// Uses a wide `JumpI2` placeholder; [`build`](Self::build) narrows it
    /// to `JumpI1` when the sequential id fits in `u8`.
    pub fn emit_jump_if(&mut self, label: LabelId) -> &mut Self {
        self.emit_with_fixup(
            Instruction::JumpI2 { label: u16::MAX },
            JumpKind::JumpIf,
            label,
        )
    }

    fn emit_with_fixup(&mut self, instr: Instruction, kind: JumpKind, label: LabelId) -> &mut Self {
        let site = self.buf.len();
        let enc = codec::encode(&instr);
        self.buf.extend_from_slice(&enc);
        self.fixups.push(Fixup { site, kind, label });
        self
    }

    // Generated from the opcode table: emit_* no-arg and single-register
    // methods.  JUMP, JUMPI, ENERGY, and PUSH1..PUSH8 are excluded -- they
    // have dedicated hand-written methods.
    opcodes!(impl_builder_methods);

    // -----------------------------------------------------------------------
    // Stack
    // -----------------------------------------------------------------------

    /// Emit the smallest `PushN` instruction that faithfully represents `val`.
    ///
    /// * fits in i8  -- [`Push1`](Instruction::Push1) (2 bytes)
    /// * fits in i16 -- [`Push2`](Instruction::Push2) (3 bytes)
    /// * fits in i24 -- [`Push3`](Instruction::Push3) (4 bytes)
    /// * fits in i32 -- [`Push4`](Instruction::Push4) (5 bytes)
    /// * fits in i40 -- [`Push5`](Instruction::Push5) (6 bytes)
    /// * fits in i48 -- [`Push6`](Instruction::Push6) (7 bytes)
    /// * fits in i56 -- [`Push7`](Instruction::Push7) (8 bytes)
    /// * any i64     -- [`Push8`](Instruction::Push8) (9 bytes)
    pub fn emit_push(&mut self, val: i64) -> &mut Self {
        self.emit(minimal_push(val))
    }

    // -----------------------------------------------------------------------
    // Hamiltonian energy
    // -----------------------------------------------------------------------

    /// Emit an `ENERGY` instruction.
    pub fn emit_energy(&mut self, model: Register, sample: Register) -> &mut Self {
        self.emit(Instruction::Energy { model, sample })
    }

    // -----------------------------------------------------------------------
    // Finalise
    // -----------------------------------------------------------------------

    /// Resolve all jump fixups, renumber labels in stream order, and return
    /// the assembled [`Program`].
    ///
    /// After QUI-405 the wire-format label index in `JUMP`/`JUMPI` is the
    /// **sequential id** of the corresponding `TARGET` opcode in stream
    /// order, not the allocation order returned by [`label`](Self::label).
    /// Builds whose call sites already happen to use labels in stream order
    /// see no semantic change; out-of-order placements (forward jumps to a
    /// label whose `.label()` was called before another label that ends up
    /// earlier in the stream) are renumbered automatically here.
    ///
    /// `b.jump` / `b.jump_if` use `Jump2`/`JumpI2` as placeholders during
    /// assembly; `build` narrows each to `Jump1`/`JumpI1` (2 bytes) when the
    /// final sequential id fits in `u8`, keeping bytecode compact for
    /// programs with ≤ 256 labels (the common case).
    ///
    /// # Errors
    ///
    /// - [`Error::UnplacedLabel`] -- a label used in a jump was never placed.
    /// - [`Error::UnusedLabel`] -- a label was placed but never referenced
    ///   by any jump instruction.
    /// - [`Error::TooManyTargets`] -- the program contains more than
    ///   `u16::MAX + 1` `TARGET` opcodes (~65 536). This is well beyond any
    ///   realistic XQVM program.
    /// - [`Error::FixupOutOfBounds`] -- a fixup site is outside the assembled
    ///   buffer; this indicates a bug in the builder itself.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::{InstructionBuilder, InstructionStream};
    /// use xqvm::Instruction;
    ///
    /// let mut b = InstructionBuilder::new();
    /// let done = b.label();
    /// b.emit_push(0).emit_jump_if(done).emit_push(1).place(done).unwrap().emit_halt();
    ///
    /// let program = b.build().unwrap();
    ///
    /// // The placed label produced one TARGET, recorded in the jump table.
    /// assert_eq!(program.jump_table().len(), 1);
    /// ```
    pub fn build(mut self) -> Result<Program> {
        // Collect which labels are actually referenced by jump fixups.
        let mut referenced = vec![false; self.label_positions.len()];

        // 1. Validate all fixup-referenced labels are placed.
        for fixup in &self.fixups {
            let label_placed = self.label_positions.get(fixup.label.0).copied().flatten();
            if label_placed.is_none() {
                return Err(Error::UnplacedLabel { id: fixup.label.0 });
            }
            if let Some(slot) = referenced.get_mut(fixup.label.0) {
                *slot = true;
            }
        }

        // 2. Validate no placed labels are unused (unreferenced by any jump).
        //    Entry block at byte offset 0 is exempt (implicit entry point).
        for (idx, pos) in self.label_positions.iter().enumerate() {
            if pos.is_some() && !referenced.get(idx).copied().unwrap_or(false) && *pos != Some(0) {
                return Err(Error::UnusedLabel { id: idx });
            }
        }

        // 3. Sort placed labels by byte position to derive their sequential
        //    ids -- the runtime jump-table scan visits TARGETs in stream
        //    order, so this is the order the wire format must reference.
        let mut placed: Vec<(usize, usize)> = self
            .label_positions
            .iter()
            .enumerate()
            .filter_map(|(idx, pos)| pos.map(|p| (idx, p)))
            .collect();
        placed.sort_by_key(|&(_, pos)| pos);

        if placed.len() > usize::from(u16::MAX) + 1 {
            return Err(Error::TooManyTargets {
                count: placed.len(),
            });
        }

        // alloc_to_seq[alloc_id] = sequential_id (or None if not placed).
        // placed.len() <= u16::MAX + 1 (checked above), so seq_id fits in u16.
        let mut alloc_to_seq: Vec<Option<u16>> = vec![None; self.label_positions.len()];
        for (seq_id, &(alloc_id, _)) in placed.iter().enumerate() {
            let seq_u16 = u16::try_from(seq_id).map_err(|_| Error::TooManyTargets {
                count: placed.len(),
            })?;
            if let Some(slot) = alloc_to_seq.get_mut(alloc_id) {
                *slot = Some(seq_u16);
            }
        }

        // 4. Patch fixups: write the *sequential* label id into the
        //    instruction bytes. `b.jump` / `b.jump_if` always emit Jump2 /
        //    JumpI2 (the wide u16 form), so the conversion always fits.
        for fixup in &self.fixups {
            let seq_id = alloc_to_seq
                .get(fixup.label.0)
                .copied()
                .flatten()
                .ok_or(Error::UnplacedLabel { id: fixup.label.0 })?;
            let instr = match fixup.kind {
                JumpKind::Jump => Instruction::Jump2 { label: seq_id },
                JumpKind::JumpIf => Instruction::JumpI2 { label: seq_id },
            };
            let encoded = codec::encode(&instr);
            #[expect(
                clippy::arithmetic_side_effects,
                reason = "`fixup.site` indexes `self.buf`, whose length is bounded by isize::MAX, so the end offset cannot overflow usize; `get_mut` rejects it if it is past the buffer"
            )]
            let end = fixup.site + encoded.len();
            let buf_len = self.buf.len();
            self.buf
                .get_mut(fixup.site..end)
                .ok_or(Error::FixupOutOfBounds {
                    site: fixup.site,
                    buf_len,
                })?
                .copy_from_slice(&encoded);
        }

        // 5. Narrow Jump2/JumpI2 → Jump1/JumpI1 where the sequential id fits
        //    in u8.  Process fixups in ascending site order so that the
        //    cumulative byte shrinkage (one byte per narrowed instruction)
        //    correctly maps original site positions to their shifted actuals.
        let mut narrowable: Vec<(usize, JumpKind, u8)> = Vec::new();
        for f in &self.fixups {
            let seq_id = alloc_to_seq
                .get(f.label.0)
                .copied()
                .flatten()
                .ok_or(Error::UnplacedLabel { id: f.label.0 })?;
            if let Ok(narrow_id) = u8::try_from(seq_id) {
                narrowable.push((f.site, f.kind, narrow_id));
            }
        }
        narrowable.sort_by_key(|&(site, _, _)| site);
        #[expect(
            clippy::arithmetic_side_effects,
            reason = "sites are ascending and at least three bytes apart (the wide jump form), so the nth site is at least 3n and cannot fall below the n bytes already removed; `actual + nb_len` stays inside a buffer bounded by isize::MAX and `get_mut` rejects it otherwise"
        )]
        for (shrinkage, (site, kind, narrow_id)) in narrowable.into_iter().enumerate() {
            let actual = site - shrinkage;
            let narrow = match kind {
                JumpKind::Jump => Instruction::Jump1 { label: narrow_id },
                JumpKind::JumpIf => Instruction::JumpI1 { label: narrow_id },
            };
            let nb = codec::encode(&narrow);
            // `nb` is 2 bytes; the wide form was 3.  Replace the first two
            // bytes in place and remove the now-redundant third byte.
            let nb_len = nb.len();
            let buf_len = self.buf.len();
            self.buf
                .get_mut(actual..actual + nb_len)
                .ok_or(Error::FixupOutOfBounds {
                    site: actual,
                    buf_len,
                })?
                .copy_from_slice(&nb);
            let _ = self.buf.remove(actual + nb_len);
        }

        Ok(Program::new(self.buf))
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Return the smallest `PushN` instruction that faithfully represents `val`.
#[expect(
    clippy::arithmetic_side_effects,
    reason = "`n` ranges over 1..=7, so `n * 8` is at most 56 and `64 - bits` stays in 8..=56"
)]
fn minimal_push(val: i64) -> Instruction {
    let be = val.to_be_bytes();
    for n in 1..=7 {
        let bits = n * 8;
        let shift = 64 - bits;
        if (val << shift) >> shift == val {
            return match n {
                1 => Instruction::Push1 { val: [be[7]] },
                2 => Instruction::Push2 {
                    val: [be[6], be[7]],
                },
                3 => Instruction::Push3 {
                    val: [be[5], be[6], be[7]],
                },
                4 => Instruction::Push4 {
                    val: [be[4], be[5], be[6], be[7]],
                },
                5 => Instruction::Push5 {
                    val: [be[3], be[4], be[5], be[6], be[7]],
                },
                6 => Instruction::Push6 {
                    val: [be[2], be[3], be[4], be[5], be[6], be[7]],
                },
                7 => Instruction::Push7 {
                    val: [be[1], be[2], be[3], be[4], be[5], be[6], be[7]],
                },
                _ => unreachable!(),
            };
        }
    }
    Instruction::Push8 { val: be }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
#[expect(
    unused_results,
    reason = "builder methods return &mut Self; results are intentionally discarded in test chains"
)]
mod tests {
    use super::*;
    use crate::bytecode::stream::InstructionStream;
    use crate::bytecode::types::Instruction;

    fn decode_all(buf: &[u8]) -> Vec<Instruction> {
        InstructionStream::new(buf).map(|r| r.unwrap().2).collect()
    }

    #[test]
    fn empty_builder_produces_empty_code() {
        let program = InstructionBuilder::new().build().unwrap();
        assert!(program.code().is_empty());
    }

    #[test]
    fn push_zero_emits_push1() {
        let mut b = InstructionBuilder::new();
        b.emit_push(0).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(instrs[0], Instruction::Push1 { val: [0x00] });
    }

    #[test]
    fn push_i8_emits_push1() {
        let mut b = InstructionBuilder::new();
        b.emit_push(42).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(instrs[0], Instruction::Push1 { val: [42] });
    }

    #[test]
    fn push_minus_one_emits_push1() {
        let mut b = InstructionBuilder::new();
        b.emit_push(-1).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        // -1 as i8 = 0xFF
        assert_eq!(instrs[0], Instruction::Push1 { val: [0xFF] });
    }

    #[test]
    fn push_i8_max_uses_push1() {
        let mut b = InstructionBuilder::new();
        b.emit_push(127).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(instrs[0], Instruction::Push1 { val: [0x7F] });
    }

    #[test]
    fn push_i8_max_plus_one_uses_push2() {
        let mut b = InstructionBuilder::new();
        b.emit_push(128).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(instrs[0], Instruction::Push2 { val: [0x00, 0x80] });
    }

    #[test]
    fn push_i64_max_uses_push8() {
        let mut b = InstructionBuilder::new();
        b.emit_push(i64::MAX).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(
            instrs[0],
            Instruction::Push8 {
                val: i64::MAX.to_be_bytes()
            }
        );
    }

    #[test]
    fn push_i64_min_uses_push8() {
        let mut b = InstructionBuilder::new();
        b.emit_push(i64::MIN).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(
            instrs[0],
            Instruction::Push8 {
                val: i64::MIN.to_be_bytes()
            }
        );
    }

    #[test]
    fn backward_jump_resolves_correctly() {
        // After QUI-404 + QUI-405 + QUI-437 narrowing:
        //   TARGET   (1 byte at 0)  <- emitted by place()
        //   Push1    (2 bytes at 1)
        //   JumpI1   (2 bytes at 3) <- narrowed; seq id 0 fits in u8
        let mut b = InstructionBuilder::new();
        let top = b.label();
        b.place(top).unwrap().emit_push(0).emit_jump_if(top);

        let program = b.build().unwrap();
        let instrs = decode_all(program.code());
        assert_eq!(instrs[0], Instruction::Target {});
        assert_eq!(instrs[1], Instruction::Push1 { val: [0x00] });
        // Narrowed to JumpI1; sequential id 0 (the only TARGET).
        assert_eq!(instrs[2], Instruction::JumpI1 { label: 0 });

        // The jump table is built by scanning the byte stream for TARGETs;
        // the only TARGET is at byte 0 so seq id 0 -> 0.
        assert_eq!(program.jump_table().len(), 1);
        assert_eq!(program.jump_table().get(0), Some(0));
    }

    #[test]
    fn forward_jump_resolves_correctly() {
        // After QUI-404 + QUI-405 + QUI-437 narrowing:
        //   Jump1    (2 bytes at 0)  <- narrowed from placeholder Jump2
        //   Nop      (1 byte  at 2)
        //   TARGET   (1 byte  at 3)  <- emitted by place()
        //   Halt     (1 byte  at 4)
        let mut b = InstructionBuilder::new();
        let done = b.label();
        b.emit_jump(done)
            .emit_nop()
            .place(done)
            .unwrap()
            .emit_halt();

        let program = b.build().unwrap();
        let instrs = decode_all(program.code());
        assert_eq!(instrs[0], Instruction::Jump1 { label: 0 });
        assert_eq!(instrs[1], Instruction::Nop {});
        assert_eq!(instrs[2], Instruction::Target {});
        assert_eq!(instrs[3], Instruction::Halt {});

        // Sequential id 0 -> byte 3 (the TARGET, shifted left by the narrowing).
        assert_eq!(program.jump_table().get(0), Some(3));
    }

    #[test]
    fn multiple_jumps_to_same_label() {
        let mut b = InstructionBuilder::new();
        let done = b.label();
        b.emit_push(0)
            .emit_jump_if(done)
            .emit_push(0)
            .emit_jump_if(done)
            .place(done)
            .unwrap()
            .emit_halt();

        let program = b.build().unwrap();
        let instrs = decode_all(program.code());
        // Layout: Push1 .. JumpI1 .0 .. Push1 .. JumpI1 .0 .. Target .. Halt
        assert_eq!(*instrs.last().unwrap(), Instruction::Halt {});
        assert_eq!(instrs[0], Instruction::Push1 { val: [0x00] });
        assert_eq!(instrs[2], Instruction::Push1 { val: [0x00] });
        // Both jumps target sequential id 0 (the only TARGET in the program).
        assert!(matches!(instrs[1], Instruction::JumpI1 { label: 0 }));
        assert!(matches!(instrs[3], Instruction::JumpI1 { label: 0 }));
        // place() inserts an inline TARGET before HALT.
        assert_eq!(instrs[4], Instruction::Target {});
        assert_eq!(instrs[5], Instruction::Halt {});
    }

    #[test]
    fn build_renumbers_labels_in_stream_order() {
        // Allocate labels in one order, place them in the opposite order.
        // The fixup must use the placed-order id, not the allocation id.
        let mut b = InstructionBuilder::new();
        let first = b.label(); // alloc id 0
        let second = b.label(); // alloc id 1

        // Reference both before placing.
        b.emit_jump_if(first).emit_jump(second);
        // Place `second` first (alloc id 1 -> seq id 0).
        b.place(second).unwrap().emit_nop();
        // Then place `first` (alloc id 0 -> seq id 1).
        b.place(first).unwrap().emit_halt();

        let program = b.build().unwrap();
        let instrs = decode_all(program.code());
        // jump_if `first` was alloc id 0, but `first` is the *second* TARGET
        // in stream order, so its sequential id is 1.
        assert!(matches!(instrs[0], Instruction::JumpI1 { label: 1 }));
        // jump `second` was alloc id 1, but `second` is the *first* TARGET
        // in stream order, so its sequential id is 0.
        assert!(matches!(instrs[1], Instruction::Jump1 { label: 0 }));
    }

    #[test]
    fn jump_narrows_to_jump1_for_small_seq_id() {
        // build() narrows Jump2 → Jump1 when the sequential id fits in u8.
        // For a single label, seq id is 0 which always fits.
        let mut b = InstructionBuilder::new();
        let l = b.label();
        b.emit_jump(l).place(l).unwrap().emit_halt();

        let program = b.build().unwrap();
        let instrs = decode_all(program.code());
        // Narrowed to Jump1 (2 bytes) since seq id 0 fits in u8.
        assert_eq!(instrs[0], Instruction::Jump1 { label: 0 });
    }

    #[test]
    fn jump_if_narrows_to_jumpi1_for_small_seq_id() {
        let mut b = InstructionBuilder::new();
        let l = b.label();
        b.emit_push(1).emit_jump_if(l).place(l).unwrap().emit_halt();

        let program = b.build().unwrap();
        let instrs = decode_all(program.code());
        assert_eq!(instrs[1], Instruction::JumpI1 { label: 0 });
    }

    #[test]
    fn unplaced_label_returns_error() {
        let mut b = InstructionBuilder::new();
        let ghost = b.label();
        b.emit_jump(ghost).emit_halt();
        assert_eq!(b.build(), Err(Error::UnplacedLabel { id: 0 }));
    }

    #[test]
    fn unused_label_returns_error() {
        let mut b = InstructionBuilder::new();
        let l0 = b.label();
        let _l1 = b.label(); // placed but never jumped to
        b.emit_jump(l0);
        b.place(l0).unwrap().emit_nop();
        // l1 is allocated but not placed, which is fine (not used).
        // But if we place it without referencing it, that's an error.
        let mut b2 = InstructionBuilder::new();
        let target = b2.label();
        let unused = b2.label();
        b2.emit_jump(target)
            .place(target)
            .unwrap()
            .emit_nop()
            .place(unused)
            .unwrap()
            .emit_halt();
        assert_eq!(b2.build(), Err(Error::UnusedLabel { id: 1 }));
    }

    #[test]
    fn emit_arbitrary_instruction() {
        let mut b = InstructionBuilder::new();
        b.emit(Instruction::Copy {}).emit(Instruction::Halt {});
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(instrs, [Instruction::Copy {}, Instruction::Halt {}]);
    }

    #[test]
    fn arithmetic_chain() {
        let mut b = InstructionBuilder::new();
        b.emit_push(10)
            .emit_push(3)
            .emit_add()
            .emit_push(2)
            .emit_mul()
            .emit_neg()
            .emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(instrs[2], Instruction::Add {});
        assert_eq!(instrs[4], Instruction::Mul {});
        assert_eq!(instrs[5], Instruction::Neg {});
    }

    #[test]
    fn energy_method_encodes_both_registers() {
        let mut b = InstructionBuilder::new();
        b.emit_energy(Register(1), Register(2)).emit_halt();
        let instrs = decode_all(b.build().unwrap().code());
        assert_eq!(
            instrs[0],
            Instruction::Energy {
                model: Register(1),
                sample: Register(2)
            },
        );
    }

    #[test]
    fn place_twice_errors() {
        let mut b = InstructionBuilder::new();
        let l = b.label();
        assert!(b.place(l).is_ok());
        assert_eq!(b.place(l).unwrap_err(), Error::DuplicateLabel { id: 0 });
    }

    #[test]
    fn jump_table_has_correct_ranges() {
        // .0: NOP; .1: HALT
        let mut b = InstructionBuilder::new();
        let l0 = b.label();
        let l1 = b.label();
        b.place(l0)
            .unwrap()
            .emit_nop()
            .emit_jump(l1)
            .place(l1)
            .unwrap()
            .emit_halt();

        let program = b.build().unwrap();
        // The runtime jump table records each TARGET's byte offset in
        // stream order; the narrowed layout is TARGET (1) + NOP (1) +
        // JUMP1 (2) + TARGET (1) + HALT (1).
        // Sequential id 0 -> first TARGET at byte 0.
        // Sequential id 1 -> second TARGET at byte 4 (shifted by narrowing).
        assert_eq!(program.jump_table().len(), 2);
        assert_eq!(program.jump_table().get(0), Some(0));
        assert_eq!(program.jump_table().get(1), Some(4));
    }
}
