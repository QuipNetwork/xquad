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

//! QUIP-17 handover stack-cycle and error-precedence regressions.
//!
//! Work bounds are pinned by the private unit tests
//! `main_and_loop_body_depth_walks_bound_visits_independently_of_depth`,
//! `auditor_main_cycle_has_bounded_visits`,
//! `auditor_loop_cycle_main_walk_has_bounded_visits`, and
//! `auditor_loop_body_cycle_has_bounded_visits`.

#![expect(
    unused_results,
    reason = "handover builder chains intentionally discard fluent results"
)]

use xqvm::{Instruction, InstructionBuilder, dataflow::check_stack_depth, verifier};

#[expect(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test setup and expected rejection"
)]
fn error(builder: InstructionBuilder) -> &'static str {
    let program = builder.build().expect("valid bytecode");
    check_stack_depth(&program).unwrap_err().variant_name()
}

#[test]
fn structured_loops_take_priority_over_ordinary_joins() {
    let mut b = InstructionBuilder::new();
    let body = b.label();
    b.emit_push(1)
        .emit_push(0)
        .emit_push(3)
        .emit_push(1)
        .emit_jump_if(body)
        .emit_pop();
    b.place(body)
        .unwrap()
        .emit_range()
        .emit_sclr()
        .emit_push(1)
        .emit_next()
        .emit_halt();
    assert_eq!(
        error(b),
        "LoopStackImbalance",
        "mixed path into loop opener"
    );

    let mut b = InstructionBuilder::new();
    let next = b.label();
    b.emit_push(1)
        .emit_push(0)
        .emit_push(3)
        .emit_push(1)
        .emit_jump_if(next)
        .emit_pop()
        .emit_range()
        .emit_push(1);
    b.place(next).unwrap().emit_next().emit_halt();
    assert_eq!(error(b), "LoopStackImbalance", "ordinary jump into NEXT");

    let mut b = InstructionBuilder::new();
    let body = b.label();
    b.emit_push(1)
        .emit_push(0)
        .emit_push(3)
        .emit_push(1)
        .emit_jump_if(body)
        .emit_pop()
        .emit_range();
    b.place(body).unwrap().emit_push(1).emit_next().emit_halt();
    assert_eq!(
        error(b),
        "LoopStackImbalance",
        "ordinary jump into loop body"
    );
}

#[test]
fn vv_poc_cycle_rejection_is_bounded_without_losing_loop_priority() {
    let mut b = InstructionBuilder::new();
    for _ in 0..8_000 {
        b.emit_push(1);
    }
    let cycle = b.label();
    b.place(cycle).unwrap();
    for _ in 0..1_000 {
        b.emit(Instruction::Target {});
    }
    b.emit_pop().emit_jump(cycle).emit_halt();
    let program = b.build().unwrap();
    assert_eq!(program.encode().len(), 17_020);
    let result = verifier::verify(&program);
    assert!(result.is_err());
}

#[test]
fn internal_loop_body_cycle_is_bounded_too() {
    let mut b = InstructionBuilder::new();
    for _ in 0..8_000 {
        b.emit_push(1);
    }
    b.emit_push(0)
        .emit_push(1)
        .emit_range()
        .emit(Instruction::Nop {});
    let cycle = b.label();
    b.place(cycle).unwrap();
    for _ in 0..1_000 {
        b.emit(Instruction::Target {});
    }
    b.emit_pop().emit_jump(cycle).emit_next().emit_halt();
    let program = b.build().unwrap();
    assert!(verifier::verify(&program).is_err());
}

#[test]
fn stack_cycle_vectors_are_rejected() {
    for source in [
        include_str!("vectors/control-flow/audit_negative_depth_cycle/program.xqasm"),
        include_str!("vectors/control-flow/audit_negative_depth_cycle_in_loop/program.xqasm"),
    ] {
        let program = xqasm::assemble_source(source).unwrap();
        assert_eq!(
            verifier::verify(&program).unwrap_err().variant_name(),
            "StackUnderflow"
        );
    }
}
