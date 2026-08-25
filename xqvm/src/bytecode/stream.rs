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

//! Incremental, seekable reader for XQVM bytecode.
//!
//! [`InstructionStream`] wraps a raw byte buffer and decodes one instruction
//! at a time, advancing an internal cursor after each successful read.
//! Seeking moves the cursor to any absolute byte offset, which lets the VM
//! execute `JUMP`/`JUMPI` targets directly without re-scanning the buffer.
//!
//! Labels are derived from the [`JumpTable`](crate::JumpTable) attached to a
//! [`Program`]. When constructed via [`from_program`](InstructionStream::from_program),
//! the stream maps each jump-table entry's start offset to a label name
//! (`.0`, `.1`, ...). When constructed from raw bytes via [`new`](InstructionStream::new),
//! no labels are available.
//!
//! The stream also implements [`Iterator`] so it can be consumed with
//! standard iterator combinators.
//!
//! # Errors
//!
//! [`enum@Error`] is returned when a byte cannot be decoded or a seek is out of
//! bounds. On decode errors the cursor advances by one byte so the stream
//! always makes progress and infinite loops in consumer code are prevented.
//!
//! # Examples
//!
//! ```rust
//! use xqvm::{codec, Instruction, InstructionStream};
//!
//! let program = [
//!     Instruction::Push1 { val: [1] },
//!     Instruction::Push1 { val: [2] },
//!     Instruction::Add  {},
//!     Instruction::Halt {},
//! ];
//! let buf: Vec<u8> = program.iter().flat_map(codec::encode).collect();
//!
//! // No jump table -- every label is None.
//! let mut stream = InstructionStream::new(&buf);
//! let mut decoded = Vec::new();
//! while let Some(item) = stream.next() {
//!     let (_offset, label, instr) = item.expect("decode");
//!     assert!(label.is_none());
//!     decoded.push(instr);
//! }
//!
//! assert_eq!(decoded.len(), 4);
//! assert_eq!(decoded[0], Instruction::Push1 { val: [1] });
//! assert_eq!(decoded[3], Instruction::Halt {});
//! ```

#[cfg(not(feature = "std"))]
use alloc::{collections::BTreeMap, format, string::String};
#[cfg(feature = "std")]
use std::collections::BTreeMap;

use thiserror::Error;

use super::codec;
use super::jump_table::JumpTable;
use super::program::Program;
use super::types::Opcode;

// ---------------------------------------------------------------------------
// Error and Result
// ---------------------------------------------------------------------------

/// All errors produced by [`InstructionStream`].
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum Error {
    /// The byte at `offset` is not a recognised XQVM opcode.
    ///
    /// The stream cursor advances past the offending byte so iteration always
    /// makes progress.
    #[error("unknown opcode 0x{byte:02X} at offset {offset:#06X}")]
    UnknownOpcode {
        /// Absolute byte offset of the bad opcode byte.
        offset: usize,
        /// The unrecognised byte value.
        byte: u8,
    },

    /// The buffer ends before all operand bytes of the instruction at
    /// `offset` have been read.
    ///
    /// The stream cursor advances past the offending byte so iteration always
    /// makes progress.
    #[error("truncated instruction at offset {offset:#06X}")]
    TruncatedInstruction {
        /// Absolute byte offset of the opcode whose operands are missing.
        offset: usize,
    },

    /// The seek target exceeds the length of the underlying buffer.
    #[error("seek target {target} is out of bounds (buffer length {len})")]
    SeekOutOfBounds {
        /// The requested byte offset.
        target: usize,
        /// Total length of the underlying buffer.
        len: usize,
    },
}

type Result<T> = core::result::Result<T, Error>;

// ---------------------------------------------------------------------------
// InstructionStream
// ---------------------------------------------------------------------------

/// Incremental, seekable reader over a raw XQVM bytecode buffer.
///
/// The stream holds a shared reference to a byte slice, an internal cursor,
/// and a label map derived from the program's jump table. Calling
/// [`next_instruction`](InstructionStream::next_instruction) (or iterating)
/// decodes the next instruction at the current cursor position, advances the
/// cursor, and returns the optional label assigned to that address.
///
/// [`seek`](InstructionStream::seek) moves the cursor to any absolute byte
/// offset in `[0, len]`. Seeking to `len` is valid and positions the stream
/// past the end so the next read returns `None`.
///
/// # Examples
///
/// ```rust
/// use xqvm::Instruction;
/// use xqvm::{codec, InstructionStream};
///
/// let buf: Vec<u8> = [
///     Instruction::Push1 { val: [3] },
///     Instruction::Halt {},
/// ].iter().flat_map(|i| codec::encode(i)).collect();
///
/// let mut stream = InstructionStream::new(&buf);
///
/// let (off0, _, instr0) = stream.next_instruction().unwrap().unwrap();
/// assert_eq!(off0, 0);
/// assert_eq!(instr0, Instruction::Push1 { val: [3] });
/// ```
#[derive(Debug, Clone)]
pub struct InstructionStream<'a> {
    bytes: &'a [u8],
    pos: usize,
    labels: BTreeMap<usize, String>,
}

impl<'a> InstructionStream<'a> {
    /// Create a new stream positioned at the start of `bytes`.
    ///
    /// No labels are assigned; use [`with_jump_table`](Self::with_jump_table)
    /// or [`from_program`](Self::from_program) to get label information.
    pub fn new(bytes: &'a [u8]) -> Self {
        Self {
            bytes,
            pos: 0,
            labels: BTreeMap::new(),
        }
    }

    /// Create a stream with labels derived from a [`JumpTable`].
    ///
    /// Each `TARGET` byte offset is mapped to `.{seq_id}` in the label map,
    /// where `seq_id` is the sequential id of that `TARGET` in stream order
    /// (the same id that `JUMP`/`JUMPI` operands reference).
    pub fn with_jump_table(bytes: &'a [u8], table: &JumpTable) -> Self {
        let labels = table
            .targets()
            .iter()
            .enumerate()
            .map(|(seq_id, &offset)| (offset, format!(".{seq_id}")))
            .collect();
        Self {
            bytes,
            pos: 0,
            labels,
        }
    }

    /// Create a stream from a [`Program`], borrowing its instruction bytes.
    ///
    /// Labels are derived from the program's jump table.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Program;
    /// use xqvm::InstructionStream;
    /// use xqvm::Instruction;
    ///
    /// let program = Program::new(vec![0xFFu8]); // HALT
    ///
    /// let mut stream = InstructionStream::from_program(&program);
    /// let (_, _, instr) = stream.next_instruction().unwrap().unwrap();
    /// assert_eq!(instr, Instruction::Halt {});
    /// ```
    pub fn from_program(program: &'a Program) -> Self {
        Self::with_jump_table(program.code(), program.jump_table())
    }

    /// Current cursor position (byte offset into the buffer).
    pub fn pos(&self) -> usize {
        self.pos
    }

    /// Total length of the underlying byte buffer.
    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    /// Returns `true` if the buffer contains no bytes.
    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty()
    }

    /// The raw byte slice underlying this stream.
    pub fn bytes(&self) -> &[u8] {
        self.bytes
    }

    /// The label map (possibly empty if constructed from raw bytes).
    ///
    /// Keys are absolute byte offsets; values are label names (`.0`,
    /// `.1`, ...) derived from the jump table.
    pub fn labels(&self) -> &BTreeMap<usize, String> {
        &self.labels
    }

    /// Seek to absolute byte offset `pos`.
    ///
    /// `pos` may be anywhere in `[0, len]`. Seeking to `len` positions the
    /// stream just past the last byte; the next [`next_instruction`](Self::next_instruction) call
    /// will return `None`.
    ///
    /// # Errors
    ///
    /// Returns [`Error::SeekOutOfBounds`] if `pos > len`.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Instruction;
    /// use xqvm::{InstructionStream, codec};
    /// use xqvm::bytecode::error::StreamError as Error;
    ///
    /// let buf = codec::encode(&Instruction::Nop {});
    /// let mut stream = InstructionStream::new(&buf);
    ///
    /// assert!(stream.seek(0).is_ok());
    /// assert!(stream.seek(buf.len()).is_ok()); // seek to end is valid
    /// assert_eq!(
    ///     stream.seek(buf.len() + 1),
    ///     Err(Error::SeekOutOfBounds { target: buf.len() + 1, len: buf.len() }),
    /// );
    /// ```
    pub fn seek(&mut self, pos: usize) -> Result<()> {
        if pos > self.bytes.len() {
            return Err(Error::SeekOutOfBounds {
                target: pos,
                len: self.bytes.len(),
            });
        }
        self.pos = pos;
        Ok(())
    }

    /// Decode and return the next instruction, advancing the cursor.
    ///
    /// Returns:
    /// - `None` when the cursor is at or past the end of the buffer.
    /// - `Some(Ok((offset, label, instruction)))` on a successful decode,
    ///   where `offset` is the byte position of the opcode and `label` is the
    ///   name assigned to that address (e.g. `Some(".0")`), or `None` when
    ///   no jump-table entry targets this address.
    /// - `Some(Err(e))` when the bytes at the cursor cannot be decoded.
    ///   The cursor advances by one byte so subsequent calls make progress.
    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`self.pos < self.bytes.len()` is checked on entry and one instruction is at most nine bytes (`spec/xqvm/ENCODING.md`), so the cursor cannot overflow usize"
    )]
    pub fn next_instruction(
        &mut self,
    ) -> Option<Result<(usize, Option<String>, crate::bytecode::types::Instruction)>> {
        if self.pos >= self.bytes.len() {
            return None;
        }

        let offset = self.pos;
        let label = self.labels.get(&offset).cloned();

        match codec::decode(
            self.bytes
                .get(offset..)
                .unwrap_or_else(|| unreachable!("offset < bytes.len() checked above")),
        ) {
            Ok((instr, consumed)) => {
                self.pos += consumed;
                Some(Ok((offset, label, instr)))
            }
            Err(e) => {
                // Always advance by one so the stream makes progress.
                self.pos += 1;
                Some(Err(map_decode_error(
                    e,
                    offset,
                    *self
                        .bytes
                        .get(offset)
                        .unwrap_or_else(|| unreachable!("offset < bytes.len() checked above")),
                )))
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Iterator
// ---------------------------------------------------------------------------

impl Iterator for InstructionStream<'_> {
    type Item = Result<(usize, Option<String>, crate::bytecode::types::Instruction)>;

    fn next(&mut self) -> Option<Self::Item> {
        self.next_instruction()
    }
}

// ---------------------------------------------------------------------------
// Error mapping
// ---------------------------------------------------------------------------

fn map_decode_error(err: codec::DecodeError, offset: usize, byte: u8) -> Error {
    match err {
        codec::DecodeError::UnknownOpcode { byte } => Error::UnknownOpcode { offset, byte },
        codec::DecodeError::EmptyInput | codec::DecodeError::TruncatedOperand { .. } => {
            // The buffer ended mid-instruction. Surface the recognised-opcode
            // case as `TruncatedInstruction` so VM diagnostics still highlight
            // the failing opcode; for an empty payload we fall back to the
            // raw byte (which can only happen when callers pass an empty
            // slice, never during normal stream walking).
            if Opcode::try_from(byte).is_ok() {
                Error::TruncatedInstruction { offset }
            } else {
                Error::UnknownOpcode { offset, byte }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::types::{Instruction, Register};

    fn assemble(program: &[Instruction]) -> Vec<u8> {
        program.iter().flat_map(codec::encode).collect()
    }

    #[test]
    fn empty_buffer_yields_none_immediately() {
        let mut stream = InstructionStream::new(&[]);
        assert_eq!(stream.next_instruction(), None);
    }

    #[test]
    fn single_instruction_roundtrip() {
        let buf = assemble(&[Instruction::Halt {}]);
        let mut stream = InstructionStream::new(&buf);
        assert_eq!(
            stream.next_instruction(),
            Some(Ok((0, None, Instruction::Halt {}))),
        );
        assert_eq!(stream.next_instruction(), None);
    }

    #[test]
    fn offsets_advance_correctly() {
        // Pop (1 byte) at 0, HALT (1 byte) at 1.
        let buf = assemble(&[Instruction::Pop {}, Instruction::Halt {}]);
        let mut stream = InstructionStream::new(&buf);

        let (off0, _, _) = stream.next_instruction().unwrap().unwrap();
        let (off1, _, _) = stream.next_instruction().unwrap().unwrap();
        assert_eq!(off0, 0);
        assert_eq!(off1, 1);
        assert_eq!(stream.next_instruction(), None);
    }

    #[test]
    fn iterator_collects_all_instructions() {
        let program = [
            Instruction::Push1 { val: [5] },
            Instruction::Push1 { val: [3] },
            Instruction::Add {},
            Instruction::Halt {},
        ];
        let buf = assemble(&program);
        let items: Vec<_> = InstructionStream::new(&buf)
            .collect::<Result<Vec<_>>>()
            .unwrap();
        assert_eq!(items.len(), 4);
        for (i, (_, _, instr)) in items.iter().enumerate() {
            assert_eq!(*instr, program[i]);
        }
    }

    #[test]
    fn jump_table_labels_are_assigned() {
        // Build byte buffer: Push1 (2 bytes) + Jump2 (3 bytes) + Halt (1 byte)
        let buf = assemble(&[
            Instruction::Push1 { val: [3] },
            Instruction::Jump2 { label: 0 },
            Instruction::Halt {},
        ]);
        // Hand-construct a jump table with the HALT at byte 5 marked as
        // sequential id 0 (rather than calling JumpTable::scan which would
        // find no TARGETs in this buffer).
        let table = JumpTable::new(vec![5]);
        let mut stream = InstructionStream::with_jump_table(&buf, &table);

        let (_, label0, _) = stream.next_instruction().unwrap().unwrap();
        let (_, label1, _) = stream.next_instruction().unwrap().unwrap();
        let (_, label2, _) = stream.next_instruction().unwrap().unwrap();

        assert_eq!(label0, None);
        assert_eq!(label1, None);
        assert_eq!(label2.as_deref(), Some(".0"));
    }

    #[test]
    fn no_labels_when_no_jump_table() {
        let buf = assemble(&[Instruction::Push1 { val: [1] }, Instruction::Halt {}]);
        let stream = InstructionStream::new(&buf);
        assert!(stream.labels().is_empty());
    }

    #[test]
    fn seek_to_second_instruction_skips_first() {
        let buf = assemble(&[
            Instruction::Pop {},
            Instruction::Nop {},
            Instruction::Halt {},
        ]);
        let mut stream = InstructionStream::new(&buf);

        stream.seek(1).unwrap();
        let (off, _, instr) = stream.next_instruction().unwrap().unwrap();
        assert_eq!(off, 1);
        assert_eq!(instr, Instruction::Nop {});
    }

    #[test]
    fn seek_to_end_makes_stream_done() {
        let buf = assemble(&[Instruction::Halt {}]);
        let mut stream = InstructionStream::new(&buf);
        stream.seek(buf.len()).unwrap();
        assert_eq!(stream.next_instruction(), None);
    }

    #[test]
    fn seek_out_of_bounds_returns_error() {
        let buf = assemble(&[Instruction::Halt {}]);
        let mut stream = InstructionStream::new(&buf);
        assert_eq!(
            stream.seek(buf.len() + 1),
            Err(Error::SeekOutOfBounds {
                target: buf.len() + 1,
                len: buf.len()
            }),
        );
        assert_eq!(stream.pos(), 0);
    }

    #[test]
    fn seek_back_simulates_jump() {
        // Push1 { val: [3] } at offset 0 (2 bytes), Jump2 to label 0 at offset 2 (3 bytes).
        let buf = assemble(&[
            Instruction::Push1 { val: [3] },
            Instruction::Jump2 { label: 0 },
        ]);
        // Treat the Push1 byte (offset 0) as the synthetic TARGET id 0 so
        // the jump label resolves back to it.
        let table = JumpTable::new(vec![0]);
        let mut stream = InstructionStream::with_jump_table(&buf, &table);

        let (_, _, _push) = stream.next_instruction().unwrap().unwrap();
        let (_, _, jump) = stream.next_instruction().unwrap().unwrap();

        // Look up the target from the jump table.
        let target = if let Instruction::Jump2 { label } = jump {
            table.get(label).unwrap()
        } else {
            panic!("expected Jump2");
        };

        stream.seek(target).unwrap();
        let (off, label, instr) = stream.next_instruction().unwrap().unwrap();
        assert_eq!(off, 0);
        assert_eq!(label.as_deref(), Some(".0"));
        assert_eq!(instr, Instruction::Push1 { val: [3] });
    }

    #[test]
    fn unknown_opcode_returns_error_and_advances() {
        let buf = [0x0Du8, Instruction::Halt {}.opcode() as u8];
        let mut stream = InstructionStream::new(&buf);

        assert_eq!(
            stream.next_instruction(),
            Some(Err(Error::UnknownOpcode {
                offset: 0,
                byte: 0x0D,
            })),
        );
        assert_eq!(
            stream.next_instruction(),
            Some(Ok((1, None, Instruction::Halt {}))),
        );
    }

    #[test]
    fn truncated_instruction_returns_error_and_advances() {
        // Push8 (0x18) needs 8 operand bytes -- feeding just the opcode truncates it.
        let buf: Vec<u8> = vec![0x18u8];
        let mut stream = InstructionStream::new(&buf);

        assert_eq!(
            stream.next_instruction(),
            Some(Err(Error::TruncatedInstruction { offset: 0 })),
        );
        assert_eq!(stream.next_instruction(), None);
    }

    #[test]
    fn pos_and_len_are_correct() {
        let buf = assemble(&[Instruction::Nop {}, Instruction::Halt {}]);
        let mut stream = InstructionStream::new(&buf);
        assert_eq!(stream.len(), 2);
        assert_eq!(stream.pos(), 0);
        let _ = stream.next_instruction();
        assert_eq!(stream.pos(), 1);
        let _ = stream.next_instruction();
        assert_eq!(stream.pos(), 2);
    }

    #[test]
    fn register_instruction_decodes_correctly() {
        let buf = assemble(&[Instruction::Load { reg: Register(7) }]);
        let mut stream = InstructionStream::new(&buf);
        let (_, _, instr) = stream.next_instruction().unwrap().unwrap();
        assert_eq!(instr, Instruction::Load { reg: Register(7) });
    }
}
