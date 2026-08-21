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
use xqvm::{Domain, Error, RegVal, Vm};

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
fn wrapping_add_overflow() {
    let vm = run(|b| {
        b.emit_push(i64::MAX).emit_push(1).emit_add().emit_halt();
    });
    assert_eq!(vm.stack(), &[i64::MIN]);
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
fn iter_rejects_inverted_range() {
    let err = run_err(|b| {
        b.emit_vec_i(Register(0));
        b.emit_push(1).emit_vec_push(Register(0));
        b.emit_push(2).emit_vec_push(Register(0));
        b.emit_push(2).emit_push(1).emit_iter(Register(0));
        b.emit_next().emit_halt();
    });
    assert!(
        matches!(err, Error::IndexOutOfBounds { index: 1, .. }),
        "expected IndexOutOfBounds rejecting inverted range, got {err:?}"
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
    let mut b = InstructionBuilder::new();
    let top = b.label();
    b.place(top).unwrap().emit_nop().emit_jump(top);
    let bytecode = b.build().unwrap();

    let mut vm = Vm::new();
    vm.set_step_limit(0);
    let err = vm
        .run(&bytecode)
        .expect_err("a zero step limit must execute nothing");
    assert!(matches!(err, Error::StepLimitExceeded { .. }));
    assert_eq!(vm.steps(), 0, "no instruction may have run");
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
