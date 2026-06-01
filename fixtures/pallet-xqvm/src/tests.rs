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
    new_test_ext, MaxCalldata, MaxProgramSize, RuntimeEvent, RuntimeOrigin, Test, XqvmPallet,
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
            XqvmPallet::submit_program(RuntimeOrigin::signed(1), bad, bounded_calldata(vec![])),
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
            ),
            sp_runtime::traits::BadOrigin,
        );
    });
}
