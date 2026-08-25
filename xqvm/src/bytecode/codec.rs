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

//! Binary codec for XQVM instructions.
//!
//! [`Instruction`] is encoded as a fixed-width big-endian sequence
//! `[opcode: u8, operands...]`. Each operand field is written at its natural
//! width in big-endian byte order:
//!
//! | Field type | Bytes |
//! |---|---|
//! | `u8` / [`Register`] | 1 |
//! | `u16` | 2 |
//! | `i16` | 2 |
//! | `i64` | 8 |
//! | `[u8; N]` | `N` (raw bytes, no length prefix) |
//!
//! There are no varints, no length prefixes, and no out-of-band framing -- the
//! opcode byte alone determines how many operand bytes follow. The codec is
//! generated from the [`crate::opcodes`] X-macro by an in-module declarative
//! macro, so adding a new opcode automatically extends both directions of the
//! codec with no further code changes.
//!
//! Prior to QUI-411 the codec went through `serde` + the `oxicode` crate; this
//! file now talks bytes directly. The `serde` dependency is dropped from
//! `crates/bytecode` entirely.
//!
//! # Examples
//!
//! ```rust
//! use xqvm::{Instruction, Register};
//! use xqvm::codec;
//!
//! let instr = Instruction::Pop {};
//! let bytes = codec::encode(&instr);
//! assert_eq!(bytes, [0x10]);
//!
//! let with_imm = Instruction::Push1 { val: [42] };
//! assert_eq!(codec::encode(&with_imm), [0x11, 42]);
//!
//! let (decoded, consumed) = codec::decode(&[0x11, 42]).unwrap();
//! assert_eq!(decoded, with_imm);
//! assert_eq!(consumed, 2);
//! ```

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

use thiserror::Error;

use super::types::{Instruction, Register};

// ---------------------------------------------------------------------------
// Decode error
// ---------------------------------------------------------------------------

/// Errors returned by [`decode`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
pub enum DecodeError {
    /// The input is empty.
    #[error("instruction stream truncated: empty input")]
    EmptyInput,

    /// The opcode byte does not match any known XQVM opcode.
    #[error("unknown XQVM opcode 0x{byte:02X}")]
    UnknownOpcode {
        /// The unrecognised byte.
        byte: u8,
    },

    /// The buffer ended before all operand bytes for the matched opcode could
    /// be read.
    #[error(
        "instruction stream truncated: opcode 0x{opcode:02X} needs {needed} more byte(s), got {available}"
    )]
    TruncatedOperand {
        /// The opcode byte whose operands could not be fully read.
        opcode: u8,
        /// Number of additional bytes the operand needed.
        needed: usize,
        /// Number of bytes that were actually available after the opcode.
        available: usize,
    },
}

// ---------------------------------------------------------------------------
// EncodeOperand / DecodeOperand
// ---------------------------------------------------------------------------

/// Append an operand field's wire bytes to `buf`.
///
/// Used by the [`impl_codec!`] macro to assemble instruction payloads. Each
/// operand type writes itself in big-endian order at its natural width.
trait EncodeOperand {
    /// Append the wire bytes for `self` to `buf`.
    fn encode_into(&self, buf: &mut Vec<u8>);
}

/// Read an operand field's wire bytes from the start of `bytes`.
///
/// Returns the decoded value and the number of bytes consumed. Used by
/// [`impl_codec!`] to dispatch on each variant's operand list.
trait DecodeOperand: Sized {
    /// Decode an operand of this type from the start of `bytes`.
    ///
    /// `opcode` identifies the instruction being decoded and is included in
    /// truncation errors so callers can surface a precise diagnostic.
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError>;
}

impl EncodeOperand for u8 {
    fn encode_into(&self, buf: &mut Vec<u8>) {
        buf.push(*self);
    }
}

impl DecodeOperand for u8 {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        bytes
            .first()
            .copied()
            .map(|v| (v, 1))
            .ok_or(DecodeError::TruncatedOperand {
                opcode,
                needed: 1,
                available: bytes.len(),
            })
    }
}

impl EncodeOperand for u16 {
    fn encode_into(&self, buf: &mut Vec<u8>) {
        buf.extend_from_slice(&self.to_be_bytes());
    }
}

impl DecodeOperand for u16 {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        let slice = bytes.get(..2).ok_or(DecodeError::TruncatedOperand {
            opcode,
            needed: 2,
            available: bytes.len(),
        })?;
        // SAFETY: get(..2) returned Some, so the slice has length 2.
        let arr: [u8; 2] = slice
            .try_into()
            .unwrap_or_else(|_| unreachable!("slice length 2 always converts to [u8; 2]"));
        Ok((Self::from_be_bytes(arr), 2))
    }
}

impl EncodeOperand for i16 {
    fn encode_into(&self, buf: &mut Vec<u8>) {
        buf.extend_from_slice(&self.to_be_bytes());
    }
}

impl DecodeOperand for i16 {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        let slice = bytes.get(..2).ok_or(DecodeError::TruncatedOperand {
            opcode,
            needed: 2,
            available: bytes.len(),
        })?;
        let arr: [u8; 2] = slice
            .try_into()
            .unwrap_or_else(|_| unreachable!("slice length 2 always converts to [u8; 2]"));
        Ok((Self::from_be_bytes(arr), 2))
    }
}

impl EncodeOperand for i64 {
    fn encode_into(&self, buf: &mut Vec<u8>) {
        buf.extend_from_slice(&self.to_be_bytes());
    }
}

impl DecodeOperand for i64 {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        let slice = bytes.get(..8).ok_or(DecodeError::TruncatedOperand {
            opcode,
            needed: 8,
            available: bytes.len(),
        })?;
        let arr: [u8; 8] = slice
            .try_into()
            .unwrap_or_else(|_| unreachable!("slice length 8 always converts to [u8; 8]"));
        Ok((Self::from_be_bytes(arr), 8))
    }
}

impl EncodeOperand for Register {
    fn encode_into(&self, buf: &mut Vec<u8>) {
        buf.push(self.0);
    }
}

impl DecodeOperand for Register {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        let (slot, n) = u8::decode_from(bytes, opcode)?;
        Ok((Self(slot), n))
    }
}

impl<const N: usize> EncodeOperand for [u8; N] {
    fn encode_into(&self, buf: &mut Vec<u8>) {
        buf.extend_from_slice(self);
    }
}

impl<const N: usize> DecodeOperand for [u8; N] {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        let slice = bytes.get(..N).ok_or(DecodeError::TruncatedOperand {
            opcode,
            needed: N,
            available: bytes.len(),
        })?;
        let arr: Self = slice
            .try_into()
            .unwrap_or_else(|_| unreachable!("slice length N always converts to [u8; N]"));
        Ok((arr, N))
    }
}

// ---------------------------------------------------------------------------
// Macro-generated encode / decode
// ---------------------------------------------------------------------------

/// Generate the public `encode` and `decode` functions from the X-macro
/// opcode table. Each variant's operand list expands to a sequence of
/// [`EncodeOperand`] / [`DecodeOperand`] calls in declaration order.
macro_rules! impl_codec {
    ( $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
          $_delta:expr, {$($fname:ident: $ftype:ty),*}) ),* $(,)? ) => {

        /// Encode a single instruction to a `Vec<u8>` in the wire format
        /// `[opcode: u8][operand fields]`.
        ///
        /// Integer operands are encoded big-endian at their natural width,
        /// `Register` operands are a single raw byte, and `[u8; N]` operands
        /// are written as `N` consecutive bytes with no length prefix.
        ///
        /// # Examples
        ///
        /// ```rust
        /// use xqvm::Instruction;
        /// use xqvm::codec;
        ///
        /// assert_eq!(codec::encode(&Instruction::Halt {}), [0xFF]);
        /// assert_eq!(codec::encode(&Instruction::Nop {}),  [0xF0]);
        /// assert_eq!(codec::encode(&Instruction::Pop {}),  [0x10]);
        /// assert_eq!(codec::encode(&Instruction::Push1 { val: [42] }), [0x11, 42]);
        /// ```
        pub fn encode(instr: &Instruction) -> Vec<u8> {
            let mut buf = Vec::new();
            match instr {
                $(
                    Instruction::$variant { $($fname,)* } => {
                        buf.push($code as u8);
                        $( EncodeOperand::encode_into($fname, &mut buf); )*
                    }
                )*
            }
            buf
        }

        /// Decode a single instruction from the start of `bytes`.
        ///
        /// Returns `(instruction, bytes_consumed)` on success.
        ///
        /// # Errors
        ///
        /// Returns [`DecodeError`] when the opcode byte is missing,
        /// unknown, or when the buffer ends before the matched opcode's
        /// operands have been fully read.
        ///
        /// # Examples
        ///
        /// ```rust
        /// use xqvm::{Instruction, Register};
        /// use xqvm::codec;
        ///
        /// // LOAD r3 -> [0x0A, 0x03]
        /// let bytes: &[u8] = &[0x0A, 0x03];
        /// let (instr, n) = codec::decode(bytes).unwrap();
        /// assert_eq!(instr, Instruction::Load { reg: Register(3) });
        /// assert_eq!(n, 2);
        /// ```
        #[expect(
            clippy::arithmetic_side_effects,
            reason = "`pos` accumulates one instruction's operand widths, at most eight bytes by `spec/xqvm/ENCODING.md`'s instruction-length table"
        )]
        pub fn decode(bytes: &[u8]) -> Result<(Instruction, usize), DecodeError> {
            let opcode = *bytes.first().ok_or(DecodeError::EmptyInput)?;
            let payload = bytes.get(1..).unwrap_or(&[]);
            let mut pos = 0usize;
            match opcode {
                $(
                    $code => {
                        $(
                            let ($fname, _n) = <$ftype as DecodeOperand>::decode_from(
                                payload.get(pos..).unwrap_or(&[]),
                                opcode,
                            )?;
                            pos += _n;
                        )*
                        Ok((Instruction::$variant { $($fname,)* }, pos + 1))
                    }
                )*
                unknown => Err(DecodeError::UnknownOpcode { byte: unknown }),
            }
        }
    };
}

opcodes!(impl_codec);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::types::Register;

    macro_rules! all_instructions {
        ( $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
              $_delta:expr, {$($fname:ident: $ftype:ty),*}) ),* $(,)? ) => {
            [ $( Instruction::$variant { $($fname: <$ftype as Default>::default(),)* } ),* ]
        };
    }

    #[test]
    fn encode_decode_roundtrip_all_87() {
        for instr in opcodes!(all_instructions) {
            let bytes = encode(&instr);
            let (decoded, consumed) = decode(&bytes).expect("decode failed");
            assert_eq!(decoded, instr, "roundtrip mismatch for {instr:?}");
            assert_eq!(
                consumed,
                bytes.len(),
                "consumed != encoded length for {instr:?}"
            );
        }
    }

    #[test]
    fn halt_is_one_byte() {
        assert_eq!(encode(&Instruction::Halt {}), [0xFF]);
    }

    #[test]
    fn nop_is_one_byte() {
        assert_eq!(encode(&Instruction::Nop {}), [0xF0]);
    }

    #[test]
    fn pop_is_one_byte() {
        assert_eq!(encode(&Instruction::Pop {}), [0x10]);
    }

    #[test]
    fn push1_is_two_bytes() {
        assert_eq!(encode(&Instruction::Push1 { val: [42] }), [0x11, 42]);
    }

    #[test]
    fn push2_is_three_bytes() {
        assert_eq!(
            encode(&Instruction::Push2 { val: [0x00, 0x80] }),
            [0x12, 0x00, 0x80]
        );
    }

    #[test]
    fn push3_is_four_bytes() {
        assert_eq!(
            encode(&Instruction::Push3 {
                val: [0x01, 0x02, 0x03]
            }),
            [0x13, 0x01, 0x02, 0x03]
        );
    }

    #[test]
    fn push8_is_nine_bytes() {
        let v = 1_000_000_000_000i64.to_be_bytes();
        let mut expected = [0u8; 9];
        expected[0] = 0x18;
        expected[1..].copy_from_slice(&v);
        assert_eq!(encode(&Instruction::Push8 { val: v }), expected);
    }

    #[test]
    fn jump2_label_fixint_be() {
        // Jump2 (wide form) at byte 0x03; u16(5) in BE = 0x0005
        let bytes = encode(&Instruction::Jump2 { label: 5u16 });
        assert_eq!(bytes, [0x03, 0x00, 0x05]);
    }

    #[test]
    fn jump1_label_is_two_bytes() {
        // Jump1 (narrow form) at byte 0x01; u8 label is one byte
        let bytes = encode(&Instruction::Jump1 { label: 7u8 });
        assert_eq!(bytes, [0x01, 0x07]);
    }

    #[test]
    fn jumpi1_label_is_two_bytes() {
        let bytes = encode(&Instruction::JumpI1 { label: 0u8 });
        assert_eq!(bytes, [0x02, 0x00]);
    }

    #[test]
    fn energy_two_register_bytes() {
        // Register serializes as raw u8.
        let bytes = encode(&Instruction::Energy {
            model: Register(2),
            sample: Register(3),
        });
        assert_eq!(bytes, [0x7F, 2, 3]);
    }

    #[test]
    fn unknown_opcode_returns_error() {
        // 0x0D is a reserved gap (not assigned).
        assert!(matches!(
            decode(&[0x0Du8]),
            Err(DecodeError::UnknownOpcode { byte: 0x0D })
        ));
    }

    #[test]
    fn empty_input_returns_error() {
        assert!(matches!(decode(&[]), Err(DecodeError::EmptyInput)));
    }

    #[test]
    fn truncated_operand_returns_error() {
        // Push8 opcode (0x18) needs 8 operand bytes; supply only the opcode.
        assert!(matches!(
            decode(&[0x18u8]),
            Err(DecodeError::TruncatedOperand {
                opcode: 0x18,
                needed: 8,
                available: 0,
            })
        ));
        // LOAD opcode needs one register byte.
        assert!(matches!(
            decode(&[0x0Au8]),
            Err(DecodeError::TruncatedOperand {
                opcode: 0x0A,
                needed: 1,
                available: 0,
            })
        ));
        // JUMP2 needs two label bytes.
        assert!(matches!(
            decode(&[0x03u8, 0x00]),
            Err(DecodeError::TruncatedOperand {
                opcode: 0x03,
                needed: 2,
                available: 1,
            })
        ));
    }

    #[test]
    fn encode_sequence_then_decode_each() {
        let program = [
            Instruction::Push1 { val: [5] },
            Instruction::Push1 { val: [3] },
            Instruction::Add {},
            Instruction::Halt {},
        ];
        let mut buf = Vec::new();
        for instr in &program {
            buf.extend_from_slice(&encode(instr));
        }
        let mut pos = 0usize;
        for expected in &program {
            let (got, n) = decode(&buf[pos..]).unwrap();
            assert_eq!(got, *expected);
            pos += n;
        }
        assert_eq!(pos, buf.len());
    }
}
