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

use crate::Program;
use crate::bytecode::{Instruction, InstructionStream};

use super::error::VerifierError;
use super::phase::Phase;
use super::scan::stream_err;

/// Checks that every `JUMP*`/`JUMPI*` instruction references a label id that
/// exists in the program's jump table.
///
/// The jump table is pre-computed at [`Program::new`](crate::Program::new)
/// time, so each label lookup is O(1).
pub struct JumpTargetPhase;

impl Phase for JumpTargetPhase {
    type Error = VerifierError;

    fn run(&self, program: &Program) -> Result<(), VerifierError> {
        let target_count = program.jump_table().len();
        let mut stream = InstructionStream::new(program.code());

        while let Some(item) = stream.next_instruction() {
            let (pos, _label, instr) = item.map_err(|e| stream_err(&e))?;
            match instr {
                Instruction::Jump1 { label } | Instruction::JumpI1 { label }
                    if usize::from(label) >= target_count =>
                {
                    return Err(VerifierError::UndefinedJumpTarget {
                        offset: pos,
                        label: u16::from(label),
                        target_count,
                    });
                }
                Instruction::Jump2 { label } | Instruction::JumpI2 { label }
                    if usize::from(label) >= target_count =>
                {
                    return Err(VerifierError::UndefinedJumpTarget {
                        offset: pos,
                        label,
                        target_count,
                    });
                }
                _ => {}
            }
        }
        Ok(())
    }
}
