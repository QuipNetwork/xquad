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
//! A single dispatchable `submit_program` accepts raw XQBC bytecode and
//! a vector of `i64` calldata values, runs the program through the VM,
//! and emits a `ProgramExecuted` event carrying the integer output slots.
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
        #[pallet::call_index(0)]
        // Fixture-only placeholder weight; production pallets must provide benchmarked weights.
        #[pallet::weight(Weight::from_parts(10_000, 0).saturating_add(T::DbWeight::get().writes(1)))]
        pub fn submit_program(
            origin: OriginFor<T>,
            bytecode: BoundedVec<u8, T::MaxProgramSize>,
            calldata: BoundedVec<i64, T::MaxCalldata>,
        ) -> DispatchResult {
            let who = ensure_signed(origin)?;

            let program =
                xqvm::Program::decode(&bytecode).map_err(|_| Error::<T>::BytecodeInvalid)?;

            let output_count = usize::from(program.output_slots());

            let calldata_regs: Vec<xqvm::RegVal> =
                calldata.iter().map(|&v| xqvm::RegVal::Int(v)).collect();

            let mut vm = xqvm::Vm::new();
            vm.set_calldata(calldata_regs);
            vm.set_output_slots(output_count);
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

            let bounded: BoundedVec<i64, T::MaxCalldata> =
                int_outputs.try_into().map_err(|_| Error::<T>::OutputOverflow)?;

            StoredProgram::<T>::put(bytecode);
            Self::deposit_event(Event::ProgramExecuted { who, outputs: bounded });

            Ok(())
        }
    }
}
