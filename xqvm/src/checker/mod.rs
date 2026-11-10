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

//! Pre-execution bytecode checker for XQVM programs.
//!
//! Checking is structured as composable [`Phase`]s. Each phase performs
//! one focused check over a [`Program`] and maps failures to [`CheckerError`]
//! via its `type Error: Into<CheckerError>`. A [`Checker`] runs phases
//! sequentially, stopping at the first failure.
//!
//! The low-level `scan` function is the shared kernel: it makes one linear
//! pass over the instruction bytes, builds the [`crate::JumpTable`], and returns the
//! first structural violation. [`Program::new`] calls it to obtain the jump
//! table (ignoring any error); [`Checker::default`] calls it once per
//! invocation rather than running three separate stream passes.
//!
//! # Phases
//!
//! | Phase | What it checks | Errors emitted |
//! |---|---|---|
//! | [`StructuralPhase`] | Truncated bytes, unknown opcodes | [`CheckerError::TruncatedInstruction`], [`CheckerError::BadOpcode`] |
//! | [`JumpTargetPhase`] | Jump label ≥ target count | [`CheckerError::UndefinedJumpTarget`] |
//! | [`LoopNestingPhase`] | Loop open/close balance, loop-context reads | [`CheckerError::NoActiveLoop`], [`CheckerError::UnmatchedLoop`] |
//! | [`RegisterTypePhase`] | CFG-based read-before-write; type-state mismatches | [`CheckerError::ReadUnsetRegister`], [`CheckerError::RegisterTypeMismatch`] |
//! | [`UninitRegisterPhase`] | CFG AND-meet: registers uninit on at least one path | [`CheckerError::ReadUnsetRegister`] |
//! | [`StackDepthPhase`] | CFG-based stack depth: underflow/overflow, loop balance, join-point mismatch | [`CheckerError::StackUnderflow`], [`CheckerError::StackOverflowRisk`], [`CheckerError::LoopStackImbalance`], [`CheckerError::StackDepthMismatch`] |
//!
//! The structural, jump, and loop phases run as a single combined pass via
//! `scan`. The remaining phases each build a control-flow graph and run a
//! forward worklist analysis:
//!
//! * [`RegisterTypePhase`] -- permissive Any-meet at join points; catches
//!   read-before-write and type mismatches on all CFG paths.
//! * [`UninitRegisterPhase`] -- AND-meet at join points; flags registers that
//!   are definitely unset on at least one incoming path.
//! * [`StackDepthPhase`] -- min-meet for underflow detection, plus a strict-
//!   equality pass over each join point's incoming edges (program entry
//!   included) for [`CheckerError::StackDepthMismatch`].
//!
//! # Quick start
//!
//! ```rust
//! use xqvm::{checker, InstructionBuilder};
//!
//! let mut b = InstructionBuilder::new();
//! b.emit_push(1).emit_push(2).emit_add().emit_halt();
//! let program = b.build().unwrap();
//! assert!(checker::check(&program).is_ok());
//! ```
//!
//! # Custom composition
//!
//! ```rust
//! use xqvm::checker::{Phase, Checker, JumpTargetPhase, LoopNestingPhase};
//! use xqvm::InstructionBuilder;
//!
//! let mut b = InstructionBuilder::new();
//! b.emit_halt();
//! let program = b.build().unwrap();
//!
//! // Run only the jump and loop checks, skip the structural pass.
//! let result = Checker::new()
//!     .with_phase(JumpTargetPhase)
//!     .with_phase(LoopNestingPhase)
//!     .run(&program);
//! assert!(result.is_ok());
//! ```

mod error;
mod jump_target;
mod loop_nesting;
mod phase;
pub(crate) mod reg_type;
mod register_type;
mod scan;
mod stack_depth;
mod structural;
mod uninit_register;

#[cfg(test)]
mod tests;

// ---------------------------------------------------------------------------
// Public re-exports
// ---------------------------------------------------------------------------

pub use error::CheckerError;
pub use jump_target::JumpTargetPhase;
pub use loop_nesting::LoopNestingPhase;
pub use phase::{Checker, Phase};
pub use reg_type::RegType;
pub use register_type::RegisterTypePhase;
pub use stack_depth::StackDepthPhase;
pub use structural::StructuralPhase;
pub use uninit_register::UninitRegisterPhase;

pub(crate) use scan::scan;

use crate::Program;

/// Run the full four-phase checker over `program`.
///
/// Equivalent to `Checker::default().run(program)`. Runs `CombinedPhase`
/// (structural, jump-target, loop-nesting) followed by [`RegisterTypePhase`]
/// (CFG-based read-before-write and type-state checks) followed by
/// [`UninitRegisterPhase`] (CFG AND-meet must-init analysis) followed by
/// [`StackDepthPhase`] (CFG-based stack depth, loop balance, and join-point
/// depth consistency). Returns the first [`CheckerError`] found, or
/// `Ok(())` if all checks pass.
///
/// # Errors
///
/// Returns a [`CheckerError`] on the first violation detected.
///
/// # Examples
///
/// ```rust
/// use xqvm::{checker, InstructionBuilder};
///
/// let mut b = InstructionBuilder::new();
/// b.emit_push(42).emit_halt();
/// assert!(checker::check(&b.build().unwrap()).is_ok());
/// ```
pub fn check(program: &Program) -> Result<(), CheckerError> {
    Checker::default().run(program)
}
