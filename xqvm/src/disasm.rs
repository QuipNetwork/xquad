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

//! Pretty printer for XQVM bytecode.
//!
//! The entry point is [`Disassembly`], which wraps either a raw byte slice or
//! a complete [`Program`] and
//! implements [`Display`](std::fmt::Display). Jump targets are rendered
//! with their `.N` label from the jump table, both at the destination and as
//! the operand of the originating `JUMP`/`JUMPI`.
//!
//! `PUSH1`..`PUSHC_8` operands are rendered as their sign-extended decimal
//! value.
//!
//! Unknown bytes (invalid opcodes or truncated operands) are displayed as
//! `.byte 0xXX` pseudo-instructions so no information is silently dropped.
//!
//! Label collection is delegated to [`InstructionStream`],
//! which derives the label map from the [`Program`]'s jump table.
//!
//! # Output format
//!
//! Labels appear inline between the byte offset and the mnemonic. When no
//! label is present at an address the column is left blank so columns align:
//!
//! ```text
//!   0x0000:  .0:  PUSH2   12345
//!   0x0003:       GT
//!   0x0004:       JUMPI    .0
//!   0x0007:       HALT
//! ```
//!
//! # Examples
//!
//! ```rust
//! use xqvm::Instruction;
//! use xqvm::bytecode::codec;
//! use xqvm::Disassembly;
//!
//! // No jumps -- label column is suppressed entirely.
//! let program = [
//!     Instruction::Push1 { val: [1] },
//!     Instruction::Push1 { val: [2] },
//!     Instruction::Add  {},
//!     Instruction::Halt {},
//! ];
//! let buf: Vec<u8> = program.iter().flat_map(|i| codec::encode(i)).collect();
//!
//! let text = Disassembly::new(&buf).to_string();
//! assert!(text.contains("PUSH1"));
//! assert!(text.contains("ADD"));
//! assert!(text.contains("HALT"));
//! ```

use std::collections::BTreeMap;
use std::fmt;
use std::io;

use crate::bytecode::error::StreamError;
use crate::bytecode::{Instruction, InstructionStream, Program, Register};
use crate::opcodes;

// ---------------------------------------------------------------------------
// Helpers
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

// ---------------------------------------------------------------------------
// Operand display
// ---------------------------------------------------------------------------

/// Format a single operand field of an instruction.
///
/// `u16` fields are formatted as `.N` label references; all other field
/// types are formatted as-is.
trait FmtOperand {
    fn fmt_operand(
        &self,
        f: &mut dyn io::Write,
        labels: &BTreeMap<usize, String>,
        instr_offset: usize,
    ) -> io::Result<()>;
}

impl FmtOperand for Register {
    fn fmt_operand(
        &self,
        f: &mut dyn io::Write,
        _labels: &BTreeMap<usize, String>,
        _instr_offset: usize,
    ) -> io::Result<()> {
        write!(f, "r{}", self.0)
    }
}

impl FmtOperand for i64 {
    fn fmt_operand(
        &self,
        f: &mut dyn io::Write,
        _labels: &BTreeMap<usize, String>,
        _instr_offset: usize,
    ) -> io::Result<()> {
        write!(f, "{self}")
    }
}

/// `[u8; N]` fields appear on `PUSH1`..`PUSHC_8` -- render as a decimal
/// by sign-extending the bytes to `i64`.
macro_rules! impl_fmt_operand_byte_array {
    ($($n:literal),+) => {
        $(
            impl FmtOperand for [u8; $n] {
                fn fmt_operand(
                    &self,
                    f: &mut dyn io::Write,
                    _labels: &BTreeMap<usize, String>,
                    _instr_offset: usize,
                ) -> io::Result<()> {
                    write!(f, "{}", sign_extend_be(self))
                }
            }
        )+
    };
}

impl_fmt_operand_byte_array!(1, 2, 3, 4, 5, 6, 7, 8);

impl FmtOperand for u16 {
    /// Format a `JUMP2`/`JUMPI2` label index as `.N`.
    fn fmt_operand(
        &self,
        f: &mut dyn io::Write,
        _labels: &BTreeMap<usize, String>,
        _instr_offset: usize,
    ) -> io::Result<()> {
        write!(f, ".{self}")
    }
}

impl FmtOperand for u8 {
    /// Format a `JUMP1`/`JUMPI1` (narrow) label index as `.N`.
    fn fmt_operand(
        &self,
        f: &mut dyn io::Write,
        _labels: &BTreeMap<usize, String>,
        _instr_offset: usize,
    ) -> io::Result<()> {
        write!(f, ".{self}")
    }
}

// ---------------------------------------------------------------------------
// Macro-generated instruction formatter
// ---------------------------------------------------------------------------

macro_rules! impl_fmt_instruction {
    ( $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
          $_delta:expr, {$($fname:ident: $ftype:ty),*}) ),* $(,)? ) => {

        fn fmt_instruction(
            instr: &Instruction,
            instr_offset: usize,
            labels: &BTreeMap<usize, String>,
            f: &mut dyn io::Write,
        ) -> io::Result<()> {
            match instr {
                $(
                    Instruction::$variant { $($fname,)* } => {
                        write!(f, "{:<8}", $mnem)?;
                        // `_first` and its mutation are unused when there are no
                        // operands; the attribute suppresses the resulting warning.
                        let mut _first = true;
                        $(
                            if !_first { write!(f, ", ")?; }
                            $fname.fmt_operand(f, labels, instr_offset)?;
                            _first = false;
                        )*
                        let _ = _first;
                        Ok(())
                    }
                )*
            }
        }
    };
}

opcodes!(impl_fmt_instruction);

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// A pretty-printed view of raw XQVM bytecode or a complete [`Program`].
///
/// Jump targets are rendered with their `.N` label from the jump table.
/// Each label is printed inline between the byte offset and the mnemonic.
/// When no label exists at an address the column is left blank so all
/// columns align. `JUMP`/`JUMPI` operands are rendered as `.N` label
/// references. `PUSH1`..`PUSH8` operands are sign-extended and
/// rendered as decimals.
///
/// # Examples
///
/// ```rust
/// use xqvm::{Instruction, Register};
/// use xqvm::bytecode::codec;
/// use xqvm::Disassembly;
///
/// let program = [
///     Instruction::Push1 { val: [5] },
///     Instruction::Push1 { val: [0xFF] },
///     Instruction::Add    {},
///     Instruction::Halt   {},
/// ];
/// let buf: Vec<u8> = program.iter().flat_map(|i| codec::encode(i)).collect();
/// print!("{}", Disassembly::new(&buf));
/// ```
#[derive(Debug, Clone)]
pub struct Disassembly<'a> {
    stream: InstructionStream<'a>,
}

impl<'a> Disassembly<'a> {
    /// Wrap a raw bytecode buffer for display.
    ///
    /// Constructs an [`InstructionStream`] immediately so that repeated
    /// calls to [`write_to`](Self::write_to) reuse the already-computed
    /// label map.
    pub fn new(bytes: &'a [u8]) -> Self {
        Self {
            stream: InstructionStream::new(bytes),
        }
    }

    /// Wrap a [`Program`] for display.
    pub fn from_program(program: &'a Program) -> Self {
        Self {
            stream: InstructionStream::from_program(program),
        }
    }

    /// Write the disassembly listing to `out`.
    ///
    /// Each instruction is emitted as one line in the form:
    ///
    /// ```text
    ///   0x<offset>:  [<label>:]  <MNEMONIC>  [operands]
    /// ```
    ///
    /// The label column is omitted entirely when no jumps are present.
    /// Invalid bytes are rendered as `.byte 0xXX` pseudo-instructions so no
    /// information is silently dropped.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Instruction;
    /// use xqvm::bytecode::codec;
    /// use xqvm::Disassembly;
    ///
    /// let program = [Instruction::Push1 { val: [1] }, Instruction::Halt {}];
    /// let buf: Vec<u8> = program.iter().flat_map(codec::encode).collect();
    ///
    /// let mut out = Vec::new();
    /// Disassembly::new(&buf).write_to(&mut out).unwrap();
    /// let text = String::from_utf8(out).unwrap();
    /// assert!(text.contains("PUSH1"));
    /// assert!(text.contains("HALT"));
    /// ```
    ///
    /// # Errors
    ///
    /// Returns an `io::Error` if writing to `out` fails.
    pub fn write_to(&self, out: &mut impl io::Write) -> io::Result<()> {
        // Width of the label column: longest ".N:" string, or 0 when there
        // are no labels so the column is omitted entirely.
        #[expect(
            clippy::arithmetic_side_effects,
            reason = "`s` is a jump-table label name, so its length plus one cannot overflow usize"
        )]
        let label_col = self
            .stream
            .labels()
            .values()
            .map(|s| s.len() + 1)
            .max()
            .unwrap_or(0);

        // Clone the label map so `fmt_instruction` can resolve u16 operands
        // to label names after the stream is consumed by iteration.
        let labels = self.stream.labels().clone();

        // Clone the stream to iterate from position 0 without mutating self.
        for item in self.stream.clone() {
            match item {
                Ok((offset, label, instr)) => {
                    let label_str = label.map(|s| format!("{s}:")).unwrap_or_default();
                    write!(out, "  0x{offset:04X}:  ")?;
                    if label_col > 0 {
                        write!(out, "{label_str:<label_col$}  ")?;
                    }
                    fmt_instruction(&instr, offset, &labels, out)?;
                    writeln!(out)?;
                }
                Err(ref e) => {
                    let (offset, byte) = match e {
                        StreamError::UnknownOpcode { offset, byte } => (*offset, *byte),
                        StreamError::TruncatedInstruction { offset } => {
                            let byte = *self.stream.bytes().get(*offset).unwrap_or_else(|| {
                                unreachable!("TruncatedInstruction offset within buffer")
                            });
                            (*offset, byte)
                        }
                        StreamError::SeekOutOfBounds { .. } => {
                            unreachable!("stream never seeks internally")
                        }
                    };
                    let label_str = labels
                        .get(&offset)
                        .map(|s| format!("{s}:"))
                        .unwrap_or_default();
                    write!(out, "  0x{offset:04X}:  ")?;
                    if label_col > 0 {
                        write!(out, "{label_str:<label_col$}  ")?;
                    }
                    writeln!(out, ".byte    0x{byte:02X}")?;
                }
            }
        }

        Ok(())
    }
}

impl fmt::Display for Disassembly<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut buf = Vec::new();
        self.write_to(&mut buf).map_err(|_| fmt::Error)?;
        f.write_str(std::str::from_utf8(&buf).map_err(|_| fmt::Error)?)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::{Instruction, JumpTable, Program, Register, codec};

    fn assemble(program: &[Instruction]) -> Vec<u8> {
        program.iter().flat_map(codec::encode).collect()
    }

    #[test]
    fn basic_program_contains_mnemonics() {
        let buf = assemble(&[Instruction::Push1 { val: [42] }, Instruction::Halt {}]);
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains("PUSH1"), "missing PUSH1 in:\n{text}");
        assert!(text.contains("42"), "missing immediate 42 in:\n{text}");
        assert!(text.contains("HALT"), "missing HALT in:\n{text}");
    }

    #[test]
    fn register_operand_displays_as_r_slot() {
        let buf = assemble(&[Instruction::Load { reg: Register(3) }]);
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains("r3"), "expected r3 in:\n{text}");
    }

    #[test]
    fn byte_offsets_are_correct() {
        // POP is 1 byte (opcode only); HALT starts at offset 1.
        let buf = assemble(&[Instruction::Pop {}, Instruction::Halt {}]);
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains("0x0000"), "missing 0x0000 in:\n{text}");
        assert!(text.contains("0x0001"), "missing 0x0001 in:\n{text}");
    }

    #[test]
    fn backward_jump_gets_label() {
        // TARGET at offset 0 + body + JUMPI1 0 + HALT. After QUI-405 the
        // jump table is built by scanning the buffer for TARGET opcodes.
        let code = assemble(&[
            Instruction::Target {},
            Instruction::Push1 { val: [5] },
            Instruction::Gt {},
            Instruction::JumpI1 { label: 0u8 },
            Instruction::Halt {},
        ]);
        let program = Program::new(code);
        let text = Disassembly::from_program(&program).to_string();
        assert!(text.contains(".0:"), "missing label .0 in:\n{text}");
        assert!(
            text.contains("JUMPI1  .0"),
            "expected 'JUMPI1  .0' in:\n{text}"
        );
    }

    #[test]
    fn forward_jump_gets_label() {
        // JUMP1 .0 at offset 0; NOP; TARGET (the label) at offset 3; HALT.
        let code = assemble(&[
            Instruction::Jump1 { label: 0u8 },
            Instruction::Nop {},
            Instruction::Target {},
            Instruction::Halt {},
        ]);
        let program = Program::new(code);
        let text = Disassembly::from_program(&program).to_string();
        assert!(text.contains(".0:"), "missing label in:\n{text}");
        assert!(
            text.contains("JUMP1   .0"),
            "expected 'JUMP1   .0' in:\n{text}"
        );
    }

    #[test]
    fn two_distinct_labels_appear() {
        // Two TARGETs in the stream -> sequential ids 0 and 1.
        let code = assemble(&[
            Instruction::Target {},
            Instruction::JumpI1 { label: 0u8 },
            Instruction::Jump1 { label: 1u8 },
            Instruction::Target {},
            Instruction::Halt {},
        ]);
        let program = Program::new(code);
        // Sanity: the scan found both TARGETs.
        assert_eq!(program.jump_table().len(), 2);
        let _ = JumpTable::default(); // keep import live
        let text = Disassembly::from_program(&program).to_string();
        assert!(text.contains(".0:"), "missing .0 in:\n{text}");
        assert!(text.contains(".1:"), "missing .1 in:\n{text}");
    }

    #[test]
    fn unknown_byte_displays_as_dot_byte() {
        let buf = [0xFEu8]; // not a valid opcode
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains(".byte"), "expected .byte in:\n{text}");
        assert!(text.contains("0xFE"), "expected 0xFE in:\n{text}");
    }

    #[test]
    fn energy_instruction_shows_two_registers() {
        let buf = assemble(&[Instruction::Energy {
            model: Register(1),
            sample: Register(2),
        }]);
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains("ENERGY"), "missing ENERGY in:\n{text}");
        assert!(text.contains("r1"), "missing r1 in:\n{text}");
        assert!(text.contains("r2"), "missing r2 in:\n{text}");
    }

    #[test]
    fn negative_immediate_displays_correctly() {
        let buf = assemble(&[Instruction::Push1 { val: [0xF9] }]); // -7 as i8
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains("-7"), "expected -7 in:\n{text}");
    }

    #[test]
    fn empty_bytecode_produces_empty_output() {
        assert_eq!(Disassembly::new(&[]).to_string(), "");
    }

    #[test]
    fn push2_displays_value() {
        // PUSH2 with 0x0007 encodes the value 7.
        let buf = assemble(&[
            Instruction::Push2 { val: [0x00, 0x07] },
            Instruction::Halt {},
        ]);
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains("PUSH2"), "missing PUSH2 in:\n{text}");
        assert!(text.contains('7'), "missing value 7 in:\n{text}");
    }

    #[test]
    fn push3_displays_large_value() {
        // 100000 = 0x0001_86A0 fits in PUSH3.
        let buf = assemble(&[
            Instruction::Push3 {
                val: [0x01, 0x86, 0xA0],
            },
            Instruction::Halt {},
        ]);
        let text = Disassembly::new(&buf).to_string();
        assert!(text.contains("100000"), "missing 100000 in:\n{text}");
    }
}
