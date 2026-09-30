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

//! Per-byte cost of turning bytecode into a runnable [`Program`].
//!
//! [`Program::decode`] is what an embedder pays before every run: the XQBC
//! header and CRC-32 check, a copy of the code, and the jump-table walk in
//! [`Program::new`]. Both are timed over two 64 KiB programs:
//!
//! - **realistic** -- the TSP encoder, decoder and verifier from
//!   `examples/tsp/`, repeated.
//! - **dense** -- `TARGET RANGE JUMP1 NEXT` repeated, the densest mix of the
//!   instructions the walk records.
//!
//! ```text
//! cargo bench -p xqvm --bench decode
//! ```

mod support;

use std::error::Error;
use std::hint::black_box;

use xqvm::{Instruction, Program, codec};

/// Size every corpus is repeated up to.
const CORPUS_BYTES: usize = 64 * 1024;

fn main() -> Result<(), Box<dyn Error>> {
    for (name, code) in [("realistic", realistic()?), ("dense", dense())] {
        let encoded = Program::new(code.clone()).encode();
        let bytes = u64::try_from(code.len())?;
        support::report(name, "Program::new", bytes, "B", || {
            Program::new(black_box(code.clone()))
        });
        support::report(name, "Program::decode", bytes, "B", || {
            Program::decode(black_box(&encoded))
        });
    }
    Ok(())
}

/// Repeat `unit` as many whole times as fits in [`CORPUS_BYTES`].
fn repeat(unit: &[u8]) -> Vec<u8> {
    let copies = CORPUS_BYTES / unit.len().max(1);
    unit.repeat(copies)
}

fn realistic() -> Result<Vec<u8>, Box<dyn Error>> {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/examples/tsp");
    let mut unit = Vec::new();
    for name in ["encoder", "decoder", "verifier"] {
        let source = std::fs::read_to_string(format!("{dir}/{name}.xqasm"))?;
        let program = xqasm::assemble_source(&source).map_err(|e| format!("{name}: {e}"))?;
        unit.extend_from_slice(program.code());
    }
    Ok(repeat(&unit))
}

fn dense() -> Vec<u8> {
    let unit: Vec<u8> = [
        Instruction::Target {},
        Instruction::Range {},
        Instruction::Jump1 { label: 0 },
        Instruction::Next {},
    ]
    .iter()
    .flat_map(codec::encode)
    .collect();
    repeat(&unit)
}
