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

//! Mock runtime for `pallet-xqvm` tests.

use crate::{self as pallet_xqvm};
use frame_support::{derive_impl, parameter_types};
use sp_runtime::BuildStorage;

type Block = frame_system::mocking::MockBlock<Test>;

frame_support::construct_runtime!(
    pub enum Test {
        System: frame_system,
        XqvmPallet: pallet_xqvm,
    }
);

#[derive_impl(frame_system::config_preludes::TestDefaultConfig)]
impl frame_system::Config for Test {
    type Block = Block;
}

parameter_types! {
    /// 64 KiB -- enough for any realistic fixture program.
    pub const MaxProgramSize: u32 = 65_536;
    /// 256 slots matches the xqvm register file width.
    pub const MaxCalldata: u32 = 256;
}

impl pallet_xqvm::Config for Test {
    type MaxProgramSize = MaxProgramSize;
    type MaxCalldata = MaxCalldata;
}

/// Build a `TestExternalities` with block number 1 initialised.
///
/// frame_system only deposits events when the block number is non-zero;
/// calling `System::set_block_number(1)` is required before events appear
/// in `System::events()`.
pub fn new_test_ext() -> sp_io::TestExternalities {
    let t = frame_system::GenesisConfig::<Test>::default()
        .build_storage()
        .unwrap();
    let mut ext = sp_io::TestExternalities::new(t);
    ext.execute_with(|| System::set_block_number(1));
    ext
}
