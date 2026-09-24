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

//! Typed Python exceptions for [`xqvm::Error`], registered in `xqffi.vm`.
//!
//! Every fault is a subclass of `XqvmError`, which is itself a
//! `RuntimeError`, so a host that caught `RuntimeError` before these
//! existed still catches every VM fault. The class names are the
//! conformance fault vocabulary (`conformance/src/lib.rs`, `Fault`); the
//! message is the error's `Display` text.

use pyo3::create_exception;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

/// Declare `XqvmError` and one subclass per fault, and the function that
/// registers all of them on a module, from a single list.
macro_rules! faults {
    ($($name:ident => $doc:literal,)+) => {
        create_exception!(
            xqffi.vm,
            XqvmError,
            PyRuntimeError,
            "Base class of every fault the XQVM raises."
        );
        $(create_exception!(xqffi.vm, $name, XqvmError, $doc);)+

        /// Add `XqvmError` and every fault class to `m`.
        pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
            let py = m.py();
            m.add("XqvmError", py.get_type::<XqvmError>())?;
            $(m.add(stringify!($name), py.get_type::<$name>())?;)+
            Ok(())
        }
    };
}

faults! {
    StackUnderflow => "A pop was attempted on an empty stack.",
    StackOverflow => "The value stack exceeded its depth limit.",
    TypeMismatch => "An operand had the wrong value kind.",
    UnsetRegister => "A register was read while unset.",
    DivisionByZero => "Division or modulo by zero.",
    IndexOutOfBounds => "An index fell outside the addressed container.",
    NoActiveLoop => "A loop instruction executed with no active loop.",
    UnmatchedLoop => "A RANGE or ITER had no matching NEXT.",
    BadJumpTarget => "A jump named a target outside the program.",
    InvalidLabel => "A jump named a label the program does not define.",
    BadOpcode => "An unknown opcode byte was decoded.",
    TruncatedInstruction => "An instruction's operands ran past the end of the program.",
    CallDataIndex => "An INPUT addressed a calldata slot that does not exist.",
    OutputIndex => "An OUTPUT addressed an output slot that does not exist.",
    SizeMismatch => "A model and a sample disagreed on variable count.",
    VecLengthMismatch => "Two vector operands disagreed on length.",
    ArithmeticOverflow => "An operation produced a value outside the signed 64-bit range.",
    StepLimitExceeded => "Execution ran past its step budget.",
    MemoryLimitExceeded => "An allocating instruction ran past its allocation budget.",
    InvalidShift => "A shift amount fell outside the representable range.",
    InvalidGridDimensions => "Grid dimensions were not positive, or did not fit the model.",
    InvalidIntegerK => "An XQMX or XSMX allocation used k < 2.",
    SampleOutOfDomain => "A SETLINE or ADDLINE write put a value outside a sample's domain.",
    TraceFailed => "A tracer refused a step.",
    InvalidAllocation => "An allocator was handed a size that is not an allocation.",
    LoopStackOverflow => "Loop nesting exceeded its depth limit.",
}

/// Convert a VM error into the typed Python exception for its fault.
///
/// The match is exhaustive and `xqvm::Error` is not `#[non_exhaustive]`, so
/// a new variant fails to compile here until it is mapped. `RegisterType`
/// and `IncompatibleType` share `TypeMismatch`, as they do in the
/// conformance vocabulary.
pub(crate) fn vm_error(error: &xqvm::Error) -> PyErr {
    use xqvm::Error as E;

    let message = error.to_string();
    match error {
        E::StackUnderflow { .. } => StackUnderflow::new_err(message),
        E::StackOverflow { .. } => StackOverflow::new_err(message),
        E::RegisterType { .. } | E::IncompatibleType(_) => TypeMismatch::new_err(message),
        E::UnsetRegister { .. } => UnsetRegister::new_err(message),
        E::DivisionByZero { .. } => DivisionByZero::new_err(message),
        E::ArithmeticOverflow { .. } => ArithmeticOverflow::new_err(message),
        E::IndexOutOfBounds { .. } => IndexOutOfBounds::new_err(message),
        E::NoActiveLoop { .. } => NoActiveLoop::new_err(message),
        E::BadJumpTarget { .. } => BadJumpTarget::new_err(message),
        E::InvalidLabel { .. } => InvalidLabel::new_err(message),
        E::BadOpcode { .. } => BadOpcode::new_err(message),
        E::TruncatedInstruction { .. } => TruncatedInstruction::new_err(message),
        E::CallDataIndex { .. } => CallDataIndex::new_err(message),
        E::OutputIndex { .. } => OutputIndex::new_err(message),
        E::SizeMismatch { .. } => SizeMismatch::new_err(message),
        E::VecLengthMismatch { .. } => VecLengthMismatch::new_err(message),
        E::StepLimitExceeded { .. } => StepLimitExceeded::new_err(message),
        E::MemoryLimitExceeded { .. } => MemoryLimitExceeded::new_err(message),
        E::InvalidShift { .. } => InvalidShift::new_err(message),
        E::InvalidGridDimensions { .. } => InvalidGridDimensions::new_err(message),
        E::InvalidIntegerK { .. } => InvalidIntegerK::new_err(message),
        E::SampleOutOfDomain { .. } => SampleOutOfDomain::new_err(message),
        E::UnmatchedLoop { .. } => UnmatchedLoop::new_err(message),
        E::TraceFailed { .. } => TraceFailed::new_err(message),
        E::InvalidAllocation { .. } => InvalidAllocation::new_err(message),
        E::LoopStackOverflow { .. } => LoopStackOverflow::new_err(message),
    }
}

/// Error raised when bytes handed to `Vm.run` are not a decodable program.
///
/// Not a VM fault: decoding happens before the VM runs, so this stays a
/// plain `RuntimeError` rather than an `XqvmError`.
pub(crate) fn decode_error(error: &xqvm::ProgramDecodeError) -> PyErr {
    PyRuntimeError::new_err(format!("decode error: {error:?}"))
}
