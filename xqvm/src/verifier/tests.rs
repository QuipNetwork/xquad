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

use crate::bytecode::codec;
use crate::{Instruction, InstructionBuilder, Program, Register};

use super::error::VerifierError;
use super::jump_target::JumpTargetPhase;
use super::loop_nesting::LoopNestingPhase;
use super::phase::{Phase, Verifier};
use super::register_type::RegisterTypePhase;
use super::stack_depth::StackDepthPhase;
use super::structural::StructuralPhase;
use super::verify;

fn bytes(instrs: &[Instruction]) -> Vec<u8> {
    instrs.iter().flat_map(codec::encode).collect()
}

// --- StructuralPhase ---

#[test]
fn structural_accepts_valid_stream() {
    let mut b = InstructionBuilder::new();
    let _ = b.emit_push(1).emit_push(2).emit_add().emit_halt();
    assert!(StructuralPhase.run(&b.build().unwrap()).is_ok());
}

#[test]
fn structural_rejects_truncated_push2() {
    // PUSH2 (0x12) needs 2 operand bytes; supply only 1.
    let prog = Program::new(vec![0x12, 0x00]);
    let err = StructuralPhase.run(&prog).unwrap_err();
    assert_eq!(err.variant_name(), "TruncatedInstruction");
}

#[test]
fn structural_rejects_reserved_opcode() {
    // 0x0D is the reserved gap in the opcode table.
    let err = StructuralPhase.run(&Program::new(vec![0x0D])).unwrap_err();
    assert_eq!(err.variant_name(), "BadOpcode");
}

// --- JumpTargetPhase ---

#[test]
fn jump_target_accepts_valid_label() {
    // TARGET(.0) then JUMP1 0.
    let code = bytes(&[
        Instruction::Target {},
        Instruction::Jump1 { label: 0 },
        Instruction::Halt {},
    ]);
    assert!(JumpTargetPhase.run(&Program::new(code)).is_ok());
}

#[test]
fn jump1_to_nonexistent_label() {
    // No TARGETs; label 0 is undefined.
    let code = bytes(&[Instruction::Jump1 { label: 0 }, Instruction::Halt {}]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

#[test]
fn jumpi1_to_nonexistent_label() {
    let code = bytes(&[Instruction::JumpI1 { label: 0 }, Instruction::Halt {}]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

#[test]
fn jump2_to_nonexistent_label() {
    let code = bytes(&[Instruction::Jump2 { label: 0 }, Instruction::Halt {}]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

#[test]
fn jump2_label_out_of_range_with_one_target() {
    // One TARGET (id 0); JUMP2 label=1 is out of range.
    let code = bytes(&[
        Instruction::Target {},
        Instruction::Jump2 { label: 1 },
        Instruction::Halt {},
    ]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

// --- LoopNestingPhase ---

#[test]
fn loop_nesting_accepts_balanced_range() {
    let mut b = InstructionBuilder::new();
    let _ = b
        .emit_push(0)
        .emit_push(3)
        .emit_range()
        .emit_next()
        .emit_halt();
    assert!(LoopNestingPhase.run(&b.build().unwrap()).is_ok());
}

#[test]
fn unmatched_range_at_eof() {
    let code = bytes(&[Instruction::Range {}, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UnmatchedLoop");
}

#[test]
fn next_outside_any_loop() {
    let code = bytes(&[Instruction::Next {}, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "NoActiveLoop");
}

#[test]
fn lval_outside_loop() {
    let code = bytes(&[Instruction::LVal { reg: Register(0) }, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "NoActiveLoop");
}

#[test]
fn lidx_outside_loop() {
    let code = bytes(&[Instruction::Lidx { reg: Register(0) }, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "NoActiveLoop");
}

#[test]
fn nested_loops_balanced() {
    // RANGE { RANGE { NEXT } NEXT } HALT
    let code = bytes(&[
        Instruction::Range {},
        Instruction::Range {},
        Instruction::Next {},
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    assert!(LoopNestingPhase.run(&Program::new(code)).is_ok());
}

#[test]
fn nested_loops_outer_unmatched() {
    // RANGE { RANGE { NEXT } HALT -- outer RANGE has no NEXT
    let code = bytes(&[
        Instruction::Range {},
        Instruction::Range {},
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UnmatchedLoop");
    // Outermost opener is at offset 0.
    assert!(matches!(
        err,
        VerifierError::UnmatchedLoop {
            offset: 0,
            depth: 1
        }
    ));
}

// --- RegisterTypePhase ---

#[test]
fn reg_type_stow_then_load_ok() {
    let code = bytes(&[
        Instruction::Push1 { val: [7] },
        Instruction::Stow { reg: Register(0) },
        Instruction::Load { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_load_unset_register_is_error() {
    let code = bytes(&[Instruction::Load { reg: Register(1) }, Instruction::Halt {}]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "ReadUnsetRegister");
}

#[test]
fn reg_type_output_unset_register_is_error() {
    let code = bytes(&[
        Instruction::Push1 { val: [0] }, // output slot index
        Instruction::Output { reg: Register(2) },
        Instruction::Halt {},
    ]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "ReadUnsetRegister");
}

#[test]
fn reg_type_drop_clears_register() {
    // STOW r0, DROP r0, LOAD r0 -- Load after Drop should error.
    let code = bytes(&[
        Instruction::Push1 { val: [1] },
        Instruction::Stow { reg: Register(0) },
        Instruction::Drop { reg: Register(0) },
        Instruction::Load { reg: Register(0) },
        Instruction::Halt {},
    ]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "ReadUnsetRegister");
}

#[test]
fn reg_type_wrong_type_for_load() {
    // BQMX writes Model to r0; LOAD expects Int.
    let code = bytes(&[
        Instruction::Push1 { val: [4] }, // size
        Instruction::Bqmx { reg: Register(0) },
        Instruction::Load { reg: Register(0) },
        Instruction::Halt {},
    ]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_input_satisfies_any_read() {
    // INPUT writes Any; subsequent LOAD should not error.
    let code = bytes(&[
        Instruction::Push1 { val: [0] }, // calldata index
        Instruction::Input { reg: Register(0) },
        Instruction::Load { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_bqmx_then_getline_ok() {
    let code = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bqmx { reg: Register(0) },
        Instruction::Push1 { val: [0] },
        Instruction::GetLine { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_bsmx_then_getline_ok() {
    // GETLINE reads a sample's assignment as readily as a model's bias.
    let code = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bsmx { reg: Register(0) },
        Instruction::Push1 { val: [0] },
        Instruction::GetLine { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_bsmx_then_setline_ok() {
    // SETLINE assigns a sample variable. The value's domain is a run-time
    // quantity the VM checks; the verifier only settles the register kind.
    let code = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bsmx { reg: Register(0) },
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [1] },
        Instruction::SetLine { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_bsmx_then_addline_ok() {
    let code = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bsmx { reg: Register(0) },
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [1] },
        Instruction::AddLine { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_bsmx_then_setquad_mismatch() {
    // The linear opcodes widened to accept a sample; the quadratic ones did
    // not. A sample has no place to put a coupling term.
    let code = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bsmx { reg: Register(0) },
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [1] },
        Instruction::Push1 { val: [1] },
        Instruction::SetQuad { reg: Register(0) },
        Instruction::Halt {},
    ]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

// --- Branch joins of a related pair (QUI-1160) ---
//
// `meet_reg` used to discard any two differing types to `RegType::Any`, and
// `Any` satisfies every requirement. So a join of two *related* types lost
// the capability they share and handed out the ones they do not: a
// `BQMX`/`BSMX` join satisfied `SETQUAD`'s Model requirement, and the
// program reached a quadratic write on a sample using nothing but its own
// instructions. The meet now keeps the intersection instead.
//
// A third branch of an unrelated type used to undo that: `Grid` met with
// `Int` collapsed back to `Any`, so the same bypass returned with one more
// branch, and which of the three came first decided the verdict. Unrelated
// types now meet to `Conflict`, which satisfies `NonUnset` and nothing else,
// and the meet is associative over the concrete types.
//
// The positive cases are the ones that catch an over-tightening; the
// negative ones alone would pass even if the new variants satisfied nothing.

/// Emit one block per entry in `branches`, each writing r0, all joined at a
/// common `tail`.
///
/// Branch `i` is selected by `INPUT r(i+1) / LOAD / JUMPI`, so every block is
/// a real predecessor of the join and every path is reachable at run time.
/// The order the branches appear in is the order the CFG records their edges,
/// and so the order the solver folds them: a meet that is not associative
/// lets that order decide the verdict.
fn joined_branches(branches: &[Instruction], tail: &[Instruction]) -> Program {
    let mut b = InstructionBuilder::new();
    let after = b.label();

    let (last, dispatched) = branches.split_last().expect("at least one branch");
    for (branch, (slot, reg)) in dispatched.iter().zip((0i64..).zip(1u8..)) {
        let next = b.label();
        let _ = b.emit_push(slot);
        let _ = b.emit_input(Register(reg));
        let _ = b.emit_load(Register(reg));
        let _ = b.emit_jump_if(next);

        let _ = b.emit_push(4);
        let _ = b.emit(*branch);
        let _ = b.emit_jump(after);

        let _ = b.place(next).unwrap();
    }
    let _ = b.emit_push(4);
    let _ = b.emit(*last);

    let _ = b.place(after).unwrap();
    for instr in tail {
        let _ = b.emit(*instr);
    }
    let _ = b.emit_halt();
    b.build().unwrap()
}

#[test]
fn reg_type_model_sample_join_rejects_a_model_only_opcode() {
    let code = joined_branches(
        &[
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Bsmx { reg: Register(0) },
        ],
        &[
            Instruction::Push1 { val: [0] },
            Instruction::Push1 { val: [1] },
            Instruction::Push1 { val: [1] },
            Instruction::SetQuad { reg: Register(0) },
        ],
    );
    let err = RegisterTypePhase.run(&code).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_model_sample_join_rejects_a_sample_only_opcode() {
    // ENERGY's second operand must be a Sample; the join is not one.
    let code = joined_branches(
        &[
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Bsmx { reg: Register(0) },
        ],
        &[
            Instruction::Push1 { val: [4] },
            Instruction::Bqmx { reg: Register(2) },
            Instruction::Energy {
                model: Register(2),
                sample: Register(0),
            },
        ],
    );
    let err = RegisterTypePhase.run(&code).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_model_sample_join_still_accepts_a_grid_opcode() {
    // RESIZE, ROWSUM and the linear trio address the surface both members
    // share, so the join must keep satisfying them.
    let code = joined_branches(
        &[
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Bsmx { reg: Register(0) },
        ],
        &[
            Instruction::Push1 { val: [2] },
            Instruction::Push1 { val: [2] },
            Instruction::Resize { reg: Register(0) },
            Instruction::Push1 { val: [0] },
            Instruction::RowSum { reg: Register(0) },
            Instruction::Pop {},
            Instruction::Push1 { val: [0] },
            Instruction::GetLine { reg: Register(0) },
            Instruction::Pop {},
        ],
    );
    assert!(RegisterTypePhase.run(&code).is_ok());
}

#[test]
fn reg_type_vec_join_rejects_a_vecint_only_opcode() {
    let code = joined_branches(
        &[
            Instruction::Vec { reg: Register(0) },
            Instruction::VecX { reg: Register(0) },
        ],
        &[
            Instruction::Push1 { val: [1] },
            Instruction::VecPush { reg: Register(0) },
        ],
    );
    let err = RegisterTypePhase.run(&code).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_vec_join_still_accepts_veclen() {
    let code = joined_branches(
        &[
            Instruction::Vec { reg: Register(0) },
            Instruction::VecX { reg: Register(0) },
        ],
        &[
            Instruction::VecLen { reg: Register(0) },
            Instruction::Pop {},
        ],
    );
    assert!(RegisterTypePhase.run(&code).is_ok());
}

#[test]
fn reg_type_three_way_join_rejects_a_model_only_opcode() {
    // Both orders, and for the same reason: the edge order is the fold
    // order, so an associative meet is the only thing that makes these two
    // programs agree. Before `Conflict` the first accepted and the second
    // rejected.
    for branches in [
        [
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Bsmx { reg: Register(0) },
            Instruction::Stow { reg: Register(0) },
        ],
        [
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Stow { reg: Register(0) },
            Instruction::Bsmx { reg: Register(0) },
        ],
    ] {
        let code = joined_branches(
            &branches,
            &[
                Instruction::Push1 { val: [0] },
                Instruction::Push1 { val: [1] },
                Instruction::Push1 { val: [1] },
                Instruction::SetQuad { reg: Register(0) },
            ],
        );
        let err = RegisterTypePhase.run(&code).unwrap_err();
        assert_eq!(err.variant_name(), "RegisterTypeMismatch", "{branches:?}");
    }
}

#[test]
fn reg_type_three_way_vec_join_rejects_a_vecint_only_opcode() {
    // The vec twin: `AnyVec` met with `Int` is a conflict, not a vec.
    let code = joined_branches(
        &[
            Instruction::Vec { reg: Register(0) },
            Instruction::VecX { reg: Register(0) },
            Instruction::Stow { reg: Register(0) },
        ],
        &[
            Instruction::Push1 { val: [1] },
            Instruction::VecPush { reg: Register(0) },
        ],
    );
    let err = RegisterTypePhase.run(&code).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_conflicting_join_still_accepts_output() {
    // `Conflict` is not `Unset`: the register is written on every path, so
    // the requirement both branches do share must keep passing. Without this
    // the tightening would be indistinguishable from rejecting the join
    // outright.
    let code = joined_branches(
        &[
            Instruction::Bqmx { reg: Register(0) },
            Instruction::Stow { reg: Register(0) },
        ],
        &[
            Instruction::Push1 { val: [0] },
            Instruction::Output { reg: Register(0) },
        ],
    );
    assert!(RegisterTypePhase.run(&code).is_ok());
}

#[test]
fn reg_type_bsmx_then_onehotr_mismatch() {
    // The same guard for the high-level constraint group: a penalty
    // expansion writes coefficients, which a sample does not hold.
    let code = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bsmx { reg: Register(0) },
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [1] },
        Instruction::OneHotR { reg: Register(0) },
        Instruction::Halt {},
    ]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_veci_then_getline_mismatch() {
    // VECI writes VecInt to r0; GETLINE accepts Model or Sample, not a vec.
    let code = bytes(&[
        Instruction::VecI { reg: Register(0) },
        Instruction::Push1 { val: [0] },
        Instruction::GetLine { reg: Register(0) },
        Instruction::Halt {},
    ]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_energy_wrong_sample_slot() {
    // BQMX r0=Model, BSMX r1=Sample, ENERGY with model=r1, sample=r0.
    // r1 is Sample (ok for model slot? no -- model slot requires Model).
    let code = bytes(&[
        Instruction::Push1 { val: [2] },
        Instruction::Bqmx { reg: Register(0) }, // r0 = Model
        Instruction::Push1 { val: [2] },
        Instruction::Bsmx { reg: Register(1) }, // r1 = Sample
        // Energy with model=r1(Sample) -- should fail
        Instruction::Energy {
            model: Register(1),
            sample: Register(0),
        },
        Instruction::Halt {},
    ]);
    let err = RegisterTypePhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "RegisterTypeMismatch");
}

#[test]
fn reg_type_correct_energy_program() {
    let code = bytes(&[
        Instruction::Push1 { val: [2] },
        Instruction::Bqmx { reg: Register(0) }, // r0 = Model
        Instruction::Push1 { val: [2] },
        Instruction::Bsmx { reg: Register(1) }, // r1 = Sample
        Instruction::Energy {
            model: Register(0),
            sample: Register(1),
        },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_veclen_accepts_vec_xqmx() {
    let code = bytes(&[
        Instruction::VecX { reg: Register(0) }, // r0 = VecXqmx
        Instruction::VecLen { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code)).is_ok());
}

#[test]
fn reg_type_resize_accepts_model_or_sample() {
    // RESIZE accepts both Model and Sample via the Grid requirement.
    let code_model = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bqmx { reg: Register(0) },
        Instruction::Push1 { val: [8] },
        Instruction::Push1 { val: [8] },
        Instruction::Resize { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code_model)).is_ok());

    let code_sample = bytes(&[
        Instruction::Push1 { val: [4] },
        Instruction::Bsmx { reg: Register(0) },
        Instruction::Push1 { val: [8] },
        Instruction::Push1 { val: [8] },
        Instruction::Resize { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert!(RegisterTypePhase.run(&Program::new(code_sample)).is_ok());
}

// --- Verifier composition ---

#[test]
fn default_verifier_passes_valid_program() {
    let mut b = InstructionBuilder::new();
    let _ = b.emit_push(10).emit_push(32).emit_add().emit_halt();
    assert!(verify(&b.build().unwrap()).is_ok());
}

#[test]
fn custom_verifier_only_jump_phase() {
    // An unmatched RANGE is not caught if LoopNestingPhase is not included.
    let code = bytes(&[Instruction::Range {}, Instruction::Halt {}]);
    let result = Verifier::new()
        .with_phase(JumpTargetPhase)
        .run(&Program::new(code));
    assert!(
        result.is_ok(),
        "jump-only verifier should not catch loop errors"
    );
}

#[test]
fn custom_verifier_structural_then_loop() {
    // A bad opcode is caught by StructuralPhase before LoopNestingPhase runs.
    let prog = Program::new(vec![0x0D]);
    let err = Verifier::new()
        .with_phase(StructuralPhase)
        .with_phase(LoopNestingPhase)
        .run(&prog)
        .unwrap_err();
    assert_eq!(err.variant_name(), "BadOpcode");
}

// --- StackDepthPhase ---

#[test]
fn stack_depth_mismatch_at_conditional_join() {
    // PUSH1(1); JUMPI1(label=0); PUSH1(2); TARGET(label=0); HALT
    // Taken (JUMPI1 fires, depth 0) vs fall-through (depth 1) at TARGET.
    let mut b = InstructionBuilder::new();
    let label = b.label();
    let _ = b
        .emit_push(1)
        .emit_jump_if(label) // taken: depth 0
        .emit_push(2) // fall-through only: depth 1
        .place(label)
        .unwrap()
        .emit_halt();
    let err = StackDepthPhase.run(&b.build().unwrap()).unwrap_err();
    assert_eq!(err.variant_name(), "StackDepthMismatch");
}

#[test]
fn stack_depth_mismatch_on_back_edge_to_entry_block() {
    // .0: PUSH 1; JUMP .0; HALT
    //
    // The loop grows the stack by one per iteration and overflows at runtime.
    // It used to verify clean: the entry block's only *predecessor block* is
    // itself, so the join check skipped it. Program entry is an implicit
    // incoming edge at depth 0, and counting it makes this the same
    // disagreement the conditional-join test above catches.
    let mut b = InstructionBuilder::new();
    let top = b.label();
    let _ = b
        .place(top)
        .unwrap()
        .emit_push(1)
        .emit_jump(top)
        .emit_halt();
    let err = StackDepthPhase.run(&b.build().unwrap()).unwrap_err();
    let VerifierError::StackDepthMismatch {
        target_offset,
        depth_a,
        depth_b,
    } = err
    else {
        panic!("expected StackDepthMismatch, got {err:?}");
    };
    // Program entry contributes depth 0; the back-edge arrives at depth 1.
    assert_eq!(target_offset, 0);
    assert_eq!(depth_a, 0);
    assert_eq!(depth_b, 1);
}

#[test]
fn stack_depth_mismatch_on_entry_block_with_two_explicit_predecessors() {
    // .0: PUSH 1; JUMPI .1; PUSH 5; PUSH 5; JUMP .0
    // .1: PUSH 5; PUSH 5; JUMP .0
    //
    // The second shape the fix newly rejects: the entry block already has two
    // explicit predecessors, so the pre-fix eligibility check fired -- but both
    // back-edges agree (depth 2), and only the implicit program-entry edge
    // (depth 0) creates the disagreement. Guards against weakening the fix to
    // "count the implicit edge only when there are fewer than two explicit
    // predecessors", which passes the other two tests and silently reinstates
    // acceptance of this program, growing the stack by 2 per iteration.
    let mut b = InstructionBuilder::new();
    let top = b.label();
    let alt = b.label();
    let _ = b
        .place(top)
        .unwrap()
        .emit_push(1)
        .emit_jump_if(alt)
        .emit_push(5)
        .emit_push(5)
        .emit_jump(top)
        .place(alt)
        .unwrap()
        .emit_push(5)
        .emit_push(5)
        .emit_jump(top);
    let err = StackDepthPhase.run(&b.build().unwrap()).unwrap_err();
    let VerifierError::StackDepthMismatch {
        target_offset,
        depth_a,
        depth_b,
    } = err
    else {
        panic!("expected StackDepthMismatch, got {err:?}");
    };
    assert_eq!(target_offset, 0);
    assert_eq!(depth_a, 0);
    assert_eq!(depth_b, 2);
}

#[test]
fn balanced_back_edge_to_entry_block_is_accepted() {
    // The counterpart: a loop whose body leaves the stack as it found it must
    // still verify, so the implicit entry edge does not reject honest programs.
    //
    // .0: PUSH 1; POP; JUMP .0; HALT  -- net zero per iteration.
    let mut b = InstructionBuilder::new();
    let top = b.label();
    let _ = b
        .place(top)
        .unwrap()
        .emit_push(1)
        .emit_pop()
        .emit_jump(top)
        .emit_halt();
    assert!(StackDepthPhase.run(&b.build().unwrap()).is_ok());
}

#[test]
fn sclr_back_edge_to_entry_block_is_rejected() {
    // .0: SCLR; PUSH 1; JUMP .0; HALT
    //
    // This loop cannot overflow at runtime -- SCLR resets the stack every
    // iteration -- so rejecting it is an over-rejection, pinned here as
    // intended rather than incidental: the same loop with a NOP before the
    // label was already rejected at a non-entry join before the implicit
    // entry edge was counted, and the entry block gets no special exemption.
    let mut b = InstructionBuilder::new();
    let top = b.label();
    let _ = b
        .place(top)
        .unwrap()
        .emit_sclr()
        .emit_push(1)
        .emit_jump(top)
        .emit_halt();
    let err = StackDepthPhase.run(&b.build().unwrap()).unwrap_err();
    let VerifierError::StackDepthMismatch {
        target_offset,
        depth_a,
        depth_b,
    } = err
    else {
        panic!("expected StackDepthMismatch, got {err:?}");
    };
    // Program entry contributes depth 0; the back-edge arrives at depth 1.
    assert_eq!(target_offset, 0);
    assert_eq!(depth_a, 0);
    assert_eq!(depth_b, 1);
}

#[test]
fn stack_effect_underflow_on_pop_empty() {
    // POP with nothing on the stack.
    let code = bytes(&[Instruction::Pop {}, Instruction::Halt {}]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

#[test]
fn stack_effect_underflow_on_binary_op() {
    // ADD on an empty stack pops two values that are not there.
    let code = bytes(&[Instruction::Add {}, Instruction::Halt {}]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

#[test]
fn stack_effect_underflow_on_binary_op_with_one_operand() {
    // ADD pops two before pushing one; a single value is not enough, even
    // though the block's net depth never goes negative (+1, then -1).
    let code = bytes(&[
        Instruction::Push1 { val: [1] },
        Instruction::Add {},
        Instruction::Halt {},
    ]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

#[test]
fn stack_effect_underflow_on_swap_with_one_value() {
    // SWAP pops two and pushes two: net zero, but it needs two values.
    let code = bytes(&[
        Instruction::Push1 { val: [1] },
        Instruction::Swap {},
        Instruction::Halt {},
    ]);
    let program = Program::new(code);
    let err = StackDepthPhase.run(&program).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
    // The full pipeline, which the CLI and the chain run, rejects it too.
    assert!(matches!(
        verify(&program),
        Err(VerifierError::StackUnderflow { .. })
    ));
}

#[test]
fn stack_effect_underflow_on_copy_of_empty_stack() {
    // COPY pops one and pushes two: net +1, but it needs a value to copy.
    let code = bytes(&[Instruction::Copy {}, Instruction::Halt {}]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

#[test]
fn stack_effect_underflow_on_idxgrid_with_two_operands() {
    // The example spec/xqvm/VERIFIER.md uses: IDXGRID pops three.
    let code = bytes(&[
        Instruction::Push1 { val: [1] },
        Instruction::Push1 { val: [2] },
        Instruction::IdxGrid {},
        Instruction::Halt {},
    ]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

#[test]
fn stack_effect_underflow_after_sclr_counts_pops() {
    // After SCLR the depth is absolute: PUSH 1 / SWAP underflows there too.
    let code = bytes(&[
        Instruction::Sclr {},
        Instruction::Push1 { val: [1] },
        Instruction::Swap {},
        Instruction::Halt {},
    ]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

#[test]
fn a_second_sclr_does_not_erase_an_underflow_after_the_first() {
    // Both programs fault at run time on the instruction between the two
    // SCLRs; the second SCLR resets the depth but not that fault.
    let programs: [&[Instruction]; 2] = [
        &[
            Instruction::Sclr {},
            Instruction::Push1 { val: [1] },
            Instruction::Swap {},
            Instruction::Sclr {},
            Instruction::Halt {},
        ],
        &[
            Instruction::Sclr {},
            Instruction::Pop {},
            Instruction::Sclr {},
            Instruction::Halt {},
        ],
    ];
    for program in programs {
        let err = StackDepthPhase
            .run(&Program::new(bytes(program)))
            .unwrap_err();
        assert_eq!(err.variant_name(), "StackUnderflow", "{program:?}");
    }
}

#[test]
fn stack_effect_underflow_on_a_path_through_a_join() {
    // Both arms leave depth 1 at the join, where SWAP needs 2.
    let mut b = InstructionBuilder::new();
    let join = b.label();
    let _ = b.emit_push(1);
    let _ = b.emit_push(0);
    let _ = b.emit_jump_if(join);
    let _ = b.place(join).unwrap();
    let _ = b.emit(Instruction::Swap {});
    let _ = b.emit_halt();
    let program = b.build().unwrap();
    let err = StackDepthPhase.run(&program).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

/// Every opcode in the table, zero-initialised, for the table-driven test
/// below.
macro_rules! all_default_instructions {
    (
        $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $_stack:expr,
            {$($fname:ident: $fty:ty),* $(,)?}) ),*
        $(,)?
    ) => {
        [ $( Instruction::$variant { $($fname: <$fty as Default>::default(),)* } ),* ]
    };
}

/// Every opcode that pops is rejected one value short of its pop count and
/// accepted at exactly it, whatever it pushes. Control-flow openers and
/// conditional jumps are left out: their programs need a matching `NEXT`
/// or `TARGET`, and the plain pop-only case is already covered above.
#[test]
fn every_popping_opcode_needs_its_full_pop_count() {
    let with = |instr: Instruction, values: u8| {
        let mut instrs: Vec<Instruction> = (0..values)
            .map(|_| Instruction::Push1 { val: [1] })
            .collect();
        instrs.extend([instr, Instruction::Halt {}]);
        Program::new(bytes(&instrs))
    };
    let mut pop_and_push = Vec::new();
    for instr in crate::opcodes!(all_default_instructions) {
        let crate::StackEffect::Fixed { pops, pushes } = instr.stack_effect() else {
            continue;
        };
        // `checked_sub` also skips the opcodes that pop nothing.
        let Some(one_short) = pops.checked_sub(1) else {
            continue;
        };
        if matches!(
            instr,
            Instruction::JumpI1 { .. }
                | Instruction::JumpI2 { .. }
                | Instruction::Range {}
                | Instruction::Iter { .. }
        ) {
            continue;
        }
        if pushes > 0 {
            pop_and_push.push(instr.mnemonic());
        }
        let short = StackDepthPhase.run(&with(instr, one_short));
        assert!(
            matches!(short, Err(VerifierError::StackUnderflow { .. })),
            "{}: {one_short} values for {pops} pops should underflow, got {short:?}",
            instr.mnemonic()
        );
        let exact = StackDepthPhase.run(&with(instr, pops));
        assert!(
            exact.is_ok(),
            "{}: {pops} values for {pops} pops should verify, got {exact:?}",
            instr.mnemonic()
        );
    }
    // Every opcode that both pops and pushes went through the loop above;
    // spec/xqvm/VERIFIER.md's per-opcode table lists the same forty.
    assert_eq!(pop_and_push.len(), 40, "{pop_and_push:?}");
}

#[test]
fn stack_effect_valid_push_pop() {
    let code = bytes(&[
        Instruction::Push1 { val: [42] },
        Instruction::Pop {},
        Instruction::Halt {},
    ]);
    assert!(StackDepthPhase.run(&Program::new(code)).is_ok());
}

#[test]
fn stack_effect_sclr_resets_depth() {
    // PUSH, PUSH, SCLR, then POP would underflow — SCLR drops both items.
    let code = bytes(&[
        Instruction::Push1 { val: [1] },
        Instruction::Push1 { val: [2] },
        Instruction::Sclr {},
        Instruction::Pop {},
        Instruction::Halt {},
    ]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "StackUnderflow");
}

#[test]
fn stack_effect_sclr_leaves_clean_stack() {
    // PUSH, PUSH, SCLR -- depth is 0 afterwards; HALT is fine.
    let code = bytes(&[
        Instruction::Push1 { val: [1] },
        Instruction::Push1 { val: [2] },
        Instruction::Sclr {},
        Instruction::Halt {},
    ]);
    assert!(StackDepthPhase.run(&Program::new(code)).is_ok());
}

// SCLR inside a loop resets the depth counter to 0 unconditionally. If the
// depth at loop body entry was N > 0, the exit depth will be 0 != N and
// LoopStackImbalance is raised. The error message says "loop has non-zero
// stack effect", which is technically correct but the root cause is a global
// stack reset mid-iteration rather than an unmatched push or pop.
#[test]
fn sclr_inside_loop_body_causes_imbalance() {
    let code = bytes(&[
        Instruction::Push1 { val: [1] }, // extra item -- entry depth before RANGE = 3
        Instruction::Push1 { val: [0] }, // start
        Instruction::Push1 { val: [3] }, // count
        Instruction::Range {},           // pops 2; entry depth recorded = 1
        Instruction::Sclr {},            // resets depth to 0; exit depth = 0 != 1
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "LoopStackImbalance");
}

#[test]
fn loop_body_neutral_passes() {
    // PUSH start, PUSH count, RANGE, NEXT, HALT -- body is empty, net effect = 0.
    let code = bytes(&[
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [3] },
        Instruction::Range {},
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    assert!(StackDepthPhase.run(&Program::new(code)).is_ok());
}

#[test]
fn loop_body_push_causes_imbalance() {
    // Loop body does a PUSH but no matching POP: each iteration leaks one item.
    let code = bytes(&[
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [3] },
        Instruction::Range {},
        Instruction::Push1 { val: [99] }, // unmatched push inside loop
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "LoopStackImbalance");
}

#[test]
fn loop_body_pop_causes_imbalance() {
    // Loop body pops the loop's own operands off the stack.
    // Entry depth after RANGE's -2 = 0. POP inside would underflow -- caught as underflow.
    // Instead use a loop that has 1 extra item before RANGE.
    // depth before RANGE = 3 (start=0, count=3, extra=1 → no, RANGE pops 2 → depth=1)
    // body pops 1 → depth=0, NEXT expects depth=1 → LoopStackImbalance
    let code = bytes(&[
        Instruction::Push1 { val: [1] }, // extra item
        Instruction::Push1 { val: [0] }, // start
        Instruction::Push1 { val: [3] }, // count
        Instruction::Range {},           // pops 2, depth = 1
        Instruction::Pop {},             // pops extra, depth = 0
        Instruction::Next {},            // expects depth = 1
        Instruction::Halt {},
    ]);
    let err = StackDepthPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "LoopStackImbalance");
}

#[test]
fn nested_loops_both_neutral() {
    let code = bytes(&[
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [2] },
        Instruction::Range {},
        Instruction::Push1 { val: [0] },
        Instruction::Push1 { val: [2] },
        Instruction::Range {},
        Instruction::Next {},
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    assert!(StackDepthPhase.run(&Program::new(code)).is_ok());
}

#[test]
fn stack_effect_on_all_variants_does_not_panic() {
    // Smoke test: stack_effect() must not panic for any instruction variant.
    use crate::bytecode::types::Register;
    let instrs: &[Instruction] = &[
        Instruction::Target {},
        Instruction::Jump1 { label: 0 },
        Instruction::JumpI1 { label: 0 },
        Instruction::Nop {},
        Instruction::Halt {},
        Instruction::Sclr {},
        Instruction::Add {},
        Instruction::Energy {
            model: Register(0),
            sample: Register(1),
        },
    ];
    for instr in instrs {
        let _ = instr.stack_effect();
    }
}
