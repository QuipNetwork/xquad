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
use crate::dataflow::register::check_register_types;

use super::error::VerifierError;
use super::phase::Phase;

/// CFG-based forward register type-state phase.
///
/// Builds a control-flow graph over basic blocks and runs a forward worklist
/// analysis tracking the type of each of the 256 registers.  Unlike the
/// previous linear scan, this phase:
///
/// - Skips unreachable blocks (dead code after unconditional jumps), avoiding
///   false positives from writes in dead code.
/// - Propagates type state along all live CFG edges, catching type mismatches
///   on conditional-branch paths that a linear scan would miss.
///
/// At join points the per-register meet is permissive: registers written on
/// only one incoming path become `Any` (not `Unset`), so reads after such
/// joins are not flagged.  See [`crate::dataflow::register`] for the full
/// meet semantics.
pub struct RegisterTypePhase;

impl Phase for RegisterTypePhase {
    type Error = VerifierError;

    fn run(&self, program: &Program) -> Result<(), VerifierError> {
        check_register_types(program)
    }
}
