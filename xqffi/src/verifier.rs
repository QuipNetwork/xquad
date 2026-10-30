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

//! `xqffi.verifier` -- `PyO3` bindings around `xqvm::verifier`.
//!
//! Two entry points:
//!
//! - [`verify`] -- run the default verifier over pre-assembled bytecode.
//! - [`verify_source`] -- assemble from `.xqasm` source, then verify.
//!
//! Both raise `ValueError` on the first violation found. The
//! message begins with the stable variant name
//! (e.g. `"NoActiveLoop: loop instruction at byte 0x0000 ..."`), so
//! callers can do `msg.split(": ", 1)[0]` for programmatic dispatch
//! without needing a custom exception class.
//!
//! # Examples (Python)
//!
//! ```python
//! from xqffi.verifier import verify_source
//!
//! verify_source("PUSH 1 HALT")          # returns None
//! verify_source("NEXT HALT")            # raises ValueError: NoActiveLoop: ...
//! ```

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use xqasm::assemble_source;
use xqvm::{Program, verifier};

/// Run the default bytecode verifier over pre-assembled bytecode bytes.
///
/// Decodes *bytecode* into a [`Program`] and runs all verification phases
/// (structural, jump-target, loop-nesting, register type-state, stack depth).
///
/// # Errors
///
/// Raises ``ValueError`` if the bytecode cannot be decoded, or on the first
/// violation found.  The message starts with the variant name for
/// programmatic dispatch: ``"<Variant>: <description>"``.
#[pyfunction]
fn verify(bytecode: &[u8]) -> PyResult<()> {
    let program = Program::decode(bytecode)
        .map_err(|e| PyValueError::new_err(format!("decode error: {e}")))?;
    verifier::verify(&program)
        .map_err(|e| PyValueError::new_err(format!("{}: {e}", e.variant_name())))
}

/// Assemble `.xqasm` source and run the default bytecode verifier.
///
/// Equivalent to assembling with ``xqffi.asm.assemble_source`` and then
/// calling :func:`verify`, but in a single round-trip.
///
/// # Errors
///
/// Raises ``ValueError`` on assembly failure or any verification violation.
#[pyfunction]
fn verify_source(source: &str) -> PyResult<()> {
    let program = assemble_source(source).map_err(|e| PyValueError::new_err(format!("{e}")))?;
    verifier::verify(&program)
        .map_err(|e| PyValueError::new_err(format!("{}: {e}", e.variant_name())))
}

pub(crate) fn register(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(verify, m)?)?;
    m.add_function(wrap_pyfunction!(verify_source, m)?)?;
    Ok(())
}
