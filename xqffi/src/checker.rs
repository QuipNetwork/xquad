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

//! `xqffi.checker` -- `PyO3` bindings around `xqvm::checker`.
//!
//! Two entry points:
//!
//! - [`check`] -- run the default checker over pre-assembled bytecode.
//! - [`check_source`] -- assemble from `.xqasm` source, then check.
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
//! from xqffi.checker import check_source
//!
//! check_source("PUSH 1 HALT")          # returns None
//! check_source("NEXT HALT")            # raises ValueError: NoActiveLoop: ...
//! ```

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use xqasm::assemble_source;
use xqvm::{Program, checker};

/// Run the default bytecode checker over pre-assembled bytecode bytes.
///
/// Decodes *bytecode* into a [`Program`] and runs all checker phases
/// (structural, jump-target, loop-nesting, register type-state, must-init,
/// stack depth).
///
/// # Errors
///
/// Raises ``ValueError`` if the bytecode cannot be decoded, or on the first
/// violation found.  The message starts with the variant name for
/// programmatic dispatch: ``"<Variant>: <description>"``.
#[pyfunction]
fn check(bytecode: &[u8]) -> PyResult<()> {
    let program = Program::decode(bytecode)
        .map_err(|e| PyValueError::new_err(format!("decode error: {e}")))?;
    checker::check(&program)
        .map_err(|e| PyValueError::new_err(format!("{}: {e}", e.variant_name())))
}

/// Assemble `.xqasm` source and run the default bytecode checker.
///
/// Equivalent to assembling with ``xqffi.asm.assemble_source`` and then
/// calling :func:`check`, but in a single round-trip.
///
/// # Errors
///
/// Raises ``ValueError`` on assembly failure or any checker violation.
#[pyfunction]
fn check_source(source: &str) -> PyResult<()> {
    let program = assemble_source(source).map_err(|e| PyValueError::new_err(format!("{e}")))?;
    checker::check(&program)
        .map_err(|e| PyValueError::new_err(format!("{}: {e}", e.variant_name())))
}

pub(crate) fn register(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(check, m)?)?;
    m.add_function(wrap_pyfunction!(check_source, m)?)?;
    Ok(())
}
