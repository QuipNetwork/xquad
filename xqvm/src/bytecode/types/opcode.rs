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

use thiserror::Error;

/// Error returned when an unknown byte is decoded as an [`Opcode`].
///
/// # Examples
///
/// ```rust
/// use xqvm::Opcode;
/// use xqvm::bytecode::error::OpcodeDecodeError;
///
/// // 0xFE is an unassigned byte; decoding it yields an error carrying the byte.
/// let err = Opcode::try_from(0xFEu8).unwrap_err();
/// assert_eq!(err, OpcodeDecodeError(0xFE));
/// ```
#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
#[error("unknown opcode byte 0x{0:02X}")]
pub struct DecodeError(pub u8);

// ---------------------------------------------------------------------------
// Macro-generated Opcode enum
// ---------------------------------------------------------------------------

macro_rules! impl_opcode {
    (
        $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $_stack:expr, {$($field:tt)*}) ),*
        $(,)?
    ) => {
        /// A set of XQVM opcodes, encoded as a single `u8` byte.
        ///
        /// The numeric discriminant of each variant matches its wire encoding.
        /// Variants and their documentation are generated from the
        /// [`opcodes!`](crate::opcodes) x-macro table.
        ///
        /// # Examples
        ///
        /// ```rust
        /// use xqvm::Opcode;
        ///
        /// assert_eq!(Opcode::Pop as u8, 0x10);
        /// assert_eq!(Opcode::try_from(0x10u8).unwrap(), Opcode::Pop);
        /// ```
        #[repr(u8)]
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
        pub enum Opcode {
            $(
                #[doc = $doc]
                $variant = $code,
            )*
        }

        impl TryFrom<u8> for Opcode {
            type Error = DecodeError;

            fn try_from(byte: u8) -> Result<Self, Self::Error> {
                match byte {
                    $( $code => Ok(Self::$variant), )*
                    unknown  => Err(DecodeError(unknown)),
                }
            }
        }

        impl Opcode {
            /// Return the uppercase assembly mnemonic for this opcode.
            ///
            /// # Examples
            ///
            /// ```rust
            /// use xqvm::Opcode;
            ///
            /// assert_eq!(Opcode::Energy.mnemonic(), "ENERGY");
            /// assert_eq!(Opcode::Nop.mnemonic(), "NOP");
            /// ```
            pub fn mnemonic(self) -> &'static str {
                match self {
                    $( Self::$variant => $mnem, )*
                }
            }
        }
    };
}

opcodes!(impl_opcode);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    macro_rules! all_opcodes_array {
        (
            $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $_stack:expr, {$($field:tt)*}) ),*
            $(,)?
        ) => {
            [ $( (Opcode::$variant, $code, $mnem) ),* ]
        };
    }

    #[test]
    fn roundtrip_all_opcodes() {
        for (op, code, _) in opcodes!(all_opcodes_array) {
            let decoded = Opcode::try_from(code).expect("known opcode should decode");
            assert_eq!(decoded, op, "roundtrip failed for 0x{code:02X}");
        }
    }

    #[test]
    fn discriminant_matches_code() {
        for (op, code, _) in opcodes!(all_opcodes_array) {
            assert_eq!(op as u8, code, "discriminant mismatch for {op:?}");
        }
    }

    #[test]
    fn mnemonic_matches_table() {
        for (op, _, mnem) in opcodes!(all_opcodes_array) {
            assert_eq!(op.mnemonic(), mnem, "mnemonic mismatch for {op:?}");
        }
    }

    #[test]
    fn opcode_count_is_93() {
        assert_eq!(opcodes!(all_opcodes_array).len(), 93);
    }

    #[test]
    fn unknown_opcode_returns_error() {
        for byte in [
            0x0Du8, 0x19, 0x1D, 0x35, 0x46, 0x49, 0x59, 0x5C, 0x80, 0x81, 0xEF, 0xFE,
        ] {
            assert_eq!(
                Opcode::try_from(byte),
                Err(DecodeError(byte)),
                "byte 0x{byte:02X} should be unknown"
            );
        }
    }

    #[test]
    fn spot_check_discriminants() {
        assert_eq!(Opcode::Target as u8, 0x00);
        assert_eq!(Opcode::Jump1 as u8, 0x01);
        assert_eq!(Opcode::JumpI1 as u8, 0x02);
        assert_eq!(Opcode::Jump2 as u8, 0x03);
        assert_eq!(Opcode::JumpI2 as u8, 0x04);
        assert_eq!(Opcode::Iter as u8, 0x09);
        assert_eq!(Opcode::Pop as u8, 0x10);
        assert_eq!(Opcode::Push1 as u8, 0x11);
        assert_eq!(Opcode::Add as u8, 0x20);
        assert_eq!(Opcode::Not as u8, 0x36);
        assert_eq!(Opcode::Bqmx as u8, 0x40);
        assert_eq!(Opcode::VecPush as u8, 0x50);
        assert_eq!(Opcode::GetLine as u8, 0x60);
        assert_eq!(Opcode::OneHotR as u8, 0x70);
        assert_eq!(Opcode::Energy as u8, 0x7F);
        assert_eq!(Opcode::Nop as u8, 0xF0);
        assert_eq!(Opcode::Halt as u8, 0xFF);
    }
}
