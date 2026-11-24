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
//!
//! [`Checker::default`] runs all three as a single combined pass via `scan`.
//! The checker reads the bytecode's shape only: it does not follow control
//! flow, so register contents and stack depth are the VM's to enforce as the
//! program runs.
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
mod scan;
mod structural;

#[cfg(test)]
mod tests;

// ---------------------------------------------------------------------------
// Public re-exports
// ---------------------------------------------------------------------------

pub use error::CheckerError;
pub use jump_target::JumpTargetPhase;
pub use loop_nesting::LoopNestingPhase;
pub use phase::{Checker, Phase};
pub use structural::StructuralPhase;

pub(crate) use scan::scan;

use crate::Program;

/// Run the structural checker over `program`.
///
/// Equivalent to `Checker::default().run(program)`: one pass over the
/// instruction bytes that checks every instruction decodes, every jump
/// names a defined `TARGET`, and every `RANGE`/`ITER` is closed by a `NEXT`
/// with `NEXT`, `LVAL` and `LIDX` only inside an open loop. Returns the first
/// [`CheckerError`] found, or `Ok(())` if all checks pass.
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
