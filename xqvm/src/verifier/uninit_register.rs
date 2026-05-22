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

use crate::Program;
use crate::dataflow::uninit::check_uninit_registers;

use super::error::VerifierError;
use super::phase::Phase;

/// CFG-based forward must-init phase.
///
/// Runs a forward AND-meet analysis over basic blocks tracking per-register
/// initialization state (`bool`).  A register is considered definitely
/// initialized only if it is written on **every** path to the read site.
///
/// This catches the case [`RegisterTypePhase`](super::register_type::RegisterTypePhase)
/// misses: a register written on only one branch of a conditional, then read
/// after the join.  The type-state pass uses a permissive `Any`-meet at join
/// points; this pass uses AND-meet so partial initialization is flagged.
///
/// Unreachable blocks are skipped.  Only [`VerifierError::ReadUnsetRegister`]
/// is emitted; type mismatches are handled by [`super::register_type::RegisterTypePhase`].
pub struct UninitRegisterPhase;

impl Phase for UninitRegisterPhase {
    type Error = VerifierError;

    fn run(&self, program: &Program) -> Result<(), VerifierError> {
        check_uninit_registers(program)
    }
}
