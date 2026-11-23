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

//! Checker regression tests.
//!
//! Each module below pins a checker behaviour that was wrong before xquad
//! 0.6.0. The table maps each finding of Aeon's Quip Chain Security Audit
//! (v1.1 final, 2026-11-17; the fixes were reviewed at `4a31c96`, merge request
//! 242) to the tests and vectors that fail if it comes back. The audit's ids
//! appear only here and in the commit messages.
//!
//! | Finding | Behaviour pinned | Fails if it returns |
//! |---|---|---|
//! | QUIP-02 | Register-type analysis terminates on a reachable join where every register is `Any` | `register_cycles`; vector `control-flow/cycle_writing_every_register_terminates`; unit tests `all_register_cycle_obeys_lattice_visit_bound` and `cyclic_programs_terminate_with_the_production_budget_disabled` (`dataflow::register`) |
//! | QUIP-16 | A `DROP` in dead code does not make the checker reject a valid program | `dead_code_init`; vectors `control-flow/dead_drop_does_not_poison_live_code`, `live_read_after_drop_is_unset` and `drop_after_full_init_still_unsets` |
//! | QUIP-17 | A decreasing depth cycle costs work linear in the CFG, inside loops too | unit tests `all_cost_controls_have_linear_exact_height_work` and `restoring_minimum_walks_fails_the_linear_work_guard` (`dataflow::bytecode`); `stack_cycles` and vectors `control-flow/decreasing_depth_cycle_underflows` and `decreasing_depth_cycle_in_loop_underflows` pin the verdicts only, which were already right |
//! | QUIP-19 | A `SCLR` keeps an underflow recorded before it: a second live `SCLR` (part a), or a dead one after `HALT` (part b) | part a: `a_second_sclr_does_not_erase_an_underflow_after_the_first` (`checker::tests`); part b: `sclr_after_halt`. Both were fixed in 0.5.0, before the audit's fix review |
//! | O3 | A block's analysed bytes end at its first terminator | `block_ranges`; vectors `control-flow/dead_write_after_jump_does_not_init` and `dead_read_after_halt_is_not_live` |
//! | O7 | A stack peak inside one block is checked against the limit | `stack_peaks`; vector `control-flow/stack_peak_before_sclr_overflows` |
//!
//! The vectors pin what the VM does with each program; the vector runner does
//! not run the checker. The checker verdicts on the same programs are asserted
//! in the modules here.

mod block_ranges {
    //! Checker block ranges stop at their first terminator.

    #[test]
    fn dead_writes_cannot_initialize_a_live_read() {
        let program = xqasm::assemble_source(include_str!(
            "vectors/control-flow/dead_write_after_jump_does_not_init/program.xqasm"
        ))
        .unwrap();
        assert_eq!(
            xqvm::checker::check(&program).unwrap_err().variant_name(),
            "ReadUnsetRegister"
        );
        assert!(matches!(
            xqvm::Vm::new().run(&program),
            Err(xqvm::Error::UnsetRegister { .. })
        ));
    }

    #[test]
    fn dead_reads_after_halt_do_not_reject_a_live_program() {
        let program = xqasm::assemble_source(include_str!(
            "vectors/control-flow/dead_read_after_halt_is_not_live/program.xqasm"
        ))
        .unwrap();
        assert!(xqvm::checker::check(&program).is_ok());
        assert!(xqvm::Vm::new().run(&program).is_ok());
    }
}

mod stack_peaks {
    //! Every intra-block stack peak must respect the VM limit.

    use xqvm::{Instruction, InstructionBuilder, checker};

    #[test]
    fn balanced_one_block_overflow_is_rejected_but_exact_limit_is_valid() {
        for peak in [8192, 8193] {
            let mut b = InstructionBuilder::new();
            let _ = b.emit_push(1);
            for _ in 1..peak {
                let _ = b.emit(Instruction::Copy {});
            }
            for _ in 0..peak {
                let _ = b.emit_pop();
            }
            let _ = b.emit_halt();
            let program = b.build().unwrap();
            if peak == 8193 {
                assert_eq!(program.code().len(), 16_388);
                assert_eq!(
                    checker::check(&program).unwrap_err().variant_name(),
                    "StackOverflowRisk"
                );
                assert!(matches!(
                    xqvm::Vm::new().run(&program),
                    Err(xqvm::Error::StackOverflow { .. })
                ));
            } else {
                assert!(checker::check(&program).is_ok());
                assert!(xqvm::Vm::new().run(&program).is_ok());
            }
        }
    }

    #[test]
    fn peaks_before_and_after_resets_survive_later_clears() {
        let vector = xqasm::assemble_source(include_str!(
            "vectors/control-flow/stack_peak_before_sclr_overflows/program.xqasm"
        ))
        .unwrap();
        assert_eq!(
            checker::check(&vector).unwrap_err().variant_name(),
            "StackOverflowRisk"
        );
        for reset_first in [false, true] {
            let mut b = InstructionBuilder::new();
            if reset_first {
                let _ = b.emit_push(1).emit_sclr();
            }
            for _ in 0..8193 {
                let _ = b.emit_push(1);
            }
            let _ = b.emit_sclr().emit_halt();
            assert_eq!(
                checker::check(&b.build().unwrap())
                    .unwrap_err()
                    .variant_name(),
                "StackOverflowRisk"
            );
        }
    }

    #[test]
    fn incoming_depth_is_added_to_the_relative_block_peak() {
        let mut b = InstructionBuilder::new();
        for _ in 0..4096 {
            let _ = b.emit_push(1);
        }
        let next = b.label();
        let _ = b.emit_jump(next).place(next).unwrap();
        for _ in 0..4097 {
            let _ = b.emit_push(1);
        }
        let _ = b.emit_sclr().emit_halt();
        assert_eq!(
            checker::check(&b.build().unwrap())
                .unwrap_err()
                .variant_name(),
            "StackOverflowRisk"
        );
    }
}

#[expect(
    unused_results,
    reason = "test builder chains intentionally discard fluent results"
)]
mod stack_cycles {
    //! Stack-depth cycle programs under deterministic first-witness diagnostics.
    //!
    //! Exact-height work is pinned by `all_cost_controls_have_linear_exact_height_work`.
    //! The historical oracle retains the private unit tests
    //! `main_and_loop_body_depth_walks_bound_visits_independently_of_depth`,
    //! `auditor_main_cycle_has_bounded_visits`,
    //! `auditor_loop_cycle_main_walk_has_bounded_visits`, and
    //! `auditor_loop_body_cycle_has_bounded_visits`.

    use xqvm::{Instruction, InstructionBuilder, checker, dataflow::check_stack_depth};

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
    fn first_witnessed_edge_conflict_determines_the_diagnostic() {
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
            "StackDepthMismatch",
            "mixed path into loop opener is witnessed first"
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
        assert_eq!(error(b), "StackDepthMismatch", "ordinary jump into NEXT");

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
    fn cycle_rejection_is_bounded_with_exact_heights() {
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
        let result = checker::check(&program);
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
        assert!(checker::check(&program).is_err());
    }

    #[test]
    fn stack_cycle_vectors_are_rejected() {
        for source in [
            include_str!("vectors/control-flow/decreasing_depth_cycle_underflows/program.xqasm"),
            include_str!(
                "vectors/control-flow/decreasing_depth_cycle_in_loop_underflows/program.xqasm"
            ),
        ] {
            let program = xqasm::assemble_source(source).unwrap();
            assert_eq!(
                checker::check(&program).unwrap_err().variant_name(),
                "StackDepthMismatch"
            );
        }
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test setup and expected rejection assertions"
)]
mod register_cycles {
    //! Register-type analysis converges on cycles that write every register.
    //!
    //! The work bound is pinned by the private unit test
    //! `all_register_cycle_obeys_lattice_visit_bound`, which instruments the
    //! production solver with a synchronous, test-only iteration ceiling.

    use xqvm::{Instruction, InstructionBuilder, Program, Register, checker};

    fn cyclic_program(registers: u16) -> Program {
        let mut b = InstructionBuilder::new();
        let _ = b.emit(Instruction::Nop {});
        let cycle = b.label();
        let _ = b.place(cycle).unwrap();
        let _ = b.emit(Instruction::Target {});
        for slot in 0..registers {
            let _ = b.emit(Instruction::Bsmx {
                reg: Register(u8::try_from(slot).unwrap()),
            });
        }
        let _ = b.emit_jump(cycle).emit_halt();
        b.build().unwrap()
    }

    #[test]
    fn one_unwritten_register_returns() {
        let result = checker::check(&cyclic_program(255));
        assert!(result.is_err());
    }

    #[test]
    fn all_registers_written_returns() {
        let program = cyclic_program(256);
        let vector = xqasm::assemble_source(include_str!(
            "vectors/control-flow/cycle_writing_every_register_terminates/program.xqasm"
        ))
        .unwrap();
        assert!(checker::check(&program).is_err());
        assert!(checker::check(&vector).is_err());
    }
}

mod dead_code_init {
    //! Dead writes and dead reads must not affect live-read must-init checks.

    use xqvm::bytecode::codec;
    use xqvm::dataflow::uninit::check_uninit_registers;
    use xqvm::{Instruction, Program, Register, checker};

    fn program(instructions: &[Instruction]) -> Program {
        Program::new(instructions.iter().flat_map(codec::encode).collect())
    }

    #[test]
    fn dead_drop_must_not_reject_live_halt() {
        // The sole live path stops at the first HALT. The DROP and LOAD are in
        // separate CFG nodes after it and cannot execute.
        let dead = program(&[
            Instruction::Halt {},
            Instruction::Target {},
            Instruction::Drop { reg: Register(0) },
            Instruction::Jump2 { label: 1 },
            Instruction::Target {},
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);

        // The same DROP/LOAD sequence on a live path must remain rejected.
        let live = program(&[
            Instruction::Target {},
            Instruction::Drop { reg: Register(0) },
            Instruction::Jump2 { label: 1 },
            Instruction::Target {},
            Instruction::Load { reg: Register(0) },
            Instruction::Halt {},
        ]);
        assert_eq!(
            check_uninit_registers(&live).unwrap_err().variant_name(),
            "ReadUnsetRegister"
        );
        assert_eq!(
            checker::check(&live).unwrap_err().variant_name(),
            "ReadUnsetRegister"
        );

        let narrow = check_uninit_registers(&dead);
        let full = checker::check(&dead);
        assert!(narrow.is_ok(), "must-init rejected dead code: {narrow:?}");
        assert!(full.is_ok(), "full checker rejected dead code: {full:?}");
    }

    #[test]
    fn reachability_vectors_pin_must_init_and_full_checker() {
        for (source, expected) in [
            (
                include_str!(
                    "vectors/control-flow/dead_drop_does_not_poison_live_code/program.xqasm"
                ),
                None,
            ),
            (
                include_str!("vectors/control-flow/live_read_after_drop_is_unset/program.xqasm"),
                Some("ReadUnsetRegister"),
            ),
            (
                include_str!(
                    "vectors/control-flow/drop_after_full_init_still_unsets/program.xqasm"
                ),
                Some("ReadUnsetRegister"),
            ),
        ] {
            let program = xqasm::assemble_source(source).unwrap();
            assert_eq!(
                check_uninit_registers(&program)
                    .err()
                    .map(|e| e.variant_name()),
                expected
            );
            assert_eq!(
                checker::check(&program).err().map(|e| e.variant_name()),
                expected
            );
        }
    }
}

mod sclr_after_halt {
    //! A `SCLR` after `HALT` is dead and cannot erase an underflow on the live
    //! path before it.
    //!
    //! Two rules each keep this true: a `SCLR` never clears a recorded
    //! underflow, and a block's scan stops at its first terminator. The test
    //! fails only when both are lost, as in xqvm 0.4.0.

    use xqvm::{Error, Program, Vm, checker};

    /// `SCLR, POP, HALT, SCLR`: the live `POP` underflows, and the trailing
    /// `SCLR` never runs.
    #[test]
    fn dead_sclr_after_halt_keeps_the_live_underflow() {
        let program = Program::new(vec![0x1a, 0x10, 0xff, 0x1a]);
        assert!(matches!(
            Vm::new().run(&program),
            Err(Error::StackUnderflow { .. })
        ));
        assert!(
            checker::check(&program).is_err(),
            "checker accepted a live POP underflow"
        );
    }

    /// Controls: the same underflow without the dead `SCLR` is rejected, and
    /// a dead `SCLR` after a clean `HALT` is accepted.
    #[test]
    fn dead_sclr_controls() {
        let live_underflow = Program::new(vec![0x1a, 0x10, 0xff]);
        assert!(checker::check(&live_underflow).is_err());
        assert!(matches!(
            Vm::new().run(&live_underflow),
            Err(Error::StackUnderflow { .. })
        ));

        let safe_halt = Program::new(vec![0x1a, 0xff, 0x1a]);
        assert!(checker::check(&safe_halt).is_ok());
        assert!(Vm::new().run(&safe_halt).is_ok());
    }
}
