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

use crate::bytecode::error::StreamError;
use crate::bytecode::stream::map_decode_error;
use crate::bytecode::{Instruction, JumpTable, codec};

use super::error::VerifierError;

/// Convert a [`StreamError`] into a [`VerifierError`].
///
/// `SeekOutOfBounds` cannot be triggered by sequential stream iteration,
/// so it is mapped defensively to `TruncatedInstruction`.
pub(super) fn stream_err(e: &StreamError) -> VerifierError {
    match e {
        StreamError::TruncatedInstruction { offset } => {
            VerifierError::TruncatedInstruction { offset: *offset }
        }
        StreamError::UnknownOpcode { offset, byte } => VerifierError::BadOpcode {
            offset: *offset,
            byte: *byte,
        },
        StreamError::SeekOutOfBounds { target, .. } => {
            VerifierError::TruncatedInstruction { offset: *target }
        }
    }
}

/// Build a [`JumpTable`], count slot instructions, and detect the first Phase 1
/// violation in one pass.
///
/// Walks the instruction bytes exactly once with [`codec::decode`], collecting:
/// - byte positions of `TARGET` opcodes (for the jump table),
/// - counts of `INPUT` and `OUTPUT` instructions (clamped to `u8::MAX`),
/// - loop-opener positions (to check nesting balance),
/// - jump-instruction labels (for deferred target-count validation).
///
/// Structural errors (`BadOpcode`, `TruncatedInstruction`) abort the walk
/// immediately. Loop and jump-target errors are recorded after the walk.
///
/// The walk calls the codec directly rather than iterating an
/// [`InstructionStream`](crate::InstructionStream): it needs no labels, and
/// walking through the stream doubled the cost of [`crate::Program::new`]
/// (measured on QUI-1057). Every [`crate::Program::decode`] pays for this
/// walk, so the difference is paid on every run an embedder decodes for.
///
/// Returns `(jump_table, (input_slots, output_slots), first_error)`.
#[expect(
    clippy::arithmetic_side_effects,
    reason = "`consumed` is the length of one instruction decoded from `code[pos..]`, at most nine bytes (`spec/xqvm/ENCODING.md`), so `pos + consumed <= code.len()` and the cursor cannot overflow usize"
)]
pub(crate) fn scan(code: &[u8]) -> (JumpTable, (u8, u8), Option<VerifierError>) {
    let mut targets: Vec<usize> = Vec::new();
    let mut loop_offsets: Vec<usize> = Vec::new();
    let mut jump_refs: Vec<(usize, u16)> = Vec::new();
    let mut first_error: Option<VerifierError> = None;
    let mut input_slots: u8 = 0;
    let mut output_slots: u8 = 0;

    let mut pos: usize = 0;
    while let Some(rest) = code.get(pos..)
        && let Some(&byte) = rest.first()
    {
        let (instr, consumed) = match codec::decode(rest) {
            Ok(v) => v,
            Err(e) => {
                let _ =
                    first_error.get_or_insert_with(|| stream_err(&map_decode_error(e, pos, byte)));
                break;
            }
        };

        match instr {
            Instruction::Target {} => targets.push(pos),
            Instruction::Input { .. } => input_slots = input_slots.saturating_add(1),
            Instruction::Output { .. } => output_slots = output_slots.saturating_add(1),
            Instruction::Range {} | Instruction::Iter { .. } => loop_offsets.push(pos),
            Instruction::Next {} if first_error.is_none() && loop_offsets.pop().is_none() => {
                first_error = Some(VerifierError::NoActiveLoop { offset: pos });
            }
            Instruction::Lidx { .. } | Instruction::LVal { .. } if loop_offsets.is_empty() => {
                let _ = first_error.get_or_insert(VerifierError::NoActiveLoop { offset: pos });
            }
            Instruction::Jump1 { label } | Instruction::JumpI1 { label } => {
                jump_refs.push((pos, u16::from(label)));
            }
            Instruction::Jump2 { label } | Instruction::JumpI2 { label } => {
                jump_refs.push((pos, label));
            }
            _ => {}
        }
        pos += consumed;
    }

    // Unmatched loop openers: report the outermost (first-pushed) one.
    if first_error.is_none()
        && let Some(&outermost) = loop_offsets.first()
    {
        first_error = Some(VerifierError::UnmatchedLoop {
            offset: outermost,
            depth: loop_offsets.len(),
        });
    }

    let jump_table = JumpTable::new(targets);

    // Deferred jump-target check: needs the final TARGET count.
    if first_error.is_none() {
        let target_count = jump_table.len();
        for (offset, label) in jump_refs {
            if usize::from(label) >= target_count {
                first_error = Some(VerifierError::UndefinedJumpTarget {
                    offset,
                    label,
                    target_count,
                });
                break;
            }
        }
    }

    (jump_table, (input_slots, output_slots), first_error)
}
