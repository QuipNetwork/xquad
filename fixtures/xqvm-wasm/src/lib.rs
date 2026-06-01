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
    use xqvm::{InstructionBuilder, Program, RegVal, Register, Vm};

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
}
