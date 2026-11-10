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

//! Execution tracing for the XQVM interpreter.
//!
//! The [`Tracer`] trait is the observer interface injected into
//! [`Vm::run_trace`](crate::Vm::run_trace). A [`NoopTracer`] eliminates all
//! tracing overhead via monomorphization.
//!
//! Concrete formatters ([`TextTracer`], [`JsonTracer`]) are available behind
//! `#[cfg(feature = "std")]`.

#[cfg(feature = "std")]
mod json;
#[cfg(feature = "std")]
mod text;

#[cfg(feature = "std")]
pub use json::JsonTracer;
#[cfg(feature = "std")]
pub use text::TextTracer;

use crate::bytecode::Instruction;

use crate::value::RegVal;

/// Snapshot of VM state passed to the tracer after each instruction executes.
#[derive(Debug)]
pub struct StepState<'a> {
    /// Byte offset of the current instruction in the code section.
    pub pos: usize,
    /// Step number (1-based).
    pub step: u64,
    /// The instruction that just executed.
    pub instruction: &'a Instruction,
    /// Current stack contents (bottom-first).
    pub stack: &'a [i64],
    /// Registers read by this instruction (index, value at read time).
    pub read_regs: &'a [(u8, RegVal)],
    /// Registers written by this instruction (index, new value after exec).
    pub written_regs: &'a [(u8, RegVal)],
    /// Current loop nesting depth.
    pub loop_depth: usize,
}

/// Observer notified on every VM execution step.
///
/// Implement this trait to receive step-by-step execution state.
/// The associated [`ENABLED`](Tracer::ENABLED) constant allows the compiler
/// to eliminate all tracing code when set to `false`.
///
/// A tracer can also ask to be polled between instructions, through
/// [`POLL_INTERVAL`](Tracer::POLL_INTERVAL) and [`poll`](Tracer::poll), so
/// an embedder can stop a long run from outside the program, for example on
/// a host signal. Polling is independent of `ENABLED`: a tracer that records
/// nothing can still poll.
pub trait Tracer {
    /// Error type for when tracing I/O fails.
    type Error;

    /// Whether this tracer is active. When `false`, the VM skips all
    /// snapshotting and tracing calls, guaranteeing zero overhead.
    const ENABLED: bool = true;

    /// Metered steps between calls to [`poll`](Tracer::poll); `0`, the
    /// default, never polls.
    ///
    /// The VM polls before the first instruction that would take the steps
    /// charged since the previous poll (or since the run started) past this
    /// interval, so an instruction charging many steps can carry the count
    /// well past it. The boundary shares the step budget's comparison, so
    /// an instruction between two polls costs what it costs without the
    /// hook.
    const POLL_INTERVAL: u64 = 0;

    /// Called after each instruction executes, with the resulting state.
    ///
    /// # Errors
    ///
    /// Returns `Self::Error` if the tracer fails to record the step.
    fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error>;

    /// Called between two instructions, every
    /// [`POLL_INTERVAL`](Tracer::POLL_INTERVAL) metered steps.
    ///
    /// Every instruction before the poll has finished, control flow
    /// included, and the next one has been decoded but not charged. An error
    /// stops the run there with
    /// [`Error::TraceFailed`](crate::Error::TraceFailed) at the offset of
    /// that next instruction, leaving the stack, registers, outputs and
    /// counters as the instructions before it left them. The default does
    /// nothing.
    ///
    /// # Errors
    ///
    /// Returns `Self::Error` to stop the run.
    fn poll(&mut self) -> Result<(), Self::Error> {
        Ok(())
    }
}

/// A tracer that does nothing. Used as the default for [`Vm::run`](crate::Vm::run).
///
/// The compiler eliminates all tracing overhead via monomorphization and
/// dead-code elimination (DCE), producing identical code to a tracer-free
/// execution loop.
#[derive(Debug, Clone, Copy)]
pub struct NoopTracer;

impl Tracer for NoopTracer {
    type Error = core::convert::Infallible;

    const ENABLED: bool = false;

    #[inline]
    fn on_step(&mut self, _state: &StepState<'_>) -> Result<(), Self::Error> {
        Ok(())
    }
}
