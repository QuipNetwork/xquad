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

//! Runtime error types for the XQVM interpreter.
//!
//! [`enum@Error`] describes every fault that can occur during bytecode execution.
//! For rich terminal diagnostics (requires the `std` feature), convert it to a
//! [`RuntimeDiagnostic`] via [`Error::into_diagnostic`], which disassembles the
//! bytecode and points a miette caret at the failing instruction.
//!
//! # Examples
//!
//! ```rust
//! use xqvm::Error;
//! use xqvm::{Instruction, InstructionBuilder};
//!
//! let mut builder = InstructionBuilder::new();
//! builder.emit_push(0).emit(Instruction::Halt {});
//! let program = builder.build().unwrap();
//!
//! let err = Error::DivisionByZero { pos: 0 };
//! # #[cfg(feature = "std")]
//! let _diag = err.into_diagnostic(&program, "prog.xqbc");
//! // diag implements miette::Diagnostic and can be returned from main()
//! ```

#[cfg(not(feature = "std"))]
use alloc::string::String;

#[cfg(feature = "std")]
use miette::{Diagnostic, NamedSource, SourceSpan};
use thiserror::Error;

#[cfg(feature = "std")]
use crate::bytecode::Program;
#[cfg(feature = "std")]
use crate::disasm::Disassembly;

/// Renders an optional byte offset as the `" at byte 0x0000"` suffix every
/// other variant spells out inline, or as nothing when there is no position.
///
/// Display-only, and allocation-free so the `no_std` build carries no `format!`.
struct OptionalPos(Option<usize>);

impl core::fmt::Display for OptionalPos {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self.0 {
            Some(pos) => write!(f, " at byte {pos:#06x}"),
            None => Ok(()),
        }
    }
}

/// Errors that can occur during XQVM bytecode execution.
///
/// This enum is deliberately not `#[non_exhaustive]`. The attribute forces a
/// wildcard arm on matches in *downstream* crates, and `fault_from_rust` in
/// the `conformance` crate depends on not having one: it maps each variant
/// onto the implementation-neutral fault identity that `spec/xqvm/SPEC.md`
/// specifies, and being wildcard-free is what makes adding a variant without
/// extending it a compile error rather than a silent degradation to an
/// "unknown" fault -- which is the whole value of those identities being
/// normative. The `byte_pos` helper below is a second wildcard-free match
/// over this enum, but it lives in this crate, where the attribute would
/// change nothing.
///
/// The cost is accepted rather than overlooked: adding a variant is a
/// breaking change to this crate's public API and needs a major version bump.
#[derive(Debug, Error)]
#[expect(
    missing_docs,
    reason = "error variants are documented via their #[error(...)] display strings"
)]
pub enum Error {
    /// The value stack was empty when a pop was attempted.
    #[error("stack underflow at byte {pos:#06x}")]
    StackUnderflow { pos: usize },

    /// The value stack exceeded the 8192-element depth limit.
    #[error("stack overflow at byte {pos:#06x} (limit: 8192)")]
    StackOverflow { pos: usize },

    /// A register held the wrong value kind for the operation.
    #[error("register r{reg} holds {got}, expected {expected}")]
    RegisterType {
        reg: u8,
        expected: &'static str,
        got: &'static str,
    },

    /// Type mismatch without register context. Produced via
    /// `From<IncompatibleTypeError>` when the caller does not track which
    /// register triggered the fault.
    #[error("{0}")]
    IncompatibleType(crate::value::IncompatibleTypeError),

    /// A `LOAD` was attempted on a register that is unset (`DROP`ped or
    /// never written). Matches xq-py's `RegisterNotFound` exception.
    #[error("register r{reg} is unset at byte {pos:#06x}")]
    UnsetRegister { pos: usize, reg: u8 },

    /// Division or modulo by zero.
    #[error("division by zero at byte {pos:#06x}")]
    DivisionByZero { pos: usize },

    /// An operation produced a value outside the signed 64-bit range.
    ///
    /// Raised by every `i64` operation the VM performs on a program's
    /// behalf, intermediates included: arithmetic and shift opcodes, loop
    /// control, index construction, grid address arithmetic, constraint
    /// expansion and `ENERGY` accumulation. Because each step is checked
    /// rather than the final result, a computation whose mathematical answer
    /// is representable still faults when a partial result is not.
    ///
    /// `pos` is the byte offset of the faulting instruction where one is
    /// known. Model mutations and reductions raise without a position,
    /// because they are reached through an API that carries no program
    /// counter; the VM supplies the position when it calls them.
    #[error("arithmetic overflow{}", OptionalPos(*pos))]
    ArithmeticOverflow { pos: Option<usize> },

    /// An index operand fell outside the range of what it addresses.
    ///
    /// Not only a vec index. The same identity covers a coefficient index
    /// against a model's declared size, a grid row or column against the
    /// extent `RESIZE` declared, and a sample assignment index, so the
    /// message names neither the container nor the opcode. `len` is the
    /// exclusive bound the index had to fall under.
    #[error("index {index} out of bounds (len {len}) at byte {pos:#06x}")]
    IndexOutOfBounds { pos: usize, index: i64, len: usize },

    /// NEXT or LVAL executed outside a loop.
    #[error("loop instruction at byte {pos:#06x} with no active loop")]
    NoActiveLoop { pos: usize },

    /// Jump target is outside the bytecode buffer.
    #[error("jump from {pos:#06x} to {target:#010x} is out of bounds")]
    BadJumpTarget { pos: usize, target: usize },

    /// A JUMP/JUMPI referenced a label not present in the jump table.
    #[error("invalid label .{label} at byte {pos:#06x}")]
    InvalidLabel { pos: usize, label: u16 },

    /// The bytecode stream returned a decode error.
    #[error("bad opcode {byte:#04x} at byte {pos:#06x}")]
    BadOpcode { pos: usize, byte: u8 },

    /// The bytecode stream hit a truncated instruction.
    #[error("truncated instruction at byte {pos:#06x}")]
    TruncatedInstruction { pos: usize },

    /// Calldata index out of range.
    #[error("calldata index {index} out of range (len {len})")]
    CallDataIndex { index: i64, len: usize },

    /// Output index out of range.
    #[error("output index {index} out of range (len {len})")]
    OutputIndex { index: i64, len: usize },

    /// The model and sample sizes do not match.
    #[error("model has {model_size} variables but sample has {sample_len}")]
    SizeMismatch {
        model_size: usize,
        sample_len: usize,
    },

    /// Two parallel vectors used together (e.g. EQUALITY's `indices` and
    /// `coeffs` registers) have different lengths.
    #[error("vector length mismatch: {what} has {a} entries but {other} has {b}")]
    VecLengthMismatch {
        what: &'static str,
        a: usize,
        other: &'static str,
        b: usize,
    },

    /// Execution exceeded the configured step budget.
    ///
    /// `requested` is the charge that could not be paid: `1` for the base
    /// cost every instruction pays before dispatch, or the extra units an
    /// opcode asked for before doing work whose size the program controls.
    /// `pos` is absent only for the base charge, which is levied before the
    /// instruction is decoded.
    #[error(
        "step charge of {requested} exceeds the step limit of {limit} \
         ({used} steps already charged)"
    )]
    StepLimitExceeded {
        pos: Option<usize>,
        requested: u64,
        used: u64,
        limit: u64,
    },

    /// An allocating instruction asked for more memory than the remaining
    /// allocation budget allows. Distinct from [`Error::StepLimitExceeded`]
    /// so an embedder can tell a runaway loop from an oversized allocation.
    #[error(
        "allocation of {requested} bytes at byte {pos:#06x} exceeds the memory limit \
         ({used} of {limit} bytes already charged)"
    )]
    MemoryLimitExceeded {
        pos: usize,
        requested: u64,
        used: u64,
        limit: u64,
    },

    /// Left shift amount is negative or too large.
    #[error("invalid shift amount {amount} at byte {pos:#06x}")]
    InvalidShift { pos: usize, amount: i64 },

    /// A grid extent is unusable, on `RESIZE` or on an opcode that reads one.
    ///
    /// Three distinct conditions share the identity, and the `rows`/`cols`
    /// pair in the message tells them apart:
    ///
    /// - `RESIZE` with either extent not strictly positive.
    /// - `RESIZE` with `rows * cols` exceeding the register's declared size.
    ///   A grid reinterprets variables the program already declared, so it
    ///   cannot describe cells that do not exist. The bound is `<=`, not
    ///   `==`: `EQUALITY`, `ATLEAST`, `ATLEASTW` and `REDUCE` append
    ///   variables past the grid.
    /// - An opcode that reads a grid finding none. `ROWFIND`, `COLFIND`,
    ///   `ROWSUM`, `COLSUM`, `ONEHOTR` and `ONEHOTC` on a register with
    ///   `rows = cols = 0` raise rather than reducing over nothing.
    #[error("invalid grid dimensions {rows}x{cols} at byte {pos:#06x}")]
    InvalidGridDimensions { pos: usize, rows: i64, cols: i64 },

    /// `XQMX`/`XSMX` was called with a discrete-domain `k` smaller than 2.
    /// The signed domain `[-k, k-1]` collapses to a single value at `k = 1`
    /// and is empty at `k <= 0`, so neither carries useful semantics; the
    /// reference (`spec/xqvm/SPEC.md`) requires `k >= 2`.
    #[error("XQMX/XSMX requires k >= 2 for the [-k, k-1] domain, got k = {k} at byte {pos:#06x}")]
    InvalidDiscreteK { pos: usize, k: i64 },

    /// A RANGE or ITER skip-forward scan reached end-of-stream without
    /// finding a matching NEXT (nesting depth never reached zero).
    #[error("unmatched RANGE/ITER at byte {pos:#06x}: no matching NEXT found")]
    UnmatchedLoop { pos: usize },

    /// A tracer callback returned an error (e.g. I/O write failure).
    #[error("trace failed at byte {pos:#06x}: {message}")]
    TraceFailed { pos: usize, message: String },

    /// An allocator was handed a negative size, or -- only above the memory
    /// limit `Vm::allocation_size` documents -- one too large for the
    /// executing target to address.
    ///
    /// The size is carried as the `i64` the program pushed, not as a `usize`,
    /// so the fault reports what the program asked for on every target. See
    /// `Vm::allocation_size` for the validate-charge-convert order that keeps
    /// this identity target-independent, and for the limit above which it
    /// stops doing so.
    #[error("invalid allocation size {size} at byte {pos:#06x}")]
    InvalidAllocation { pos: usize, size: i64 },

    /// The loop stack exceeded the 8192-frame nesting limit.
    ///
    /// `RANGE` and `ITER` each push a frame and only `NEXT` pops one, so a
    /// program that jumps back over a loop header without running its `NEXT`
    /// grows the stack without bound. The value stack has been capped at 8192
    /// since the first release; this is the same cap on the other stack.
    #[error("loop stack overflow at byte {pos:#06x} (limit: 8192)")]
    LoopStackOverflow { pos: usize },
}

// `into_diagnostic` and `byte_pos` require the disassembler (std-only).
#[cfg(feature = "std")]
impl Error {
    /// Convert this error into a [`RuntimeDiagnostic`] with a disassembly
    /// listing as source context.
    ///
    /// `bytecode` is the program that was being executed when the fault
    /// occurred. `name` is used as the file name in the diagnostic output
    /// (e.g. `"prog.xqbc"` or `"<stdin>"`).
    ///
    /// When the error carries a byte position, the corresponding line of the
    /// disassembly is highlighted with a caret. Errors without a position
    /// (e.g. [`Error::StepLimitExceeded`]) produce a diagnostic with no
    /// source label.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Vm;
    /// use xqvm::InstructionBuilder;
    ///
    /// fn run() -> miette::Result<()> {
    ///     let mut b = InstructionBuilder::new();
    ///     b.emit_push(10).emit_push(0).emit_div().emit_halt();
    ///     let program = b.build().unwrap();
    ///
    ///     let mut vm = Vm::new();
    ///     vm.run(&program)
    ///         .map_err(|e| e.into_diagnostic(&program, "<inline>"))?;
    ///     Ok(())
    /// }
    /// ```
    pub fn into_diagnostic(self, program: &Program, name: &str) -> RuntimeDiagnostic {
        let disasm_text = Disassembly::from_program(program).to_string();
        let span = self
            .byte_pos()
            .and_then(|pos| find_line_span(&disasm_text, pos));
        RuntimeDiagnostic {
            inner: self,
            disasm: NamedSource::new(name, disasm_text),
            span,
        }
    }

    /// Returns the byte offset embedded in this error, if any.
    fn byte_pos(&self) -> Option<usize> {
        match self {
            Self::StackUnderflow { pos }
            | Self::StackOverflow { pos }
            | Self::DivisionByZero { pos }
            | Self::NoActiveLoop { pos }
            | Self::BadOpcode { pos, .. }
            | Self::TruncatedInstruction { pos }
            | Self::InvalidShift { pos, .. }
            | Self::InvalidGridDimensions { pos, .. }
            | Self::InvalidDiscreteK { pos, .. }
            | Self::UnmatchedLoop { pos }
            | Self::LoopStackOverflow { pos }
            | Self::TraceFailed { pos, .. }
            | Self::BadJumpTarget { pos, .. }
            | Self::InvalidLabel { pos, .. }
            | Self::UnsetRegister { pos, .. }
            | Self::MemoryLimitExceeded { pos, .. }
            | Self::InvalidAllocation { pos, .. }
            | Self::IndexOutOfBounds { pos, .. } => Some(*pos),
            Self::ArithmeticOverflow { pos } | Self::StepLimitExceeded { pos, .. } => *pos,
            Self::RegisterType { .. }
            | Self::IncompatibleType(_)
            | Self::CallDataIndex { .. }
            | Self::OutputIndex { .. }
            | Self::SizeMismatch { .. }
            | Self::VecLengthMismatch { .. } => None,
        }
    }
}

impl From<crate::value::IncompatibleTypeError> for Error {
    fn from(e: crate::value::IncompatibleTypeError) -> Self {
        Self::IncompatibleType(e)
    }
}

impl From<crate::bytecode::error::StreamError> for Error {
    fn from(e: crate::bytecode::error::StreamError) -> Self {
        use crate::bytecode::error::StreamError as SE;
        match e {
            SE::UnknownOpcode { offset, byte } => Self::BadOpcode { pos: offset, byte },
            SE::TruncatedInstruction { offset } => Self::TruncatedInstruction { pos: offset },
            SE::SeekOutOfBounds { target, .. } => Self::BadJumpTarget { pos: 0, target },
        }
    }
}

// ---------------------------------------------------------------------------
// RuntimeDiagnostic (std-only -- requires miette and the disassembler)
// ---------------------------------------------------------------------------

/// A runtime [`enum@Error`] enriched with a disassembly listing as source context.
///
/// Construct it via [`Error::into_diagnostic`]. When the error carries a byte
/// offset, the corresponding disassembly line is highlighted with a caret.
///
/// `RuntimeDiagnostic` implements [`miette::Diagnostic`] so it can be
/// returned directly from a `fn main() -> miette::Result<()>`.
///
/// This type is only available when the `std` feature is enabled.
///
/// # Examples
///
/// ```rust
/// use xqvm::Vm;
/// use xqvm::InstructionBuilder;
///
/// fn run() -> miette::Result<()> {
///     let mut b = InstructionBuilder::new();
///     b.emit_push(3).emit_push(4).emit_add().emit_halt();
///     let program = b.build().unwrap();
///
///     let mut vm = Vm::new();
///     vm.run(&program)
///         .map_err(|e| e.into_diagnostic(&program, "<inline>"))?;
///     Ok(())
/// }
/// ```
#[cfg(feature = "std")]
#[derive(Debug, Error, Diagnostic)]
#[error("{inner}")]
#[diagnostic(code(xqvm::runtime_error))]
pub struct RuntimeDiagnostic {
    inner: Error,
    #[source_code]
    disasm: NamedSource<String>,
    #[label("execution failed here")]
    span: Option<SourceSpan>,
}

// ---------------------------------------------------------------------------
// Helpers (std-only)
// ---------------------------------------------------------------------------

/// Find the byte range of the disassembly line that starts at `byte_pos`.
///
/// Each disassembly line has the form `  0x{offset:04X}:  ...`, so we search
/// for `"0x{byte_pos:04X}:"` and extend the span to cover the whole line.
/// Returns `None` when no matching line is found.
#[cfg(feature = "std")]
#[expect(
    clippy::arithmetic_side_effects,
    reason = "both offsets are byte positions inside `text`, whose length is bounded by isize::MAX"
)]
fn find_line_span(text: &str, byte_pos: usize) -> Option<SourceSpan> {
    let needle = format!("0x{byte_pos:04X}:");
    let match_start = text.find(&needle)?;

    let line_start = text[..match_start].rfind('\n').map_or(0, |n| n + 1);

    let line_end = text[match_start..]
        .find('\n')
        .map_or(text.len(), |n| match_start + n);

    Some(SourceSpan::from(line_start..line_end))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::value::{IncompatibleTypeError, RegValKind};

    #[test]
    fn incompatible_type_from_impl_round_trips() {
        let inner = IncompatibleTypeError {
            expected: &[RegValKind::Model],
            actual: RegValKind::Int,
        };
        let err = Error::from(inner.clone());
        assert!(
            matches!(err, Error::IncompatibleType(ref e) if e == &inner),
            "From<IncompatibleTypeError> should produce Error::IncompatibleType"
        );
        assert_eq!(
            err.to_string(),
            "incompatible types: expected model, got int"
        );
    }
}
