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

//! FRAME pallet fixture that embeds the XQVM bytecode interpreter.
//!
//! This pallet is **not** for production use. It exists as a compile-time
//! and runtime integration gate: if `xqvm`'s public API changes in a way
//! that breaks Substrate pallet integration, CI fails here before it can
//! reach `quip-protocol-rs`.
//!
//! # What it does
//!
//! A single dispatchable `submit_program` accepts raw XQBC bytecode, a
//! vector of `i64` calldata values and the VM's two budgets, runs the
//! program through the VM, and emits a `ProgramExecuted` event carrying
//! the integer output slots.
//!
//! Both budgets are extrinsic arguments rather than constants because
//! that is the shape the real pallet has to take: what a caller
//! pre-pays for is what the VM may spend. The fixture is the in-repo
//! stand-in for the threat model, so a budget the caller cannot name is
//! a threat model the fixture cannot express.
//!
//! A caller-named budget is only half of that claim. The other half is
//! the pair of caps, `Config::MaxStepLimit` and `Config::MaxMemoryLimit`,
//! which bound what the caller may name: the extrinsic's weight is a
//! fixed constant, so without them a caller buys `u64::MAX` steps and a
//! gigabyte of allocation at the price of one. The bounds are what make
//! the constant weight defensible in a fixture; a production pallet
//! benchmarks the weight against the budgets instead.
//!
//! The allocation budget matters here in a way it does not off-chain.
//! `Vm::new` installs a 1 GiB default, and a runtime heap is far below
//! that, so a program that allocates a few hundred megabytes exhausts
//! the heap long before it exhausts the budget. Inside a Wasm runtime a
//! failed allocation traps the whole execution rather than returning a
//! fault the pallet can report as `Error::ExecutionFailed`, and that is
//! the case the budget exists to prevent. Leaving the default in place
//! would have left the pre-pay claim above true of steps only.
//!
//! # Admissibility
//!
//! All 93 XQVM opcodes are admissible on chain, including the model and
//! sample allocators, the vec containers, and the constraint builders. That
//! is a property of the instruction set rather than a decision each runtime
//! makes: on-chain verifiable deterministic execution is what the VM is
//! for, so an operation that cannot be metered and specified never becomes
//! an opcode in the first place. The bar is stated in the book, under
//! "On-Chain Admissibility" -- integer arithmetic only, no host state, a
//! fixed iteration order, allocation and per-instruction work both charged
//! against a budget before they happen -- the step budget prices work
//! rather than dispatches, per `spec/xqvm/METERING.md` -- and behaviour
//! specified in `spec/xqvm/` with a conformance vector covering it,
//! failure paths included. The same page records the parts of that bar the
//! tree does not yet enforce.
//!
//! A production runtime enforces the bar in two places: `verifier::verify`
//! before it stores or runs a program, and `Vm::set_memory_limit` plus
//! `Vm::set_step_limit` before it executes one. A static opcode allowlist
//! is deliberately not part of the design -- the denied set is empty by
//! construction, so there is nothing for one to scan for.
//!
//! This fixture does only the second of those. It sets both budgets, from
//! the caller's arguments under the `Config` caps, but it never calls
//! `verifier::verify` -- it decodes and runs -- and it charges a fixed
//! placeholder weight rather than a benchmarked one. That is adequate for
//! an integration gate running trusted bytecode in tests, and is not a
//! template for a runtime that admits bytecode from an untrusted account.
//!
//! # Running the fixture
//!
//! ```sh
//! make test-substrate-fixture
//! ```

#![cfg_attr(not(feature = "std"), no_std)]

extern crate alloc;

pub use pallet::*;

#[cfg(test)]
mod mock;
#[cfg(test)]
mod tests;

#[frame_support::pallet]
pub mod pallet {
    use alloc::vec::Vec;
    use frame_support::pallet_prelude::*;
    use frame_system::pallet_prelude::*;

    #[pallet::config]
    pub trait Config: frame_system::Config<RuntimeEvent: From<Event<Self>>> {
        /// Maximum byte length of an uploaded XQBC program.
        #[pallet::constant]
        type MaxProgramSize: Get<u32>;

        /// Maximum number of calldata input slots and output slots.
        #[pallet::constant]
        type MaxCalldata: Get<u32>;

        /// Largest step budget a caller may ask `submit_program` for.
        ///
        /// The extrinsic charges a fixed weight, so the step budget is the
        /// only thing bounding how long a call may run. Unbounded, the
        /// caller sets that themselves.
        #[pallet::constant]
        type MaxStepLimit: Get<u64>;

        /// Largest allocation budget a caller may ask `submit_program` for.
        ///
        /// The counterpart to `MaxStepLimit` for the VM's other budget. A
        /// runtime should set this well below the heap it actually has:
        /// the budget is what turns an over-large allocation into a
        /// reportable fault instead of a trap that takes the whole
        /// execution with it.
        #[pallet::constant]
        type MaxMemoryLimit: Get<u64>;
    }

    #[pallet::pallet]
    pub struct Pallet<T>(_);

    /// Stores the bytecode of the most recently executed program.
    #[pallet::storage]
    pub type StoredProgram<T: Config> =
        StorageValue<_, BoundedVec<u8, T::MaxProgramSize>, OptionQuery>;

    #[pallet::event]
    #[pallet::generate_deposit(pub(super) fn deposit_event)]
    pub enum Event<T: Config> {
        /// A program ran to completion.
        ProgramExecuted {
            who: T::AccountId,
            /// Integer values written to output slots (in slot order).
            /// Slots written with a non-Int value are omitted.
            outputs: BoundedVec<i64, T::MaxCalldata>,
        },
    }

    #[pallet::error]
    pub enum Error<T> {
        /// The supplied bytes are not a valid XQBC program.
        BytecodeInvalid,
        /// The VM halted with a runtime fault (stack underflow, bad jump, etc.).
        ExecutionFailed,
        /// The program produced more output slots than `MaxCalldata` allows.
        OutputOverflow,
        /// The requested step budget exceeds `MaxStepLimit`.
        StepLimitTooLarge,
        /// The requested allocation budget exceeds `MaxMemoryLimit`.
        MemoryLimitTooLarge,
        /// The requested output-slot count exceeds `MaxCalldata`.
        OutputSlotsTooLarge,
    }

    #[pallet::call]
    impl<T: Config> Pallet<T> {
        /// Execute an XQVM program and emit its integer output slots.
        ///
        /// # Parameters
        ///
        /// - `bytecode`: a valid XQBC-encoded program (produced by
        ///   `InstructionBuilder::build().encode()`).
        /// - `calldata`: `i64` values injected as `RegVal::Int` into the VM's
        ///   calldata slots before execution.
        /// - `output_slots`: the number of output slots to reserve. The
        ///   caller declares this rather than the pallet deriving it from
        ///   the bytecode: the XQBC header's `output_slots` byte counts
        ///   `OUTPUT` *instructions*, not slots, so one `OUTPUT` inside a
        ///   loop writing slots 0 and 1 records `1` against a required
        ///   count of 2 and the program faults on a slot it legitimately
        ///   writes. `spec/xqvm/ENCODING.md` states that neither header
        ///   count may be used to pre-size a slot array.
        ///   It may not exceed [`Config::MaxCalldata`], which bounds the
        ///   output vec the event carries.
        /// - `step_limit`: the number of instructions the run may execute.
        ///   The bound is exact, so `0` executes nothing and the call fails
        ///   with [`Error::ExecutionFailed`] rather than succeeding
        ///   vacuously. It may not exceed [`Config::MaxStepLimit`]: the
        ///   weight below is a constant, so an unbounded budget would let a
        ///   caller buy arbitrary execution at a fixed price.
        /// - `memory_limit`: the bytes the run may allocate. It may not
        ///   exceed [`Config::MaxMemoryLimit`]. Naming it is what keeps an
        ///   over-large allocation a reportable
        ///   [`Error::ExecutionFailed`]; left at the VM's 1 GiB default it
        ///   would instead exhaust a runtime heap and trap.
        ///
        /// # Events
        ///
        /// Emits [`Event::ProgramExecuted`] on success.
        ///
        /// # Errors
        ///
        /// - [`Error::BytecodeInvalid`] -- `bytecode` failed XQBC decode.
        /// - [`Error::ExecutionFailed`] -- the VM faulted at runtime.
        /// - [`Error::OutputOverflow`] -- output count exceeds `MaxCalldata`.
        /// - [`Error::StepLimitTooLarge`] -- `step_limit` exceeds `MaxStepLimit`.
        /// - [`Error::MemoryLimitTooLarge`] -- `memory_limit` exceeds `MaxMemoryLimit`.
        /// - [`Error::OutputSlotsTooLarge`] -- `output_slots` exceeds `MaxCalldata`.
        #[pallet::call_index(0)]
        // Fixture-only placeholder weight; production pallets must provide benchmarked weights.
        #[pallet::weight(Weight::from_parts(10_000, 0).saturating_add(T::DbWeight::get().writes(1)))]
        pub fn submit_program(
            origin: OriginFor<T>,
            bytecode: BoundedVec<u8, T::MaxProgramSize>,
            calldata: BoundedVec<i64, T::MaxCalldata>,
            output_slots: u32,
            step_limit: u64,
            memory_limit: u64,
        ) -> DispatchResult {
            let who = ensure_signed(origin)?;

            // Checked before the decode: refusing an over-budget request is
            // cheaper than parsing the program it would have run.
            ensure!(
                step_limit <= T::MaxStepLimit::get(),
                Error::<T>::StepLimitTooLarge
            );
            ensure!(
                memory_limit <= T::MaxMemoryLimit::get(),
                Error::<T>::MemoryLimitTooLarge
            );
            ensure!(
                output_slots <= T::MaxCalldata::get(),
                Error::<T>::OutputSlotsTooLarge
            );

            let program =
                xqvm::Program::decode(&bytecode).map_err(|_| Error::<T>::BytecodeInvalid)?;

            // The caller's count, not `program.output_slots()`. That header
            // byte is a saturating count of OUTPUT instructions -- see
            // `xqvm/src/verifier/scan.rs` -- so a single OUTPUT inside a
            // RANGE writing slots 0 and 1 records 1, sizes `outputs` to 1,
            // and turns a legitimate write into OutputIndex and so into
            // ExecutionFailed. spec/xqvm/ENCODING.md is explicit that
            // neither header count is a slot count and that neither may be
            // used to pre-size a slot array. Every other host in the tree
            // (xqcli's --outputs, the wasm fixture) fixes it independently.
            let output_count = usize::try_from(output_slots).unwrap_or(usize::MAX);

            let calldata_regs: Vec<xqvm::RegVal> =
                calldata.iter().map(|&v| xqvm::RegVal::Int(v)).collect();

            let mut vm = xqvm::Vm::new();
            vm.set_calldata(calldata_regs);
            vm.set_output_slots(output_count);
            vm.set_step_limit(step_limit);
            vm.set_memory_limit(memory_limit);
            vm.run(&program).map_err(|_| Error::<T>::ExecutionFailed)?;

            let int_outputs: Vec<i64> = vm
                .outputs()
                .iter()
                .filter_map(|v| {
                    if let xqvm::RegVal::Int(n) = v {
                        Some(*n)
                    } else {
                        None
                    }
                })
                .collect();

            let bounded: BoundedVec<i64, T::MaxCalldata> = int_outputs
                .try_into()
                .map_err(|_| Error::<T>::OutputOverflow)?;

            StoredProgram::<T>::put(bytecode);
            Self::deposit_event(Event::ProgramExecuted {
                who,
                outputs: bounded,
            });

            Ok(())
        }
    }
}
