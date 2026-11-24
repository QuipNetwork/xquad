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

use super::error::CheckerError;
use super::phase::Phase;
use super::scan::stream_err;

/// Rejects `JUMP`/`JUMPI` instructions that cross a loop-body boundary.
///
/// # The rule
///
/// Every program position has a *body id*: the innermost lexical loop body that
/// encloses it, by `RANGE`/`ITER`..`NEXT` pairing. A `RANGE`/`ITER` instruction
/// belongs to the *enclosing* body; its matching `NEXT` belongs to the *inner*
/// body it closes. A jump is rejected when its target's body id differs from the
/// jump's own.
///
/// # Why it is sufficient
///
/// With no cross-body jumps, the runtime loop stack equals the lexical nesting
/// at every pc, because `SkipLoop` resynchronizes by a lexical scan. A jump that
/// stays within one body cannot change the active loop set between source and
/// target, so the stack the VM carries into the target is exactly the one the
/// lexical structure predicts. This deliberately also rejects break/continue
/// style exits, which the toolchain never emits as raw cross-body jumps.
///
/// Reachability is not consulted: a loop with an unreachable `NEXT` is safe and
/// is not rejected here (production assigns exact heights and checks every edge
/// separately).
///
/// # Cost
///
/// Memory is proportional to the number of loop openers, `NEXT`s and jumps, not
/// to the number of instructions: the body id changes only at a loop boundary,
/// so the scan records those transitions and looks up a target's body by binary
/// search. A program with no loops and no jumps allocates nothing here.
///
/// # Errors
///
/// [`CheckerError::JumpAcrossLoopBoundary`] for the first jump whose target
/// lies in a different loop body, and [`CheckerError::UndefinedJumpTarget`]
/// for a jump whose label is missing from the program's jump table. A
/// malformed instruction stream is reported as the stream error.
///
/// # Examples
///
/// ```rust
/// use xqvm::checker::{CheckerError, JumpSpanPhase, Phase};
/// use xqvm::{Instruction, InstructionBuilder};
///
/// // The JUMP sits inside the loop body; its TARGET is after NEXT, outside it.
/// let mut b = InstructionBuilder::new();
/// let out = b.label();
/// b.emit_push(1).emit(Instruction::Range {}).emit_jump(out);
/// b.emit(Instruction::Next {});
/// b.place(out).unwrap().emit(Instruction::Halt {});
/// let program = b.build().unwrap();
///
/// assert!(matches!(
///     JumpSpanPhase.run(&program),
///     Err(CheckerError::JumpAcrossLoopBoundary { .. })
/// ));
/// ```
pub struct JumpSpanPhase;

impl Phase for JumpSpanPhase {
    type Error = CheckerError;

    fn run(&self, program: &Program) -> Result<(), CheckerError> {
        // Pass 1: one lexical scan. `current` is the active body, `stack` holds
        // the enclosing bodies, and `next_id` mints a fresh id per loop body.
        // The body id only changes at a loop boundary, so instead of a body per
        // instruction this keeps `transitions`: `(pos, body)` meaning "every
        // position after `pos` is in `body`" (until the next transition). A
        // `RANGE`/`ITER` at `pos` is itself in the enclosing body and a `NEXT`
        // at `pos` in the inner body it closes, which is exactly "after `pos`".
        // Each jump is recorded with the body it sits in.
        let mut transitions: Vec<(usize, u32)> = Vec::new();
        let mut jumps: Vec<(usize, u16, u32)> = Vec::new();
        let mut current: u32 = 0;
        let mut next_id: u32 = 1;
        let mut stack: Vec<u32> = Vec::new();

        let mut stream = InstructionStream::new(program.code());
        while let Some(item) = stream.next_instruction() {
            let (pos, _label, instr) = item.map_err(|e| stream_err(&e))?;
            match instr {
                Instruction::Range {} | Instruction::Iter { .. } => {
                    stack.push(current);
                    current = next_id;
                    next_id = next_id.saturating_add(1);
                    transitions.push((pos, current));
                }
                Instruction::Next {} => {
                    // A balanced program (LoopNestingPhase runs first) always has
                    // an enclosing body to pop.
                    current = stack.pop().unwrap_or(0);
                    transitions.push((pos, current));
                }
                Instruction::Jump1 { label } | Instruction::JumpI1 { label } => {
                    jumps.push((pos, u16::from(label), current));
                }
                Instruction::Jump2 { label } | Instruction::JumpI2 { label } => {
                    jumps.push((pos, label, current));
                }
                _ => {}
            }
        }

        // Pass 2: reject any jump whose target lies in a different body. The
        // target's body is the one set by the last transition strictly before
        // it (top level, 0, if none). `transitions` is in stream order, so it is
        // sorted by position. A label missing from `program.jump_table()` is
        // rejected here rather than assumed checked: the default pipeline's
        // `CombinedPhase` validates labels against the table it rebuilds from
        // the code, not this one, and this phase can also run on its own.
        for (pos, label, jump_body) in jumps {
            let Some(target) = program.jump_table().get(label) else {
                return Err(CheckerError::UndefinedJumpTarget {
                    offset: pos,
                    label,
                    target_count: program.jump_table().len(),
                });
            };
            let after = transitions.partition_point(|&(at, _)| at < target);
            let target_body = after
                .checked_sub(1)
                .and_then(|last| transitions.get(last))
                .map_or(0, |&(_, body)| body);
            if jump_body != target_body {
                return Err(CheckerError::JumpAcrossLoopBoundary {
                    offset: pos,
                    target,
                });
            }
        }

        Ok(())
    }
}
