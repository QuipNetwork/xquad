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
use crate::bytecode::InstructionStream;

use super::error::VerifierError;
use super::phase::Phase;
use super::scan::stream_err;

/// Checks that every byte in the instruction stream decodes without error.
///
/// Catches [`VerifierError::TruncatedInstruction`] and
/// [`VerifierError::BadOpcode`]. This phase should run first so subsequent
/// phases can safely iterate the stream without re-checking decode errors.
pub struct StructuralPhase;

impl Phase for StructuralPhase {
    type Error = VerifierError;

    fn run(&self, program: &Program) -> Result<(), VerifierError> {
        let mut stream = InstructionStream::new(program.code());
        while let Some(item) = stream.next_instruction() {
            let _ = item.map_err(|e| stream_err(&e))?;
        }
        Ok(())
    }
}
