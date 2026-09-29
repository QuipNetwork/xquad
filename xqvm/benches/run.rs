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

//! Per-step cost of [`Vm::run`], and the fixed cost of starting a run.
//!
//! Three programs, each timed as `reset` followed by `run` on one [`Vm`]:
//!
//! - **range** -- a `RANGE` loop summing its index, 100,000 iterations of
//!   cheap register and stack ops. Dispatch overhead is the largest share of
//!   a step here.
//! - **jumpi** -- a `TARGET`/`JUMPI` countdown, 100,000 iterations, in a
//!   program carrying 4,096 more `TARGET`s after its `HALT`, so a lookup
//!   keyed by `TARGET` offset has a realistic table to search.
//! - **targets** -- `HALT` followed by 64 KiB of `TARGET`. One step runs; the
//!   rest is whatever a run pays per `TARGET` before it starts.
//!
//! ```text
//! cargo bench -p xqvm --bench run
//! ```

mod support;

use std::error::Error;

use xqvm::{Instruction, Program, Vm, codec};

/// Iterations of each loop program.
const ITERATIONS: u32 = 100_000;
/// Unreached `TARGET`s appended to the `jumpi` program.
const DEAD_TARGETS: u32 = 4_096;
/// `TARGET` bytes in the `targets` program.
const TARGET_BYTES: usize = 64 * 1024;

fn main() -> Result<(), Box<dyn Error>> {
    let programs = [
        ("range", assemble(&range_source())?),
        ("jumpi", jumpi()?),
        ("targets", targets()),
    ];
    for (case, program) in &programs {
        let mut vm = Vm::new();
        vm.run(program)?;
        let steps = vm.instructions();
        support::report(case, "Vm::run", steps, "step", || {
            vm.reset();
            vm.run(program)
        });
    }
    Ok(())
}

fn assemble(source: &str) -> Result<Program, Box<dyn Error>> {
    Ok(xqasm::assemble_source(source).map_err(|e| e.to_string())?)
}

fn range_source() -> String {
    format!(
        "PUSH 0\nSTOW r1\n\
         PUSH 0\nPUSH {ITERATIONS}\nRANGE\n\
         LIDX r2\nLOAD r1\nLOAD r2\nADD\nSTOW r1\n\
         NEXT\nHALT\n"
    )
}

/// The countdown, with [`DEAD_TARGETS`] unreached `TARGET`s appended as raw
/// bytes: the assembler rejects a label nothing jumps to.
fn jumpi() -> Result<Program, Box<dyn Error>> {
    let source = format!(
        "PUSH {ITERATIONS}\nSTOW r0\n\
         TARGET .0\n\
         LOAD r0\nDEC\nSTOW r0\nLOAD r0\nJUMPI .0\n\
         HALT\n"
    );
    let mut code = assemble(&source)?.code().to_vec();
    code.extend(target_bytes(usize::try_from(DEAD_TARGETS)?));
    Ok(Program::new(code))
}

fn targets() -> Program {
    let mut code = codec::encode(&Instruction::Halt {});
    code.extend(target_bytes(TARGET_BYTES));
    Program::new(code)
}

/// `count` encoded `TARGET` instructions, one byte each.
fn target_bytes(count: usize) -> Vec<u8> {
    codec::encode(&Instruction::Target {}).repeat(count)
}
