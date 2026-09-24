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

/// Errors produced by the pre-execution bytecode verifier.
///
/// Each variant carries the byte offset of the offending instruction so that
/// diagnostics can highlight the exact location in the bytecode stream.
#[derive(Debug, Error)]
#[expect(
    missing_docs,
    reason = "error variants are documented via their #[error(...)] display strings"
)]
pub enum VerifierError {
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

    /// An instruction tried to read a register that has never been written.
    #[error("register r{reg} read at byte {offset:#06x} before being written")]
    ReadUnsetRegister { offset: usize, reg: u8 },

    /// An instruction found a register holding the wrong type.
    #[error("register r{reg} at byte {offset:#06x}: expected {expected}, got {got}")]
    RegisterTypeMismatch {
        offset: usize,
        reg: u8,
        expected: &'static str,
        got: &'static str,
    },

    /// A stack-consuming instruction was reached with insufficient items on the stack.
    ///
    /// An instruction needs as many values as it pops, checked before any
    /// of its pushes are counted, so an opcode that both pops and pushes
    /// (e.g. `IDXGRID`: pop 3, push 1) is held to its full pop count. See
    /// "Stack underflow rule" under Phase 4 in `spec/xqvm/VERIFIER.md`.
    #[error("stack underflow at byte {offset:#06x}")]
    StackUnderflow { offset: usize },

    /// The stack depth may exceed the VM limit of 8192 at this instruction.
    #[error("potential stack overflow at byte {offset:#06x} (depth {depth})")]
    StackOverflowRisk { offset: usize, depth: usize },

    /// A loop body's net stack effect is non-zero: the depth at `NEXT` differs
    /// from the depth at the matching `RANGE`/`ITER` opener.
    #[error(
        "loop starting at byte {opener:#06x} has non-zero stack effect \
         (entry depth {entry}, exit depth {exit})"
    )]
    LoopStackImbalance {
        opener: usize,
        entry: usize,
        exit: usize,
    },

    /// Two control-flow paths arrive at a join point with different stack
    /// depths, making the stack state undefined at that point.  This is a
    /// structural ill-formedness: the stack depth after this join point is
    /// not deterministic, so subsequent instructions may underflow on one
    /// path while appearing correct on another.
    #[error(
        "stack depth mismatch at join point byte {target_offset:#06x}: \
         one path has depth {depth_a}, another has depth {depth_b}"
    )]
    StackDepthMismatch {
        target_offset: usize,
        depth_a: usize,
        depth_b: usize,
    },
}

impl VerifierError {
    /// A stable string tag for each variant used by the conformance harness
    /// to match expected errors without coupling to the `Display` format.
    pub fn variant_name(&self) -> &'static str {
        match self {
            Self::UndefinedJumpTarget { .. } => "UndefinedJumpTarget",
            Self::NoActiveLoop { .. } => "NoActiveLoop",
            Self::UnmatchedLoop { .. } => "UnmatchedLoop",
            Self::TruncatedInstruction { .. } => "TruncatedInstruction",
            Self::BadOpcode { .. } => "BadOpcode",
            Self::ReadUnsetRegister { .. } => "ReadUnsetRegister",
            Self::RegisterTypeMismatch { .. } => "RegisterTypeMismatch",
            Self::StackUnderflow { .. } => "StackUnderflow",
            Self::StackOverflowRisk { .. } => "StackOverflowRisk",
            Self::LoopStackImbalance { .. } => "LoopStackImbalance",
            Self::StackDepthMismatch { .. } => "StackDepthMismatch",
        }
    }
}
