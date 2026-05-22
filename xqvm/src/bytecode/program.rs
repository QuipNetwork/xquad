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

//! Complete XQVM program: instruction stream plus a pre-computed jump table.
//!
//! # Wire format (`.xqb`)
//!
//! Every `.xqb` file begins with a 15-byte XQBC header, followed by the raw
//! instruction stream:
//!
//! ```text
//!  Offset  Width  Description
//!  ------  -----  -----------
//!    0..4    4    Magic: b"XQBC"
//!       4    1    Version: 0x01
//!       5    1    input_slots  (count of INPUT instructions, clamped to 255)
//!       6    1    output_slots (count of OUTPUT instructions, clamped to 255)
//!    7..11   4    code_len: u32 big-endian — byte length of instruction stream
//!   11..15   4    crc32: u32 big-endian — CRC-32/ISO-HDLC of instruction stream
//!   15+      *    instruction stream (raw opcode + operand bytes)
//! ```
//!
//! [`Program::encode`] writes this layout; [`Program::decode`] validates magic,
//! version, length, and CRC-32 before accepting the payload.
//!
//! # Examples
//!
//! ```rust
//! use xqvm::Program;
//!
//! let program = Program::new(vec![0xFFu8]); // HALT
//! let bytes = program.encode();
//! let decoded = Program::decode(&bytes).expect("decode");
//! assert_eq!(decoded.code(), &[0xFF]);
//! assert!(decoded.jump_table().is_empty());
//! ```

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

use thiserror::Error;

use super::jump_table::JumpTable;
use super::stream::InstructionStream;
use super::types::Instruction;

// ---------------------------------------------------------------------------
// Header constants
// ---------------------------------------------------------------------------

const MAGIC: &[u8; 4] = b"XQBC";
const FORMAT_VERSION: u8 = 1;
/// Total size of the XQBC binary header in bytes.
pub(crate) const HEADER_SIZE: usize = 15;

// ---------------------------------------------------------------------------
// Program
// ---------------------------------------------------------------------------

/// A complete XQVM program.
///
/// Holds the instruction-stream bytes and a pre-computed [`JumpTable`].
/// [`Self::input_slots`] and [`Self::output_slots`] record the number of
/// `INPUT` / `OUTPUT` instructions respectively -- callers can use them to
/// size VM calldata and output-slot allocation without re-scanning the code.
///
/// The wire format is described in the module-level documentation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Program {
    code: Vec<u8>,
    jump_table: JumpTable,
    input_slots: u8,
    output_slots: u8,
}

impl Program {
    /// Wrap raw instruction bytes in a [`Program`], computing the jump table
    /// and slot counts in a single combined pass over the buffer.
    pub fn new(code: Vec<u8>) -> Self {
        let (jump_table, (input_slots, output_slots), _) = crate::verifier::scan(&code);
        Self {
            code,
            jump_table,
            input_slots,
            output_slots,
        }
    }

    /// Wrap raw instruction bytes with an explicit jump table.
    ///
    /// Useful in tests where the caller has already computed the table.
    /// In normal use [`Self::new`] is preferable.
    pub fn from_parts(code: Vec<u8>, jump_table: JumpTable) -> Self {
        let (input_slots, output_slots) = count_slots(&code);
        Self {
            code,
            jump_table,
            input_slots,
            output_slots,
        }
    }

    /// The pre-computed jump table.
    pub fn jump_table(&self) -> &JumpTable {
        &self.jump_table
    }

    /// The raw instruction bytes.
    pub fn code(&self) -> &[u8] {
        &self.code
    }

    /// Number of `INPUT` instructions (i.e. the calldata arity).
    pub fn input_slots(&self) -> u8 {
        self.input_slots
    }

    /// Number of `OUTPUT` instructions (i.e. the minimum output-slot count).
    pub fn output_slots(&self) -> u8 {
        self.output_slots
    }

    /// Encode the program to its wire format: a 15-byte XQBC header followed
    /// by the raw instruction stream.
    ///
    /// The header contains a magic marker, format version, slot counts,
    /// the instruction-stream byte length, and a CRC-32/ISO-HDLC checksum.
    ///
    /// # Panics
    ///
    /// Panics if the instruction stream exceeds 4 GiB (a `u32` overflow),
    /// which is not a realistic scenario for any XQVM program.
    pub fn encode(&self) -> Vec<u8> {
        let code = self.code.as_slice();
        let code_len = code.len();
        let crc = crc32fast::hash(code);

        let mut out = Vec::with_capacity(HEADER_SIZE + code_len);
        out.extend_from_slice(MAGIC);
        out.push(FORMAT_VERSION);
        out.push(self.input_slots);
        out.push(self.output_slots);
        #[expect(
            clippy::expect_used,
            reason = "a >4 GiB instruction stream is not a realistic scenario"
        )]
        let code_len_u32 = u32::try_from(code_len).expect("code_len fits in u32");
        out.extend_from_slice(&code_len_u32.to_be_bytes());
        out.extend_from_slice(&crc.to_be_bytes());
        out.extend_from_slice(code);
        out
    }

    /// Decode a program from its wire format.
    ///
    /// Validates the XQBC magic bytes, format version, instruction-stream
    /// length, and CRC-32 checksum before accepting the payload.
    ///
    /// # Errors
    ///
    /// Returns `Err(ProgramDecodeError)` if the bytes are not a valid XQBC file.
    pub fn decode(bytes: &[u8]) -> Result<Self, ProgramDecodeError> {
        if bytes.len() < HEADER_SIZE {
            return Err(ProgramDecodeError::TruncatedHeader);
        }

        let magic = bytes.get(..4).ok_or(ProgramDecodeError::TruncatedHeader)?;
        if magic != MAGIC {
            return Err(ProgramDecodeError::BadMagic);
        }

        let version = bytes
            .get(4)
            .copied()
            .ok_or(ProgramDecodeError::TruncatedHeader)?;
        if version != FORMAT_VERSION {
            return Err(ProgramDecodeError::UnsupportedVersion {
                found: version,
                expected: FORMAT_VERSION,
            });
        }

        // bytes 5–6: input_slots and output_slots (informational; not validated here).

        let code_len_bytes: [u8; 4] = bytes
            .get(7..11)
            .ok_or(ProgramDecodeError::TruncatedHeader)?
            .try_into()
            .map_err(|_| ProgramDecodeError::TruncatedHeader)?;
        let code_len = u32::from_be_bytes(code_len_bytes);

        let crc_bytes: [u8; 4] = bytes
            .get(11..15)
            .ok_or(ProgramDecodeError::TruncatedHeader)?
            .try_into()
            .map_err(|_| ProgramDecodeError::TruncatedHeader)?;
        let expected_crc = u32::from_be_bytes(crc_bytes);

        let code = bytes
            .get(HEADER_SIZE..)
            .ok_or(ProgramDecodeError::TruncatedHeader)?;

        if code.len() as u64 != u64::from(code_len) {
            return Err(ProgramDecodeError::LengthMismatch {
                expected: code_len,
                got: code.len(),
            });
        }

        let actual_crc = crc32fast::hash(code);
        if actual_crc != expected_crc {
            return Err(ProgramDecodeError::ChecksumMismatch {
                expected: expected_crc,
                got: actual_crc,
            });
        }

        Ok(Self::new(code.to_vec()))
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Count `INPUT` and `OUTPUT` instructions in a raw instruction stream.
///
/// Both counts are saturated at `u8::MAX` (255) -- no real program should
/// approach that limit.
fn count_slots(code: &[u8]) -> (u8, u8) {
    let mut inputs: u8 = 0;
    let mut outputs: u8 = 0;
    for item in InstructionStream::new(code) {
        let Ok((_offset, _label, instr)) = item else {
            continue;
        };
        match instr {
            Instruction::Input { .. } => inputs = inputs.saturating_add(1),
            Instruction::Output { .. } => outputs = outputs.saturating_add(1),
            _ => {}
        }
    }
    (inputs, outputs)
}

// ---------------------------------------------------------------------------
// ProgramDecodeError
// ---------------------------------------------------------------------------

/// Error returned by [`Program::decode`].
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ProgramDecodeError {
    /// The bytes do not start with `b"XQBC"`.
    #[error("not an XQBC file (wrong magic bytes)")]
    BadMagic,

    /// The format version byte is not `0x01`.
    #[error("unsupported XQBC version {found} (expected {expected})")]
    UnsupportedVersion {
        /// The version byte found in the file.
        found: u8,
        /// The version byte this decoder expects.
        expected: u8,
    },

    /// The byte slice is too short to contain a complete header.
    #[error("XQBC header is truncated")]
    TruncatedHeader,

    /// The header's `code_len` field does not match the actual payload length.
    #[error("instruction stream length mismatch: header says {expected} bytes, got {got}")]
    LengthMismatch {
        /// Byte count declared in the header.
        expected: u32,
        /// Actual byte count of the payload.
        got: usize,
    },

    /// The CRC-32 of the instruction stream does not match the header.
    #[error("CRC-32 mismatch: expected 0x{expected:08X}, computed 0x{got:08X}")]
    ChecksumMismatch {
        /// CRC-32 stored in the header.
        expected: u32,
        /// CRC-32 computed over the payload.
        got: u32,
    },
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::codec;
    use crate::{Instruction, Register};

    fn assemble(instrs: &[Instruction]) -> Vec<u8> {
        instrs.iter().flat_map(codec::encode).collect()
    }

    // -- round-trip ----------------------------------------------------------

    #[test]
    fn encode_decode_round_trips() {
        let prog = Program::new(vec![0xFF]);
        let bytes = prog.encode();
        let decoded = Program::decode(&bytes).expect("decode");
        assert_eq!(decoded.code(), &[0xFF]);
        assert!(decoded.jump_table().is_empty());
    }

    #[test]
    fn encode_has_correct_header_length() {
        let prog = Program::new(vec![0xFF]);
        let bytes = prog.encode();
        // 15-byte header + 1-byte instruction
        assert_eq!(bytes.len(), HEADER_SIZE + 1);
    }

    #[test]
    fn encode_starts_with_magic() {
        let prog = Program::new(vec![0xFF]);
        let bytes = prog.encode();
        assert_eq!(bytes.get(..4), Some(b"XQBC".as_slice()));
    }

    #[test]
    fn encode_version_byte_is_one() {
        let prog = Program::new(vec![0xFF]);
        let bytes = prog.encode();
        assert_eq!(bytes.get(4).copied(), Some(1u8));
    }

    #[test]
    fn empty_program_encodes_to_header_only() {
        let prog = Program::new(vec![]);
        let bytes = prog.encode();
        assert_eq!(bytes.len(), HEADER_SIZE);
        let decoded = Program::decode(&bytes).expect("decode");
        assert!(decoded.code().is_empty());
        assert!(decoded.jump_table().is_empty());
    }

    #[test]
    fn jump_table_is_built_from_targets_in_code() {
        let buf = assemble(&[
            Instruction::Target {},
            Instruction::Nop {},
            Instruction::Target {},
            Instruction::Halt {},
        ]);
        let prog = Program::new(buf);
        let table = prog.jump_table();
        assert_eq!(table.len(), 2);
        assert_eq!(table.get(0), Some(0));
        assert_eq!(table.get(1), Some(2));
    }

    // -- slot counts ---------------------------------------------------------

    #[test]
    fn input_slots_counts_input_instructions() {
        let buf = assemble(&[
            Instruction::Input { reg: Register(0) },
            Instruction::Input { reg: Register(1) },
            Instruction::Halt {},
        ]);
        let prog = Program::new(buf);
        assert_eq!(prog.input_slots(), 2);
        assert_eq!(prog.output_slots(), 0);
    }

    #[test]
    fn output_slots_counts_output_instructions() {
        let buf = assemble(&[
            Instruction::Output { reg: Register(0) },
            Instruction::Halt {},
        ]);
        let prog = Program::new(buf);
        assert_eq!(prog.input_slots(), 0);
        assert_eq!(prog.output_slots(), 1);
    }

    #[test]
    fn slot_counts_survive_round_trip() {
        let buf = assemble(&[
            Instruction::Input { reg: Register(0) },
            Instruction::Output { reg: Register(0) },
            Instruction::Halt {},
        ]);
        let prog = Program::new(buf);
        let decoded = Program::decode(&prog.encode()).expect("decode");
        assert_eq!(decoded.input_slots(), 1);
        assert_eq!(decoded.output_slots(), 1);
    }

    #[test]
    fn slot_counts_in_header_bytes() {
        let buf = assemble(&[
            Instruction::Input { reg: Register(0) },
            Instruction::Input { reg: Register(1) },
            Instruction::Output { reg: Register(0) },
            Instruction::Halt {},
        ]);
        let bytes = Program::new(buf).encode();
        // byte 5 = input_slots, byte 6 = output_slots
        assert_eq!(bytes.get(5).copied(), Some(2u8));
        assert_eq!(bytes.get(6).copied(), Some(1u8));
    }

    // -- decode error paths --------------------------------------------------

    #[test]
    fn decode_empty_slice_is_truncated_header() {
        assert_eq!(
            Program::decode(&[]),
            Err(ProgramDecodeError::TruncatedHeader)
        );
    }

    #[test]
    fn decode_short_slice_is_truncated_header() {
        assert_eq!(
            Program::decode(&[0; 14]),
            Err(ProgramDecodeError::TruncatedHeader)
        );
    }

    #[test]
    fn decode_wrong_magic_is_bad_magic() {
        let mut bytes = Program::new(vec![0xFF]).encode();
        bytes[0] = b'X';
        bytes[1] = b'Q';
        bytes[2] = b'V';
        bytes[3] = b'M';
        assert_eq!(Program::decode(&bytes), Err(ProgramDecodeError::BadMagic));
    }

    #[test]
    fn decode_wrong_version_is_unsupported_version() {
        let mut bytes = Program::new(vec![0xFF]).encode();
        bytes[4] = 42;
        assert_eq!(
            Program::decode(&bytes),
            Err(ProgramDecodeError::UnsupportedVersion {
                found: 42,
                expected: 1
            })
        );
    }

    #[test]
    fn decode_truncated_payload_is_length_mismatch() {
        let prog = Program::new(vec![0xFF, 0xF0, 0xFF]);
        let mut bytes = prog.encode();
        let _ = bytes.pop(); // remove last byte
        assert!(matches!(
            Program::decode(&bytes),
            Err(ProgramDecodeError::LengthMismatch { .. })
        ));
    }

    #[test]
    fn decode_crc_mismatch_is_checksum_error() {
        let prog = Program::new(vec![0xFF]);
        let mut bytes = prog.encode();
        // Corrupt the last byte of the instruction stream.
        let last = bytes.len() - 1;
        let b = bytes.get_mut(last).expect("last byte exists");
        *b ^= 0xFF;
        assert!(matches!(
            Program::decode(&bytes),
            Err(ProgramDecodeError::ChecksumMismatch { .. })
        ));
    }
}
