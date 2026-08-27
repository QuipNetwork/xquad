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

//! Integration tests for `pallet-xqvm`.

use crate::mock::{
    new_test_ext, MaxCalldata, MaxMemoryLimit, MaxProgramSize, MaxStepLimit, RuntimeEvent,
    RuntimeOrigin, Test, XqvmPallet,
};
use crate::{Event, StoredProgram};
use frame_support::{assert_noop, assert_ok, BoundedVec};
use xqvm::{InstructionBuilder, Register};

// ---------------------------------------------------------------------------
// Helpers

fn build_bytecode(build: impl FnOnce(&mut InstructionBuilder)) -> BoundedVec<u8, MaxProgramSize> {
    let mut b = InstructionBuilder::new();
    build(&mut b);
    let bytes = b.build().unwrap().encode();
    BoundedVec::try_from(bytes).unwrap()
}

fn bounded_calldata(vals: Vec<i64>) -> BoundedVec<i64, MaxCalldata> {
    BoundedVec::try_from(vals).unwrap()
}

/// Step budget for a test that is not about the budget.
///
/// Generous enough that no program here reaches it, and named so the
/// zero-budget test below reads as the deliberate contrast it is.
const AMPLE_STEPS: u64 = 1_000;

/// Allocation budget for a test that is not about the budget.
///
/// 1 MiB: more than any fixture program allocates and far less than the
/// 1 GiB `Vm::new()` installs, so every call site passing it is also a
/// standing demonstration that naming a smaller budget is what the
/// argument is for.
const AMPLE_MEMORY: u64 = 1 << 20;

/// Output-slot count for a test that is not about the slot count.
///
/// The caller declares its own arity, so every call site names one. Slots
/// left unwritten stay `Unset` and are dropped from the event's output
/// vec, so a generous count does not change what a test observes.
const AMPLE_SLOTS: u32 = 4;

// ---------------------------------------------------------------------------
// Happy-path tests

/// PUSH 3 PUSH 4 ADD STOW R0 PUSH 0 OUTPUT R0 HALT
/// No calldata; output slot 0 should contain 7.
#[test]
fn arithmetic_program_emits_output() {
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(3)
                .emit_push(4)
                .emit_add()
                .emit_stow(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_ok!(XqvmPallet::submit_program(
            RuntimeOrigin::signed(1),
            bytecode.clone(),
            bounded_calldata(vec![]),
            AMPLE_SLOTS,
            AMPLE_STEPS,
            AMPLE_MEMORY,
        ));

        let events: Vec<_> = frame_system::Pallet::<Test>::events()
            .into_iter()
            .filter_map(|e| {
                if let RuntimeEvent::XqvmPallet(inner) = e.event {
                    Some(inner)
                } else {
                    None
                }
            })
            .collect();
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], Event::ProgramExecuted { who: 1, outputs }
                if outputs.as_slice() == [7_i64]),
            "expected ProgramExecuted {{ who: 1, outputs: [7] }}, got {:?}",
            events[0]
        );

        // Program stored in state.
        assert_eq!(StoredProgram::<Test>::get(), Some(bytecode));
    });
}

/// Calldata passthrough: INPUT R0 from slot 0, OUTPUT R0 to slot 0.
#[test]
fn calldata_passthrough_roundtrip() {
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(0)
                .emit_input(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_ok!(XqvmPallet::submit_program(
            RuntimeOrigin::signed(2),
            bytecode,
            bounded_calldata(vec![42]),
            AMPLE_SLOTS,
            AMPLE_STEPS,
            AMPLE_MEMORY,
        ));

        let outputs = frame_system::Pallet::<Test>::events()
            .into_iter()
            .find_map(|e| {
                if let RuntimeEvent::XqvmPallet(Event::ProgramExecuted { outputs, .. }) = e.event {
                    Some(outputs)
                } else {
                    None
                }
            })
            .expect("ProgramExecuted event not found");

        assert_eq!(outputs.as_slice(), [42_i64]);
    });
}

/// Two calldata values summed and written to output slot 0.
#[test]
fn two_calldata_values_summed() {
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(0).emit_input(Register(0));
            b.emit_push(1).emit_input(Register(1));
            b.emit_load(Register(0));
            b.emit_load(Register(1));
            b.emit_add();
            b.emit_stow(Register(2));
            b.emit_push(0).emit_output(Register(2));
            b.emit_halt();
        });

        assert_ok!(XqvmPallet::submit_program(
            RuntimeOrigin::signed(3),
            bytecode,
            bounded_calldata(vec![10, 20]),
            AMPLE_SLOTS,
            AMPLE_STEPS,
            AMPLE_MEMORY,
        ));

        let outputs = frame_system::Pallet::<Test>::events()
            .into_iter()
            .find_map(|e| {
                if let RuntimeEvent::XqvmPallet(Event::ProgramExecuted { outputs, .. }) = e.event {
                    Some(outputs)
                } else {
                    None
                }
            })
            .expect("ProgramExecuted event not found");

        assert_eq!(outputs.as_slice(), [30_i64]);
    });
}

// ---------------------------------------------------------------------------
// Error-path tests

/// Garbage bytes are rejected before execution starts.
#[test]
fn invalid_bytecode_returns_error() {
    new_test_ext().execute_with(|| {
        let bad: BoundedVec<u8, <Test as crate::Config>::MaxProgramSize> =
            BoundedVec::try_from(vec![0xDE, 0xAD, 0xBE, 0xEF]).unwrap();

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bad,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                AMPLE_STEPS,
                AMPLE_MEMORY,
            ),
            crate::Error::<Test>::BytecodeInvalid,
        );
    });
}

/// ADD with an empty stack faults at runtime.
#[test]
fn stack_underflow_returns_execution_failed() {
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_add().emit_halt();
        });

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bytecode,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                AMPLE_STEPS,
                AMPLE_MEMORY,
            ),
            crate::Error::<Test>::ExecutionFailed,
        );
    });
}

/// Unsigned origin is rejected by `ensure_signed`.
#[test]
fn unsigned_origin_rejected() {
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_halt();
        });

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::none(),
                bytecode,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                AMPLE_STEPS,
                AMPLE_MEMORY,
            ),
            sp_runtime::traits::BadOrigin,
        );
    });
}

/// A zero step budget executes nothing, so the call fails.
#[test]
fn zero_step_budget_returns_execution_failed() {
    // The fixture is the in-repo stand-in for the threat model, and until
    // now it constructed `Vm::new()` and took no budget at all, so its
    // behaviour did not change across the whole hardening series. The bound
    // is exact: a caller who pre-pays for nothing buys nothing, and the
    // program that would have written 7 to slot 0 writes nothing instead of
    // running unmetered.
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(3)
                .emit_push(4)
                .emit_add()
                .emit_stow(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bytecode,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                0,
                AMPLE_MEMORY,
            ),
            crate::Error::<Test>::ExecutionFailed,
        );
    });
}

/// A budget one step short of the program's length still fails.
#[test]
fn a_budget_below_the_program_length_returns_execution_failed() {
    // Seven instructions -- push, push, add, stow, push, output, halt --
    // under a budget of five: the bound is on instructions executed, so the
    // program faults at the sixth (OUTPUT) rather than being truncated into
    // a partial success that emits an event.
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(3)
                .emit_push(4)
                .emit_add()
                .emit_stow(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bytecode,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                5,
                AMPLE_MEMORY,
            ),
            crate::Error::<Test>::ExecutionFailed,
        );
    });
}

/// A budget above `MaxMemoryLimit` is refused rather than granted.
#[test]
fn a_memory_budget_above_the_cap_returns_memory_limit_too_large() {
    // The counterpart to the step-budget cap below, and refused for the
    // same reason: a fixed weight cannot price an unbounded allocation.
    // The program halts in seven steps and allocates nothing, so the cap
    // is the only thing that can reject it.
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(3)
                .emit_push(4)
                .emit_add()
                .emit_stow(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bytecode,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                AMPLE_STEPS,
                u64::MAX,
            ),
            crate::Error::<Test>::MemoryLimitTooLarge,
        );
    });
}

/// The memory cap is inclusive, like the step cap.
#[test]
fn a_memory_budget_exactly_at_the_cap_is_accepted() {
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(3)
                .emit_push(4)
                .emit_add()
                .emit_stow(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_ok!(XqvmPallet::submit_program(
            RuntimeOrigin::signed(1),
            bytecode,
            bounded_calldata(vec![]),
            AMPLE_SLOTS,
            AMPLE_STEPS,
            MaxMemoryLimit::get(),
        ));
    });
}

/// A budget the program outgrows is a reportable fault, not a trap.
#[test]
fn a_memory_budget_shorter_than_the_program_returns_execution_failed() {
    // The case the argument exists for. `BQMX` is charged its declared
    // size before it allocates, so a 4 KiB budget refuses a model that
    // wants far more and the VM returns MemoryLimitExceeded, which the
    // pallet reports as ExecutionFailed.
    //
    // Left at the VM's 1 GiB default this program would instead be
    // admitted, and inside a Wasm runtime the allocation would exhaust the
    // heap and trap the whole execution rather than returning a fault the
    // pallet can map. That is the difference between this test passing and
    // there being no argument to pass.
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(1 << 20).emit_bqmx(Register(0)).emit_halt();
        });

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bytecode,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                AMPLE_STEPS,
                4096,
            ),
            crate::Error::<Test>::ExecutionFailed,
        );
    });
}

/// A budget above `MaxStepLimit` is refused rather than granted.
#[test]
fn a_step_budget_above_the_cap_returns_step_limit_too_large() {
    // The extrinsic's weight is a fixed constant, so an uncapped
    // caller-supplied budget sells arbitrary execution at the price of one
    // call. The cap is what ties the two together; without it the module
    // preamble's "what a caller pre-pays for is what the VM may spend" is
    // only half true. The program below halts in seven steps, so nothing
    // but the cap can be what rejects it.
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(3)
                .emit_push(4)
                .emit_add()
                .emit_stow(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bytecode,
                bounded_calldata(vec![]),
                AMPLE_SLOTS,
                u64::MAX,
                AMPLE_MEMORY,
            ),
            crate::Error::<Test>::StepLimitTooLarge,
        );
    });
}

/// The cap itself is admitted: the bound is inclusive.
#[test]
fn a_step_budget_exactly_at_the_cap_is_accepted() {
    // An off-by-one in the other direction would make MaxStepLimit
    // unreachable, which is a different bug with the same symptom in a
    // fixture whose programs all run in single-digit steps.
    new_test_ext().execute_with(|| {
        let bytecode = build_bytecode(|b| {
            b.emit_push(3)
                .emit_push(4)
                .emit_add()
                .emit_stow(Register(0))
                .emit_push(0)
                .emit_output(Register(0))
                .emit_halt();
        });

        assert_ok!(XqvmPallet::submit_program(
            RuntimeOrigin::signed(1),
            bytecode,
            bounded_calldata(vec![]),
            AMPLE_SLOTS,
            MaxStepLimit::get(),
            AMPLE_MEMORY,
        ));
    });
}

/// One `OUTPUT` inside a loop writes as many slots as the loop has
/// iterations, which is the case the XQBC header byte gets wrong.
///
/// This is the regression test for deriving the slot count from
/// `program.output_slots()`. That byte is a saturating count of `OUTPUT`
/// *instructions* (`xqvm/src/verifier/scan.rs`), so the program below
/// records 1 while legitimately writing slots 0 and 1. Sizing the output
/// vec from it turned the slot-1 write into `OutputIndex` and so into
/// `ExecutionFailed`, rejecting a program the VM and the verifier both
/// accept. `spec/xqvm/ENCODING.md` states that neither header count may be
/// used to pre-size a slot array.
#[test]
fn one_output_in_a_loop_writes_a_slot_per_iteration() {
    new_test_ext().execute_with(|| {
        let bytecode = loop_writing_two_slots();

        // The caller declares 2, which is what the program actually needs.
        assert_ok!(XqvmPallet::submit_program(
            RuntimeOrigin::signed(1),
            bytecode,
            bounded_calldata(vec![]),
            2,
            AMPLE_STEPS,
            AMPLE_MEMORY,
        ));

        let outputs: Vec<_> = frame_system::Pallet::<Test>::events()
            .into_iter()
            .filter_map(|e| match e.event {
                RuntimeEvent::XqvmPallet(crate::Event::ProgramExecuted { outputs, .. }) => {
                    Some(outputs.into_inner())
                }
                _ => None,
            })
            .next()
            .expect("ProgramExecuted");
        assert_eq!(outputs, vec![7, 7]);
    });
}

/// The same program with the count the header byte would have supplied.
#[test]
fn the_header_instruction_count_is_too_small_for_a_looping_output() {
    // Pinning the failure the derivation produced, so the two tests
    // together say what the header byte is and is not good for: the
    // program is unchanged and only the declared count differs.
    new_test_ext().execute_with(|| {
        let bytecode = loop_writing_two_slots();
        assert_eq!(
            xqvm::Program::decode(&bytecode).unwrap().output_slots(),
            1,
            "the header counts OUTPUT instructions, of which there is one"
        );

        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                bytecode,
                bounded_calldata(vec![]),
                1,
                AMPLE_STEPS,
                AMPLE_MEMORY,
            ),
            crate::Error::<Test>::ExecutionFailed,
        );
    });
}

/// A count above `MaxCalldata` is refused before the program is decoded.
#[test]
fn an_output_slot_count_above_the_cap_is_rejected() {
    new_test_ext().execute_with(|| {
        assert_noop!(
            XqvmPallet::submit_program(
                RuntimeOrigin::signed(1),
                loop_writing_two_slots(),
                bounded_calldata(vec![]),
                MaxCalldata::get() + 1,
                AMPLE_STEPS,
                AMPLE_MEMORY,
            ),
            crate::Error::<Test>::OutputSlotsTooLarge,
        );
    });
}

/// `PUSH 0 / PUSH 2 / RANGE ... NEXT` with a single `OUTPUT` in the body,
/// writing 7 to slot 0 and then to slot 1.
fn loop_writing_two_slots() -> BoundedVec<u8, <Test as crate::Config>::MaxProgramSize> {
    build_bytecode(|b| {
        b.emit_push(7).emit_stow(Register(1));
        b.emit_push(0).emit_push(2).emit_range();
        b.emit_l_val(Register(0));
        b.emit_load(Register(0));
        b.emit_output(Register(1));
        b.emit_next();
        b.emit_halt();
    })
}
