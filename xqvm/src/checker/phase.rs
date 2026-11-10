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

#[cfg(not(feature = "std"))]
use alloc::{boxed::Box, vec::Vec};

use crate::Program;
use crate::dataflow::bytecode::{build_cfg, check_stack_depth_with_context};
use crate::dataflow::register::check_register_types_with_cfg;
use crate::dataflow::uninit::check_uninit_registers_with_cfg;

use super::error::CheckerError;
use super::scan::scan;

/// A single composable checking pass over a [`Program`].
///
/// `type Error` is the phase-specific failure type. It must implement
/// `Into<CheckerError>` so it can be collected by a [`Checker`].
///
/// # Implementing a phase
///
/// ```rust
/// use xqvm::checker::{Phase, CheckerError};
/// use xqvm::bytecode::Program;
///
/// pub struct NopPhase;
///
/// impl Phase for NopPhase {
///     type Error = CheckerError;
///     fn run(&self, _program: &Program) -> Result<(), CheckerError> {
///         Ok(())
///     }
/// }
/// ```
pub trait Phase {
    /// The error this phase can produce. Must be convertible to [`CheckerError`].
    type Error: Into<CheckerError>;

    /// Run this phase over `program`.
    ///
    /// # Errors
    ///
    /// Returns a phase-specific error on the first violation detected.
    fn run(&self, program: &Program) -> Result<(), Self::Error>;
}

/// Type-erased, heap-allocated phase closure.
type PhaseBox = Box<dyn Fn(&Program) -> Result<(), CheckerError>>;

/// A sequenced collection of [`Phase`]s that run over a [`Program`].
///
/// Build a checker with the fluent [`with_phase`](Self::with_phase) method.
/// [`Checker::default`] creates the standard checker with all built-in checks.
///
/// Phases run sequentially; the first failure stops the chain (fail-fast).
///
/// # Examples
///
/// ```rust
/// use xqvm::checker::{Checker, StructuralPhase, JumpTargetPhase};
/// use xqvm::InstructionBuilder;
///
/// let mut b = InstructionBuilder::new();
/// b.emit_halt();
/// let program = b.build().unwrap();
///
/// let result = Checker::new()
///     .with_phase(StructuralPhase)
///     .with_phase(JumpTargetPhase)
///     .run(&program);
/// assert!(result.is_ok());
/// ```
pub struct Checker {
    phases: Vec<PhaseBox>,
}

impl Checker {
    /// Create an empty checker with no phases.
    pub fn new() -> Self {
        Self { phases: Vec::new() }
    }

    /// Append a phase to this checker.
    ///
    /// The phase is type-erased: its concrete `Phase::Error` is converted to
    /// [`CheckerError`] via `Into` at the point of insertion.
    #[must_use]
    pub fn with_phase<P>(mut self, phase: P) -> Self
    where
        P: Phase + 'static,
    {
        self.phases
            .push(Box::new(move |prog| phase.run(prog).map_err(Into::into)));
        self
    }

    /// Run all phases in insertion order, returning the first error.
    ///
    /// # Errors
    ///
    /// Returns the first [`CheckerError`] produced by any phase.
    pub fn run(&self, program: &Program) -> Result<(), CheckerError> {
        for phase in &self.phases {
            phase(program)?;
        }
        Ok(())
    }
}

impl Default for Checker {
    /// Creates the default checker with all built-in checks.
    ///
    /// Callers that need selective phase composition should use [`Checker::new`]
    /// combined with specific phase types.
    fn default() -> Self {
        Self::new()
            .with_phase(CombinedPhase)
            .with_phase(AllCfgPhases)
    }
}

/// Runs the Phase 1 check set in a single stream pass via [`scan`].
///
/// Used by [`Checker::default`]; not exposed publicly because callers that
/// need selective phase composition should use the individual named phases.
pub(super) struct CombinedPhase;

impl Phase for CombinedPhase {
    type Error = CheckerError;

    fn run(&self, program: &Program) -> Result<(), CheckerError> {
        let (_table, _slots, err) = scan(program.code());
        err.map_or(Ok(()), Err)
    }
}

/// Builds the CFG once and runs all three CFG-based checks in sequence.
///
/// Used by [`Checker::default`] to avoid triple CFG construction when all
/// three analyses are needed together.
pub(super) struct AllCfgPhases;

impl Phase for AllCfgPhases {
    type Error = CheckerError;

    fn run(&self, program: &Program) -> Result<(), CheckerError> {
        let code = program.code();
        if code.is_empty() {
            return Ok(());
        }
        let Some(ctx) = build_cfg(code, program.jump_table()) else {
            return Ok(());
        };
        check_register_types_with_cfg(program, &ctx.cfg)?;
        check_uninit_registers_with_cfg(program, &ctx.cfg)?;
        check_stack_depth_with_context(&ctx.cfg, &ctx.effects, &ctx.loop_regions)
    }
}
