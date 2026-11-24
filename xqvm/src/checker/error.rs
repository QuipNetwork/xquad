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

use thiserror::Error;

/// Errors produced by the pre-execution bytecode checker.
///
/// Every variant carries the byte offset of the offending instruction.
#[derive(Debug, Error)]
#[expect(
    missing_docs,
    reason = "error variants are documented via their #[error(...)] display strings"
)]
pub enum CheckerError {
    /// A jump instruction references a `TARGET` label id that does not exist
    /// in the program's jump table.
    #[error(
        "jump at byte {offset:#06x} references undefined target label {label} \
         (program has {target_count} targets)"
    )]
    UndefinedJumpTarget {
        offset: usize,
        label: u16,
        target_count: usize,
    },

    /// `NEXT`, `LVAL`, or `LIDX` was reached with no `RANGE`/`ITER` active.
    #[error("loop instruction at byte {offset:#06x} executed outside any active loop")]
    NoActiveLoop { offset: usize },

    /// At least one `RANGE` or `ITER` was opened without a matching `NEXT`
    /// before the end of the program. `offset` is the byte position of the
    /// outermost unmatched loop opener.
    #[error(
        "unmatched loop: RANGE/ITER at byte {offset:#06x} has no corresponding NEXT \
         ({depth} loop(s) still open at end of program)"
    )]
    UnmatchedLoop { offset: usize, depth: usize },

    /// The instruction stream hit a truncated operand sequence.
    #[error("truncated instruction at byte {offset:#06x}")]
    TruncatedInstruction { offset: usize },

    /// The instruction stream encountered an unrecognized opcode byte.
    #[error("unknown opcode {byte:#04x} at byte {offset:#06x}")]
    BadOpcode { offset: usize, byte: u8 },
}

impl CheckerError {
    /// A stable string tag for each variant used by the conformance harness
    /// to match expected errors without coupling to the `Display` format.
    pub fn variant_name(&self) -> &'static str {
        match self {
            Self::UndefinedJumpTarget { .. } => "UndefinedJumpTarget",
            Self::NoActiveLoop { .. } => "NoActiveLoop",
            Self::UnmatchedLoop { .. } => "UnmatchedLoop",
            Self::TruncatedInstruction { .. } => "TruncatedInstruction",
            Self::BadOpcode { .. } => "BadOpcode",
        }
    }
}
