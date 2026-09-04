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

//! Opcode-level coverage of the conformance vector suite.
//!
//! Answers one question: which of the 93 opcodes does no vector cover?
//! Where a vector is missing, the harness makes no claim about that opcode
//! at all -- `IDXTRIU` diverged between the two implementations in two
//! separate ways precisely because nothing exercised it, and both were
//! found by reading the implementations rather than by any check.
//!
//! # Two measurements, because one is not enough
//!
//! **Present** -- the opcode appears in some vector's assembled program.
//! This is the literal "has a vector" question, and it is the one the
//! ratchet guards. It is measured by decoding each program's code section,
//! so it counts an opcode a vector was written for even when the run
//! faults before reaching it.
//!
//! **Reached** -- the opcode executed successfully during the run,
//! recorded by a [`Tracer`]. Strictly stronger, and strictly smaller: an
//! opcode behind an untaken branch is present but never reached.
//!
//! Neither alone is honest. Present-only would credit an opcode sitting in
//! dead code. Reached-only would report `DIV` as uncovered despite
//! `arithmetic/div_by_zero` existing, because a faulting instruction never
//! completes a step -- and error vectors are exactly what QUI-1017 added
//! the machinery for. Reporting both keeps each bias visible.
//!
//! # What neither measures
//!
//! Covering an opcode is not the same as exercising it. A single `ADD`
//! that never overflows counts here while saying nothing about the
//! overflow path. This is a floor on the harness's reach, not a claim of
//! completeness.

use std::collections::BTreeSet;

use xqvm::{StepState, Tracer};

/// Build the full `(code, mnemonic)` opcode list from the `opcodes!`
/// x-macro, so the denominator cannot drift from the opcode table.
macro_rules! all_opcodes_table {
    (
        $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal, $delta:expr, {$($f:tt)*}) ),*
        $(,)?
    ) => {
        &[ $( ($code, $mnem), )* ]
    };
}

/// Every opcode in the table, in wire-byte order.
const ALL_OPCODES: &[(u8, &str)] = xqvm::opcodes!(all_opcodes_table);

/// Records the opcode of every instruction the VM completes.
#[derive(Debug, Default)]
struct OpcodeTracer {
    seen: BTreeSet<u8>,
}

impl Tracer for OpcodeTracer {
    type Error = core::convert::Infallible;

    fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error> {
        let _ = self.seen.insert(state.instruction.opcode() as u8);
        Ok(())
    }
}

/// Per-opcode coverage across the whole vector suite.
#[derive(Debug, Default)]
pub struct Coverage {
    present: BTreeSet<u8>,
    reached: BTreeSet<u8>,
    vectors: usize,
    /// Vectors that could not be assembled or decoded, as
    /// `<category>/<name>: <reason>`. A vector whose program *faults* is
    /// not listed here -- the fault is the point of an error vector.
    unreadable: Vec<String>,
}

impl Coverage {
    /// Walk and run every vector under `conformance/vectors/`.
    #[must_use]
    pub fn collect() -> Self {
        let mut coverage = Self::default();
        for (category, name) in crate::discover_vectors() {
            match crate::load_vector(&category, &name) {
                Ok(vector) => coverage.record(&vector),
                Err(e) => coverage.unreadable.push(format!("{category}/{name}: {e}")),
            }
        }
        coverage
    }

    /// Fold one vector's opcodes into the totals.
    fn record(&mut self, vector: &crate::Vector) {
        self.vectors = self.vectors.saturating_add(1);

        let program = match xqasm::assemble_source(&vector.program_xqasm) {
            Ok(program) => program,
            Err(e) => {
                self.unreadable.push(format!(
                    "{}/{}: assemble_source failed: {e}",
                    vector.category, vector.name
                ));
                return;
            }
        };

        // Present: decode the code section linearly, the same walk the
        // disassembler makes. Reachability is not considered here.
        let code = program.code();
        let mut pos = 0usize;
        while pos < code.len() {
            let Some(tail) = code.get(pos..) else { break };
            match xqvm::codec::decode(tail) {
                Ok((instruction, len)) => {
                    let _ = self.present.insert(instruction.opcode() as u8);
                    if len == 0 {
                        self.unreadable.push(format!(
                            "{}/{}: decoder returned a zero-length instruction at byte {pos}",
                            vector.category, vector.name
                        ));
                        break;
                    }
                    pos = pos.saturating_add(len);
                }
                Err(e) => {
                    self.unreadable.push(format!(
                        "{}/{}: decode failed at byte {pos}: {e:?}",
                        vector.category, vector.name
                    ));
                    break;
                }
            }
        }

        // Reached: execute, recording each completed step. The VM comes
        // from the same builder `check_vector` uses, so "reached" cannot
        // describe a run the harness would not make.
        let calldata = crate::calldata_of(&vector.inputs);
        let mut vm = crate::vm_for(&calldata, &vector.inputs);

        let mut tracer = OpcodeTracer::default();
        // A runtime fault is expected for an error vector, and irrelevant
        // either way: the steps that completed are what this measures, and
        // `check_vector` is what judges the outcome.
        let _ = vm.run_trace(&mut tracer, &program);
        self.reached.extend(tracer.seen);
    }

    /// Total number of opcodes in the table.
    #[must_use]
    pub fn total() -> usize {
        ALL_OPCODES.len()
    }

    /// Number of opcodes appearing in at least one vector's program.
    #[must_use]
    pub fn present_count(&self) -> usize {
        self.present.len()
    }

    /// Number of opcodes at least one vector executes to completion.
    #[must_use]
    pub fn reached_count(&self) -> usize {
        self.reached.len()
    }

    /// Number of vectors the run visited.
    #[must_use]
    pub fn vector_count(&self) -> usize {
        self.vectors
    }

    /// Vectors that could not be read, assembled or decoded.
    #[must_use]
    pub fn unreadable(&self) -> &[String] {
        &self.unreadable
    }

    /// Mnemonics no vector's program contains, in wire-byte order.
    #[must_use]
    pub fn absent(&self) -> Vec<&'static str> {
        Self::mnemonics_outside(&self.present)
    }

    /// Mnemonics that appear in a vector but never execute, in wire-byte
    /// order. A subset of the present opcodes.
    #[must_use]
    pub fn present_but_unreached(&self) -> Vec<&'static str> {
        ALL_OPCODES
            .iter()
            .filter(|(code, _)| self.present.contains(code) && !self.reached.contains(code))
            .map(|&(_, mnemonic)| mnemonic)
            .collect()
    }

    fn mnemonics_outside(set: &BTreeSet<u8>) -> Vec<&'static str> {
        ALL_OPCODES
            .iter()
            .filter(|(code, _)| !set.contains(code))
            .map(|&(_, mnemonic)| mnemonic)
            .collect()
    }

    /// Render the report printed by `conformance --coverage` and by a
    /// failing coverage ratchet.
    #[must_use]
    pub fn render(&self) -> String {
        use std::fmt::Write as _;

        let mut out = String::new();
        let _ = writeln!(
            out,
            "opcode coverage over {} vectors, against {} opcodes:\n  \
             present in a vector: {}\n  \
             reached at runtime:  {}",
            self.vector_count(),
            Self::total(),
            self.present_count(),
            self.reached_count(),
        );

        let absent = self.absent();
        if absent.is_empty() {
            let _ = writeln!(out, "\nevery opcode appears in at least one vector");
        } else {
            let _ = writeln!(out, "\n{} opcodes in no vector at all:", absent.len());
            for chunk in absent.chunks(8) {
                let _ = writeln!(out, "  {}", chunk.join(" "));
            }
        }

        let unreached = self.present_but_unreached();
        if !unreached.is_empty() {
            let _ = writeln!(
                out,
                "\n{} opcodes present in a vector but never executed \
                 (dead branch, or the fault the vector asserts):",
                unreached.len()
            );
            for chunk in unreached.chunks(8) {
                let _ = writeln!(out, "  {}", chunk.join(" "));
            }
        }

        if !self.unreadable.is_empty() {
            let _ = writeln!(
                out,
                "\n{} vectors could not be read:",
                self.unreadable.len()
            );
            for problem in &self.unreadable {
                let _ = writeln!(out, "  {problem}");
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::{ALL_OPCODES, Coverage};

    #[test]
    fn the_denominator_is_the_whole_opcode_table() {
        assert_eq!(Coverage::total(), 93);
        assert_eq!(ALL_OPCODES.len(), 93);
    }

    #[test]
    fn every_vector_assembles_and_decodes() {
        let coverage = Coverage::collect();
        assert!(
            coverage.vector_count() > 0,
            "no vectors discovered — coverage would be vacuously empty"
        );
        assert!(
            coverage.unreadable().is_empty(),
            "vectors failed to assemble or decode:\n{}",
            coverage.unreadable().join("\n")
        );
    }

    /// The suite exercises arithmetic heavily, so a report that does not
    /// see `ADD` on both measures is measuring nothing.
    #[test]
    fn a_heavily_used_opcode_counts_on_both_measures() {
        let coverage = Coverage::collect();
        assert!(!coverage.absent().contains(&"ADD"));
        assert!(!coverage.present_but_unreached().contains(&"ADD"));
    }

    /// Reached-only would report `DIV` as uncovered, because the
    /// instruction that raises never completes a step. Present catches it.
    /// This is the case that motivates measuring both.
    #[test]
    fn the_faulting_opcode_is_present_but_not_reached() {
        let mut coverage = Coverage::default();
        let vector = crate::load_vector("arithmetic", "div_by_zero")
            .expect("arithmetic/div_by_zero must exist");
        coverage.record(&vector);

        assert!(
            coverage.unreadable().is_empty(),
            "the vector assembles and decodes; only its execution faults"
        );
        assert!(
            !coverage.absent().contains(&"DIV"),
            "DIV is in the program text"
        );
        assert!(
            coverage.present_but_unreached().contains(&"DIV"),
            "the division that raises never completes a step"
        );
    }

    #[test]
    fn reached_is_a_subset_of_present() {
        let coverage = Coverage::collect();
        assert!(coverage.reached_count() <= coverage.present_count());
    }
}
