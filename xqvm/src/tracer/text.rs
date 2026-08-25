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

//! Human-readable text tracer.

use std::io::Write;

use crate::tracer::{StepState, Tracer};
use crate::value::RegVal;

// Column widths for aligned output.
const W_STEP: usize = 6;
const W_OFFSET: usize = 8;
const W_INSTR: usize = 21;
const W_STACK: usize = 25;
const W_READ: usize = 15;
const W_WRITTEN: usize = 15;

/// Maximum stack elements shown before truncation.
const MAX_STACK_DISPLAY: usize = 8;

/// Writes human-readable trace output with aligned columns.
///
/// Each call to [`on_step`](Tracer::on_step) writes one row. The first
/// call also writes a header line.
#[derive(Debug)]
pub struct TextTracer<W: Write> {
    out: W,
    header_written: bool,
}

impl<W: Write> TextTracer<W> {
    /// Create a new text tracer writing to `out`.
    pub fn new(out: W) -> Self {
        Self {
            out,
            header_written: false,
        }
    }
}

/// Format a register value in compact form.
fn fmt_regval(val: &RegVal) -> String {
    match val {
        RegVal::Unset => "unset".to_string(),
        RegVal::Int(v) => format!("{v}"),
        RegVal::VecInt(v) => format!("vec<int>(len={})", v.len()),
        RegVal::VecXqmx(v) => format!("vec<model>(len={})", v.len()),
        RegVal::Model(m) => format!("model({}x{})", m.rows, m.cols),
        RegVal::Sample(s) => format!("sample(len={})", s.values.len()),
    }
}

/// Format a register list as `r{idx}={val}, ...`.
fn fmt_regs(regs: &[(u8, RegVal)]) -> String {
    regs.iter()
        .map(|(idx, val)| format!("r{idx}={}", fmt_regval(val)))
        .collect::<Vec<_>>()
        .join(", ")
}

/// Format the stack for display.
fn fmt_stack(stack: &[i64]) -> String {
    if stack.len() <= MAX_STACK_DISPLAY {
        format!("{stack:?}")
    } else {
        #[expect(
            clippy::arithmetic_side_effects,
            reason = "the `else` arm runs only when `stack.len() > MAX_STACK_DISPLAY`"
        )]
        let skip = stack.len() - MAX_STACK_DISPLAY;
        let top = stack.get(skip..).unwrap_or_default();
        format!("[...{skip} more, {}]", fmt_stack_elems(top))
    }
}

/// Join stack elements with commas.
fn fmt_stack_elems(elems: &[i64]) -> String {
    elems
        .iter()
        .map(|v| format!("{v}"))
        .collect::<Vec<_>>()
        .join(", ")
}

impl<W: Write> Tracer for TextTracer<W> {
    type Error = std::io::Error;

    fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error> {
        if !self.header_written {
            writeln!(
                self.out,
                "{:<W_STEP$}  {:<W_OFFSET$}  {:<W_INSTR$}  {:<W_STACK$}  {:<W_READ$}  {:<W_WRITTEN$}",
                "step", "offset", "instruction", "stack", "read-regs", "written-regs",
            )?;
            self.header_written = true;
        }

        let offset = format!("{:#06X}", state.pos);
        let instr = format!("{}", state.instruction);
        let stack = fmt_stack(state.stack);
        let read = fmt_regs(state.read_regs);
        let written = fmt_regs(state.written_regs);

        writeln!(
            self.out,
            "{:>W_STEP$}  {:<W_OFFSET$}  {:<W_INSTR$}  {:<W_STACK$}  {:<W_READ$}  {:<W_WRITTEN$}",
            state.step, offset, instr, stack, read, written,
        )?;

        Ok(())
    }
}
