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

//! Integration tests for the XQVM bytecode interpreter.

#![expect(
    unused_results,
    clippy::expect_used,
    reason = "test helpers - builder results and panics on failure are intentional"
)]

use xqvm::bytecode::{InstructionBuilder, Register};
use xqvm::{Domain, Error, Instruction, RegVal, Vm};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build bytecode and run it on a fresh VM; return the VM.
fn run(build: impl FnOnce(&mut InstructionBuilder)) -> Vm {
    let mut b = InstructionBuilder::new();
    build(&mut b);
    let bytecode = b.build().expect("builder build");
    let mut vm = Vm::new();
    vm.run(&bytecode).expect("vm run");
    vm
}

/// Build bytecode and run it on a fresh VM; expect an error.
fn run_err(build: impl FnOnce(&mut InstructionBuilder)) -> Error {
    let mut b = InstructionBuilder::new();
    build(&mut b);
    let bytecode = b.build().expect("builder build");
    let mut vm = Vm::new();
    vm.run(&bytecode).expect_err("expected error")
}

// ---------------------------------------------------------------------------
// Arithmetic
// ---------------------------------------------------------------------------

#[test]
fn add_two_numbers() {
    let vm = run(|b| {
        b.emit_push(3).emit_push(4).emit_add().emit_halt();
    });
    assert_eq!(vm.stack(), &[7]);
}

#[test]
fn sub_two_numbers() {
    let vm = run(|b| {
        b.emit_push(10).emit_push(3).emit_sub().emit_halt();
    });
    assert_eq!(vm.stack(), &[7]);
}

#[test]
fn mul_two_numbers() {
    let vm = run(|b| {
        b.emit_push(6).emit_push(7).emit_mul().emit_halt();
    });
    assert_eq!(vm.stack(), &[42]);
}

#[test]
fn div_two_numbers() {
    let vm = run(|b| {
        b.emit_push(21).emit_push(3).emit_div().emit_halt();
    });
    assert_eq!(vm.stack(), &[7]);
}

#[test]
fn div_positive_operands_uses_floor() {
    let vm = run(|b| {
        b.emit_push(7).emit_push(2).emit_div().emit_halt();
    });
    assert_eq!(vm.stack(), &[3]);
}

#[test]
fn modulo_basic() {
    let vm = run(|b| {
        b.emit_push(17).emit_push(5).emit_modulo().emit_halt();
    });
    assert_eq!(vm.stack(), &[2]);
}

#[test]
fn neg_positive() {
    let vm = run(|b| {
        b.emit_push(42).emit_neg().emit_halt();
    });
    assert_eq!(vm.stack(), &[-42]);
}

#[test]
fn neg_negative() {
    let vm = run(|b| {
        b.emit_push(-7).emit_neg().emit_halt();
    });
    assert_eq!(vm.stack(), &[7]);
}

#[test]
fn add_past_i64_max_raises() {
    let err = run_err(|b| {
        b.emit_push(i64::MAX).emit_push(1).emit_add().emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

// ---------------------------------------------------------------------------
// Comparison operators
// ---------------------------------------------------------------------------

#[test]
fn eq_equal() {
    let vm = run(|b| {
        b.emit_push(5).emit_push(5).emit_eq().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn eq_not_equal() {
    let vm = run(|b| {
        b.emit_push(5).emit_push(6).emit_eq().emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn lt_true() {
    let vm = run(|b| {
        b.emit_push(3).emit_push(5).emit_lt().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn lt_false() {
    let vm = run(|b| {
        b.emit_push(5).emit_push(3).emit_lt().emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn gt_true() {
    let vm = run(|b| {
        b.emit_push(5).emit_push(3).emit_gt().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn lte_equal() {
    let vm = run(|b| {
        b.emit_push(5).emit_push(5).emit_lte().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn gte_greater() {
    let vm = run(|b| {
        b.emit_push(6).emit_push(5).emit_gte().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

// ---------------------------------------------------------------------------
// Logical and bitwise operators
// ---------------------------------------------------------------------------

#[test]
fn not_zero() {
    let vm = run(|b| {
        b.emit_push(0).emit_not().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn not_nonzero() {
    let vm = run(|b| {
        b.emit_push(99).emit_not().emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn and_both_nonzero() {
    let vm = run(|b| {
        b.emit_push(1).emit_push(1).emit_and().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn and_one_zero() {
    let vm = run(|b| {
        b.emit_push(1).emit_push(0).emit_and().emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn or_one_nonzero() {
    let vm = run(|b| {
        b.emit_push(0).emit_push(1).emit_or().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn xor_different() {
    let vm = run(|b| {
        b.emit_push(1).emit_push(0).emit_xor().emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn xor_same() {
    let vm = run(|b| {
        b.emit_push(1).emit_push(1).emit_xor().emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn band_basic() {
    let vm = run(|b| {
        b.emit_push(0b1100)
            .emit_push(0b1010)
            .emit_b_and()
            .emit_halt();
    });
    assert_eq!(vm.stack(), &[0b1000]);
}

#[test]
fn bor_basic() {
    let vm = run(|b| {
        b.emit_push(0b1100)
            .emit_push(0b1010)
            .emit_b_or()
            .emit_halt();
    });
    assert_eq!(vm.stack(), &[0b1110]);
}

#[test]
fn bxor_basic() {
    let vm = run(|b| {
        b.emit_push(0b1100)
            .emit_push(0b1010)
            .emit_b_xor()
            .emit_halt();
    });
    assert_eq!(vm.stack(), &[0b0110]);
}

#[test]
fn bnot_basic() {
    let vm = run(|b| {
        b.emit_push(0i64).emit_b_not().emit_halt();
    });
    assert_eq!(vm.stack(), &[-1i64]);
}

#[test]
fn shl_basic() {
    let vm = run(|b| {
        b.emit_push(1).emit_push(4).emit_shl().emit_halt();
    });
    assert_eq!(vm.stack(), &[16]);
}

#[test]
fn shr_basic() {
    // Arithmetic right shift on a positive value matches integer division by 2^b.
    let vm = run(|b| {
        b.emit_push(16i64).emit_push(2).emit_shr().emit_halt();
    });
    assert_eq!(vm.stack(), &[4]);
}

#[test]
fn shr_preserves_sign_on_negative() {
    // `-1 >> 1` is `-1` under arithmetic shift: every bit is set, sign-extended.
    let vm = run(|b| {
        b.emit_push(-1i64).emit_push(1).emit_shr().emit_halt();
    });
    assert_eq!(vm.stack(), &[-1]);
}

#[test]
fn shr_arithmetic_negative_value() {
    // `-8 >> 1` == `-4`: the sign bit is replicated.
    let vm = run(|b| {
        b.emit_push(-8i64).emit_push(1).emit_shr().emit_halt();
    });
    assert_eq!(vm.stack(), &[-4]);
}

#[test]
fn shr_i64_min_does_not_overflow() {
    // Arithmetic SHR halves `i64::MIN` instead of producing `i64::MAX` (which
    // is what the old logical-shift implementation returned).
    let vm = run(|b| {
        b.emit_push(i64::MIN).emit_push(1).emit_shr().emit_halt();
    });
    assert_eq!(vm.stack(), &[i64::MIN / 2]);
}

// ---------------------------------------------------------------------------
// Stack operations
// ---------------------------------------------------------------------------

#[test]
fn push_pop() {
    let vm = run(|b| {
        b.emit_push(42).emit_pop().emit_halt();
    });
    assert!(vm.stack().is_empty());
}

#[test]
fn copy_duplicates_top() {
    let vm = run(|b| {
        b.emit_push(7).emit_copy().emit_halt();
    });
    assert_eq!(vm.stack(), &[7, 7]);
}

#[test]
fn swap_swaps_top_two() {
    let vm = run(|b| {
        b.emit_push(1).emit_push(2).emit_swap().emit_halt();
    });
    assert_eq!(vm.stack(), &[2, 1]);
}

// ---------------------------------------------------------------------------
// Register load/store
// ---------------------------------------------------------------------------

#[test]
fn stow_and_load() {
    let vm = run(|b| {
        b.emit_push(99)
            .emit_stow(Register(0))
            .emit_load(Register(0))
            .emit_halt();
    });
    assert_eq!(vm.stack(), &[99]);
}

#[test]
fn load_on_never_written_register_faults() {
    let err = run_err(|b| {
        b.emit_load(Register(5)).emit_halt();
    });
    assert!(
        matches!(err, Error::UnsetRegister { reg: 5, .. }),
        "expected UnsetRegister for reg 5, got {err:?}",
    );
}

// ---------------------------------------------------------------------------
// Unconditional and conditional jumps
// ---------------------------------------------------------------------------

#[test]
fn unconditional_jump_skips_code() {
    // PUSH 1 / JUMP over PUSH 99 / PUSH 2 / HALT
    let mut b = InstructionBuilder::new();
    let skip = b.label();
    b.emit_push(1)
        .emit_jump(skip)
        .emit_push(99)
        .place(skip)
        .unwrap()
        .emit_push(2)
        .emit_halt();
    let bytecode = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&bytecode).unwrap();
    // stack should be [1, 2] -- 99 was skipped
    assert_eq!(vm.stack(), &[1, 2]);
}

#[test]
fn conditional_jump_taken() {
    let mut b = InstructionBuilder::new();
    let done = b.label();
    b.emit_push(1)
        .emit_jump_if(done)
        .emit_push(99)
        .place(done)
        .unwrap()
        .emit_push(2)
        .emit_halt();
    let bytecode = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&bytecode).unwrap();
    assert_eq!(vm.stack(), &[2]);
}

#[test]
fn conditional_jump_not_taken() {
    let mut b = InstructionBuilder::new();
    let done = b.label();
    b.emit_push(0)
        .emit_jump_if(done)
        .emit_push(99)
        .place(done)
        .unwrap()
        .emit_push(2)
        .emit_halt();
    let bytecode = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&bytecode).unwrap();
    assert_eq!(vm.stack(), &[99, 2]);
}

// ---------------------------------------------------------------------------
// RANGE loop counting from 0 to N, accumulating a sum
// ---------------------------------------------------------------------------

#[test]
fn range_loop_sum_0_to_5() {
    // RANGE loop: sum = 0+1+2+3+4 = 10 (range [0, 5)).
    // The loop body executes once per value in [0, 5).
    let mut b = InstructionBuilder::new();

    b.emit_push(0).emit_stow(Register(0)); // r0 = 0 (accumulator)
    b.emit_push(0).emit_push(5).emit_range(); // RANGE [0, 5)
    b.emit_l_val(Register(1)); // r1 = current loop value
    b.emit_load(Register(1))
        .emit_load(Register(0))
        .emit_add()
        .emit_stow(Register(0)); // r0 += r1
    b.emit_next();
    b.emit_load(Register(0)).emit_halt();

    let bytecode = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&bytecode).unwrap();
    assert_eq!(vm.stack(), &[10]);
}

#[test]
fn range_loop_zero_count_skips_body() {
    // RANGE with count=0: body is skipped entirely (no do-while).
    // The VM scans forward to the matching NEXT without pushing a loop frame.
    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_push(0).emit_range();
    b.emit_push(99); // body: never runs
    b.emit_next();
    b.emit_halt();
    let bytecode = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&bytecode).unwrap();
    assert_eq!(vm.stack(), &[]);
}

#[test]
fn range_checks_its_exclusive_bound() {
    // `end = start + count` was a `wrapping_add`, so this program ran one
    // iteration here and three on xqvm_py, with different register contents
    // and different step counts -- and step count is metering on a chain
    // that prices per step. `SPEC.md` ranges its overflow rule over every
    // i64 operation the VM performs on a program's behalf; loop control is
    // not carved out of it.
    let err = run_err(|b| {
        b.emit_push(i64::MAX - 1).emit_push(3).emit_range();
        b.emit_lidx(Register(0));
        b.emit_next().emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn range_at_the_exact_upper_bound_still_runs() {
    // The boundary the check must not move: `start + count == i64::MAX` is
    // representable, so the loop runs its one iteration and `current`
    // reaches `i64::MAX - 1`.
    let vm = run(|b| {
        b.emit_push(i64::MAX - 1).emit_push(1).emit_range();
        b.emit_lidx(Register(0)).emit_load(Register(0));
        b.emit_next().emit_halt();
    });
    assert_eq!(vm.stack(), &[i64::MAX - 1]);
}

#[test]
fn next_cannot_advance_a_range_past_the_representable_range() {
    // `NEXT`'s counter advance was a bare `*current += 1`, which panicked
    // under a profile with overflow checks and wrapped without them. It is
    // checked now. With `RANGE`'s bound checked, `current + 1 <= end <=
    // i64::MAX` holds for every frame the VM can build, so the shortest
    // program that used to reach the panic is rejected one instruction
    // earlier -- which is what this asserts.
    let err = run_err(|b| {
        b.emit_push(i64::MAX).emit_push(1).emit_range();
        b.emit_next().emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn slack_checks_its_index_arithmetic() {
    // `vec.push(start_index + i)` was bare. `start_index` comes straight off
    // the value stack, so the second iteration leaves the range: the program
    // charges 64 bytes and passes at any default budget, then panics under
    // `ci-test`, wraps under `release`, and stored 2^63 on xqvm_py. No
    // vector was anywhere near the boundary.
    let err = run_err(|b| {
        b.emit_vec_i(Register(0));
        b.emit_vec_i(Register(1));
        b.emit_push(i64::MAX).emit_push(3);
        b.emit(Instruction::Slack {
            indices: Register(0),
            coeffs: Register(1),
        });
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn slack_at_the_exact_upper_bound_still_appends() {
    // The boundary: `capacity = 1` appends exactly one entry, so
    // `start_index + 0 == i64::MAX` is the largest index SLACK can write and
    // must not be rejected.
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        b.emit_vec_i(Register(1));
        b.emit_push(i64::MAX).emit_push(1);
        b.emit(Instruction::Slack {
            indices: Register(0),
            coeffs: Register(1),
        });
        b.emit_halt();
    });
    assert_eq!(*vm.register(0), RegVal::VecInt(vec![i64::MAX]));
    assert_eq!(*vm.register(1), RegVal::VecInt(vec![1]));
}

#[test]
fn slack_rejecting_coeffs_leaves_indices_unmutated() {
    // `coeffs` used to be discriminated only after the index loop had
    // already appended to `indices`, so a `SLACK` that faults grew a
    // register on its way out. Both registers are resolved before either is
    // written now.
    //
    // The conformance vector of the same name pins the fault identity; only
    // a crate-local test can read the register afterwards, which is the half
    // that discriminates the ordering.
    let mut b = InstructionBuilder::new();
    b.emit_vec_i(Register(0));
    b.emit_push(7).emit_stow(Register(1));
    b.emit_push(0).emit_push(3);
    b.emit(Instruction::Slack {
        indices: Register(0),
        coeffs: Register(1),
    });
    b.emit_halt();
    let bytecode = b.build().expect("builder build");
    let mut vm = Vm::new();
    let err = vm.run(&bytecode).expect_err("expected error");
    assert!(
        matches!(err, Error::RegisterType { reg: 1, .. }),
        "expected RegisterType on r1, got {err:?}"
    );
    assert_eq!(
        *vm.register(0),
        RegVal::VecInt(vec![]),
        "a faulting SLACK must not have appended to its indices register"
    );
}

// ---------------------------------------------------------------------------
// ITER loop over a vec
// ---------------------------------------------------------------------------

#[test]
fn iter_loop_over_vec_int() {
    // Build [10, 20, 30] in r0, then ITER over the full slice [0, 3) and sum
    // elements into r1.
    let mut b = InstructionBuilder::new();

    b.emit_vec_i(Register(0)); // r0 = []
    b.emit_push(10).emit_vec_push(Register(0));
    b.emit_push(20).emit_vec_push(Register(0));
    b.emit_push(30).emit_vec_push(Register(0));

    b.emit_push(0).emit_stow(Register(1)); // r1 = 0 (accumulator)
    b.emit_push(0).emit_push(3).emit_iter(Register(0)); // ITER r0 over [0, 3)
    b.emit_l_val(Register(2)); // r2 = current element
    b.emit_load(Register(2))
        .emit_load(Register(1))
        .emit_add()
        .emit_stow(Register(1));
    b.emit_next();
    b.emit_load(Register(1)).emit_halt();

    let bytecode = b.build().unwrap();
    let mut vm = Vm::new();
    vm.run(&bytecode).unwrap();
    assert_eq!(vm.stack(), &[60]);
}

#[test]
fn iter_loop_over_slice_skips_outside_range() {
    // Iterate vec[1..3] of [10, 20, 30, 40, 50], summing 20 + 30 + 40 = 90.
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        for v in [10, 20, 30, 40, 50] {
            b.emit_push(v).emit_vec_push(Register(0));
        }
        b.emit_push(0).emit_stow(Register(1));
        b.emit_push(1).emit_push(4).emit_iter(Register(0));
        b.emit_l_val(Register(2));
        b.emit_load(Register(2))
            .emit_load(Register(1))
            .emit_add()
            .emit_stow(Register(1));
        b.emit_next();
        b.emit_load(Register(1)).emit_halt();
    });
    assert_eq!(vm.stack(), &[20 + 30 + 40]);
}

#[test]
fn iter_lval_uses_slice_copy_not_source_vec() {
    // ITER copies the slice, so mutating the source vec inside the body
    // must not be visible to subsequent LVAL calls. Set vec[0] = 999 in
    // the first iteration and check that the second iteration still sees
    // the original 20.
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(10).emit_vec_push(Register(0));
        b.emit_push(20).emit_vec_push(Register(0));
        b.emit_push(0).emit_push(2).emit_iter(Register(0));
        b.emit_l_val(Register(1));
        b.emit_load(Register(1));
        // Overwrite vec[0] to 999 mid-loop.
        b.emit_push(0).emit_push(999).emit_vec_set(Register(0));
        b.emit_next();
        b.emit_halt();
    });
    // First iteration should yield 10, second should yield 20 (not 999),
    // so the stack ends up as [10, 20].
    assert_eq!(vm.stack(), &[10, 20]);
}

#[test]
fn iter_rejects_negative_start() {
    let err = run_err(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(1).emit_vec_push(Register(0));
        b.emit_push(-1).emit_push(1).emit_iter(Register(0));
        b.emit_next().emit_halt();
    });
    assert!(
        matches!(err, Error::IndexOutOfBounds { index: -1, .. }),
        "expected IndexOutOfBounds with index=-1, got {err:?}"
    );
}

#[test]
fn iter_rejects_end_past_len() {
    let err = run_err(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(1).emit_vec_push(Register(0));
        b.emit_push(0).emit_push(5).emit_iter(Register(0));
        b.emit_next().emit_halt();
    });
    assert!(
        matches!(err, Error::IndexOutOfBounds { index: 5, .. }),
        "expected IndexOutOfBounds with index=5, got {err:?}"
    );
}

#[test]
fn iter_skips_an_inverted_range() {
    // The empty-loop-skip clause in spec/xqvm/ISA.md is stated as
    // `start_idx >= end_idx`, so an inverted range is empty rather than
    // erroneous: the body is skipped and execution resumes after NEXT.
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(1).emit_vec_push(Register(0));
        b.emit_push(2).emit_vec_push(Register(0));
        b.emit_push(0).emit_stow(Register(1));
        b.emit_push(2).emit_push(1).emit_iter(Register(0));
        b.emit_load(Register(1)).emit_inc().emit_stow(Register(1));
        b.emit_next().emit_halt();
    });
    assert_eq!(*vm.register(1), RegVal::Int(0), "body must not run");
}

#[test]
fn iter_resolves_its_register_before_it_skips() {
    // `ITER` carries a `read` effect on its register that the verifier's
    // static type system depends on, so an empty slice does not excuse the
    // VM from resolving the register: an `ITER` whose register does not hold
    // a vec raises whether or not the body would have run. Returning
    // `SkipLoop` first made a stored program that aborted with a
    // register-type fault on 0.3.x complete and write outputs.
    let err = run_err(|b| {
        b.emit_push(7).emit_stow(Register(0));
        b.emit_push(1).emit_push(1).emit_iter(Register(0));
        b.emit_next().emit_halt();
    });
    assert!(
        matches!(err, Error::RegisterType { reg: 0, .. }),
        "expected RegisterType, got {err:?}"
    );
}

#[test]
fn iter_over_an_unset_register_raises_even_when_the_slice_is_empty() {
    // The same ordering rule for the other non-vec kind reachable here.
    // `Unset` is what a register holds before anything writes it, so this is
    // the shortest program in the class.
    //
    // The identity is `UnsetRegister`, not `RegisterType`: the two are
    // distinct faults -- `UnsetRegister` is what `xqvm_py`'s
    // `RegisterNotFound` maps onto and `RegisterType` is what its
    // `TypeMismatch` maps onto -- so folding "never written" into the type
    // arm made this program fault `TYPE_MISMATCH` here and `UNSET_REGISTER`
    // on the Python VM. Pinned across implementations by the
    // `iter_unset_register` conformance vector.
    let err = run_err(|b| {
        b.emit_push(0).emit_push(0).emit_iter(Register(3));
        b.emit_next().emit_halt();
    });
    assert!(
        matches!(err, Error::UnsetRegister { reg: 3, .. }),
        "expected UnsetRegister, got {err:?}"
    );
}

#[test]
fn lidx_in_range_loop_returns_current_value() {
    // Iterate 5..8 and capture LIDX into r0 each step. The first iteration
    // is observable on the stack via LOAD r0 before NEXT runs.
    let vm = run(|b| {
        b.emit_push(5).emit_push(3).emit_range();
        b.emit_lidx(Register(0));
        b.emit_load(Register(0));
        b.emit_next();
        b.emit_halt();
    });
    // Stack accumulates the LIDX values for each iteration in order.
    assert_eq!(vm.stack(), &[5, 6, 7]);
}

#[test]
fn lidx_matches_lval_in_range_loop() {
    // For RANGE loops the spec says LIDX is equivalent to LVAL because the
    // loop values are themselves indices. Verify by capturing both and
    // comparing.
    let vm = run(|b| {
        b.emit_push(10).emit_push(3).emit_range();
        b.emit_lidx(Register(0));
        b.emit_l_val(Register(1));
        b.emit_load(Register(0));
        b.emit_load(Register(1));
        b.emit_next();
        b.emit_halt();
    });
    // Each iteration pushes [LIDX, LVAL]; the identity holds for every
    // iteration in [10, 11, 12].
    assert_eq!(vm.stack(), &[10, 10, 11, 11, 12, 12]);
}

#[test]
fn lidx_in_iter_loop_returns_position() {
    // For ITER loops LIDX returns the current position within the *source*
    // vec, i.e. start_offset + index. Iterate the full vec so positions
    // start at 0.
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(100).emit_vec_push(Register(0));
        b.emit_push(200).emit_vec_push(Register(0));
        b.emit_push(300).emit_vec_push(Register(0));
        b.emit_push(0).emit_push(3).emit_iter(Register(0));
        b.emit_lidx(Register(1));
        b.emit_load(Register(1));
        b.emit_next();
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[0, 1, 2]);
}

#[test]
fn lidx_in_iter_slice_reports_absolute_position() {
    // Iterating vec[2..5] should give LIDX values 2, 3, 4 -- the original
    // vec positions, not 0, 1, 2.
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        for v in [10, 20, 30, 40, 50] {
            b.emit_push(v).emit_vec_push(Register(0));
        }
        b.emit_push(2).emit_push(5).emit_iter(Register(0));
        b.emit_lidx(Register(1));
        b.emit_load(Register(1));
        b.emit_next();
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[2, 3, 4]);
}

#[test]
fn lidx_outside_loop_errors() {
    // No active loop -> LIDX must surface NoActiveLoop, mirroring how
    // LVAL/NEXT behave.
    let err = run_err(|b| {
        b.emit_lidx(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::NoActiveLoop { .. }),
        "expected NoActiveLoop, got {err:?}"
    );
}

// ---------------------------------------------------------------------------
// VEC/VECI operations
// ---------------------------------------------------------------------------

#[test]
fn vec_push_get_set_len() {
    // Stack order for VECSET: pop value first, then index.
    // So to set vec[0]=999, push idx=0 first, then val=999.
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(100).emit_vec_push(Register(0));
        b.emit_push(200).emit_vec_push(Register(0));
        b.emit_push(300).emit_vec_push(Register(0));
        // len should be 3
        b.emit_vec_len(Register(0));
        // get index 1 -> 200
        b.emit_push(1).emit_vec_get(Register(0));
        // set index 0 to 999: push idx first, val second
        b.emit_push(0).emit_push(999).emit_vec_set(Register(0));
        // get index 0 -> 999
        b.emit_push(0).emit_vec_get(Register(0));
        b.emit_halt();
    });
    // stack should be [3, 200, 999]
    assert_eq!(vm.stack(), &[3, 200, 999]);
}

// ---------------------------------------------------------------------------
// XQMX model allocation (BQMX), setline, getline, setquad, getquad
//
// Stack convention for model instructions (pops from top):
//   SETLINE:  pop val (top), pop i  -> set linear[i] = val
//   ADDLINE:  pop delta (top), pop i -> add delta to linear[i]
//   GETLINE:  pop i -> push linear[i]
//   SETQUAD:  pop val (top), pop j, pop i -> set quad[i,j] = val
//   ADDQUAD:  pop delta (top), pop j, pop i -> add delta to quad[i,j]
//   GETQUAD:  pop j (top), pop i -> push quad[i,j]
// ---------------------------------------------------------------------------

#[test]
fn bqmx_setline_getline() {
    // To set linear[2]=7: push i=2 first, then val=7 on top.
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0)); // r0 = QUBO(4)
        // setline: i=2, val=7 -- push i first, val on top
        b.emit_push(2).emit_push(7).emit_set_line(Register(0));
        // getline: i=2 -> 7
        b.emit_push(2).emit_get_line(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[7]);
}

#[test]
fn bqmx_addline() {
    // set linear[0]=5, then add 3: linear[0] should be 8.
    // SETLINE: push i=0, push val=5. ADDLINE: push i=0, push delta=3.
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(0).emit_push(5).emit_set_line(Register(0));
        b.emit_push(0).emit_push(3).emit_add_line(Register(0));
        b.emit_push(0).emit_get_line(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[8]);
}

#[test]
fn bqmx_setquad_getquad() {
    // To set quad[1,2]=-1: push i=1, j=2, val=-1 (val on top).
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        // setquad: i=1, j=2, val=-1 -- push i, j, val
        b.emit_push(1)
            .emit_push(2)
            .emit_push(-1)
            .emit_set_quad(Register(0));
        // getquad: pop j=2 (top), pop i=1 -> push quad[1,2]=-1
        b.emit_push(2).emit_push(1).emit_get_quad(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[-1]);
}

#[test]
fn bqmx_addquad() {
    // set quad[0,1]=3, add 2: quad[0,1] should be 5.
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(0)
            .emit_push(1)
            .emit_push(3)
            .emit_set_quad(Register(0));
        b.emit_push(0)
            .emit_push(1)
            .emit_push(2)
            .emit_add_quad(Register(0));
        b.emit_push(1).emit_push(0).emit_get_quad(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[5]);
}

#[test]
fn getline_absent_returns_zero() {
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(3).emit_get_line(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

// ---------------------------------------------------------------------------
// ENERGY computation
// ---------------------------------------------------------------------------

#[test]
fn energy_simple_qubo_all_zero_sample() {
    // Model: H(x) = -x0 + 2*x0*x1
    // Sample: [0, 0] (BSMX default) -> H = 0
    let vm = run(|b| {
        b.emit_push(2).emit_bqmx(Register(0));
        // set linear[0] = -1: push i=0, val=-1
        b.emit_push(0).emit_push(-1).emit_set_line(Register(0));
        // set quad[0,1] = 2: push i=0, j=1, val=2
        b.emit_push(0)
            .emit_push(1)
            .emit_push(2)
            .emit_set_quad(Register(0));
        // BSMX creates sample initialized to [0, 0]
        b.emit_push(2).emit_bsmx(Register(1));
        b.emit_energy(Register(0), Register(1));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn energy_spin_ising_all_minus_one() {
    // Model: H(s) = s0*s1, sample = [-1, -1] -> H = (-1)*(-1) = 1
    let vm = run(|b| {
        b.emit_push(2).emit_sqmx(Register(0));
        // set quad[0,1] = 1: push i=0, j=1, val=1
        b.emit_push(0)
            .emit_push(1)
            .emit_push(1)
            .emit_set_quad(Register(0));
        // SSMX creates sample initialized to [-1, -1]
        b.emit_push(2).emit_ssmx(Register(1));
        b.emit_energy(Register(0), Register(1));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[1]);
}

#[test]
fn energy_rejects_model_in_sample_slot() {
    // Both slots hold a Model; ENERGY must reject because xq-py requires
    // the second register to hold an XQMX in SAMPLE mode (which xq-rs
    // models as RegVal::Sample). The shortcut that allowed Model-as-sample
    // was removed in QUI-410.
    let err = run_err(|b| {
        b.emit_push(2).emit_bqmx(Register(0));
        b.emit_push(2).emit_bqmx(Register(1));
        b.emit_energy(Register(0), Register(1));
        b.emit_halt();
    });
    match err {
        Error::RegisterType {
            reg: 1,
            expected: "sample",
            got: "model",
        } => {}
        other => panic!(
            "expected RegisterType {{ expected: \"sample\", got: \"model\" }}, got {other:?}"
        ),
    }
}

#[test]
fn energy_rejects_int_in_sample_slot() {
    // Non-XQMX values in the sample slot still produce a clear RegisterType
    // error.
    let err = run_err(|b| {
        b.emit_push(2).emit_bqmx(Register(0));
        b.emit_push(0).emit_stow(Register(1));
        b.emit_energy(Register(0), Register(1));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::RegisterType {
                reg: 1,
                expected: "sample",
                ..
            }
        ),
        "expected RegisterType expected=sample, got {err:?}"
    );
}

// ---------------------------------------------------------------------------
// INPUT/OUTPUT
// ---------------------------------------------------------------------------

#[test]
fn input_output_roundtrip() {
    let mut b = InstructionBuilder::new();
    // Input slot 0 into r0, output r0 to slot 0.
    b.emit_push(0).emit_input(Register(0));
    b.emit_push(0).emit_output(Register(0));
    b.emit_halt();
    let bytecode = b.build().unwrap();

    let mut vm = Vm::new();
    vm.set_calldata(vec![RegVal::Int(42)]).set_output_slots(4);
    vm.run(&bytecode).unwrap();
    assert_eq!(vm.outputs()[0], RegVal::Int(42));
}

// ---------------------------------------------------------------------------
// IDXGRID and IDXTRIU
// ---------------------------------------------------------------------------

#[test]
fn idx_grid_basic() {
    // IDXGRID pops cols, col, row: row=1, col=2, cols=4 -> 1*4+2 = 6
    // Push row first (bottom), then col, then cols (top)
    let vm = run(|b| {
        b.emit_push(1)
            .emit_push(2)
            .emit_push(4)
            .emit_idx_grid()
            .emit_halt();
    });
    assert_eq!(vm.stack(), &[6]);
}

#[test]
fn idx_triu_basic() {
    // IDXTRIU pops j (top), then i: i=1, j=3 -> 3*2/2 + 1 = 4
    // Push i first, j second (j on top)
    let vm = run(|b| {
        b.emit_push(1).emit_push(3).emit_idx_triu().emit_halt();
    });
    assert_eq!(vm.stack(), &[4]);
}

#[test]
fn idx_grid_checks_its_intermediate_product() {
    // row * cols leaves the range while row * cols + col lands back inside
    // it: 2^62 * 2 = 2^63 is not representable, but 2^63 - 1 is. A VM that
    // computes in a wider type and range-checks only the pushed result
    // accepts this program and pushes i64::MAX. Rust chains `checked_*`, so
    // it raises -- and per `spec/xqvm/SPEC.md` every i64 operation the VM
    // performs on a program's behalf is checked, intermediates included.
    let err = run_err(|b| {
        b.emit_push(1_i64 << 62)
            .emit_push(-1)
            .emit_push(2)
            .emit_idx_grid()
            .emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn idx_triu_checks_its_intermediate_product() {
    // The mirror: j * (j - 1) = 2^64 - 2^32 leaves the range, while the
    // halved result 2^63 - 2^31 is representable.
    let err = run_err(|b| {
        b.emit_push(0)
            .emit_push(1_i64 << 32)
            .emit_idx_triu()
            .emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn idx_triu_does_not_require_ordered_operands() {
    // The mnemonic's doc string claimed `(i <= j)` as a caller precondition
    // long after the handler started swapping the pair itself, in the opcode
    // table, in `conformance/opcodes.yaml` and on two generated pages that
    // ship with the crate. Neither ordering is a precondition, and negative
    // operands are admissible: the consuming opcode bounds-checks the index.
    let ordered = run(|b| {
        b.emit_push(1).emit_push(3).emit_idx_triu().emit_halt();
    });
    let swapped = run(|b| {
        b.emit_push(3).emit_push(1).emit_idx_triu().emit_halt();
    });
    assert_eq!(ordered.stack(), &[4]);
    assert_eq!(swapped.stack(), ordered.stack());
}

// ---------------------------------------------------------------------------
// Error cases
// ---------------------------------------------------------------------------

#[test]
fn stack_underflow_on_pop() {
    let err = run_err(|b| {
        b.emit_pop().emit_halt();
    });
    assert!(matches!(err, Error::StackUnderflow { .. }));
}

#[test]
fn stack_underflow_on_add() {
    let err = run_err(|b| {
        b.emit_push(1).emit_add().emit_halt();
    });
    assert!(matches!(err, Error::StackUnderflow { .. }));
}

#[test]
fn division_by_zero() {
    let err = run_err(|b| {
        b.emit_push(5).emit_push(0).emit_div().emit_halt();
    });
    assert!(matches!(err, Error::DivisionByZero { .. }));
}

#[test]
fn modulo_by_zero() {
    let err = run_err(|b| {
        b.emit_push(5).emit_push(0).emit_modulo().emit_halt();
    });
    assert!(matches!(err, Error::DivisionByZero { .. }));
}

#[test]
fn step_limit_exceeded() {
    // Infinite loop: PUSH 1 / JUMPI back to start.
    let mut b = InstructionBuilder::new();
    let top = b.label();
    b.place(top)
        .unwrap()
        .emit_push(1)
        .emit_jump_if(top)
        .emit_halt();
    let bytecode = b.build().unwrap();

    let mut vm = Vm::new();
    vm.set_step_limit(100);
    let err = vm.run(&bytecode).expect_err("expected step limit error");
    assert!(matches!(err, Error::StepLimitExceeded { .. }));
}

#[test]
fn zero_step_limit_executes_nothing() {
    // The limit is exact. `0` used to mean `u64::MAX`, which made the safest
    // looking value the most dangerous one -- an embedder taking a step limit
    // from untrusted input would run unbounded. See QUI-1053.
    //
    // The program is two instructions rather than the `NOP`/`JUMP` loop this
    // test used to drive: against the regression it exists to catch, that
    // loop does not terminate, so the test ran to the CI job timeout instead
    // of failing. `PUSH 1 / HALT` proves the same property -- a zero budget
    // executes nothing -- and fails in milliseconds.
    let mut b = InstructionBuilder::new();
    b.emit_push(1).emit_halt();
    let bytecode = b.build().unwrap();

    let mut vm = Vm::new();
    vm.set_step_limit(0);
    let err = vm
        .run(&bytecode)
        .expect_err("a zero step limit must execute nothing");
    assert!(matches!(err, Error::StepLimitExceeded { .. }));
    assert_eq!(vm.steps(), 0, "no instruction may have run");
    assert!(vm.stack().is_empty(), "the PUSH may not have run");
}

#[test]
fn a_zero_step_limit_completes_an_empty_program() {
    // The other half of the exact-limit rule: zero steps is enough for a
    // program that needs zero steps. The success case is pinned here rather
    // than as a conformance vector because `xquad/tests/test_step_parity.py`
    // asserts `rust_steps > 0` for every vector it replays.
    let bytecode = InstructionBuilder::new().build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_step_limit(0);
    vm.run(&bytecode)
        .expect("an empty program needs no steps to complete");
    assert_eq!(vm.steps(), 0);
}

#[test]
fn unlimited_steps_runs_to_completion() {
    // Opting out of the bound is still possible, but it has to be said aloud.
    let mut b = InstructionBuilder::new();
    for _ in 0..100 {
        b.emit_nop();
    }
    b.emit_halt();
    let bytecode = b.build().unwrap();

    let mut bounded = Vm::new();
    bounded.set_step_limit(10);
    assert!(matches!(
        bounded.run(&bytecode).expect_err("10 steps is not enough"),
        Error::StepLimitExceeded { .. }
    ));

    let mut unbounded = Vm::new();
    unbounded.set_unlimited_steps();
    unbounded
        .run(&bytecode)
        .expect("runs to HALT with no bound");
    assert_eq!(unbounded.steps(), 101);
}

#[test]
fn step_limit_equal_to_instruction_count_succeeds() {
    // Three instructions and no HALT: the program ends by running off the
    // end of the stream after executing exactly three instructions. The
    // limit bounds instructions executed, so the end-of-stream probe is
    // neither charged nor counted -- the Python VM's loop shape.
    let mut b = InstructionBuilder::new();
    b.emit_push(1).emit_push(2).emit_add();
    let bytecode = b.build().unwrap();

    let mut vm = Vm::new();
    vm.set_step_limit(3);
    vm.run(&bytecode)
        .expect("a budget of exactly the instruction count must suffice");
    assert_eq!(vm.steps(), 3, "the end-of-stream probe must not count");
    assert_eq!(vm.stack(), &[3]);
}

#[test]
fn step_limit_one_below_instruction_count_fails() {
    let mut b = InstructionBuilder::new();
    b.emit_push(1).emit_push(2).emit_add();
    let bytecode = b.build().unwrap();

    let mut vm = Vm::new();
    vm.set_step_limit(2);
    let err = vm.run(&bytecode).expect_err("two steps is one too few");
    assert!(matches!(err, Error::StepLimitExceeded { .. }));
    assert_eq!(vm.steps(), 2, "both budgeted instructions ran");
}

#[test]
fn invalid_shift_negative() {
    let err = run_err(|b| {
        b.emit_push(1).emit_push(-1).emit_shl().emit_halt();
    });
    assert!(matches!(err, Error::InvalidShift { .. }));
}

#[test]
fn invalid_shift_too_large() {
    let err = run_err(|b| {
        b.emit_push(1).emit_push(64).emit_shl().emit_halt();
    });
    assert!(matches!(err, Error::InvalidShift { .. }));
}

#[test]
fn invalid_shift_covers_shr_as_well_as_shl() {
    // SHR had no fault coverage on either implementation, and it is the
    // starker half of the divergence this pins: Python's `8 >> 64` is 0,
    // well inside the i64 range, so it never reached any check and the
    // program completed where Rust raised. No overflow is involved, so the
    // arithmetic sweep could not have found it.
    let negative = run_err(|b| {
        b.emit_push(8).emit_push(-1).emit_shr().emit_halt();
    });
    assert!(matches!(negative, Error::InvalidShift { amount: -1, .. }));
    let too_large = run_err(|b| {
        b.emit_push(8).emit_push(64).emit_shr().emit_halt();
    });
    assert!(matches!(too_large, Error::InvalidShift { amount: 64, .. }));
}

#[test]
fn energy_overflow_reports_the_instruction_that_failed() {
    // Every neighbouring model call attaches the program counter with
    // `at_pos`; ENERGY did not, so the release's headline fault printed with
    // no byte offset and no miette caret while an ADD overflow printed one.
    // `error.rs` documents the position as supplied by the VM, so the
    // rustdoc and the site disagreed.
    // A partial sum leaves the range even though the exact total does not:
    // the sorted fold walks i64::MAX, then i64::MAX + 5, before -10 could
    // bring it back. Same shape as the energy_partial_overflow vector.
    let err = run_err(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        b.emit_push(2).emit_push(-10).emit_set_line(Register(0));
        b.emit_push(0)
            .emit_push(i64::MAX)
            .emit_set_line(Register(0));
        b.emit_push(1).emit_push(5).emit_set_line(Register(0));
        b.emit_push(3).emit_bsmx(Register(1));
        b.emit_push(0).emit_push(1).emit_set_line(Register(1));
        b.emit_push(1).emit_push(1).emit_set_line(Register(1));
        b.emit_push(2).emit_push(1).emit_set_line(Register(1));
        b.emit_energy(Register(0), Register(1));
        b.emit_halt();
    });
    match err {
        Error::ArithmeticOverflow { pos } => {
            assert!(
                pos.is_some(),
                "ENERGY must report the byte it failed at, not None"
            );
        }
        other => panic!("expected ArithmeticOverflow, got {other:?}"),
    }
}

#[test]
fn register_type_error_load_on_model() {
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_load(Register(0)); // r0 is a model, not int
        b.emit_halt();
    });
    assert!(matches!(err, Error::RegisterType { .. }));
}

#[test]
fn no_active_loop_next() {
    let err = run_err(|b| {
        b.emit_next(); // raw NEXT with no loop
        b.emit_halt();
    });
    assert!(matches!(err, Error::NoActiveLoop { .. }));
}

#[test]
fn index_out_of_bounds_vec_get() {
    let err = run_err(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(10).emit_vec_push(Register(0));
        b.emit_push(5).emit_vec_get(Register(0)); // index 5 out of bounds
        b.emit_halt();
    });
    assert!(matches!(err, Error::IndexOutOfBounds { .. }));
}

// ---------------------------------------------------------------------------
// ONEHOT / EXCLUDE / IMPLIES constraints
//
// Stack convention (pops from top):
//   ONEHOT:  pop penalty (top), pop row -> apply constraint to grid row
//   EXCLUDE: pop penalty (top), pop j, pop i -> penalise x_i * x_j = 1
//   IMPLIES: pop penalty (top), pop j, pop i -> penalise x_i=1, x_j=0
//   RESIZE:  pop cols (top), pop rows -> set grid dimensions
// ---------------------------------------------------------------------------

#[test]
fn one_hot_constraint_adds_coefficients() {
    // Grid: 1 row x 3 cols. ONEHOT row 0 with penalty 1.
    // To call ONEHOT with row=0, penalty=1: push row=0 first, penalty=1 on top.
    let vm = run(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        // resize: push rows=1 first, cols=3 on top
        b.emit_push(1).emit_push(3).emit_resize(Register(0));
        // onehot: push row=0 first, penalty=1 on top
        b.emit_push(0).emit_push(1).emit_one_hot_r(Register(0));
        b.emit_halt();
    });
    let reg = vm.register(0);
    if let RegVal::Model(m) = reg {
        assert_eq!(m.get_linear(0), -1);
        assert_eq!(m.get_linear(1), -1);
        assert_eq!(m.get_linear(2), -1);
        assert_eq!(m.get_quad(0, 1), 2);
        assert_eq!(m.get_quad(0, 2), 2);
        assert_eq!(m.get_quad(1, 2), 2);
    } else {
        panic!("expected model register");
    }
}

#[test]
fn exclude_constraint_adds_coupling() {
    // exclude: i=1, j=2, penalty=5 -- push i=1, j=2, penalty=5 (penalty on top)
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(1)
            .emit_push(2)
            .emit_push(5)
            .emit_exclude(Register(0));
        b.emit_halt();
    });
    let reg = vm.register(0);
    if let RegVal::Model(m) = reg {
        assert_eq!(m.get_quad(1, 2), 5);
    } else {
        panic!("expected model register");
    }
}

#[test]
fn implies_constraint_adds_linear_and_coupling() {
    // implies: i=0, j=1, penalty=3 -> linear[0] += 3; quad[0,1] += -3
    // push i=0, j=1, penalty=3 (penalty on top)
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(0)
            .emit_push(1)
            .emit_push(3)
            .emit_implies(Register(0));
        b.emit_halt();
    });
    let reg = vm.register(0);
    if let RegVal::Model(m) = reg {
        assert_eq!(m.get_linear(0), 3);
        assert_eq!(m.get_quad(0, 1), -3);
    } else {
        panic!("expected model register");
    }
}

// ---------------------------------------------------------------------------
// Miscellaneous
// ---------------------------------------------------------------------------

#[test]
fn nop_does_nothing() {
    let vm = run(|b| {
        b.emit_push(42).emit_nop().emit_nop().emit_halt();
    });
    assert_eq!(vm.stack(), &[42]);
}

#[test]
fn halt_at_end_of_bytecode() {
    // No explicit HALT -- stream runs out, which is also fine.
    let vm = run(|b| {
        b.emit_push(1).emit_push(2).emit_add();
        // no halt -- just let stream end
    });
    assert_eq!(vm.stack(), &[3]);
}

#[test]
fn reset_clears_state() {
    let mut b = InstructionBuilder::new();
    b.emit_push(99).emit_halt();
    let bytecode = b.build().unwrap();

    let mut vm = Vm::new();
    vm.run(&bytecode).unwrap();
    assert_eq!(vm.stack(), &[99]);
    vm.reset();
    assert!(vm.stack().is_empty());
}

#[test]
fn xqmx_discrete_model() {
    // XQMX: pops k (top) then size. push size=2, k=3.
    let vm = run(|b| {
        b.emit_push(2).emit_push(3).emit_xqmx(Register(0)); // size=2, k=3
        b.emit_halt();
    });
    let reg = vm.register(0);
    if let RegVal::Model(m) = reg {
        assert_eq!(m.domain, Domain::Discrete(3));
        assert_eq!(m.size, 2);
    } else {
        panic!("expected model register");
    }
}

#[test]
fn xqmx_minimum_k_is_two() {
    // k = 2 is the smallest legal discrete domain ([-2, 1]).
    let vm = run(|b| {
        b.emit_push(4).emit_push(2).emit_xqmx(Register(0));
        b.emit_halt();
    });
    if let RegVal::Model(m) = vm.register(0) {
        assert_eq!(m.domain, Domain::Discrete(2));
        assert_eq!(m.size, 4);
    } else {
        panic!("expected model register");
    }
}

#[test]
fn xqmx_rejects_k_one() {
    // k = 1 collapses the [-k, k-1] range to a single value (-1) and is
    // explicitly forbidden by the spec.
    let err = run_err(|b| {
        b.emit_push(2).emit_push(1).emit_xqmx(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::InvalidDiscreteK { k: 1, .. }),
        "expected InvalidDiscreteK, got {err:?}"
    );
}

#[test]
fn xqmx_rejects_zero_k() {
    let err = run_err(|b| {
        b.emit_push(2).emit_push(0).emit_xqmx(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::InvalidDiscreteK { k: 0, .. }),
        "expected InvalidDiscreteK, got {err:?}"
    );
}

#[test]
fn xqmx_rejects_negative_k() {
    let err = run_err(|b| {
        b.emit_push(2).emit_push(-3).emit_xqmx(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::InvalidDiscreteK { k: -3, .. }),
        "expected InvalidDiscreteK, got {err:?}"
    );
}

#[test]
fn xsmx_allocates_discrete_sample() {
    // XSMX: pops k (top) then size. Default values are zero, which lies in
    // the centered domain [-k, k-1] for any k >= 2.
    let vm = run(|b| {
        b.emit_push(3).emit_push(4).emit_xsmx(Register(0));
        b.emit_halt();
    });
    if let RegVal::Sample(s) = vm.register(0) {
        assert_eq!(s.domain, Domain::Discrete(4));
        assert_eq!(s.values, vec![0, 0, 0]);
    } else {
        panic!("expected sample register");
    }
}

#[test]
fn xsmx_rejects_k_below_two() {
    let err = run_err(|b| {
        b.emit_push(3).emit_push(1).emit_xsmx(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::InvalidDiscreteK { k: 1, .. }),
        "expected InvalidDiscreteK, got {err:?}"
    );
}

#[test]
fn resize_sets_grid_dims() {
    // RESIZE: pops cols (top) then rows. push rows=3, cols=3.
    let vm = run(|b| {
        b.emit_push(9).emit_bqmx(Register(0));
        b.emit_push(3).emit_push(3).emit_resize(Register(0));
        b.emit_halt();
    });
    let reg = vm.register(0);
    if let RegVal::Model(m) = reg {
        assert_eq!(m.rows, 3);
        assert_eq!(m.cols, 3);
    } else {
        panic!("expected model register");
    }
}

#[test]
fn row_sum_and_col_sum() {
    // 2x2 grid: linear[0]=1, [1]=2, [2]=3, [3]=4.
    // rowsum(0) = linear[0]+linear[1] = 1+2 = 3
    // colsum(1) = linear[1]+linear[3] = 2+4 = 6
    //
    // SETLINE pops val (top) then i. Push i first, val second.
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        // resize: rows=2, cols=2
        b.emit_push(2).emit_push(2).emit_resize(Register(0));
        // set linear[0]=1: push i=0, val=1
        b.emit_push(0).emit_push(1).emit_set_line(Register(0));
        // set linear[1]=2: push i=1, val=2
        b.emit_push(1).emit_push(2).emit_set_line(Register(0));
        // set linear[2]=3: push i=2, val=3
        b.emit_push(2).emit_push(3).emit_set_line(Register(0));
        // set linear[3]=4: push i=3, val=4
        b.emit_push(3).emit_push(4).emit_set_line(Register(0));
        b.emit_push(0).emit_row_sum(Register(0));
        b.emit_push(1).emit_col_sum(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[3, 6]);
}

#[test]
fn row_sum_past_i64_max_raises() {
    // A 1x3 row holding [i64::MAX, 1, -1]: the partial sum after the second
    // column leaves the range and must raise rather than wrap.
    //
    // The third term brings the exact total back to i64::MAX, so a wrapping
    // fold pushes the exactly correct answer and halts. Two terms would have
    // faulted under `ci-test`'s overflow checks either way and told the two
    // behaviours apart nowhere. The same row also pins the ascending
    // flat-index order: folded descending it never leaves the range.
    let err = run_err(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        b.emit_push(1).emit_push(3).emit_resize(Register(0));
        b.emit_push(0)
            .emit_push(i64::MAX)
            .emit_set_line(Register(0));
        b.emit_push(1).emit_push(1).emit_set_line(Register(0));
        b.emit_push(2).emit_push(-1).emit_set_line(Register(0));
        b.emit_push(0).emit_row_sum(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn col_sum_past_i64_min_raises() {
    // The negative mirror of `row_sum_past_i64_max_raises`, discriminating
    // the same way: a 3x1 column holding [i64::MIN, -1, 1], whose wrapping
    // fold walks i64::MIN -> i64::MAX -> i64::MIN and pushes the exactly
    // correct total.
    let err = run_err(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        b.emit_push(3).emit_push(1).emit_resize(Register(0));
        b.emit_push(0)
            .emit_push(i64::MIN)
            .emit_set_line(Register(0));
        b.emit_push(1).emit_push(-1).emit_set_line(Register(0));
        b.emit_push(2).emit_push(1).emit_set_line(Register(0));
        b.emit_push(0).emit_col_sum(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn row_sum_row_out_of_range_raises() {
    // The one valid row of a 1x3 grid is 0; row 1 addresses no line of the
    // grid and must raise rather than sum absent coefficients to 0.
    //
    // The grid is deliberately not square. `grid_axis_index` takes the axis
    // extent as a bare `usize` fifth parameter that the caller has to get
    // right, and against a square grid -- which is what every earlier test
    // here used -- passing `cols` where `rows` belongs is invisible.
    let err = run_err(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        b.emit_push(1).emit_push(3).emit_resize(Register(0));
        b.emit_push(1).emit_row_sum(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::IndexOutOfBounds { .. }),
        "expected IndexOutOfBounds, got {err:?}"
    );
}

#[test]
fn col_sum_col_out_of_range_raises() {
    // COLSUM had no fault coverage anywhere, on either identity. The one
    // valid column of a 3x1 grid is 0; column 1 addresses no line of it.
    //
    // Non-square for the same reason as `row_sum_row_out_of_range_raises`:
    // this is the case that catches `grid_axis_index` being handed `rows`
    // where `cols` belongs.
    let err = run_err(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        b.emit_push(3).emit_push(1).emit_resize(Register(0));
        b.emit_push(1).emit_col_sum(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::IndexOutOfBounds {
                index: 1,
                len: 1,
                ..
            }
        ),
        "expected IndexOutOfBounds, got {err:?}"
    );
}

#[test]
fn col_sum_without_a_grid_raises() {
    // The other half of COLSUM's missing fault coverage: no RESIZE ran, so
    // there is no column to sum.
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(0).emit_col_sum(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::InvalidGridDimensions { .. }),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn row_sum_without_a_grid_raises() {
    // No RESIZE ran, so there is no row to sum: raising matches ONEHOTR's
    // no-grid behaviour rather than pushing 0 from an empty reduction.
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(0).emit_row_sum(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::InvalidGridDimensions { .. }),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn col_find_col_out_of_range_raises() {
    // Valid columns of a 2x2 grid are 0 and 1; column 5 must raise rather
    // than scan absent coefficients and push -1.
    let err = run_err(|b| {
        b.emit_push(4).emit_bsmx(Register(0));
        b.emit_push(2).emit_push(2).emit_resize(Register(0));
        b.emit_push(5).emit_push(1).emit_col_find(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::IndexOutOfBounds { .. }),
        "expected IndexOutOfBounds, got {err:?}"
    );
}

#[test]
fn row_find_without_a_grid_raises() {
    // An ungridded scan used to answer -1, indistinguishable from a real
    // row that lacks the value; it now raises like the other grid opcodes.
    let err = run_err(|b| {
        b.emit_push(4).emit_bsmx(Register(0));
        b.emit_push(0).emit_push(1).emit_row_find(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::InvalidGridDimensions { .. }),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn row_find_and_col_find() {
    // 2x2 grid: [0]=10, [1]=20, [2]=30, [3]=10.
    // ROWFIND: pops value (top) then row -> push first col where match, or -1
    // rowfind(row=0, value=20) -> col=1
    // COLFIND: pops value (top) then col -> push first row where match, or -1
    // colfind(col=1, value=10) -> row=1
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(2).emit_push(2).emit_resize(Register(0));
        b.emit_push(0).emit_push(10).emit_set_line(Register(0));
        b.emit_push(1).emit_push(20).emit_set_line(Register(0));
        b.emit_push(2).emit_push(30).emit_set_line(Register(0));
        b.emit_push(3).emit_push(10).emit_set_line(Register(0));
        // rowfind: push row=0, value=20 on top
        b.emit_push(0).emit_push(20).emit_row_find(Register(0));
        // colfind: push col=1, value=10 on top
        b.emit_push(1).emit_push(10).emit_col_find(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[1, 1]);
}

// ---------------------------------------------------------------------------
// New opcodes added in spec migration
// ---------------------------------------------------------------------------

#[test]
fn drop_marks_register_unset() {
    let vm = run(|b| {
        b.emit_push(42)
            .emit_stow(Register(3))
            .emit_drop(Register(3))
            .emit_halt();
    });
    assert_eq!(vm.register(3), &RegVal::Unset);
}

#[test]
fn sclr_clears_entire_stack() {
    let vm = run(|b| {
        b.emit_push(1)
            .emit_push(2)
            .emit_push(3)
            .emit_sclr()
            .emit_halt();
    });
    assert!(vm.stack().is_empty());
}

#[test]
fn sqr_squares_top() {
    let vm = run(|b| {
        b.emit_push(7).emit_sqr().emit_halt();
    });
    assert_eq!(vm.stack(), &[49]);
}

#[test]
fn abs_positive_unchanged() {
    let vm = run(|b| {
        b.emit_push(5).emit_abs().emit_halt();
    });
    assert_eq!(vm.stack(), &[5]);
}

#[test]
fn abs_negative_becomes_positive() {
    let vm = run(|b| {
        b.emit_push(-9).emit_abs().emit_halt();
    });
    assert_eq!(vm.stack(), &[9]);
}

#[test]
fn min_returns_smaller() {
    let vm = run(|b| {
        b.emit_push(10).emit_push(3).emit_min().emit_halt();
    });
    assert_eq!(vm.stack(), &[3]);
}

#[test]
fn max_returns_larger() {
    let vm = run(|b| {
        b.emit_push(10).emit_push(3).emit_max().emit_halt();
    });
    assert_eq!(vm.stack(), &[10]);
}

#[test]
fn inc_adds_one() {
    let vm = run(|b| {
        b.emit_push(41).emit_inc().emit_halt();
    });
    assert_eq!(vm.stack(), &[42]);
}

#[test]
fn dec_subtracts_one() {
    let vm = run(|b| {
        b.emit_push(43).emit_dec().emit_halt();
    });
    assert_eq!(vm.stack(), &[42]);
}

#[test]
fn one_hot_scale_factors_are_validated_before_any_term_is_emitted() {
    // A 2x1 grid gives ONEHOTR one column, so the pair loop that consumes
    // 2 * penalty never runs; the 1x2 mirror does the same for ONEHOTC. Both
    // must still raise, because an expansion validates its scale factors
    // before it emits terms -- the rule `expand_equality` has always
    // followed. penalty = 2^62 doubles out of range while -penalty stays
    // inside it, so only the quadratic factor can be the one raising.
    #[expect(clippy::type_complexity, reason = "test table of builder closures")]
    let cases: [(&str, Box<dyn FnOnce(&mut InstructionBuilder)>); 2] = [
        (
            "ONEHOTR",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(2).emit_bqmx(Register(0));
                b.emit_push(2).emit_push(1).emit_resize(Register(0));
                b.emit_push(0)
                    .emit_push(1_i64 << 62)
                    .emit_one_hot_r(Register(0));
                b.emit_halt();
            }),
        ),
        (
            "ONEHOTC",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(2).emit_bqmx(Register(0));
                b.emit_push(1).emit_push(2).emit_resize(Register(0));
                b.emit_push(0)
                    .emit_push(1_i64 << 62)
                    .emit_one_hot_c(Register(0));
                b.emit_halt();
            }),
        ),
    ];
    for (name, build) in cases {
        let err = run_err(build);
        assert!(
            matches!(err, Error::ArithmeticOverflow { .. }),
            "{name}: expected ArithmeticOverflow, got {err:?}"
        );
    }
}

#[test]
fn one_hot_c_applies_column_constraint() {
    // 2x2 BQMX, apply one-hot over column 0 with penalty 1.
    // Variables: (0,0)=idx 0, (1,0)=idx 2.
    // Expected: linear[0] += -1, linear[2] += -1, quad(0,2) += 2.
    let vm = run(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(2).emit_push(2).emit_resize(Register(0));
        b.emit_push(0).emit_push(1).emit_one_hot_c(Register(0));
        b.emit_halt();
    });
    if let RegVal::Model(m) = vm.register(0) {
        assert_eq!(m.get_linear(0), -1);
        assert_eq!(m.get_linear(2), -1);
        assert_eq!(m.get_quad(0, 2), 2);
    } else {
        panic!("r0 should be a model");
    }
}

#[test]
fn stack_overflow_at_8192() {
    // Push 8193 values: the 8193rd push should trigger StackOverflow.
    let err = run_err(|b| {
        // Loop: push counter from 0 to 8192 (8193 pushes total).
        b.emit_push(0).emit_push(8193).emit_range();
        b.emit_push(0); // push inside the loop body
        b.emit_next().emit_halt();
    });
    assert!(matches!(err, Error::StackOverflow { .. }));
}

// ---------------------------------------------------------------------------
// Allocation budget
// ---------------------------------------------------------------------------

/// Build bytecode and run it on a VM with the given allocation budget.
/// Returns the VM and the run result so both state and error can be asserted.
fn run_with_memory_limit(
    limit: u64,
    build: impl FnOnce(&mut InstructionBuilder),
) -> (Vm, Result<(), Error>) {
    let mut b = InstructionBuilder::new();
    build(&mut b);
    let bytecode = b.build().expect("builder build");
    let mut vm = Vm::new();
    vm.set_memory_limit(limit);
    let result = vm.run(&bytecode);
    (vm, result)
}

#[test]
fn bsmx_beyond_the_budget_allocates_nothing() {
    // The reported case: a three-instruction program asks for a 1 GiB sample.
    let (vm, result) = run_with_memory_limit(1 << 20, |b| {
        b.emit_push(1 << 27).emit_bsmx(Register(0)).emit_halt();
    });
    let err = result.expect_err("expected the budget to reject the allocation");
    assert!(
        matches!(err, Error::MemoryLimitExceeded { .. }),
        "expected MemoryLimitExceeded, got {err:?}"
    );
    assert_eq!(
        vm.register(0),
        &RegVal::Unset,
        "a rejected allocator must not write its register"
    );
    assert_eq!(vm.memory_used(), 0, "a rejected charge must not be kept");
}

#[test]
fn sample_allocators_charge_eight_bytes_per_variable() {
    for (name, build) in [
        (
            "BSMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(100).emit_bsmx(Register(0)).emit_halt();
            }) as Box<dyn FnOnce(&mut InstructionBuilder)>,
        ),
        (
            "SSMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(100).emit_ssmx(Register(0)).emit_halt();
            }),
        ),
        (
            "XSMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(100)
                    .emit_push(2)
                    .emit_xsmx(Register(0))
                    .emit_halt();
            }),
        ),
    ] {
        let (vm, result) = run_with_memory_limit(1 << 20, build);
        result.unwrap_or_else(|e| panic!("{name} within budget should run: {e}"));
        assert_eq!(vm.memory_used(), 800, "{name} should charge 100 * 8 bytes");
    }
}

#[test]
fn the_default_budget_rejects_an_allocation_larger_than_itself() {
    // Every other budget test installs a reduced limit, so nothing pinned
    // the shipped default. 2^30 variables at VARIABLE_BYTES each is 8 GiB
    // against the 1 GiB default: three instructions from a signed account.
    //
    // The identity is `MemoryLimitExceeded`, not `InvalidAllocation`. The
    // size is a valid allocation request that is merely too large, and the
    // charge is taken off the `i64` before the `usize` conversion so the
    // answer is the same on wasm32, where the conversion would otherwise
    // decide it.
    let mut b = InstructionBuilder::new();
    b.emit_push(1 << 30).emit_bqmx(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    let err = vm
        .run(&bytecode)
        .expect_err("expected the default budget to reject the allocation");
    assert!(
        matches!(
            err,
            Error::MemoryLimitExceeded {
                requested: 8_589_934_592,
                used: 0,
                limit: 1_073_741_824,
                ..
            }
        ),
        "expected MemoryLimitExceeded, got {err:?}"
    );
    assert_eq!(vm.memory_used(), 0, "a refused charge may not be banked");
}

#[test]
fn model_allocators_charge_their_declared_size() {
    // A model stores coefficients sparsely, but its declared size is an
    // obligation every consumer has to materialise, so it is charged.
    let (_vm, result) = run_with_memory_limit(1 << 20, |b| {
        b.emit_push(1 << 30).emit_bqmx(Register(0)).emit_halt();
    });
    assert!(matches!(
        result.expect_err("expected the budget to reject the declaration"),
        Error::MemoryLimitExceeded { .. }
    ));
}

#[test]
fn a_negative_allocator_size_is_not_an_allocation() {
    // `PUSH -1 / BQMX r0 / HALT` used to halt Ok with an empty model: the
    // size went through `usize::try_from(..).unwrap_or(0)`, so a negative
    // value became zero, charged nothing, and installed a model the program
    // never asked for. All six allocators share the operand, so all six are
    // pinned here.
    #[expect(clippy::type_complexity, reason = "test table of builder closures")]
    let cases: [(&str, Box<dyn FnOnce(&mut InstructionBuilder)>); 6] = [
        (
            "BQMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1).emit_bqmx(Register(0)).emit_halt();
            }),
        ),
        (
            "SQMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1).emit_sqmx(Register(0)).emit_halt();
            }),
        ),
        (
            "XQMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1)
                    .emit_push(2)
                    .emit_xqmx(Register(0))
                    .emit_halt();
            }),
        ),
        (
            "BSMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1).emit_bsmx(Register(0)).emit_halt();
            }),
        ),
        (
            "SSMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1).emit_ssmx(Register(0)).emit_halt();
            }),
        ),
        (
            "XSMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1)
                    .emit_push(2)
                    .emit_xsmx(Register(0))
                    .emit_halt();
            }),
        ),
    ];
    for (name, build) in cases {
        let (vm, result) = run_with_memory_limit(1 << 20, build);
        let err = result.expect_err("expected a negative size to be rejected");
        assert!(
            matches!(err, Error::InvalidAllocation { size: -1, .. }),
            "{name}: expected InvalidAllocation, got {err:?}"
        );
        assert_eq!(
            vm.register(0),
            &RegVal::Unset,
            "{name}: a rejected allocator must not write its register"
        );
        assert_eq!(
            vm.memory_used(),
            0,
            "{name}: a rejected size must not be charged"
        );
    }
}

#[test]
fn an_allocator_size_is_charged_before_it_is_narrowed() {
    // The order the fault identity rests on: validate the sign, charge off
    // the `i64`, then convert to `usize`. A size that overflows a 32-bit
    // `usize` must therefore raise `MemoryLimitExceeded` and not
    // `InvalidAllocation`, so wasm32 and a native node agree. On a 64-bit
    // host the conversion always succeeds, so the budget is the only thing
    // that can reject this program -- which is exactly the property under
    // test.
    let (vm, result) = run_with_memory_limit(1 << 20, |b| {
        b.emit_push(1_i64 << 33).emit_bqmx(Register(0)).emit_halt();
    });
    let err = result.expect_err("expected the budget to reject the allocation");
    assert!(
        matches!(err, Error::MemoryLimitExceeded { .. }),
        "expected MemoryLimitExceeded, got {err:?}"
    );
    assert_eq!(vm.memory_used(), 0);
}

#[test]
fn a_zero_size_allocation_is_still_an_allocation() {
    // Zero is a valid, free allocation: the guard rejects sizes that are not
    // allocations, not empty ones.
    let vm = run(|b| {
        b.emit_push(0).emit_bqmx(Register(0)).emit_halt();
    });
    assert!(matches!(vm.register(0), &RegVal::Model(_)));
    assert_eq!(vm.memory_used(), 0);
}

#[test]
fn discrete_k_is_rejected_before_the_budget_is_charged() {
    // Error precedence: an invalid domain is a program error regardless of
    // the budget, so it must win over the allocation charge.
    let (_vm, result) = run_with_memory_limit(8, |b| {
        b.emit_push(1 << 30)
            .emit_push(1)
            .emit_xsmx(Register(0))
            .emit_halt();
    });
    assert!(matches!(
        result.expect_err("expected an error"),
        Error::InvalidDiscreteK { .. }
    ));
}

#[test]
fn discrete_k_is_rejected_before_the_allocation_size() {
    // Error precedence between the two allocator preconditions, now that
    // both exist. `spec/xqvm/SPEC.md` and `ISA.md` state it: for the
    // discrete allocators the domain width `k` is validated before the size.
    // `PUSH -1 / PUSH 1 / XQMX r0` is invalid twice over and must report the
    // `k` fault.
    for (name, build) in [
        (
            "XQMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1)
                    .emit_push(1)
                    .emit_xqmx(Register(0))
                    .emit_halt();
            }) as Box<dyn FnOnce(&mut InstructionBuilder)>,
        ),
        (
            "XSMX",
            Box::new(|b: &mut InstructionBuilder| {
                b.emit_push(-1)
                    .emit_push(1)
                    .emit_xsmx(Register(0))
                    .emit_halt();
            }),
        ),
    ] {
        let err = run_err(build);
        assert!(
            matches!(err, Error::InvalidDiscreteK { k: 1, .. }),
            "{name}: expected InvalidDiscreteK, got {err:?}"
        );
    }
}

#[test]
fn vec_push_is_charged_on_growth() {
    // Four elements fit in a 64-byte budget at 16 bytes each; the fifth does not.
    let (vm, result) = run_with_memory_limit(64, |b| {
        b.emit_vec_i(Register(0));
        for i in 0..5 {
            b.emit_push(i).emit_vec_push(Register(0));
        }
        b.emit_halt();
    });
    assert!(matches!(
        result.expect_err("expected the fifth push to exhaust the budget"),
        Error::MemoryLimitExceeded { .. }
    ));
    if let RegVal::VecInt(v) = vm.register(0) {
        assert_eq!(v.len(), 4, "the four charged pushes should have landed");
    } else {
        panic!("r0 should be a vec<int>");
    }
}

#[test]
fn equality_expansion_is_charged_before_it_expands() {
    // EQUALITY writes one quadratic term per pair of indices: 200 indices cost
    // 19,900 terms, built by a program of 400 pushes. Charged up front, so the
    // model is left untouched when the budget cannot cover it.
    let n = 200i64;
    let (vm, result) = run_with_memory_limit(1 << 14, |b| {
        b.emit_push(n).emit_bqmx(Register(0));
        b.emit_vec_i(Register(1)).emit_vec_i(Register(2));
        for i in 0..n {
            b.emit_push(i).emit_vec_push(Register(1));
            b.emit_push(1).emit_vec_push(Register(2));
        }
        b.emit_push(1).emit_push(1).emit(Instruction::Equality {
            model: Register(0),
            indices: Register(1),
            coeffs: Register(2),
        });
        b.emit_halt();
    });
    assert!(matches!(
        result.expect_err("expected the expansion to exceed the budget"),
        Error::MemoryLimitExceeded { .. }
    ));
    if let RegVal::Model(m) = vm.register(0) {
        assert_eq!(
            m.quadratic_len(),
            0,
            "a rejected expansion must not write coefficients"
        );
    } else {
        panic!("r0 should be a model");
    }
}

#[test]
fn at_least_w_weight_sum_past_i64_max_raises() {
    // The weight sum is program-controlled; a sum past the range must raise
    // rather than derive the slack count from a wrapped excess.
    //
    // The operands discriminate rather than merely agree. Three weights of
    // 2^62 wrap to -2^62, which makes the excess negative, allocates no
    // slack variables, and -- with a penalty of 0 -- emits nothing, so the
    // wrapping build HALTs successfully with a size-3 all-zero model. That
    // is the silently wrong answer this release exists to close, and a
    // weight pair that faulted on the wrapping build too would not have
    // told the two behaviours apart.
    let err = run_err(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        b.emit_vec_i(Register(1));
        b.emit_push(0).emit_vec_push(Register(1));
        b.emit_push(1).emit_vec_push(Register(1));
        b.emit_push(2).emit_vec_push(Register(1));
        b.emit_vec_i(Register(2));
        for _ in 0..3 {
            b.emit_push(1_i64 << 62).emit_vec_push(Register(2));
        }
        b.emit_push(1_i64 << 61)
            .emit_push(0)
            .emit(Instruction::AtLeastW {
                model: Register(0),
                indices: Register(1),
                coeffs: Register(2),
            });
        b.emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn one_hot_r_over_a_huge_grid_is_rejected() {
    // ONEHOTR expands O(cols^2) terms in a single step. Without the budget
    // this runs until the host dies; with it the instruction is rejected
    // before expanding.
    //
    // The grid here is legitimately oversized rather than degenerate: the
    // model declares 4096 variables and the grid describes exactly those
    // 4096 cells, so RESIZE accepts it and the budget is what stops the
    // expansion. Before RESIZE bounded its extents this test used a size-4
    // model resized to `1 x 2^20`, which now fails one instruction earlier
    // with a better identity and would no longer reach the expansion at all.
    let (vm, result) = run_with_memory_limit(1 << 20, |b| {
        b.emit_push(4096).emit_bqmx(Register(0));
        b.emit_push(1).emit_push(4096).emit_resize(Register(0));
        b.emit_push(0).emit_push(1).emit_one_hot_r(Register(0));
        b.emit_halt();
    });
    assert!(matches!(
        result.expect_err("expected the expansion to exceed the budget"),
        Error::MemoryLimitExceeded { .. }
    ));
    assert_eq!(
        vm.memory_used(),
        4096 * 8,
        "the allocation is charged and the expansion is not, so nothing past \
         the 4096 declared variables was spent"
    );
}

// ---------------------------------------------------------------------------
// RESIZE extent bound
// ---------------------------------------------------------------------------

#[test]
fn resize_beyond_the_register_size_is_rejected() {
    // The twenty-two byte reproducer. A size-4 model resized to `1 x 2^62`
    // makes the ROWSUM that follows scan 2^62 cells for one metered step,
    // charging nothing: a caller pre-paying ten steps buys an execution that
    // never finishes. The grid describes cells the model never declared, so
    // RESIZE rejects it.
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(1).emit_push(1 << 62).emit_resize(Register(0));
        b.emit_push(0).emit_row_sum(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::InvalidGridDimensions {
                rows: 1,
                cols: 4_611_686_018_427_387_904,
                ..
            }
        ),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn resize_to_exactly_the_register_size_is_accepted() {
    let vm = run(|b| {
        b.emit_push(6).emit_bqmx(Register(0));
        b.emit_push(2).emit_push(3).emit_resize(Register(0));
        b.emit_push(1).emit_row_sum(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn resize_may_leave_variables_outside_the_grid() {
    // The bound is `<=`, not `==`. EQUALITY, ATLEAST, ATLEASTW and REDUCE
    // append slack and auxiliary variables past the grid, so a model whose
    // size exceeds its extent is the normal state after any of them and a
    // later RESIZE must still be allowed.
    let vm = run(|b| {
        b.emit_push(10).emit_bqmx(Register(0));
        b.emit_push(2).emit_push(3).emit_resize(Register(0));
        b.emit_push(1).emit_row_sum(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.stack(), &[0]);
}

#[test]
fn resize_bounds_a_sample_by_its_value_count() {
    let err = run_err(|b| {
        b.emit_push(4).emit_bsmx(Register(0));
        b.emit_push(3).emit_push(3).emit_resize(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::InvalidGridDimensions {
                rows: 3,
                cols: 3,
                ..
            }
        ),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn a_host_supplied_grid_with_an_unaddressable_extent_is_rejected() {
    // `RESIZE` cannot produce `rows * cols` overflow any more, but a host can
    // install a register directly -- `xqffi` exposes both extents to Python
    // unvalidated -- so `grid_axis_index`'s addressability arm is still the
    // guard that keeps `usize_row * cols` in the read-only grid handlers from
    // overflowing. Deleting it turns this case into a `ci-test` panic and a
    // `release` wrap.
    let mut model = xqvm::XqmxModel::new(Domain::Binary, 4);
    model.rows = usize::MAX / 2;
    model.cols = 8;

    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_row_sum(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_register(0, RegVal::Model(model));
    let err = vm.run(&bytecode).expect_err("expected error");
    assert!(
        matches!(err, Error::InvalidGridDimensions { .. }),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn the_budget_is_cumulative_across_reallocations() {
    // Repeatedly overwriting the same register still spends budget: bytes are
    // charged when allocated and never refunded, so an allocate-and-discard
    // loop cannot outlive the bound.
    let (vm, result) = run_with_memory_limit(1000, |b| {
        b.emit_push(0).emit_push(10).emit_range();
        b.emit_push(50).emit_bsmx(Register(0));
        b.emit_next().emit_halt();
    });
    assert!(matches!(
        result.expect_err("expected the loop to exhaust the budget"),
        Error::MemoryLimitExceeded { .. }
    ));
    assert_eq!(
        vm.memory_used(),
        800,
        "two 400-byte samples should have been charged before the third failed"
    );
}

#[test]
fn memory_limit_error_is_distinguishable_from_the_step_limit() {
    let mut b = InstructionBuilder::new();
    b.emit_push(1 << 27).emit_bsmx(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_step_limit(10);
    vm.set_memory_limit(1 << 20);
    let err = vm.run(&bytecode).expect_err("expected an error");
    match err {
        Error::MemoryLimitExceeded {
            pos,
            requested,
            used,
            limit,
        } => {
            // PUSH 1<<27 encodes as a five-byte PUSH4, so BSMX starts at 5.
            assert_eq!(pos, 5, "should point at the BSMX instruction");
            assert_eq!(requested, (1 << 27) * 8);
            assert_eq!(used, 0);
            assert_eq!(limit, 1 << 20);
        }
        other => panic!("expected MemoryLimitExceeded, got {other:?}"),
    }
}

#[test]
fn each_run_starts_with_a_fresh_budget() {
    let mut b = InstructionBuilder::new();
    b.emit_push(100).emit_bsmx(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_memory_limit(1000);
    vm.run(&bytecode).expect("first run");
    assert_eq!(vm.memory_used(), 800);
    vm.run(&bytecode)
        .expect("second run should not inherit the first run's charge");
    assert_eq!(vm.memory_used(), 800);
    vm.reset();
    assert_eq!(vm.memory_used(), 0);
}

#[test]
fn the_default_budget_admits_ordinary_programs() {
    // A 100,000-variable sample is 800 KB: comfortably inside the 1 GiB default.
    let vm = run(|b| {
        b.emit_push(100_000).emit_bsmx(Register(0)).emit_halt();
    });
    assert_eq!(vm.memory_limit(), 1 << 30);
    assert_eq!(vm.memory_used(), 800_000);
}

#[test]
fn reentrant_iter_copies_are_charged() {
    // A loop frame is only popped by `NEXT`, so a back-edge that re-enters an
    // `ITER` piles up one copy of the slice per execution -- growth the step
    // limit alone bounds only at 8 bytes per element per step.
    let (vm, result) = run_with_memory_limit(4096, |b| {
        b.emit_vec_i(Register(0));
        for i in 0..64 {
            b.emit_push(i).emit_vec_push(Register(0));
        }
        let top = b.label();
        b.place(top).expect("place label");
        b.emit_push(0).emit_push(64).emit_iter(Register(0));
        b.emit_jump(top);
        b.emit_halt();
    });
    assert!(
        matches!(
            result.expect_err("expected the accumulating copies to exhaust the budget"),
            Error::MemoryLimitExceeded { .. }
        ),
        "re-entrant ITER copies must be charged"
    );
    assert!(
        vm.memory_used() > 1024,
        "the charge should cover more than the 64 vec pushes, got {}",
        vm.memory_used()
    );
}

// ---------------------------------------------------------------------------
// Model accumulation order (spec/xqvm/HLF.md, "Accumulation order")
// ---------------------------------------------------------------------------

#[test]
fn model_terms_iterate_in_sorted_key_order() {
    // The spec makes sorted key order normative for every reduction over a
    // model's sparse tables, because insertion order is a property of the
    // program's history rather than of the model. BTreeMap gives that for
    // free today; this pins it so a swap to a hash container cannot pass
    // silently.
    let mut model = xqvm::XqmxModel::new(Domain::Binary, 8);
    for i in [5_usize, 0, 3, 1] {
        model.set_linear(i, 1);
    }
    for (i, j) in [(2_usize, 3_usize), (0, 1), (1, 2)] {
        model.set_quad(i, j, 1);
    }

    let linear: Vec<usize> = model.iter_linear().map(|(i, _)| i).collect();
    let quadratic: Vec<(usize, usize)> = model.iter_quadratic().map(|(i, j, _)| (i, j)).collect();

    assert_eq!(linear, vec![0, 1, 3, 5]);
    assert_eq!(quadratic, vec![(0, 1), (1, 2), (2, 3)]);
}

#[test]
fn energy_folds_in_sorted_order_even_when_that_is_the_order_that_faults() {
    // linear = {0: i64::MAX, 1: 5, 2: -10} over an all-ones sample. The
    // exact total, i64::MAX - 5, is representable; the sorted fold reaches
    // it through i64::MAX + 5, which is not.
    //
    // The terms are written 2, 0, 1 so insertion order and sorted order
    // disagree, which makes one program pin both rules at once: an
    // insertion-order fold walks -10 -> i64::MAX - 10 -> i64::MAX - 5 and
    // never leaves the range, so it succeeds where the normative sorted
    // fold raises.
    let err = run_err(|b| {
        b.emit_push(3).emit_bqmx(Register(0));
        b.emit_push(2).emit_push(-10).emit_set_line(Register(0));
        b.emit_push(0)
            .emit_push(i64::MAX)
            .emit_set_line(Register(0));
        b.emit_push(1).emit_push(5).emit_set_line(Register(0));
        b.emit_push(3).emit_bsmx(Register(1));
        for i in 0..3 {
            b.emit_push(i).emit_push(1).emit_set_line(Register(1));
        }
        b.emit_energy(Register(0), Register(1)).emit_halt();
    });
    assert!(
        matches!(err, Error::ArithmeticOverflow { .. }),
        "expected ArithmeticOverflow, got {err:?}"
    );
}

#[test]
fn energy_is_independent_of_the_order_terms_were_added() {
    // Two programs that build the same model by different routes must
    // produce the same energy -- the property sorted-order accumulation
    // exists to guarantee.
    let mut forward = xqvm::XqmxModel::new(Domain::Binary, 4);
    let mut reverse = xqvm::XqmxModel::new(Domain::Binary, 4);
    for i in 0..4_usize {
        forward.set_linear(i, i64::try_from(i).expect("index fits i64") + 1);
    }
    for i in (0..4_usize).rev() {
        reverse.set_linear(i, i64::try_from(i).expect("index fits i64") + 1);
    }

    let sample = [1_i64, 1, 1, 1];
    assert_eq!(
        forward.energy(&sample).expect("forward energy"),
        reverse.energy(&sample).expect("reverse energy")
    );
}

// ---------------------------------------------------------------------------
// OUTPUT and INPUT charge for the copy they make
// ---------------------------------------------------------------------------

#[test]
fn output_charges_for_the_copy_it_hands_the_host() {
    // The output slots survive the run, so the copy is live memory the host
    // holds on to. Charging it is what stops a program from spending the
    // budget once and then handing back an unbounded multiple of it.
    let mut b = InstructionBuilder::new();
    b.emit_push(128).emit_bsmx(Register(0));
    b.emit_push(0).emit_output(Register(0));
    b.emit_push(1).emit_output(Register(0));
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_output_slots(4);
    vm.run(&bytecode).expect("vm run");
    assert_eq!(
        vm.memory_used(),
        3 * 128 * 8,
        "the 128-value sample plus two copies of it"
    );
}

#[test]
fn repeated_output_of_one_register_exhausts_the_budget() {
    // The amplification: one allocation that spends the budget exactly, then
    // one OUTPUT per slot. Before the charge site the copies were free and
    // sixteen slots turned a 1 GiB budget into roughly 17 GiB resident with
    // `memory_used()` unchanged.
    let mut b = InstructionBuilder::new();
    b.emit_push(128).emit_bsmx(Register(0));
    b.emit_push(0).emit_output(Register(0));
    b.emit_push(1).emit_output(Register(0));
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_output_slots(4);
    // Exactly enough for the allocation and one copy of it.
    vm.set_memory_limit(2 * 128 * 8);
    let err = vm
        .run(&bytecode)
        .expect_err("expected the second copy to fail");
    assert!(
        matches!(err, Error::MemoryLimitExceeded { .. }),
        "expected MemoryLimitExceeded, got {err:?}"
    );
    assert_eq!(vm.memory_used(), 2 * 128 * 8);
}

#[test]
fn output_of_an_int_costs_one_variable() {
    let mut b = InstructionBuilder::new();
    b.emit_push(7).emit_stow(Register(0));
    b.emit_push(0).emit_output(Register(0));
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_output_slots(4);
    vm.run(&bytecode).expect("vm run");
    assert_eq!(vm.memory_used(), 8);
}

#[test]
fn input_charges_for_the_calldata_it_copies() {
    // A calldata entry the host supplied is duplicated into the register
    // file, and the copy is as real as an allocation the program made itself.
    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_input(Register(0));
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_calldata(vec![RegVal::VecInt(vec![0; 64])]);
    vm.run(&bytecode).expect("vm run");
    assert_eq!(
        vm.memory_used(),
        64 * 16,
        "64 vec elements at 16 bytes each"
    );
}

#[test]
fn input_beyond_the_budget_copies_nothing() {
    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_input(Register(0));
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_calldata(vec![RegVal::VecInt(vec![0; 64])]);
    vm.set_memory_limit(64);
    let err = vm
        .run(&bytecode)
        .expect_err("expected the copy to be rejected");
    assert!(
        matches!(err, Error::MemoryLimitExceeded { .. }),
        "expected MemoryLimitExceeded, got {err:?}"
    );
    assert_eq!(
        vm.memory_used(),
        0,
        "charge-before-allocate: nothing was copied"
    );
}

// ---------------------------------------------------------------------------
// The charge schedule is a set of literals, not a function of pointer width
// ---------------------------------------------------------------------------
//
// The rates are normative and target-independent. Deriving one from
// `size_of::<usize>()` or `size_of::<XqmxModel>()` makes the deployed wasm32
// VM enforce a schedule that `xqvm_py`, the published documentation and this
// crate's own 64-bit build all disagree with. These tests pin each rate as a
// number, so a derivation reintroduced on any target fails here rather than
// on chain.

#[test]
fn a_variable_costs_eight_bytes() {
    let vm = run(|b| {
        b.emit_push(100).emit_bsmx(Register(0)).emit_halt();
    });
    assert_eq!(vm.memory_used(), 100 * 8);
}

#[test]
fn a_vec_element_costs_sixteen_bytes() {
    let vm = run(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(1).emit_vec_push(Register(0));
        b.emit_push(2).emit_vec_push(Register(0));
        b.emit_halt();
    });
    assert_eq!(vm.memory_used(), 2 * 16);
}

#[test]
fn a_linear_coefficient_costs_thirty_two_bytes() {
    let vm = run(|b| {
        b.emit_push(1).emit_bqmx(Register(0));
        b.emit_push(0).emit_push(5).emit_set_line(Register(0));
        b.emit_halt();
    });
    assert_eq!(
        vm.memory_used(),
        8 + 32,
        "one variable plus one linear entry"
    );
}

#[test]
fn a_quadratic_coefficient_costs_forty_eight_bytes() {
    let vm = run(|b| {
        b.emit_push(2).emit_bqmx(Register(0));
        b.emit_push(0)
            .emit_push(1)
            .emit_push(5)
            .emit_set_quad(Register(0));
        b.emit_halt();
    });
    assert_eq!(
        vm.memory_used(),
        2 * 8 + 48,
        "two variables plus one quadratic entry"
    );
}

#[test]
fn an_iter_over_a_model_vec_charges_what_each_model_holds() {
    // One model copy has one price. `ITER` used to charge a fixed 96-byte
    // header plus entries while `regval_bytes` -- what `OUTPUT` and `INPUT`
    // charge -- billed the declared size at VARIABLE_BYTES plus entries, so
    // the same model cost a different number of bytes depending on which
    // opcode copied it, and the header rate had no xqvm_py counterpart at
    // all. Both paths now go through `model_bytes`.
    let mut model = xqvm::XqmxModel::new(Domain::Binary, 4);
    model.set_linear(0, 7);

    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_push(1).emit_iter(Register(0));
    b.emit_next();
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_register(0, RegVal::VecXqmx(vec![model]));
    vm.run(&bytecode).expect("vm run");
    assert_eq!(
        vm.memory_used(),
        4 * 8 + 32,
        "four declared variables plus the model's single linear entry"
    );
}

#[test]
fn iterating_a_model_vec_costs_what_outputting_the_same_model_costs() {
    // The equality the previous test's number rests on, asserted directly:
    // whichever opcode performs the copy, the charge is the same.
    let mut model = xqvm::XqmxModel::new(Domain::Binary, 6);
    model.set_linear(0, 7);
    model.set_quad(0, 1, 3);

    let iter_charge = {
        let mut b = InstructionBuilder::new();
        b.emit_push(0).emit_push(1).emit_iter(Register(0));
        b.emit_next();
        b.emit_halt();
        let bytecode = b.build().expect("builder build");
        let mut vm = Vm::new();
        vm.set_register(0, RegVal::VecXqmx(vec![model.clone()]));
        vm.run(&bytecode).expect("vm run");
        vm.memory_used()
    };

    let output_charge = {
        let mut b = InstructionBuilder::new();
        b.emit_push(0).emit_output(Register(0));
        b.emit_halt();
        let bytecode = b.build().expect("builder build");
        let mut vm = Vm::new();
        vm.set_output_slots(1);
        vm.set_register(0, RegVal::Model(model));
        vm.run(&bytecode).expect("vm run");
        vm.memory_used()
    };

    assert_eq!(
        iter_charge, output_charge,
        "ITER and OUTPUT must price one model copy identically"
    );
}

// ---------------------------------------------------------------------------
// Loop-nesting cap
// ---------------------------------------------------------------------------

#[test]
fn loop_nesting_is_capped_at_8192_frames() {
    // RANGE and ITER each push a frame and only NEXT pops one, so jumping
    // back over a loop header without running its NEXT grows the loop stack
    // without bound. A LoopFrame is not charged against the allocation
    // budget either, so nothing else bounded it: five instructions grew the
    // stack until the host ran out of memory. The cap mirrors the
    // 8192-element value-stack limit.
    let mut b = InstructionBuilder::new();
    let top = b.label();
    b.place(top)
        .expect("place label")
        .emit_push(0)
        .emit_push(2)
        .emit_range()
        .emit_jump(top);
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    let err = vm
        .run(&bytecode)
        .expect_err("expected the loop stack to overflow");
    assert!(
        matches!(err, Error::LoopStackOverflow { .. }),
        "expected LoopStackOverflow, got {err:?}"
    );
}

#[test]
fn eight_thousand_one_hundred_ninety_two_frames_are_allowed() {
    // The cap is exact: the 8192nd frame is admitted and the 8193rd is not.
    let mut b = InstructionBuilder::new();
    for _ in 0..8192 {
        b.emit_push(0).emit_push(2).emit_range();
    }
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.run(&bytecode).expect("8192 frames should be admitted");
}

// ---------------------------------------------------------------------------
// reset() restores initial conditions
// ---------------------------------------------------------------------------

#[test]
fn reset_clears_the_outputs_of_the_previous_run() {
    // reset() cleared the stack, the registers, the loop stack and the two
    // counters, and left outputs, calldata and the slot count standing -- so
    // a reused VM answered with the previous run's outputs. The Rust backend
    // returned [42] where the Python one returned [] for the same reuse.
    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_input(Register(0));
    b.emit_push(0).emit_output(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_calldata(vec![RegVal::Int(42)]).set_output_slots(1);
    vm.run(&bytecode).expect("vm run");
    assert_eq!(vm.outputs(), &[RegVal::Int(42)]);

    vm.reset();
    assert!(vm.outputs().is_empty(), "outputs must not survive a reset");
    assert!(vm.stack().is_empty());
    assert_eq!(vm.steps(), 0);
    assert_eq!(vm.memory_used(), 0);

    // The calldata is gone too, so the same program now faults on INPUT
    // rather than silently reading the previous run's host input.
    let err = vm
        .run(&bytecode)
        .expect_err("calldata must not survive a reset");
    assert!(
        matches!(err, Error::CallDataIndex { .. }),
        "expected CallDataIndex, got {err:?}"
    );
}

// ---------------------------------------------------------------------------
// Guards whose only pin was a conformance vector
// ---------------------------------------------------------------------------
//
// `conformance/` is not in the `xqvm` crate tarball and QUI-1082 deletes the
// vectors after 0.4.0, so a guard reachable from bytecode needs a test that
// travels with the crate. Each test below was verified by mutating its guard
// to a no-op: before these tests existed, every per-language suite stayed
// green under that mutation and only a vector went red.

#[test]
fn one_hot_r_without_a_grid_raises() {
    // A model with no grid has no row to constrain. Writing nothing and
    // returning Continue leaves the constraint silently absent from the
    // model, which then solves cleanly and answers wrongly.
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(0).emit_push(1).emit_one_hot_r(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::InvalidGridDimensions {
                rows: 0,
                cols: 0,
                ..
            }
        ),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn one_hot_c_without_a_grid_raises() {
    // The column mirror: same guard, same identity, separate handler.
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(0).emit_push(1).emit_one_hot_c(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::InvalidGridDimensions {
                rows: 0,
                cols: 0,
                ..
            }
        ),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn resize_rejects_a_non_positive_row_count() {
    // A negative extent is not a grid. The `usize` conversion below the
    // guard rejects it too, so this pins the identity and the reported
    // extents rather than the guard; the zero case below is the one that
    // discriminates.
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(-1).emit_push(2).emit_resize(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::InvalidGridDimensions {
                rows: -1,
                cols: 2,
                ..
            }
        ),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn resize_rejects_a_zero_column_count() {
    // Zero converts cleanly, so this is the case the guard alone catches.
    // Accepting it installs `cols == 0`, which every read-only grid opcode
    // then rejects one instruction later with a fault naming the wrong
    // opcode.
    let err = run_err(|b| {
        b.emit_push(4).emit_bqmx(Register(0));
        b.emit_push(2).emit_push(0).emit_resize(Register(0));
        b.emit_halt();
    });
    assert!(
        matches!(
            err,
            Error::InvalidGridDimensions {
                rows: 2,
                cols: 0,
                ..
            }
        ),
        "expected InvalidGridDimensions, got {err:?}"
    );
}

#[test]
fn output_beyond_the_reserved_slots_raises() {
    // The slot count is the host's allocation, and `OUTPUT` may not write
    // past it. Without the bound the write silently lands nowhere and the
    // host reads an unset slot for a value the program believes it returned.
    let mut b = InstructionBuilder::new();
    b.emit_push(42).emit_stow(Register(0));
    b.emit_push(3).emit_output(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_output_slots(1);
    let err = vm.run(&bytecode).expect_err("expected error");
    assert!(
        matches!(err, Error::OutputIndex { index: 3, len: 1 }),
        "expected OutputIndex, got {err:?}"
    );
}

#[test]
fn output_to_the_last_reserved_slot_is_accepted() {
    // The bound is exclusive on the count, not on the last index: reserving
    // four slots must leave slot 3 writable.
    let mut b = InstructionBuilder::new();
    b.emit_push(42).emit_stow(Register(0));
    b.emit_push(3).emit_output(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_output_slots(4);
    vm.run(&bytecode).expect("vm run");
    assert_eq!(vm.outputs().get(3), Some(&RegVal::Int(42)));
}

#[test]
fn output_to_a_negative_slot_raises() {
    let mut b = InstructionBuilder::new();
    b.emit_push(42).emit_stow(Register(0));
    b.emit_push(-1).emit_output(Register(0)).emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_output_slots(4);
    let err = vm.run(&bytecode).expect_err("expected error");
    assert!(
        matches!(err, Error::OutputIndex { index: -1, len: 4 }),
        "expected OutputIndex, got {err:?}"
    );
}
