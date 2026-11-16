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
//! | `Unset` | `T` | `Unset` (possibly unwritten on some path) |
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
//! `Unset` is absorbing: a register possibly unwritten on any incoming path
//! is possibly unwritten after the join. The dedicated must-init analysis
//! agrees with this projection and remains useful as a standalone phase.
//! The full order is `Any` above the five concrete types, `Model`/`Sample`
//! above `Grid`, `VecInt`/`VecXqmx` above `AnyVec`, `Int`/`Grid`/`AnyVec`
//! above `Conflict`, and `Conflict` above `Unset`. A coordinate descends strictly at most four times.
//!
#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

#[cfg(feature = "std")]
use std::collections::{HashMap, HashSet};

#[cfg(not(feature = "std"))]
use hashbrown::{HashMap, HashSet};

use crate::Program;
use crate::VerifierError;
use crate::bytecode::InstructionStream;
use crate::dataflow::analysis::Analysis;
use crate::dataflow::bytecode::{BlockId, CfgContext, block_ranges, build_cfg};
use crate::dataflow::cfg::Cfg;
use crate::dataflow::direction::Forward;
use crate::dataflow::lattice::Lattice;
use crate::dataflow::solver::{solve, visit_limit};
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
/// pairs and `Conflict` for unrelated initialized types. `Unset` is bottom.
fn meet_reg(a: RegType, b: RegType) -> RegType {
    use RegType as T;
    match (a, b) {
        (T::Any, x) | (x, T::Any) => x,
        (x, y) if x == y => x,
        // Keep what the pair shares rather than discarding to the top, which
        // satisfies everything.
        (T::Model | T::Sample | T::Grid, T::Model | T::Sample | T::Grid) => T::Grid,
        (T::VecInt | T::VecXqmx | T::AnyVec, T::VecInt | T::VecXqmx | T::AnyVec) => T::AnyVec,
        // A missing write on either path survives the join.
        (T::Unset, _) | (_, T::Unset) => T::Unset,
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
    /// Reachability is distinct from the lattice top.
    reachable: HashSet<BlockId>,
}

impl Analysis for RegTypeAnalysis {
    type Value = RegTypeValue;
    type Node = BlockId;
    type Dir = Forward;

    fn boundary_value(&self) -> RegTypeValue {
        RegTypeValue([RegType::Unset; 256])
    }

    fn transfer(&self, node: &BlockId, input: &RegTypeValue) -> RegTypeValue {
        // A reachable join can have every register at Any. Only actual
        // dead blocks must contribute the meet identity without writes.
        if !self.reachable.contains(node) {
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

// ---------------------------------------------------------------------------
// check_register_types
// ---------------------------------------------------------------------------

/// Run the CFG-based forward register type-state analysis using a pre-built CFG.
///
/// Detects [`VerifierError::ReadUnsetRegister`] and
/// [`VerifierError::RegisterTypeMismatch`] errors. Blocks not reachable from program entry are skipped.
/// A reachable all-Any input is still transferred and replayed.
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
    let ranges = block_ranges(cfg, code);

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

    let analysis = RegTypeAnalysis {
        block_writes,
        reachable: cfg.reachable_nodes(),
    };
    let result = solve(&analysis, cfg, visit_limit(cfg, 4 * 256)?)?;

    for &(block_start, block_end) in &ranges {
        let before = result
            .before(&block_start)
            .cloned()
            .unwrap_or_else(RegTypeValue::top);

        if !analysis.reachable.contains(&block_start) {
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
    // Include all public values, including the identity and absorbing bottom.
    const ALL_TYPES: [RegType; 10] = [
        RegType::Any,
        RegType::Int,
        RegType::VecInt,
        RegType::VecXqmx,
        RegType::AnyVec,
        RegType::Model,
        RegType::Sample,
        RegType::Grid,
        RegType::Conflict,
        RegType::Unset,
    ];

    #[test]
    fn meet_is_commutative_and_idempotent() {
        for a in ALL_TYPES {
            assert_eq!(meet_reg(a, a), a, "{a:?} is not idempotent");
            for b in ALL_TYPES {
                assert_eq!(meet_reg(a, b), meet_reg(b, a), "{a:?} vs {b:?}");
            }
        }
    }

    #[test]
    fn meet_is_associative() {
        for a in ALL_TYPES {
            for b in ALL_TYPES {
                for c in ALL_TYPES {
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
        for a in ALL_TYPES {
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
    fn unset_is_absorbing() {
        for a in ALL_TYPES {
            assert_eq!(meet_reg(RegType::Unset, a), RegType::Unset);
            assert_eq!(meet_reg(a, RegType::Unset), RegType::Unset);
        }
        assert_eq!(meet_reg(RegType::Unset, RegType::Unset), RegType::Unset);
    }

    // Independent transitive order, specified by the Hasse covers rather
    // than by the implementation of meet being tested.
    fn below(a: RegType, b: RegType) -> bool {
        use RegType as T;
        a == b
            || a == T::Unset
            || b == T::Any
            || (a == T::Conflict && b != T::Unset)
            || matches!(
                (a, b),
                (T::Grid, T::Model | T::Sample) | (T::AnyVec, T::VecInt | T::VecXqmx)
            )
    }

    #[test]
    fn meet_is_greatest_lower_bound_of_the_independent_order() {
        for a in ALL_TYPES {
            for b in ALL_TYPES {
                let m = meet_reg(a, b);
                assert!(below(m, a) && below(m, b));
                for c in ALL_TYPES {
                    if below(c, a) && below(c, b) {
                        assert!(below(c, m));
                    }
                    if below(a, b) {
                        assert!(below(meet_reg(a, c), meet_reg(b, c)));
                    }
                }
                // Projection to the independently used must-init domain.
                assert_eq!(
                    m != RegType::Unset,
                    a != RegType::Unset && b != RegType::Unset
                );
            }
        }
    }

    #[test]
    fn every_strict_descent_decreases_rank_within_four_levels() {
        use RegType as T;
        let rank = |t| match t {
            T::Any => 4,
            T::Model | T::Sample | T::VecInt | T::VecXqmx => 3,
            T::Grid | T::AnyVec | T::Int => 2,
            T::Conflict => 1,
            T::Unset => 0,
        };
        for a in ALL_TYPES {
            for b in ALL_TYPES {
                if a != b && below(a, b) {
                    assert!(rank(a) < rank(b));
                }
            }
        }
        let longest = [T::Any, T::Model, T::Grid, T::Conflict, T::Unset];
        assert!(
            longest
                .windows(2)
                .all(|p| below(*p.get(1).unwrap(), *p.first().unwrap()))
        );
    }

    #[test]
    fn production_transfers_are_monotone_for_every_register_type() {
        for write in ALL_TYPES.into_iter().map(Some).chain([None]) {
            for live in [false, true] {
                let analysis = RegTypeAnalysis {
                    block_writes: HashMap::from([(
                        0,
                        write.map(|t| vec![(7, t)]).unwrap_or_default(),
                    )]),
                    reachable: if live {
                        HashSet::from([0])
                    } else {
                        HashSet::new()
                    },
                };
                for a in ALL_TYPES {
                    for b in ALL_TYPES {
                        if !below(a, b) {
                            continue;
                        }
                        let x = analysis.transfer(&0, &RegTypeValue([a; 256]));
                        let y = analysis.transfer(&0, &RegTypeValue([b; 256]));
                        assert!(x.0.iter().zip(y.0.iter()).all(|(&u, &v)| below(u, v)));
                    }
                }
            }
        }
    }

    #[test]
    fn cyclic_programs_terminate_with_the_production_budget_disabled() {
        use super::super::solver::test_work;
        use crate::{Instruction as I, InstructionBuilder};
        let drop_cycle = Program::new(vec![
            0x43, 0x02, 0x02, 0x02, 0x0c, 0x02, 0x02, 0x01, 0x00, 0x00, 0x00, 0x02, 0x00, 0x01,
            0x01, 0xff,
        ]);
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit(I::Target {})
            .emit(I::Jump1 { label: 4 })
            .emit(I::Target {})
            .emit_push(0)
            .emit(I::Bsmx { reg: Register(0) })
            .emit(I::Jump1 { label: 2 })
            .emit(I::Target {})
            .emit_push(0)
            .emit(I::Bsmx { reg: Register(0) })
            .emit(I::Jump1 { label: 3 })
            .emit(I::Target {})
            .emit_push(0)
            .emit(I::JumpI1 { label: 6 })
            .emit(I::Target {})
            .emit_push(0)
            .emit(I::JumpI1 { label: 6 })
            .emit(I::Target {})
            .emit(I::Jump1 { label: 3 })
            .emit(I::Target {})
            .emit_push(0)
            .emit(I::JumpI1 { label: 2 })
            .emit(I::Target {})
            .emit_halt();
        let entry_cycle = b.build().unwrap();
        let vector = xqasm::assemble_source(include_str!(
            "../../tests/vectors/control-flow/entry_cycle/program.xqasm"
        ))
        .unwrap();
        assert_eq!(vector.code(), entry_cycle.code());
        let vector = xqasm::assemble_source(include_str!(
            "../../tests/vectors/control-flow/drop_cycle/program.xqasm"
        ))
        .unwrap();
        assert_eq!(vector.code(), drop_cycle.code());
        for (p, expected) in [
            (drop_cycle, Err(VerifierError::StackUnderflow { offset: 0 })),
            (entry_cycle, Ok(())),
        ] {
            // Independent instrumentation panics rather than allowing a
            // regression to hang the test process; it cannot return a verdict.
            let (result, _) = test_work::measure(100_000, || {
                test_work::without_budget(|| crate::verifier::verify(&p))
            });
            assert_eq!(format!("{result:?}"), format!("{expected:?}"));
            assert_eq!(
                format!("{:?}", crate::verifier::verify(&p)),
                format!("{expected:?}")
            );
        }
    }

    #[test]
    fn seeded_production_cfgs_stay_within_the_register_height_bound() {
        use super::super::solver::test_work;
        let mut seed = 0x05ee_da11_u64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        for case in 0..256 {
            let n = 2 + case % 31;
            let mut cfg = Cfg::builder(0, n - 1);
            let mut writes = HashMap::new();
            for node in 0..n {
                cfg = cfg.edge(
                    node,
                    usize::try_from(next() % u64::try_from(n).unwrap()).unwrap(),
                );
                cfg = cfg.edge(node, (node + 1) % n);
                let events = (0..8)
                    .map(|_| {
                        let slot = u8::try_from(next() % 256).unwrap();
                        let ty = *ALL_TYPES
                            .get(usize::try_from(next() % 10).unwrap())
                            .unwrap();
                        (slot, ty)
                    })
                    .collect();
                let _ = writes.insert(node, events);
            }
            let cfg = cfg.build();
            let analysis = RegTypeAnalysis {
                block_writes: writes,
                reachable: cfg.reachable_nodes(),
            };
            let bound = visit_limit(&cfg, 1024).unwrap();
            let (result, visits) = test_work::measure(usize::try_from(bound).unwrap(), || {
                solve(&analysis, &cfg, bound)
            });
            assert!(result.is_ok(), "case {case}, visits {visits}");
        }
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

    // A read must be initialized on every incoming path, including in this phase.
    #[test]
    fn one_branch_writes_join_remains_possibly_unset() {
        let p = prog(&[
            Instruction::JumpI1 { label: 0 }, // taken: skip write → r0=Unset
            Instruction::Push1 { val: [4] },  // fall-through: writes r0=Model
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Target {}, // label=0: join
            // meet(Unset [taken], Model [fall-through]) = Unset
            Instruction::GetLine { reg: Register(0) },
            Instruction::Halt {},
        ]);
        assert!(matches!(
            check_register_types(&p),
            Err(VerifierError::ReadUnsetRegister { .. })
        ));
    }
}

// QUIP-02/16 auditor shape; copied unchanged into the instrumented base control.
#[cfg(test)]
mod audit_work_bounds {
    use super::*;
    use crate::dataflow::solver::test_work;
    use crate::{Instruction, InstructionBuilder, Register};

    #[test]
    fn all_register_cycle_obeys_lattice_visit_bound() {
        let mut builder = InstructionBuilder::new();
        let _ = builder.emit(Instruction::Nop {});
        let cycle = builder.label();
        let _ = builder.place(cycle).unwrap().emit(Instruction::Target {});
        for slot in 0..=u8::MAX {
            let _ = builder.emit(Instruction::Bsmx {
                reg: Register(slot),
            });
        }
        let _ = builder.emit_jump(cycle).emit_halt();
        let original = builder.build().unwrap();
        let vector = xqasm::assemble_source(include_str!(
            "../../tests/vectors/control-flow/audit_all_registers_cycle/program.xqasm"
        ))
        .unwrap();
        for program in [original, Program::new(vector.code().to_vec())] {
            let ctx = build_cfg(program.code(), program.jump_table()).unwrap();
            // This shape has at most one outgoing edge per node. Its OUTPUT
            // facts are restricted to Any -> Sample at written registers;
            // the boundary's Unset output is fixed and dead outputs stay Any.
            // Thus each of 256 coordinates changes at most once: product
            // lattice height H = 1 + 256. With N initial visits and at most
            // one enqueue per changed output, visits <= N + 256*N = H*N.
            // This is a bound for this restricted lattice, not a claim that
            // the full domain has this smaller height (its general height is 1024).
            assert!(
                ctx.cfg
                    .nodes()
                    .iter()
                    .all(|node| ctx.cfg.successors(node).len() <= 1)
            );
            let height = RegTypeValue::top().0.len().saturating_add(1);
            let limit = height.saturating_mul(ctx.cfg.nodes().len());
            let (result, visits) =
                test_work::measure(limit, || check_register_types_with_cfg(&program, &ctx.cfg));
            assert!(visits > 0 && visits <= limit);
            // Type analysis converges; the whole verifier rejects stack use.
            assert!(result.is_ok());
        }
    }
}
