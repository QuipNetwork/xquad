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

//! Execution cost units and the functions that count them.
//!
//! The VM meters execution in *steps*. A step is not an instruction: it is a
//! unit of work, calibrated so that one step is one `NOP` dispatch and no
//! step is cheaper than the operation it is charged for. Every instruction
//! pays [`BASE_STEPS`](crate::metering::BASE_STEPS) before dispatch; the opcodes whose work scales with
//! data the program controls -- evaluating a model, expanding a constraint,
//! copying a register -- charge the extra units counted here before they do
//! that work.
//!
//! Without this, a single `ENERGY` over a model the program built itself
//! costs one step and an embedder pricing `WeightPerStep * steps` underprices
//! it without bound.
//!
//! The constants are consensus-visible: `xqvm_py/metering.py` mirrors them
//! value for value, `scripts/check-metering-parity.py` enforces it, and
//! `spec/xqvm/METERING.md` specifies them normatively. Changing one is a
//! breaking change to observable behaviour.

use crate::RegVal;

/// Cost of dispatching one instruction, charged before every dispatch.
///
/// This is the unit: one `NOP` dispatch. The nanosecond figure it was
/// calibrated from is recorded in `spec/xqvm/METERING.md`, which is the
/// single place that measurement table lives.
pub const BASE_STEPS: u64 = 1;

/// Cost of writing one coefficient into a model's sparse map.
///
/// Calibrated against the read-modify-write form (`add_linear`/`add_quad`, a
/// lookup plus an insert), not the insert-only `set_*` form: every
/// constraint expansion writes through the read-modify-write path, so the
/// cheaper form would underprice exactly the opcodes that write the most
/// coefficients.
pub const COEFF_WRITE_STEPS: u64 = 13;

/// Cost of accumulating one model term into a Hamiltonian energy.
pub const MODEL_TERM_STEPS: u64 = 1;

/// Cost of reading one cell of a grid's linear surface.
///
/// `ROWSUM`, `COLSUM`, `ROWFIND` and `COLFIND` walk a whole row or column,
/// one lookup per cell, and the extent is program-controlled: `RESIZE`
/// bounds `rows * cols` by the register's declared size, which is an
/// allocation bound and not a work-per-step bound. Calibrated against the
/// model surface -- a sparse-map lookup -- rather than the sample surface's
/// vec index, so the constant is not cheaper than the dearer of the two
/// surfaces it prices.
pub const GRID_CELL_STEPS: u64 = 3;

/// Cost of writing one element of a sample buffer.
pub const SAMPLE_COPY_STEPS: u64 = 1;

/// Cost of copying one `i64` out of a vec.
pub const ELEMENT_COPY_STEPS: u64 = 1;

/// Worst-case cost of an equality expansion over `n` terms: one linear term
/// per index and one quadratic term per unordered pair.
///
/// Saturating throughout, so an `n` large enough to overflow the pair count
/// yields `u64::MAX` and is refused by the budget rather than wrapping into
/// a small charge. Repeated indices collide on one map key, so an expansion
/// can write fewer entries than it is charged for: this bounds the work, it
/// does not measure it.
#[must_use]
pub fn equality_expansion_steps(n: u64) -> u64 {
    let pairs = n.saturating_mul(n.saturating_sub(1)) / 2;
    n.saturating_add(pairs).saturating_mul(COEFF_WRITE_STEPS)
}

/// Cost of evaluating a model with `terms` nonzero coefficients against a
/// sample of `sample_len` values, including the copy of the sample.
#[must_use]
pub fn model_eval_steps(sample_len: u64, terms: u64) -> u64 {
    sample_len
        .saturating_mul(SAMPLE_COPY_STEPS)
        .saturating_add(terms.saturating_mul(MODEL_TERM_STEPS))
}

/// Cost of copying a register value.
///
/// Scalars are free: the copy is a machine word. Everything else is charged
/// for what it actually holds, because cloning a model clones its
/// coefficient maps.
///
/// `spec/xqvm/METERING.md` states the [`RegVal::VecXqmx`] case recursively,
/// as the sum of this function over the elements. The fold below inlines the
/// [`RegVal::Model`] arm instead, which is equivalent here because
/// [`RegVal::VecXqmx`] holds `XqmxModel` values and so cannot contain a
/// sample. An implementation whose vec elements can be samples must recurse,
/// as `xqvm_py/metering.py` does.
#[must_use]
pub fn value_copy_steps(value: &RegVal) -> u64 {
    match value {
        RegVal::Unset | RegVal::Int(_) => 0,
        RegVal::VecInt(v) => widen(v.len()).saturating_mul(ELEMENT_COPY_STEPS),
        RegVal::Sample(s) => widen(s.values.len()).saturating_mul(SAMPLE_COPY_STEPS),
        RegVal::Model(m) => widen(m.linear_len().saturating_add(m.quadratic_len()))
            .saturating_mul(COEFF_WRITE_STEPS),
        RegVal::VecXqmx(v) => v.iter().fold(0u64, |acc, m| {
            let terms = widen(m.linear_len().saturating_add(m.quadratic_len()));
            acc.saturating_add(terms.saturating_mul(COEFF_WRITE_STEPS))
        }),
    }
}

/// Widen a length to the charge width, saturating rather than truncating on
/// a 128-bit target. Matches `charge_variables`.
///
/// Public because `vm.rs` computes charge sizes from `usize` lengths at every
/// call site and must widen them the same way.
#[must_use]
pub fn widen(n: usize) -> u64 {
    u64::try_from(n).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Domain, RegVal, XqmxModel, XqmxSample};

    #[test]
    fn expansion_charges_every_term_and_every_pair() {
        // n linear terms and one quadratic term per unordered pair, the same
        // worst case `equality_expansion_bytes` charges for.
        assert_eq!(equality_expansion_steps(0), 0);
        assert_eq!(equality_expansion_steps(1), COEFF_WRITE_STEPS);
        assert_eq!(equality_expansion_steps(4), 10 * COEFF_WRITE_STEPS);
    }

    #[test]
    fn expansion_saturates_rather_than_wrapping() {
        // An n large enough to overflow the pair count must price out, not
        // wrap into a charge the program can afford.
        assert_eq!(equality_expansion_steps(u64::MAX), u64::MAX);
    }

    #[test]
    fn model_eval_charges_the_copy_and_every_term() {
        assert_eq!(
            model_eval_steps(8, 3),
            8 * SAMPLE_COPY_STEPS + 3 * MODEL_TERM_STEPS
        );
    }

    #[test]
    fn copying_a_scalar_is_free_and_copying_a_model_is_not() {
        assert_eq!(value_copy_steps(&RegVal::Unset), 0);
        assert_eq!(value_copy_steps(&RegVal::Int(7)), 0);
        assert_eq!(
            value_copy_steps(&RegVal::VecInt(vec![1, 2, 3])),
            3 * ELEMENT_COPY_STEPS
        );
        assert_eq!(
            value_copy_steps(&RegVal::Sample(XqmxSample::new(Domain::Binary, vec![0; 5]))),
            5 * SAMPLE_COPY_STEPS
        );

        let mut m = XqmxModel::new(Domain::Binary, 4);
        m.set_linear(0, 1);
        m.set_quad(1, 2, 3);
        assert_eq!(value_copy_steps(&RegVal::Model(m)), 2 * COEFF_WRITE_STEPS);
    }
}
