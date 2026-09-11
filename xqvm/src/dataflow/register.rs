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

//! CFG-based forward register type-state analysis for XQVM bytecode.
//!
//! The main entry point is [`check_register_types`], which:
//!
//! 1. Builds a [`Cfg`] of basic blocks via `build_cfg`.
//! 2. Runs a forward worklist analysis with a [`RegTypeValue`] lattice
//!    tracking the type of each of the 256 registers at every program point.
//! 3. For each reachable block, replays instructions from the block's entry
//!    state, reporting the first read-before-write or type-mismatch error.
//!
//! Unlike a linear scan, this analysis skips unreachable blocks (e.g. dead
//! code after an unconditional `JUMP`) and correctly propagates type state
//! along all live CFG edges.
//!
//! # Meet semantics
//!
//! At join points the per-register meet is:
//!
//! | Left | Right | Result |
//! |---|---|---|
//! | `Any` | `T` | `T` (`Any` is the lattice top, identity for meet) |
//! | `T` | `T` | `T` (same type, kept) |
//! | `Model`/`Sample`/`Grid` | ditto | `Grid` (the shared grid surface) |
//! | `VecInt`/`VecXqmx`/`AnyVec` | ditto | `AnyVec` (the shared vec surface) |
//! | `Unset` | `T` | `Any` (permissive; see below) |
//! | `T` | `U` (T ≠ U) | `Conflict` (nothing shared but non-unset-ness) |
//!
//! The join keeps what both sides share, and never more. `Any` satisfies
//! every requirement, so answering a join with it hands out capabilities
//! neither branch has: a `BQMX`/`BSMX` join used to meet to `Any` and so
//! satisfy `SETQUAD`'s `Model` requirement, which let a program reach a
//! quadratic write on a sample using nothing but its own instructions
//! (QUI-1160). `Grid` and `AnyVec` name the two pairs that do share a
//! surface; `Conflict` is the answer for every other pair, and satisfies
//! `NonUnset` alone.
//!
//! Answering unrelated pairs with `Conflict` rather than `Any` is also what
//! makes the meet associative over the concrete types, so the verdict at a
//! join of three or more predecessors does not depend on the order the CFG
//! recorded their edges. Without it `meet(meet(Model, Sample), Int)` is
//! `Any` where `meet(Model, meet(Sample, Int))` is `Model`, and a third
//! branch of an unrelated type reopens the bypass the pair rows close.
//!
//! `Unset` is the one row left out of that: `meet(Unset, Int) = Any`, so a
//! register written on only one branch is treated as `Any` (not `Unset`) at
//! the join and reads after the join are not flagged here. That row is not a
//! way back in, because [`UninitRegisterPhase`] rejects every read of a
//! register left unset on any path, and it runs on the same program. Its
//! cost is that this one row is not associative, so a join mixing an
//! unwritten branch with two related types can be rejected by either phase
//! depending on edge order. Both outcomes reject.
//!
//! [`UninitRegisterPhase`]: crate::verifier::UninitRegisterPhase

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
use crate::dataflow::cfg::Cfg;
use crate::dataflow::direction::Forward;
use crate::dataflow::lattice::Lattice;
use crate::dataflow::solver::solve;
use crate::verifier::reg_type::{RegType, apply_writes, check_reads, write_effect};

// ---------------------------------------------------------------------------
// RegTypeValue lattice
// ---------------------------------------------------------------------------

/// Abstract register type-state at a program point: one [`RegType`] per register.
///
/// The top element is `[RegType::Any; 256]` (identity for meet), meaning "no
/// information yet about any register".  The boundary value (program entry) is
/// `[RegType::Unset; 256]` (all registers unwritten).
#[derive(Clone, PartialEq, Eq)]
pub struct RegTypeValue(pub [RegType; 256]);

impl Lattice for RegTypeValue {
    fn top() -> Self {
        Self([RegType::Any; 256])
    }

    fn meet(&self, other: &Self) -> Self {
        let mut result = Self::top();
        for (out, (&a, &b)) in result.0.iter_mut().zip(self.0.iter().zip(other.0.iter())) {
            *out = meet_reg(a, b);
        }
        result
    }
}

/// Per-register meet: `Any` is the top element (identity); two concrete types
/// meet to what they share, which is `Grid` or `AnyVec` for the two related
/// pairs and `Conflict` for everything else. `Unset` against a concrete type
/// is the one row that still meets to `Any`. See the module docs' meet table.
fn meet_reg(a: RegType, b: RegType) -> RegType {
    use RegType as T;
    match (a, b) {
        (T::Any, x) | (x, T::Any) => x,
        (x, y) if x == y => x,
        // Keep what the pair shares rather than discarding to the top, which
        // satisfies everything.
        (T::Model | T::Sample | T::Grid, T::Model | T::Sample | T::Grid) => T::Grid,
        (T::VecInt | T::VecXqmx | T::AnyVec, T::VecInt | T::VecXqmx | T::AnyVec) => T::AnyVec,
        // A register written on only one branch stays permissive here; the
        // must-init pass is what rejects the read. Keeping this row above
        // the catch-all is what stops `Conflict` from swallowing it.
        (T::Unset, _) | (_, T::Unset) => T::Any,
        // Nothing in common: `NonUnset` is all that survives the join.
        _ => T::Conflict,
    }
}

// ---------------------------------------------------------------------------
// RegTypeAnalysis
// ---------------------------------------------------------------------------

/// Forward register type-state analysis over XQVM basic blocks.
pub struct RegTypeAnalysis {
    /// Per-block: ordered sequence of `(register_slot, new_type)` write events.
    ///
    /// Only registers actually written appear here; unwritten registers carry
    /// their incoming value unchanged through the transfer function.
    block_writes: HashMap<BlockId, Vec<(u8, RegType)>>,
}

impl Analysis for RegTypeAnalysis {
    type Value = RegTypeValue;
    type Node = BlockId;
    type Dir = Forward;

    fn boundary_value(&self) -> RegTypeValue {
        RegTypeValue([RegType::Unset; 256])
    }

    fn transfer(&self, node: &BlockId, input: &RegTypeValue) -> RegTypeValue {
        // A block no path reaches contributes nothing to a join. Its input is
        // the meet over an empty or equally unreachable predecessor set, which
        // is `top` -- the same test the replay loop uses to skip it -- and the
        // boundary node is excluded because its input is the all-`Unset`
        // boundary value, never `top`. Applying the writes of dead code here
        // would poison every join it falls through into: the `VECI r0` after
        // an unconditional `JUMP` would meet with the live `Model` and answer
        // `Conflict`, rejecting a program whose only fault is unreachable.
        if input == &RegTypeValue::top() {
            return RegTypeValue::top();
        }
        let mut state = input.clone();
        if let Some(writes) = self.block_writes.get(node) {
            for &(slot, reg_type) in writes {
                if let Some(entry) = state.0.get_mut(usize::from(slot)) {
                    *entry = reg_type;
                }
            }
        }
        state
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Collect the ordered write sequence for `code[block_start..block_end]`.
///
/// Each entry is `(register_slot, RegType)`.  If a register is written
/// multiple times within the block, all writes appear in order (last write
/// wins in [`RegTypeAnalysis::transfer`]).
fn collect_block_writes(code: &[u8], block_start: usize, block_end: usize) -> Vec<(u8, RegType)> {
    let block_code = match code.get(block_start..block_end) {
        Some(s) if !s.is_empty() => s,
        _ => return Vec::new(),
    };
    let mut writes: Vec<(u8, RegType)> = Vec::new();
    let mut stream = InstructionStream::new(block_code);
    while let Some(Ok((_, _, instr))) = stream.next_instruction() {
        if let Some((reg, rt)) = write_effect(&instr) {
            writes.push((reg.slot(), rt));
        }
    }
    writes
}

/// Produce a sorted list of `(block_start, block_end)` pairs from CFG nodes.
fn block_ranges(cfg: &Cfg<BlockId>, code_len: usize) -> Vec<(usize, usize)> {
    let mut starts: Vec<BlockId> = cfg.nodes().to_vec();
    starts.sort_unstable();
    starts
        .iter()
        .enumerate()
        .map(|(i, &s)| {
            #[expect(
                clippy::arithmetic_side_effects,
                reason = "`i` is an `enumerate` index into `starts`, so `i + 1` is at most its length"
            )]
            let end = starts.get(i + 1).copied().unwrap_or(code_len);
            (s, end)
        })
        .collect()
}

// ---------------------------------------------------------------------------
// check_register_types
// ---------------------------------------------------------------------------

/// Run the CFG-based forward register type-state analysis using a pre-built CFG.
///
/// Detects [`VerifierError::ReadUnsetRegister`] and
/// [`VerifierError::RegisterTypeMismatch`] errors.  Unreachable blocks
/// (entry state equals `[Any; 256]`) are skipped.
///
/// Callers running multiple analyses should use this variant with a shared
/// [`Cfg`] to avoid redundant construction.
///
/// # Errors
///
/// Returns the first [`VerifierError`] found in program order across reachable
/// blocks.
pub(crate) fn check_register_types_with_cfg(
    program: &Program,
    cfg: &Cfg<BlockId>,
) -> Result<(), VerifierError> {
    let code = program.code();
    let ranges = block_ranges(cfg, code.len());

    // Pre-compute write sequences for the transfer function.
    let block_writes: HashMap<BlockId, Vec<(u8, RegType)>> = ranges
        .iter()
        .filter_map(|&(start, end)| {
            let writes = collect_block_writes(code, start, end);
            if writes.is_empty() {
                None
            } else {
                Some((start, writes))
            }
        })
        .collect();

    let analysis = RegTypeAnalysis { block_writes };
    let result = solve(&analysis, cfg);

    let top = RegTypeValue::top();

    for &(block_start, block_end) in &ranges {
        let before = result
            .before(&block_start)
            .cloned()
            .unwrap_or_else(RegTypeValue::top);

        // Skip unreachable blocks (entry state = top means no predecessor reached here).
        if before == top {
            continue;
        }

        let block_code = code.get(block_start..block_end).unwrap_or(&[]);
        let mut state = before.0;
        let mut stream = InstructionStream::new(block_code);
        while let Some(item) = stream.next_instruction() {
            let Ok((rel_pos, _, instr)) = item else { break };
            #[expect(
                clippy::arithmetic_side_effects,
                reason = "`rel_pos` is an offset inside `code[block_start..block_end]`, so the absolute position is bounded by `code.len()`"
            )]
            let abs_pos = block_start + rel_pos;
            check_reads(abs_pos, &instr, &state)?;
            apply_writes(&instr, &mut state);
        }
    }

    Ok(())
}

/// Run the CFG-based forward register type-state analysis on `program`.
///
/// Builds the CFG from `program` and delegates to
/// `check_register_types_with_cfg`.  Returns `Ok(())` for empty programs.
///
/// # Errors
///
/// Returns the first [`VerifierError`] found in program order across reachable
/// blocks.
pub fn check_register_types(program: &Program) -> Result<(), VerifierError> {
    let code = program.code();
    if code.is_empty() {
        return Ok(());
    }
    let Some(CfgContext { cfg, .. }) = build_cfg(code, program.jump_table()) else {
        return Ok(());
    };
    check_register_types_with_cfg(program, &cfg)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::codec;
    use crate::{Instruction, Register};

    fn prog(instrs: &[Instruction]) -> Program {
        Program::new(instrs.iter().flat_map(codec::encode).collect())
    }

    // --- meet laws (QUI-1160) ---
    //
    // The join at a CFG node is a fold over predecessors, so the meet has to
    // be commutative and associative or the edge order decides the verdict.
    // `Unset` is left out on purpose: its row answers `Any` to keep a
    // one-branch write from being flagged here, which is not associative, and
    // `UninitRegisterPhase` is what rejects those reads instead.

    /// Every type the meet is required to be a semilattice over.
    const CONCRETE: [RegType; 8] = [
        RegType::Int,
        RegType::VecInt,
        RegType::VecXqmx,
        RegType::AnyVec,
        RegType::Model,
        RegType::Sample,
        RegType::Grid,
        RegType::Conflict,
    ];

    #[test]
    fn meet_is_commutative_and_idempotent() {
        for a in CONCRETE {
            assert_eq!(meet_reg(a, a), a, "{a:?} is not idempotent");
            for b in CONCRETE {
                assert_eq!(meet_reg(a, b), meet_reg(b, a), "{a:?} vs {b:?}");
            }
        }
    }

    #[test]
    fn meet_is_associative() {
        for a in CONCRETE {
            for b in CONCRETE {
                for c in CONCRETE {
                    assert_eq!(
                        meet_reg(meet_reg(a, b), c),
                        meet_reg(a, meet_reg(b, c)),
                        "{a:?}, {b:?}, {c:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn any_is_the_identity() {
        for a in CONCRETE {
            assert_eq!(meet_reg(RegType::Any, a), a);
            assert_eq!(meet_reg(a, RegType::Any), a);
        }
    }

    #[test]
    fn unrelated_types_meet_to_conflict() {
        // The pairs keep their shared surface; everything else keeps only
        // non-unset-ness, which is what stops a third branch from handing
        // `Any` back out.
        assert_eq!(meet_reg(RegType::Model, RegType::Sample), RegType::Grid);
        assert_eq!(meet_reg(RegType::VecInt, RegType::VecXqmx), RegType::AnyVec);
        assert_eq!(meet_reg(RegType::Grid, RegType::Int), RegType::Conflict);
        assert_eq!(meet_reg(RegType::AnyVec, RegType::Int), RegType::Conflict);
        assert_eq!(meet_reg(RegType::Model, RegType::VecInt), RegType::Conflict);
    }

    #[test]
    fn unset_against_a_concrete_type_stays_permissive() {
        for a in CONCRETE {
            assert_eq!(meet_reg(RegType::Unset, a), RegType::Any);
            assert_eq!(meet_reg(a, RegType::Unset), RegType::Any);
        }
        assert_eq!(meet_reg(RegType::Unset, RegType::Unset), RegType::Unset);
    }

    // --- basic valid programs ---

    #[test]
    fn empty_program_ok() {
        assert!(check_register_types(&Program::new(vec![])).is_ok());
    }

    #[test]
    fn stow_then_load_ok() {
        let p = prog(&[
            Instruction::Push1 { val: [7] },
            Instruction::Stow { reg: Register(0) },
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);
        assert!(check_register_types(&p).is_ok());
    }

    #[test]
    fn load_unset_register_is_error() {
        let p = prog(&[Instruction::Load { reg: Register(1) }, Instruction::Halt {}]);
        let err = check_register_types(&p).unwrap_err();
        assert_eq!(err.variant_name(), "ReadUnsetRegister");
    }

    #[test]
    fn wrong_type_for_load() {
        // BQMX writes Model to r0; LOAD expects Int.
        let p = prog(&[
            Instruction::Push1 { val: [4] },
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);
        let err = check_register_types(&p).unwrap_err();
        assert_eq!(err.variant_name(), "RegisterTypeMismatch");
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
        let err = check_register_types(&p).unwrap_err();
        assert_eq!(err.variant_name(), "ReadUnsetRegister");
    }

    // --- CFG path coverage ---

    // Code after an unconditional JUMP is unreachable.  The linear scan
    // processes it anyway, potentially updating register state, and then
    // emitting false-positive errors when the jump's target is reached.
    //
    // Bytecode layout (all offsets approximate):
    //   PUSH1(4); BQMX r0; JUMP2(0); VecI r0 [dead]; TARGET(0); GETLINE r0; HALT
    //
    // Runtime path: r0 = Model at GETLINE → valid.
    // Linear scan:  sees VecI r0 in dead code → r0 = VecInt → flags GETLINE as mismatch.
    // CFG:          dead block skipped → before(TARGET block) = [Model,...] → OK.
    #[test]
    fn unreachable_write_after_jump_no_false_positive() {
        let p = prog(&[
            Instruction::Push1 { val: [4] },
            Instruction::Bqmx { reg: Register(0) }, // r0 = Model
            Instruction::Jump2 { label: 0 },        // jump to TARGET(label=0)
            Instruction::VecI { reg: Register(0) }, // dead code: would set r0 = VecInt
            Instruction::Target {},                 // label=0
            Instruction::GetLine { reg: Register(0) }, // r0 = Model → OK
            Instruction::Halt {},
        ]);
        assert!(check_register_types(&p).is_ok());
    }

    // A conditional jump creates two paths through the CFG.  The taken path
    // carries the pre-branch register state; the fall-through path carries a
    // different state after the fall-through writes.  When the taken-path
    // target appears LATER in bytecode than the fall-through writes, the linear
    // scan has already "seen" those fall-through writes by the time it reaches
    // the target, producing a false negative (missed mismatch).
    //
    // Bytecode layout:
    //   VecI r0; JumpI1(0); BQMX r0; JUMP2(1); TARGET(0): GETLINE r0; TARGET(1): HALT
    //
    // Taken path (JumpI1 fires): r0 = VecInt → GETLINE needs Model → mismatch.
    // Fall-through: BQMX r0 (r0=Model), JUMP2(1) → end.
    // Linear scan: processes BQMX r0 before reaching TARGET(0), so r0=Model at GETLINE → missed.
    // CFG: before(TARGET(0) block) = after(entry block) = [VecInt,...] → mismatch caught.
    #[test]
    fn type_mismatch_on_taken_path_caught_linear_misses() {
        let p = prog(&[
            Instruction::VecI { reg: Register(0) },    // r0 = VecInt
            Instruction::JumpI1 { label: 0 }, // taken: jump to TARGET(0); fall-through → next
            Instruction::Push1 { val: [4] },  // fall-through only
            Instruction::Bqmx { reg: Register(0) }, // r0 = Model (fall-through only)
            Instruction::Jump2 { label: 1 },  // fall-through → end
            Instruction::Target {},           // label=0: taken arrives here, r0=VecInt
            Instruction::GetLine { reg: Register(0) }, // needs Model; r0=VecInt → mismatch
            Instruction::Target {},           // label=1: end
            Instruction::Halt {},
        ]);
        let err = check_register_types(&p).unwrap_err();
        assert_eq!(err.variant_name(), "RegisterTypeMismatch");
    }

    // When both branches write the same type, the join preserves it.
    #[test]
    fn both_branches_same_type_ok() {
        let p = prog(&[
            Instruction::Push1 { val: [4] },
            Instruction::Bqmx { reg: Register(0) }, // r0 = Model (before branch)
            Instruction::JumpI1 { label: 0 },       // both paths reach TARGET with Model
            Instruction::Target {},                 // label=0
            Instruction::GetLine { reg: Register(0) }, // r0 = Model → OK
            Instruction::Halt {},
        ]);
        assert!(check_register_types(&p).is_ok());
    }

    // One branch writes, the other does not: meet(Model, Unset) = Any.
    // Any satisfies all requirements → no false error at the join (permissive).
    #[test]
    fn one_branch_writes_join_is_any_permissive() {
        let p = prog(&[
            Instruction::JumpI1 { label: 0 }, // taken: skip write → r0=Unset
            Instruction::Push1 { val: [4] },  // fall-through: writes r0=Model
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Target {}, // label=0: join
            // meet(Unset [taken], Model [fall-through]) = Any → satisfies Model
            Instruction::GetLine { reg: Register(0) },
            Instruction::Halt {},
        ]);
        assert!(check_register_types(&p).is_ok());
    }
}
