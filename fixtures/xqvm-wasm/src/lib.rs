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

//! Freestanding `wasm32-unknown-unknown` correctness tests for `xqvm`.
//!
//! This crate's sole purpose is to compile and run `xqvm` with
//! `default-features = false` (no `std`) inside a real WASM environment.
//! It exercises the encode/run/decode surface that the Substrate pallet
//! integration depends on.
//!
//! Run with:
//! ```sh
//! wasm-pack test --node fixtures/xqvm-wasm
//! ```

#[cfg(test)]
#[expect(
    unused_results,
    reason = "builder and vm setter methods return &mut Self; intentionally discarded in test bodies"
)]
mod tests {

    use wasm_bindgen_test::wasm_bindgen_test;
    use wasm_bindgen_test::wasm_bindgen_test_configure;
    use xqvm::{Error, Instruction, InstructionBuilder, Program, RegVal, Register, Vm};

    wasm_bindgen_test_configure!(run_in_node_experimental);

    /// PUSH 3, PUSH 4, ADD, HALT -- result 7 on stack.
    #[wasm_bindgen_test]
    fn push_add_halt() {
        let mut b = InstructionBuilder::new();
        b.emit_push(3).emit_push(4).emit_add().emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.run(&program).expect("run");

        assert_eq!(vm.stack(), &[7]);
    }

    /// INPUT reads calldata slot 0 into R0; OUTPUT writes R0 to output slot 0.
    #[wasm_bindgen_test]
    fn calldata_passthrough() {
        let mut b = InstructionBuilder::new();
        b.emit_push(0).emit_input(Register(0));
        b.emit_push(0).emit_output(Register(0));
        b.emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.set_calldata(vec![RegVal::Int(42)]);
        vm.set_output_slots(1);
        vm.run(&program).expect("run");

        assert_eq!(vm.outputs(), &[RegVal::Int(42)]);
    }

    /// Encode a program to XQBC bytes, decode back, run -- result must match.
    #[wasm_bindgen_test]
    fn encode_decode_roundtrip() {
        let mut b = InstructionBuilder::new();
        b.emit_push(99).emit_halt();
        let original = b.build().expect("build");
        let bytes = original.encode();

        let decoded = Program::decode(&bytes).expect("decode");
        let mut vm = Vm::new();
        vm.run(&decoded).expect("run");

        assert_eq!(vm.stack(), &[99]);
    }

    /// ADD with an empty stack must return Err, not panic.
    #[wasm_bindgen_test]
    fn stack_underflow_is_err() {
        let mut b = InstructionBuilder::new();
        b.emit_add().emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        assert!(vm.run(&program).is_err());
    }

    /// Two calldata values read, summed via registers, written to output.
    #[wasm_bindgen_test]
    fn two_calldata_sum() {
        let mut b = InstructionBuilder::new();
        // R0 = calldata[0], R1 = calldata[1]
        b.emit_push(0).emit_input(Register(0));
        b.emit_push(1).emit_input(Register(1));
        // push R0, push R1, add -> stack top = 7
        b.emit_load(Register(0));
        b.emit_load(Register(1));
        b.emit_add();
        // store result in R2, then write to output slot 0
        b.emit_stow(Register(2));
        b.emit_push(0).emit_output(Register(2));
        b.emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.set_calldata(vec![RegVal::Int(3), RegVal::Int(4)]);
        vm.set_output_slots(1);
        vm.run(&program).expect("run");

        assert_eq!(vm.outputs(), &[RegVal::Int(7)]);
    }

    /// A model's declared size and its coefficient writes are charged at the
    /// same per-entry rate on wasm32 as on every other target: 8 bytes per
    /// declared variable, 32 bytes per linear entry, 48 bytes per quadratic
    /// entry.
    ///
    /// `LINEAR_ENTRY_BYTES` and `QUAD_ENTRY_BYTES` used to be derived as
    /// `2 * (size_of::<usize>() + size_of::<i64>())`, which gives 32 and 48
    /// on a 64-bit host but 24 and 32 on wasm32, where `usize` is 4 bytes
    /// wide. The reference VM, the book, and every native test said 32 and
    /// 48, so the deployed wasm VM -- the one the Substrate pallet actually
    /// runs -- enforced a charge schedule nothing else in the repository
    /// reproduced. Two nodes charging differently for the same bytecode is a
    /// consensus split. The rates are literals now; this test pins the exact
    /// total so a rate re-derived from pointer width on any target goes red
    /// here instead of on chain.
    #[wasm_bindgen_test]
    fn coefficient_writes_charge_the_declared_schedule() {
        let mut b = InstructionBuilder::new();
        // BQMX r0 declares a 2-variable model: 2 * 8 = 16 bytes.
        b.emit_push(2).emit_bqmx(Register(0));
        // SETLINE i=0, val=5: one linear entry, 32 bytes.
        b.emit_push(0).emit_push(5).emit_set_line(Register(0));
        // SETQUAD i=0, j=1, val=7: one quadratic entry, 48 bytes.
        b.emit_push(0)
            .emit_push(1)
            .emit_push(7)
            .emit_set_quad(Register(0));
        b.emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.run(&program).expect("run");

        assert_eq!(
            vm.memory_used(),
            2 * 8 + 32 + 48,
            "schedule: 8 bytes/variable, 32 bytes/linear entry, 48 bytes/quadratic entry"
        );
    }

    /// A program that asks the allocation budget for more than it has left
    /// must get a fault back from `run`, not a trap.
    ///
    /// Inside a Wasm runtime a failed allocation traps the whole execution
    /// instead of returning a fault the host can report -- there is no
    /// `Result` to inspect, the guest instance is simply gone. Charging an
    /// allocator's declared size against the budget *before* it allocates,
    /// rather than letting the allocation itself fail, is the rule that
    /// keeps this reachable as an ordinary `Err` on wasm32.
    #[wasm_bindgen_test]
    fn exhausted_budget_faults_instead_of_trapping() {
        let mut b = InstructionBuilder::new();
        b.emit_push(1 << 20).emit_bsmx(Register(0)).emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.set_memory_limit(1 << 10);
        let err = vm
            .run(&program)
            .expect_err("expected the budget to reject the allocation");

        assert!(
            matches!(err, Error::MemoryLimitExceeded { .. }),
            "expected MemoryLimitExceeded, got {err:?}"
        );
    }

    /// QUI-1315: an allocator size past `MAX_ALLOCATION_SIZE`
    /// (`u32::MAX as i64`, `4_294_967_295`) is range-checked after the budget
    /// charge and rejected with `InvalidAllocation`, independent of the
    /// target's pointer width -- on a 64-bit host the same size would
    /// otherwise narrow to a `usize` and allocate a legitimate, if huge,
    /// sparse model.
    ///
    /// The budget here (64 GiB) is large enough to pay the charge for
    /// requesting 2^32 variables, so the run fails on the range check, not
    /// on the memory budget. The model is sparse, so nothing is actually
    /// allocated even though the declared size is charged first.
    #[wasm_bindgen_test]
    fn allocator_size_past_the_maximum_is_invalid() {
        let mut b = InstructionBuilder::new();
        b.emit_push(4_294_967_296)
            .emit_bqmx(Register(0))
            .emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.set_memory_limit(64u64 << 30);
        let err = vm
            .run(&program)
            .expect_err("expected the oversized allocator to be rejected");

        assert!(
            matches!(err, Error::InvalidAllocation { .. }),
            "expected InvalidAllocation, got {err:?}"
        );
    }
    /// QUI-1315, second half: the same maximum applied to a model that grows
    /// after it was allocated.
    ///
    /// `REDUCE` appends one auxiliary variable, and the growth used to be an
    /// unchecked `model.size += 1` justified by the allocation budget having
    /// paid for the model. The budget bounds a model in bytes, not in this
    /// target's `usize` -- and `MAX_ALLOCATION_SIZE` is `u32::MAX`, which on
    /// wasm32 is also `usize::MAX`. So a model at the maximum overflowed
    /// here and nowhere else: a panic under overflow checks, a wrap to zero
    /// in release, against a 64-bit host that grew the model to 2^32 and
    /// carried on.
    ///
    /// This is the test the native suite cannot stand in for. On a 64-bit
    /// host the unchecked addition is simply correct, so only a 32-bit
    /// target can show that the bound is doing anything at all.
    #[wasm_bindgen_test]
    fn model_growth_past_the_maximum_is_invalid() {
        let mut b = InstructionBuilder::new();
        b.emit_push(4_294_967_295)
            .emit_bqmx(Register(0))
            .emit_push(0)
            .emit_push(1)
            .emit_push(5)
            .emit(Instruction::Reduce { model: Register(0) })
            .emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.set_memory_limit(64u64 << 30);
        let err = vm
            .run(&program)
            .expect_err("expected growth past the maximum to be rejected");

        assert!(
            matches!(err, Error::InvalidAllocation { .. }),
            "expected InvalidAllocation, got {err:?}"
        );
    }

    /// `EQUALITY` grows the model to cover the largest index it was handed,
    /// so that index is a model size and the maximum bounds it.
    ///
    /// This target is the one that used to disagree. The size was narrowed
    /// with `usize::try_from(..).ok()` and the failure swallowed, so an index
    /// past a 32-bit width collapsed the needed size to zero here, charged
    /// nothing, and fell through to `IndexOutOfBounds`, while 64-bit Rust and
    /// `xqvm_py` charged the full growth and raised `MemoryLimitExceeded`.
    /// That split was reachable at the shipped 1 GiB default budget, unlike
    /// the allocator operand's, which needs a budget above 32 GiB.
    ///
    /// The budget here pays the growth charge so the range check is what
    /// decides, which is the same answer every target now gives.
    #[wasm_bindgen_test]
    fn an_equality_index_past_the_maximum_is_invalid() {
        let mut b = InstructionBuilder::new();
        b.emit_push(1).emit_bqmx(Register(0));
        b.emit_vec_i(Register(1)).emit_vec_i(Register(2));
        b.emit_push(4_294_967_295).emit_vec_push(Register(1));
        b.emit_push(1).emit_vec_push(Register(2));
        b.emit_push(1).emit_push(1).emit(Instruction::Equality {
            model: Register(0),
            indices: Register(1),
            coeffs: Register(2),
        });
        b.emit_halt();
        let program = b.build().expect("build");

        let mut vm = Vm::new();
        vm.set_memory_limit(64u64 << 30);
        let err = vm
            .run(&program)
            .expect_err("expected the grown size to be rejected");

        assert!(
            matches!(err, Error::InvalidAllocation { .. }),
            "expected InvalidAllocation, got {err:?}"
        );
    }
}
