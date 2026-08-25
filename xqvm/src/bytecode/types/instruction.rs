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

use core::fmt;

use super::Opcode;
use super::RegisterEffect;

// ---------------------------------------------------------------------------
// Macro-generated Instruction enum
// ---------------------------------------------------------------------------

macro_rules! impl_instruction {
    (
        $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $_delta:expr, {$($fname:ident: $ftype:ty),*}) ),*
        $(,)?
    ) => {
        /// A fully decoded XQVM instruction with its operands.
        ///
        /// All variants use named struct syntax. Unit-like instructions (no
        /// operands) use an empty field list (`Nop {}`). This uniform
        /// representation allows the entire enum to be generated from the
        /// [`opcodes!`](crate::opcodes) x-macro without any special-casing.
        ///
        /// Use [`Instruction::opcode`] to recover the corresponding
        /// [`Opcode`], or [`Instruction::mnemonic`] for the assembly string.
        ///
        /// # Examples
        ///
        /// ```rust
        /// use xqvm::{Instruction, Opcode, Register};
        ///
        /// let instr = Instruction::Pop {};
        /// assert_eq!(instr.opcode(), Opcode::Pop);
        /// assert_eq!(instr.mnemonic(), "POP");
        ///
        /// let instr = Instruction::Energy {
        ///     model:  Register(0),
        ///     sample: Register(1),
        /// };
        /// assert_eq!(instr.opcode(), Opcode::Energy);
        /// ```
        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum Instruction {
            $(
                #[doc = $doc]
                $variant {
                    $(
                        #[expect(missing_docs, reason = "macro-generated enum variant fields don't warrant individual doc comments")]
                        $fname: $ftype,
                    )*
                },
            )*
        }

        impl Instruction {
            /// Return the [`Opcode`] corresponding to this instruction.
            ///
            /// # Examples
            ///
            /// ```rust
            /// use xqvm::{Instruction, Opcode, Register};
            ///
            /// assert_eq!(Instruction::Add {}.opcode(), Opcode::Add);
            /// assert_eq!(Instruction::Stow { reg: Register(7) }.opcode(), Opcode::Stow);
            /// ```
            pub fn opcode(&self) -> Opcode {
                match self {
                    $( Self::$variant { .. } => Opcode::$variant, )*
                }
            }

            /// Return the uppercase assembly mnemonic for this instruction.
            ///
            /// Delegates to [`Opcode::mnemonic`].
            ///
            /// # Examples
            ///
            /// ```rust
            /// use xqvm::Instruction;
            ///
            /// assert_eq!(Instruction::Halt {}.mnemonic(), "HALT");
            /// ```
            pub fn mnemonic(&self) -> &'static str {
                self.opcode().mnemonic()
            }
        }
    };
}

opcodes!(impl_instruction);

// ---------------------------------------------------------------------------
// Stack effect
// ---------------------------------------------------------------------------

/// Net effect of a single instruction on the value stack depth.
///
/// Most instructions apply a fixed `Delta(d)` where `d = pushes − pops`.
/// The sole exception is [`SCLR`](Instruction::Sclr), which resets depth to
/// zero regardless of the current depth (`Reset`).
///
/// The sentinel value `i8::MIN` in the [`opcodes!`](crate::opcodes) table
/// maps to `Reset`; all other values map to `Delta`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StackEffect {
    /// Fixed net change to stack depth: positive means net push, negative means net pop.
    Delta(i8),
    /// Set depth to zero unconditionally (SCLR).
    Reset,
}

impl StackEffect {
    /// Convert the raw `i8` delta stored in the opcode table to a [`StackEffect`].
    ///
    /// `i8::MIN` is the sentinel for [`StackEffect::Reset`] (SCLR).
    pub fn from_delta(delta: i8) -> Self {
        if delta == i8::MIN {
            Self::Reset
        } else {
            Self::Delta(delta)
        }
    }
}

macro_rules! impl_stack_effect {
    (
        $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $delta:expr, {$($f:tt)*}) ),*
        $(,)?
    ) => {
        impl Instruction {
            /// Return the net stack effect of this instruction.
            ///
            /// # Examples
            ///
            /// ```rust
            /// use xqvm::{Instruction, StackEffect};
            ///
            /// assert_eq!(Instruction::Add {}.stack_effect(), StackEffect::Delta(-1));
            /// assert_eq!(Instruction::Push1 { val: [0] }.stack_effect(), StackEffect::Delta(1));
            /// assert_eq!(Instruction::Sclr {}.stack_effect(), StackEffect::Reset);
            /// ```
            pub fn stack_effect(&self) -> StackEffect {
                match self {
                    $( Self::$variant { .. } => StackEffect::from_delta($delta as i8), )*
                }
            }
        }
    };
}

opcodes!(impl_stack_effect);

// ---------------------------------------------------------------------------
// Register introspection
// ---------------------------------------------------------------------------

impl Instruction {
    /// Register indices this instruction reads from.
    ///
    /// Returns the set of register slots whose current values are consumed
    /// by this instruction. Stack-only and control-flow-only instructions
    /// return an empty set.
    #[expect(
        clippy::too_many_lines,
        reason = "exhaustive match over the full instruction set; splitting it would scatter the read/write classification across helper fns"
    )]
    pub fn read_registers(&self) -> RegisterEffect {
        match self {
            // -- No register reads --
            // Control flow
            Self::Nop { .. }
            | Self::Target { .. }
            | Self::Jump1 { .. }
            | Self::Jump2 { .. }
            | Self::JumpI1 { .. }
            | Self::JumpI2 { .. }
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
            | Self::BitLen { .. }
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
            // Write-only: allocators + stores
            | Self::Stow { .. }
            | Self::Drop { .. }
            | Self::Input { .. }
            | Self::Lidx { .. }
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

            // -- Single register reads --
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
            | Self::Implies { reg }
            | Self::Reduce { model: reg } => RegisterEffect::one(reg.slot()),

            // -- Two register reads --
            Self::Energy { model, sample } => {
                RegisterEffect::two(model.slot(), sample.slot())
            }
            // AtLeast reads model and indices vec.
            Self::AtLeast { model, indices } => {
                RegisterEffect::two(model.slot(), indices.slot())
            }
            // Slack reads+writes both vec registers; Equality and AtLeastW
            // read the two vec registers (model is also read but
            // RegisterEffect caps at two entries).
            Self::Slack { indices, coeffs }
            | Self::Equality { indices, coeffs, .. }
            | Self::AtLeastW { indices, coeffs, .. } => {
                RegisterEffect::two(indices.slot(), coeffs.slot())
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
            // -- No register writes --
            // Control flow
            Self::Nop { .. }
            | Self::Target { .. }
            | Self::Jump1 { .. }
            | Self::Jump2 { .. }
            | Self::JumpI1 { .. }
            | Self::JumpI2 { .. }
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
            | Self::BitLen { .. }
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
            // Energy reads two, writes none
            | Self::Energy { .. } => RegisterEffect::EMPTY,

            // -- Single register writes --
            // Write-only
            Self::Stow { reg }
            | Self::Drop { reg }
            | Self::Input { reg }
            | Self::Lidx { reg }
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
            // Read+write: single register
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
            | Self::Implies { reg }
            // EQUALITY / ATLEAST / ATLEASTW / REDUCE write to the model register.
            | Self::Equality { model: reg, .. }
            | Self::AtLeast { model: reg, .. }
            | Self::AtLeastW { model: reg, .. }
            | Self::Reduce { model: reg } => RegisterEffect::one(reg.slot()),

            // SLACK mutates both vec registers.
            Self::Slack { indices, coeffs } => {
                RegisterEffect::two(indices.slot(), coeffs.slot())
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Display
// ---------------------------------------------------------------------------

/// Sign-extend a big-endian byte slice (1..=8 bytes) to `i64`.
#[expect(
    clippy::cast_possible_truncation,
    reason = "bytes.len() <= 8 per debug_assert; 8 * 8 = 64 always fits in u32"
)]
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bytes.len() is 1..=8 per the debug_assert above, so `64 - len * 8` stays in 0..=56"
)]
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
            Self::Jump1 { label } => write!(f, "JUMP1 .{label}"),
            Self::Jump2 { label } => write!(f, "JUMP2 .{label}"),
            Self::JumpI1 { label } => write!(f, "JUMPI1 .{label}"),
            Self::JumpI2 { label } => write!(f, "JUMPI2 .{label}"),

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
            | Self::Lidx { reg }
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
            | Self::Implies { reg } => {
                write!(f, "{} r{}", self.mnemonic(), reg.slot())
            }

            // No-operand variants: just the mnemonic.
            _ => write!(f, "{}", self.mnemonic()),
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::types::Register;

    // Build a flat array of (Instruction, expected_opcode, mnemonic) triples
    // from the x-macro table so every variant is covered automatically.
    // Each operand is zero-initialised: Register(0), 0i16, 0i64.
    macro_rules! all_instruction_opcode_pairs {
        (
            $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $_delta:expr, {$($fname:ident: $ftype:ty),*}) ),*
            $(,)?
        ) => {
            [
                $(
                    (
                        Instruction::$variant {
                            $( $fname: <$ftype as Default>::default(), )*
                        },
                        Opcode::$variant,
                        $mnem,
                    )
                ),*
            ]
        };
    }

    #[test]
    fn opcode_method_covers_all_variants() {
        for (instr, expected, _) in opcodes!(all_instruction_opcode_pairs) {
            assert_eq!(instr.opcode(), expected, "opcode mismatch for {instr:?}");
        }
    }

    #[test]
    fn mnemonic_method_covers_all_variants() {
        for (instr, _, expected_mnem) in opcodes!(all_instruction_opcode_pairs) {
            assert_eq!(
                instr.mnemonic(),
                expected_mnem,
                "mnemonic mismatch for {instr:?}"
            );
        }
    }

    #[test]
    fn instruction_count_is_93() {
        assert_eq!(opcodes!(all_instruction_opcode_pairs).len(), 93);
    }

    #[test]
    fn energy_named_fields() {
        let e = Instruction::Energy {
            model: Register(0),
            sample: Register(1),
        };
        assert_eq!(e.opcode(), Opcode::Energy);
        if let Instruction::Energy { model, sample } = e {
            assert_eq!(model.slot(), 0);
            assert_eq!(sample.slot(), 1);
        } else {
            panic!("pattern match failed");
        }
    }

    #[test]
    fn read_registers_stack_only() {
        let add = Instruction::Add {};
        assert!(add.read_registers().is_empty());
        assert!(add.written_registers().is_empty());
    }

    #[test]
    fn read_registers_load() {
        let load = Instruction::Load { reg: Register(5) };
        assert_eq!(load.read_registers().as_slice(), &[5]);
        assert!(load.written_registers().is_empty());
    }

    #[test]
    fn written_registers_stow() {
        let stow = Instruction::Stow { reg: Register(3) };
        assert!(stow.read_registers().is_empty());
        assert_eq!(stow.written_registers().as_slice(), &[3]);
    }

    #[test]
    fn read_write_registers_vec_push() {
        let vp = Instruction::VecPush { reg: Register(0) };
        assert_eq!(vp.read_registers().as_slice(), &[0]);
        assert_eq!(vp.written_registers().as_slice(), &[0]);
    }

    #[test]
    fn read_registers_energy() {
        let e = Instruction::Energy {
            model: Register(1),
            sample: Register(2),
        };
        assert_eq!(e.read_registers().as_slice(), &[1, 2]);
        assert!(e.written_registers().is_empty());
    }

    #[test]
    fn all_variants_covered_by_read_and_written() {
        for (instr, _, _) in opcodes!(all_instruction_opcode_pairs) {
            let _ = instr.read_registers();
            let _ = instr.written_registers();
        }
    }

    #[cfg(not(feature = "std"))]
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
            format!(
                "{}",
                Instruction::Push8 {
                    val: 42i64.to_be_bytes()
                }
            ),
            "PUSH8 42",
        );
        assert_eq!(
            format!("{}", Instruction::Push1 { val: [0xFF] }),
            "PUSH1 -1",
        );
    }

    #[test]
    fn display_jump() {
        assert_eq!(format!("{}", Instruction::Jump1 { label: 3 }), "JUMP1 .3");
        assert_eq!(
            format!("{}", Instruction::Jump2 { label: 300 }),
            "JUMP2 .300",
        );
        assert_eq!(format!("{}", Instruction::JumpI1 { label: 3 }), "JUMPI1 .3");
        assert_eq!(
            format!("{}", Instruction::JumpI2 { label: 300 }),
            "JUMPI2 .300",
        );
    }

    #[test]
    fn display_energy() {
        assert_eq!(
            format!(
                "{}",
                Instruction::Energy {
                    model: Register(0),
                    sample: Register(1)
                }
            ),
            "ENERGY r0 r1",
        );
    }
}
