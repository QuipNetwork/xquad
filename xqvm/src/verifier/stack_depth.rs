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
use crate::dataflow::bytecode::check_stack_depth;

use super::error::VerifierError;
use super::phase::Phase;

/// CFG-based forward stack-depth phase.
///
/// Builds a control-flow graph over basic blocks and runs a forward worklist
/// analysis to detect stack underflow, potential overflow, and loop-body
/// stack imbalance.  Unlike a linear scan, conditional branches are followed
/// so underflows on non-fall-through paths are also caught.
pub struct StackDepthPhase;

impl Phase for StackDepthPhase {
    type Error = VerifierError;

    fn run(&self, program: &Program) -> Result<(), VerifierError> {
        check_stack_depth(program)
    }
}
