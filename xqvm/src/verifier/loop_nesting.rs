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

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

use crate::Program;
use crate::bytecode::{Instruction, InstructionStream};

use super::error::VerifierError;
use super::phase::Phase;
use super::scan::stream_err;

/// Checks that `RANGE`/`ITER` and `NEXT` are properly balanced, and that
/// `LVAL`/`LIDX` are only used inside an active loop.
///
/// `loop_offsets` tracks the byte position of each open loop opener in
/// nesting order. Using a stack (rather than a counter) lets the error
/// report the outermost unmatched opener's position.
pub struct LoopNestingPhase;

impl Phase for LoopNestingPhase {
    type Error = VerifierError;

    fn run(&self, program: &Program) -> Result<(), VerifierError> {
        let mut loop_offsets: Vec<usize> = Vec::new();
        let mut stream = InstructionStream::new(program.code());

        while let Some(item) = stream.next_instruction() {
            let (pos, _label, instr) = item.map_err(|e| stream_err(&e))?;
            match instr {
                Instruction::Range {} | Instruction::Iter { .. } => {
                    loop_offsets.push(pos);
                }
                Instruction::Next {} => {
                    // `ok_or` converts None (empty stack) into a NoActiveLoop error.
                    let _ = loop_offsets
                        .pop()
                        .ok_or(VerifierError::NoActiveLoop { offset: pos })?;
                }
                Instruction::Lidx { .. } | Instruction::LVal { .. } if loop_offsets.is_empty() => {
                    return Err(VerifierError::NoActiveLoop { offset: pos });
                }
                _ => {}
            }
        }

        // Report the outermost (first-pushed) unmatched loop opener, if any.
        match loop_offsets.first().copied() {
            Some(outermost) => Err(VerifierError::UnmatchedLoop {
                offset: outermost,
                depth: loop_offsets.len(),
            }),
            None => Ok(()),
        }
    }
}
