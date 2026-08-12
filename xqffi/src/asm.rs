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

//! `xqffi.asm` — `PyO3` bindings around the Rust `xqasm` crate.
//!
//! Three entry points:
//!
//! - [`parse_xqasm`] — parse `.xqasm` source and return a Python dict
//!   shaped for direct consumption by `xqvm_py.program.Program`:
//!   `{"instructions": [(opcode_u8, operand_bytes, pc), ...],
//!     "jump_targets": {target_id: pc, ...}}`.
//!
//!   `operand_bytes` is a Python `bytes` object (pyo3's default
//!   conversion for `Vec<u8>`). `xqvm_py.core.executor` indexes
//!   `instr.operands[i]` to get a single byte value — both `tuple[int,
//!   ...]` and `bytes` support that; the helper `program_from_xqasm`
//!   wraps it into the tuple shape that the `Instruction` dataclass
//!   expects.
//!
//! - [`assemble_source`] — same as `xqasm::assemble_source` but returns
//!   the wire-format bytes directly (`bytes` in Python). Useful for
//!   external consumers who want `.xqb` output.
//!
//! - [`disassemble`] — wrap [`xqvm::Disassembly`] to produce a
//!   human-readable listing from raw bytecode. Output is **not**
//!   round-trippable through [`parse_xqasm`] (labels are materialised as
//!   `.N` markers and the listing carries pc offsets); it is for display
//!   only. If programmatic conversion from `.xqb` to a `Program` is
//!   needed, decode the bytes with [`xqvm::codec::decode`] directly.

use std::collections::HashMap;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use xqasm::assemble_source as xqasm_assemble_source;
use xqvm::Disassembly;
use xqvm::bytecode::codec;

/// Parse `.xqasm` source and return a dict consumable by
/// `xqvm_py.program.program_from_xqasm`.
///
/// # Errors
///
/// Raises `ValueError` on any parse or assembly failure; the message
/// includes the offending line/column.
#[pyfunction]
fn parse_xqasm<'py>(py: Python<'py>, source: &str) -> PyResult<Bound<'py, PyDict>> {
    let program =
        xqasm_assemble_source(source).map_err(|e| PyValueError::new_err(format!("{e}")))?;

    let code = program.code();
    let mut instructions: Vec<(u8, Vec<u8>, u32)> = Vec::new();
    let mut pc: usize = 0;
    while pc < code.len() {
        // The bounded-by-pc slice and subsequent indexing are guarded by
        // the loop condition (pc < code.len()) and by codec::decode's
        // contract: it only reports `consumed` bytes that it actually
        // read, so `pc..pc+consumed` is always in-bounds. The indexing/
        // slicing lints can't prove this without annotations.
        #[expect(
            clippy::indexing_slicing,
            reason = "pc and consumed are bounded by the decode loop's invariants"
        )]
        let (opcode_byte, operands) = {
            let tail = &code[pc..];
            let (_instr, consumed) = codec::decode(tail)
                .map_err(|e| PyValueError::new_err(format!("decode failed at pc={pc}: {e:?}")))?;
            let opcode_byte = code[pc];
            let operands = code[pc + 1..pc + consumed].to_vec();
            pc += consumed;
            (opcode_byte, operands)
        };
        #[expect(
            clippy::cast_possible_truncation,
            reason = "pc is bounded by code.len() which is tracked as usize but fits u32 for any realistic program"
        )]
        let pc_u32 = (pc - operands.len() - 1) as u32;
        instructions.push((opcode_byte, operands, pc_u32));
    }

    let jump_table = program.jump_table();
    let mut jump_targets: HashMap<u32, u32> = HashMap::with_capacity(jump_table.len());
    for (id, &target_pc) in jump_table.targets().iter().enumerate() {
        #[expect(
            clippy::cast_possible_truncation,
            reason = "target ids and pcs are bounded by program size (u32 suffices)"
        )]
        let _ = jump_targets.insert(id as u32, target_pc as u32);
    }

    let dict = PyDict::new(py);
    dict.set_item("instructions", instructions)?;
    dict.set_item("jump_targets", jump_targets)?;
    Ok(dict)
}

/// Assemble `.xqasm` source and return the raw wire-format bytes.
///
/// # Errors
///
/// Raises `ValueError` on any parse or assembly failure.
#[pyfunction]
fn assemble_source<'py>(py: Python<'py>, source: &str) -> PyResult<Bound<'py, PyBytes>> {
    let program =
        xqasm_assemble_source(source).map_err(|e| PyValueError::new_err(format!("{e}")))?;
    Ok(PyBytes::new(py, &program.encode()))
}

/// Disassemble XQVM bytecode into a human-readable listing.
///
/// The listing format matches `xquad dism`: one instruction per line
/// with pc offsets and `.N` labels for jump targets. It is **not**
/// round-trippable through [`parse_xqasm`] — use it for display only.
///
/// # Errors
///
/// Raises `ValueError` if `bytecode` is not a valid XQBC file, or on any
/// write error (which should not occur as the underlying writer is an
/// in-memory buffer).
#[pyfunction]
fn disassemble(bytecode: &[u8]) -> PyResult<String> {
    let program = xqvm::Program::decode(bytecode)
        .map_err(|e| PyValueError::new_err(format!("decode error: {e}")))?;
    let mut out = Vec::new();
    Disassembly::from_program(&program)
        .write_to(&mut out)
        .map_err(|e| PyValueError::new_err(format!("disassembly write failed: {e}")))?;
    String::from_utf8(out).map_err(|e| PyValueError::new_err(format!("disassembly not UTF-8: {e}")))
}

/// Count decoded instructions in raw wire-format bytecode.
///
/// # Errors
///
/// Raises `ValueError` if the bytecode cannot be decoded.
#[pyfunction]
fn instruction_count(bytecode: &[u8]) -> PyResult<usize> {
    let program = xqvm::Program::decode(bytecode)
        .map_err(|e| PyValueError::new_err(format!("decode error: {e:?}")))?;
    let mut stream = xqvm::InstructionStream::from_program(&program);
    let mut n: usize = 0;
    while stream.next().is_some() {
        n += 1;
    }
    Ok(n)
}

pub(crate) fn register(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_xqasm, m)?)?;
    m.add_function(wrap_pyfunction!(assemble_source, m)?)?;
    m.add_function(wrap_pyfunction!(disassemble, m)?)?;
    m.add_function(wrap_pyfunction!(instruction_count, m)?)?;
    Ok(())
}
