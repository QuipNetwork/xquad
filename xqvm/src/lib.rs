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

//! X-Quadratic Virtual Machine — bytecode types and interpreter.
//!
//! This crate contains two layers:
//!
//! 1. **Bytecode** (`xqvm::bytecode`) — opcode table, instruction codec,
//!    [`InstructionBuilder`], and [`Program`].  `no_std + alloc`-compatible.
//! 2. **Interpreter** — [`Vm`], [`Error`], [`RegVal`], and supporting types.
//!    The `no_std` core is always compiled; `std`-only additions (tracers,
//!    [`RuntimeDiagnostic`], [`Disassembly`]) are gated on the `std` feature.
//!
//! | Item | Description |
//! |---|---|
//! | [`opcodes!`] | X-macro — the single source of truth for the opcode table |
//! | [`Opcode`] | `#[repr(u8)]` enum |
//! | [`Instruction`] | Fully decoded instruction with operands |
//! | [`Register`] | 8-bit register slot operand |
//! | [`Program`] | Complete program: raw instruction bytes + jump table |
//! | [`InstructionBuilder`] | Fluent bytecode assembler |
//! | [`InstructionStream`] | Incremental seekable reader |
//! | [`bytecode::codec`] | [`bytecode::codec::encode`] / `decode` — wire format |
//! | [`bytecode::error`] | Bytecode-layer error types |
//! | [`Vm`] | The interpreter — stack, registers, loop stack |
//! | [`Error`] | Runtime fault variants |
//! | [`XqmxModel`] | QUBO/Ising/discrete optimization model |
//! | [`RegVal`] | Register value type |
//!
//! # Quick start
//!
//! ```rust
//! use xqvm::{Vm, InstructionBuilder};
//!
//! let mut b = InstructionBuilder::new();
//! b.emit_push(10).emit_push(32).emit_add().emit_halt();
//! let program = b.build().unwrap();
//!
//! let mut vm = Vm::new();
//! vm.run(&program).unwrap();
//! assert_eq!(vm.stack(), &[42]);
//! ```

// No standard library when the `std` feature is disabled (e.g. WASM targets).
// The `alloc` crate provides heap types (`Vec`, `BTreeMap`, ...).
#![cfg_attr(not(feature = "std"), no_std)]
// Private modules have doc tests that are only visible to maintainers.
#![expect(
    rustdoc::private_doc_tests,
    reason = "private modules contain doc tests visible only to maintainers"
)]
// Unchecked arithmetic is denied by default here, so a new bare `+` on an
// `i64` stops compiling until its author records why the wrap is permitted.
// Every exception carries an `#[expect(clippy::arithmetic_side_effects,
// reason = ...)]` naming the normative text or the structural invariant that
// allows it; `git grep -n arithmetic_side_effects -- xqvm/src` is the list.
// Two deliberate wraps are outside the lint's reach because they are method
// calls rather than operators -- `SHL`'s `wrapping_shl` and `SLACK`'s
// `power.wrapping_mul(2)` -- and carry the same reason as a comment at the
// site.
#![deny(clippy::arithmetic_side_effects)]

#[cfg(not(feature = "std"))]
extern crate alloc;

// ---------------------------------------------------------------------------
// Modules
// ---------------------------------------------------------------------------

/// Bytecode layer (no_std-compatible): opcode table, instruction codec,
/// [`InstructionBuilder`], and [`Program`].
pub mod bytecode;
pub use bytecode::codec;
// Interpreter layer.
mod error;
mod model;
pub mod tracer;
mod value;
mod vm;

// Disassembler (std-only — used by RuntimeDiagnostic in error.rs).
#[cfg(feature = "std")]
pub mod disasm;

// Pre-execution bytecode verifier.
pub mod verifier;

// Worklist data-flow analysis framework.
pub mod dataflow;

// ---------------------------------------------------------------------------
// Public API re-exports — bytecode types hoisted to crate root
// ---------------------------------------------------------------------------

pub use bytecode::{
    Instruction, InstructionBuilder, InstructionStream, JumpTable, LabelId, Opcode, Program,
    ProgramDecodeError, Register, RegisterEffect, StackEffect,
};

// ---------------------------------------------------------------------------
// Public API re-exports — interpreter
// ---------------------------------------------------------------------------

#[cfg(feature = "std")]
pub use disasm::Disassembly;
pub use error::Error;
#[cfg(feature = "std")]
pub use error::RuntimeDiagnostic;
pub use model::{Domain, XqmxModel, XqmxSample};
#[cfg(feature = "std")]
pub use tracer::{JsonTracer, TextTracer};
pub use tracer::{NoopTracer, StepState, Tracer};
pub use value::{IncompatibleTypeError, RegVal, RegValKind};
pub use verifier::{RegType, VerifierError};
pub use vm::{DEFAULT_STEP_LIMIT, Vm};
