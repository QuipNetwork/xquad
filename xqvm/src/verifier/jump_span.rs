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
use alloc::{collections::BTreeMap, vec::Vec};
#[cfg(feature = "std")]
use std::collections::BTreeMap;

use crate::Program;
use crate::bytecode::{Instruction, InstructionStream};

use super::error::VerifierError;
use super::phase::Phase;
use super::scan::stream_err;

/// Rejects `JUMP`/`JUMPI` instructions that cross a loop-body boundary (W1-011).
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
pub struct JumpSpanPhase;

impl Phase for JumpSpanPhase {
    type Error = VerifierError;

    fn run(&self, program: &Program) -> Result<(), VerifierError> {
        // Pass 1: a single lexical scan assigns every instruction position its
        // body id and records the jumps. `current` is the active body; `stack`
        // holds the enclosing bodies; `next_id` mints a fresh id per loop body.
        let mut body_of: BTreeMap<usize, u32> = BTreeMap::new();
        let mut jumps: Vec<(usize, u16)> = Vec::new();
        let mut current: u32 = 0;
        let mut next_id: u32 = 1;
        let mut stack: Vec<u32> = Vec::new();

        let mut stream = InstructionStream::new(program.code());
        while let Some(item) = stream.next_instruction() {
            let (pos, _label, instr) = item.map_err(|e| stream_err(&e))?;
            match instr {
                Instruction::Range {} | Instruction::Iter { .. } => {
                    // The opener belongs to the enclosing body; the inner body
                    // begins after it.
                    let _ = body_of.insert(pos, current);
                    stack.push(current);
                    current = next_id;
                    next_id = next_id.saturating_add(1);
                }
                Instruction::Next {} => {
                    // The closer belongs to the inner body; control then returns
                    // to the enclosing body. A balanced program (LoopNestingPhase
                    // runs first) always has an enclosing body to pop.
                    let _ = body_of.insert(pos, current);
                    current = stack.pop().unwrap_or(0);
                }
                Instruction::Jump1 { label } | Instruction::JumpI1 { label } => {
                    let _ = body_of.insert(pos, current);
                    jumps.push((pos, u16::from(label)));
                }
                Instruction::Jump2 { label } | Instruction::JumpI2 { label } => {
                    let _ = body_of.insert(pos, current);
                    jumps.push((pos, label));
                }
                _ => {
                    let _ = body_of.insert(pos, current);
                }
            }
        }

        // Pass 2: reject any jump whose target lies in a different body. An
        // undefined target is left to JumpTargetPhase, which runs first.
        for (pos, label) in jumps {
            let Some(target) = program.jump_table().get(label) else {
                continue;
            };
            let jump_body = body_of.get(&pos).copied().unwrap_or(0);
            let target_body = body_of.get(&target).copied().unwrap_or(0);
            if jump_body != target_body {
                return Err(VerifierError::JumpAcrossLoopBoundary { pos, target });
            }
        }

        Ok(())
    }
}
