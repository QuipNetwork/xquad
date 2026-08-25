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

//! CFG-based forward must-init analysis for XQVM bytecode.
//!
//! The main entry point is [`check_uninit_registers`], which catches reads of
//! registers that are not definitely initialized on all paths to the read site.
//!
//! This complements [`crate::dataflow::register`]: the type-state pass uses a
//! permissive `Any`-meet so that registers written on only one branch of a
//! conditional do not produce false positives at the join.  As a consequence,
//! it misses the case where a register is genuinely uninitialized on the taken
//! path.  This pass uses AND-meet instead, so a register must be written on
//! **every** predecessor path to be considered initialized.
//!
//! # Lattice
//!
//! The 256 registers are tracked as a 256-bit bitmap packed into four `u64`
//! words (`[u64; 4]`).  Register slot `r` maps to word `r / 64`, bit `r & 63`.
//!
//! | Bit state | Meaning |
//! |---|---|
//! | `1` | Definitely initialized on all paths to this point |
//! | `0` | Possibly uninitialized on at least one path |
//!
//! `top()` = `[u64::MAX; 4]` (all bits set -- identity for AND-meet).  The
//! boundary value (program entry) = `[0; 4]` (all registers unwritten).
//!
//! At join points: `meet(a, b) = a & b` (bitwise AND), so a register written
//! on only one incoming edge is conservatively treated as uninitialized.

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

#[cfg(feature = "std")]
use std::collections::HashMap;

#[cfg(not(feature = "std"))]
use hashbrown::HashMap;

use crate::Program;
use crate::VerifierError;
use crate::bytecode::InstructionStream;
use crate::dataflow::analysis::Analysis;
use crate::dataflow::bytecode::{BlockId, CfgContext, build_cfg};
use crate::dataflow::direction::Forward;
use crate::dataflow::lattice::Lattice;
use crate::dataflow::solver::solve;
use crate::verifier::reg_type::{RegType, check_reads, write_effect};

// ---------------------------------------------------------------------------
// InitValue lattice
// ---------------------------------------------------------------------------

/// Per-register initialization bitmap at a program point.
///
/// 256 registers are packed into four `u64` words.  Register slot `r` lives
/// in word `r / 64`, bit `r & 63`.  A set bit means the register is
/// definitely initialized on all incoming paths; a cleared bit means it may
/// be uninitialized on at least one path.
///
/// `top()` = `[u64::MAX; 4]` (all bits set -- identity for AND-meet).
/// Boundary value (program entry) = `[0; 4]` (all registers unwritten).
///
/// At join points: `meet(a, b) = a & b` (bitwise AND per word).
#[derive(Clone, PartialEq, Eq)]
pub struct InitValue([u64; 4]);

impl InitValue {
    /// Whether register `slot` is definitely initialized.
    pub(crate) fn is_init(&self, slot: u8) -> bool {
        let word = usize::from(slot) / 64;
        let bit = u32::from(slot & 63);
        self.0.get(word).is_some_and(|&w| (w >> bit) & 1 == 1)
    }

    /// Mark register `slot` as definitely initialized.
    pub(crate) fn set_init(&mut self, slot: u8) {
        let word = usize::from(slot) / 64;
        let bit = u32::from(slot & 63);
        if let Some(w) = self.0.get_mut(word) {
            *w |= 1u64 << bit;
        }
    }

    /// Mark register `slot` as possibly uninitialized.
    pub(crate) fn clear_init(&mut self, slot: u8) {
        let word = usize::from(slot) / 64;
        let bit = u32::from(slot & 63);
        if let Some(w) = self.0.get_mut(word) {
            *w &= !(1u64 << bit);
        }
    }
}

impl Lattice for InitValue {
    fn top() -> Self {
        Self([u64::MAX; 4])
    }

    fn meet(&self, other: &Self) -> Self {
        let [a0, a1, a2, a3] = self.0;
        let [b0, b1, b2, b3] = other.0;
        Self([a0 & b0, a1 & b1, a2 & b2, a3 & b3])
    }
}

// ---------------------------------------------------------------------------
// InitAnalysis
// ---------------------------------------------------------------------------

/// Forward must-init analysis over XQVM basic blocks.
pub struct InitAnalysis {
    /// Per-block: ordered sequence of `(register_slot, initialized)` events.
    ///
    /// `true` = register is written (any type except `Unset`).
    /// `false` = register is cleared (`DROP`).
    block_inits: HashMap<BlockId, Vec<(u8, bool)>>,
}

impl Analysis for InitAnalysis {
    type Value = InitValue;
    type Node = BlockId;
    type Dir = Forward;

    fn boundary_value(&self) -> InitValue {
        InitValue([0u64; 4])
    }

    fn transfer(&self, node: &BlockId, input: &InitValue) -> InitValue {
        let mut state = input.clone();
        if let Some(inits) = self.block_inits.get(node) {
            for &(slot, initialized) in inits {
                if initialized {
                    state.set_init(slot);
                } else {
                    state.clear_init(slot);
                }
            }
        }
        state
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn collect_block_inits(code: &[u8], block_start: usize, block_end: usize) -> Vec<(u8, bool)> {
    let block_code = match code.get(block_start..block_end) {
        Some(s) if !s.is_empty() => s,
        _ => return Vec::new(),
    };
    let mut inits = Vec::new();
    let mut stream = InstructionStream::new(block_code);
    while let Some(Ok((_, _, instr))) = stream.next_instruction() {
        if let Some((reg, reg_type)) = write_effect(&instr) {
            inits.push((reg.slot(), reg_type != RegType::Unset));
        }
    }
    inits
}

// ---------------------------------------------------------------------------
// check_uninit_registers
// ---------------------------------------------------------------------------

/// Run the CFG-based forward must-init analysis using a pre-built CFG.
///
/// Detects [`VerifierError::ReadUnsetRegister`] for registers that are not
/// definitely written on every path to the read site.  This includes the case
/// that [`crate::dataflow::register::check_register_types`] misses: a register
/// written on only one branch of a conditional, then read after the join.
///
/// Unreachable blocks (entry state == `top = [u64::MAX; 4]`) are skipped to
/// avoid false positives from dead code.
///
/// A `[RegType; 256]` buffer is allocated once per block (not per instruction)
/// and updated incrementally as writes are observed.
///
/// # Errors
///
/// Returns the first [`VerifierError::ReadUnsetRegister`] found in program
/// order across reachable blocks.
pub(crate) fn check_uninit_registers_with_cfg(
    program: &Program,
    cfg: &crate::dataflow::cfg::Cfg<BlockId>,
) -> Result<(), VerifierError> {
    let code = program.code();

    let mut starts: Vec<BlockId> = cfg.nodes().to_vec();
    starts.sort_unstable();
    let ranges: Vec<(usize, usize)> = starts
        .iter()
        .enumerate()
        .map(|(i, &s)| {
            #[expect(
                clippy::arithmetic_side_effects,
                reason = "`i` is an `enumerate` index into `starts`, so `i + 1` is at most its length"
            )]
            let end = starts.get(i + 1).copied().unwrap_or(code.len());
            (s, end)
        })
        .collect();

    let block_inits: HashMap<BlockId, Vec<(u8, bool)>> = ranges
        .iter()
        .filter_map(|&(start, end)| {
            let inits = collect_block_inits(code, start, end);
            if inits.is_empty() {
                None
            } else {
                Some((start, inits))
            }
        })
        .collect();

    let analysis = InitAnalysis { block_inits };
    let result = solve(&analysis, cfg);

    let top = InitValue::top();

    for &(block_start, block_end) in &ranges {
        let before = result
            .before(&block_start)
            .cloned()
            .unwrap_or_else(InitValue::top);

        if before == top {
            continue;
        }

        // Build the reg-type view once per block from the block's entry state,
        // then update it incrementally as writes are encountered.
        let mut regs = [RegType::Any; 256];
        for slot in 0u8..=255 {
            if !before.is_init(slot)
                && let Some(e) = regs.get_mut(usize::from(slot))
            {
                *e = RegType::Unset;
            }
        }

        let block_code = code.get(block_start..block_end).unwrap_or(&[]);
        let mut stream = InstructionStream::new(block_code);
        while let Some(item) = stream.next_instruction() {
            let Ok((rel_pos, _, instr)) = item else { break };
            #[expect(
                clippy::arithmetic_side_effects,
                reason = "`rel_pos` is an offset inside `code[block_start..block_end]`, so the absolute position is bounded by `code.len()`"
            )]
            let abs_pos = block_start + rel_pos;
            check_reads(abs_pos, &instr, &regs)?;
            if let Some((reg, reg_type)) = write_effect(&instr)
                && let Some(e) = regs.get_mut(usize::from(reg.slot()))
            {
                *e = if reg_type == RegType::Unset {
                    RegType::Unset
                } else {
                    RegType::Any
                };
            }
        }
    }

    Ok(())
}

/// Run the CFG-based forward must-init analysis on `program`.
///
/// Builds the CFG from `program` and delegates to
/// `check_uninit_registers_with_cfg`.  Returns `Ok(())` for empty programs.
///
/// # Errors
///
/// Returns the first [`VerifierError::ReadUnsetRegister`] found.
pub fn check_uninit_registers(program: &Program) -> Result<(), VerifierError> {
    let code = program.code();
    if code.is_empty() {
        return Ok(());
    }
    let Some(CfgContext { cfg, .. }) = build_cfg(code, program.jump_table()) else {
        return Ok(());
    };
    check_uninit_registers_with_cfg(program, &cfg)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::codec;
    use crate::{Instruction, Program, Register};

    fn prog(instrs: &[Instruction]) -> Program {
        Program::new(instrs.iter().flat_map(codec::encode).collect())
    }

    #[test]
    fn empty_program_ok() {
        assert!(check_uninit_registers(&Program::new(vec![])).is_ok());
    }

    #[test]
    fn stow_then_load_ok() {
        let p = prog(&[
            Instruction::Push1 { val: [7] },
            Instruction::Stow { reg: Register(0) },
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);
        assert!(check_uninit_registers(&p).is_ok());
    }

    #[test]
    fn load_unset_register_is_error() {
        let p = prog(&[Instruction::Load { reg: Register(1) }, Instruction::Halt {}]);
        let err = check_uninit_registers(&p).unwrap_err();
        assert_eq!(err.variant_name(), "ReadUnsetRegister");
    }

    #[test]
    fn drop_clears_register() {
        let p = prog(&[
            Instruction::Push1 { val: [1] },
            Instruction::Stow { reg: Register(0) },
            Instruction::Drop { reg: Register(0) },
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);
        let err = check_uninit_registers(&p).unwrap_err();
        assert_eq!(err.variant_name(), "ReadUnsetRegister");
    }

    // The key case this pass adds: register written on only the fall-through
    // path, then read after the join.  The type-state pass misses this because
    // meet(Unset, Int) = Any (permissive).  This pass uses AND-meet:
    // meet(false, true) = false, so the read is correctly flagged.
    //
    // Layout: JumpI1(0); STOW r0; TARGET(0): LOAD r0; HALT
    // Taken path (JumpI1 fires): r0 never written -> uninit at TARGET.
    // Fall-through: STOW r0 -> init.
    // meet(uninit, init) = uninit -> ReadUnsetRegister at LOAD.
    #[test]
    fn one_branch_skips_write_is_error() {
        let p = prog(&[
            Instruction::Push1 { val: [1] },
            Instruction::JumpI1 { label: 0 }, // taken: skip STOW
            Instruction::Stow { reg: Register(0) }, // fall-through only
            Instruction::Target {},           // label=0: join; meet(uninit, init) = uninit
            Instruction::Load { reg: Register(0) }, // ReadUnsetRegister
            Instruction::Halt {},
        ]);
        let err = check_uninit_registers(&p).unwrap_err();
        assert_eq!(err.variant_name(), "ReadUnsetRegister");
    }

    // Both branches write: meet(true, true) = true -> OK at join.
    #[test]
    fn both_branches_write_ok() {
        let p = prog(&[
            Instruction::Push1 { val: [1] },
            Instruction::JumpI1 { label: 0 },
            Instruction::Push1 { val: [2] },
            Instruction::Stow { reg: Register(0) }, // fall-through writes r0
            Instruction::Jump2 { label: 1 },
            Instruction::Target {}, // label=0
            Instruction::Push1 { val: [3] },
            Instruction::Stow { reg: Register(0) }, // taken writes r0
            Instruction::Target {},                 // label=1: join; meet(true, true) = true
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);
        assert!(check_uninit_registers(&p).is_ok());
    }

    // Dead code after an unconditional JUMP must not affect reachable block state.
    #[test]
    fn unreachable_block_no_false_positive() {
        let p = prog(&[
            Instruction::Push1 { val: [1] },
            Instruction::Jump2 { label: 0 },
            Instruction::Stow { reg: Register(0) }, // dead: would init r0
            Instruction::Target {},                 // label=0; r0 still uninit
            Instruction::Push1 { val: [2] },
            Instruction::Stow { reg: Register(0) }, // init r0
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);
        assert!(check_uninit_registers(&p).is_ok());
    }
}
