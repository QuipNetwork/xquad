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

//! Label-free, seekable reader over raw instruction bytes.
//!
//! [`Cursor`] decodes exactly as [`InstructionStream`] does -- same offsets,
//! same errors, the same one-byte advance past a bad instruction -- but
//! carries no label map and yields no label. The VM's run loop and the
//! verifier's scan read through it because neither uses labels, and walking
//! through the stream instead costs about 40% more per executed step and
//! about twice as much per decoded byte (measured on QUI-1511 and QUI-1057).
//!
//! [`InstructionStream`]: super::InstructionStream

use super::codec;
use super::error::StreamError;
use super::stream::map_decode_error;
use super::types::Instruction;

/// Seekable reader yielding `(offset, instruction)` pairs from raw bytes.
#[derive(Debug, Clone)]
pub(crate) struct Cursor<'a> {
    code: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    /// A cursor at the start of `code`.
    pub(crate) fn new(code: &'a [u8]) -> Self {
        Self { code, pos: 0 }
    }

    /// Byte offset of the next instruction to decode.
    pub(crate) fn pos(&self) -> usize {
        self.pos
    }

    /// Move to absolute byte offset `pos`, anywhere in `[0, len]`.
    ///
    /// # Errors
    ///
    /// Returns [`StreamError::SeekOutOfBounds`] if `pos` is past the end.
    pub(crate) fn seek(&mut self, pos: usize) -> Result<(), StreamError> {
        if pos > self.code.len() {
            return Err(StreamError::SeekOutOfBounds {
                target: pos,
                len: self.code.len(),
            });
        }
        self.pos = pos;
        Ok(())
    }

    /// Decode the instruction at the cursor and advance past it.
    ///
    /// Returns `None` at the end of the bytes. On a decode error the cursor
    /// advances one byte, so repeated calls always make progress.
    #[inline]
    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`offset` is in bounds (`rest.first()` is `Some`), a decoded instruction is at most nine bytes (`spec/xqvm/ENCODING.md`) and never runs past `code.len()`, and a failed decode advances one byte, so neither advance can overflow usize"
    )]
    pub(crate) fn next_instruction(&mut self) -> Option<Result<(usize, Instruction), StreamError>> {
        let offset = self.pos;
        let rest = self.code.get(offset..)?;
        let &byte = rest.first()?;
        match codec::decode(rest) {
            Ok((instr, consumed)) => {
                self.pos = offset + consumed;
                Some(Ok((offset, instr)))
            }
            Err(e) => {
                self.pos = offset + 1;
                Some(Err(map_decode_error(e, offset, byte)))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::InstructionStream;

    /// One xorshift64 step: deterministic and dependency-free.
    fn next_rand(state: &mut u64) -> u64 {
        *state ^= *state << 13;
        *state ^= *state >> 7;
        *state ^= *state << 17;
        *state
    }

    #[test]
    fn matches_the_stream_on_random_bytes() {
        let mut state: u64 = 0x2545_F491_4F6C_DD1D;
        for _ in 0..5_000 {
            let len = next_rand(&mut state) % 48;
            let code: Vec<u8> = (0..len)
                .map(|_| {
                    let [byte, ..] = next_rand(&mut state).to_le_bytes();
                    byte
                })
                .collect();
            let mut cursor = Cursor::new(&code);
            let mut stream = InstructionStream::new(&code);
            loop {
                let got = cursor.next_instruction();
                let want = stream
                    .next_instruction()
                    .map(|item| item.map(|(pos, _label, instr)| (pos, instr)));
                assert_eq!(got, want, "{code:02X?}");
                assert_eq!(cursor.pos(), stream.pos(), "{code:02X?}");
                if got.is_none() {
                    break;
                }
            }
        }
    }

    #[test]
    fn seeks_like_the_stream() {
        let code = [0xF0, 0xF0, 0xFF];
        let mut cursor = Cursor::new(&code);
        let mut stream = InstructionStream::new(&code);
        for target in 0..=code.len() + 1 {
            assert_eq!(cursor.seek(target), stream.seek(target), "seek {target}");
            assert_eq!(cursor.pos(), stream.pos(), "seek {target}");
        }
        assert_eq!(cursor.seek(2), Ok(()));
        assert_eq!(
            cursor.next_instruction(),
            Some(Ok((2, Instruction::Halt {})))
        );
        assert_eq!(cursor.next_instruction(), None);
    }
}
