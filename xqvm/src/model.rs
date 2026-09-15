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

//! XQMX model and sample types for the XQVM interpreter.
//!
//! XQMX models represent quadratic optimization problems (QUBO/Ising).
//! Samples represent candidate solutions.

#[cfg(not(feature = "std"))]
use alloc::{collections::BTreeMap, vec::Vec};
#[cfg(feature = "std")]
use std::collections::BTreeMap;

/// Variable domain for an XQMX model or sample.
///
/// The domain constrains the values a *sample* variable may hold; model
/// coefficients are unbounded `i64` by design. [`Domain::contains`] is the
/// predicate the VM enforces on `SETLINE` and `ADDLINE` writes into a sample.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Domain {
    /// Binary domain: variables take values in `{0, 1}`.
    Binary,
    /// Spin domain: variables take values in `{-1, +1}`.
    Spin,
    /// Integer domain: variables take values in `{0, ..., k-1}`.
    ///
    /// `k` is the number of values the domain holds, not a half-width. It is
    /// required to be at least 2; the VM rejects `XQMX`/`XSMX` allocations
    /// with smaller `k` via [`crate::Error::InvalidIntegerK`], because a
    /// domain of one value encodes no decision.
    Integer(i64),
}

impl Domain {
    /// The value a freshly allocated sample variable holds.
    ///
    /// Always a member of the domain. The three sample allocators fill their
    /// value vectors with this, which makes "a fresh sample is in-domain" a
    /// structural property rather than three separate coincidences.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Domain;
    ///
    /// assert_eq!(Domain::Binary.default_value(), 0);
    /// assert_eq!(Domain::Spin.default_value(), -1);
    /// assert_eq!(Domain::Integer(3).default_value(), 0);
    /// ```
    pub const fn default_value(&self) -> i64 {
        match self {
            Self::Binary | Self::Integer(_) => 0,
            Self::Spin => -1,
        }
    }

    /// Whether a sample variable in this domain may hold `v`.
    ///
    /// Defined directly rather than as an inclusive range test, for two
    /// reasons: spin has a hole at `0` that a range would wrongly admit, and
    /// a direct `Integer` test is overflow-free for every `k`, including
    /// values no allocator would produce.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Domain;
    ///
    /// assert!(Domain::Binary.contains(1));
    /// assert!(!Domain::Binary.contains(2));
    /// assert!(!Domain::Spin.contains(0));
    /// assert!(Domain::Integer(3).contains(2));
    /// assert!(!Domain::Integer(3).contains(3));
    /// assert!(!Domain::Integer(3).contains(-1));
    /// ```
    pub const fn contains(&self, v: i64) -> bool {
        match self {
            Self::Binary => v == 0 || v == 1,
            Self::Spin => v == -1 || v == 1,
            Self::Integer(k) => v >= 0 && v < *k,
        }
    }
}

impl core::fmt::Display for Domain {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::Binary => write!(f, "binary {{0, 1}}"),
            Self::Spin => write!(f, "spin {{-1, +1}}"),
            Self::Integer(k) => write!(f, "integer {{0, ..., {}}}", k.saturating_sub(1)),
        }
    }
}

/// A quadratic optimization model (QUBO/Ising/integer).
///
/// Encodes H(x) = `sum_i` linear\[i\] * x\[i\] + sum_{i<j} quadratic\[(i,j)\] * x\[i\] * x\[j\].
///
/// # Examples
///
/// ```rust
/// use xqvm::{XqmxModel, Domain};
///
/// let mut m = XqmxModel::new(Domain::Binary, 4);
/// m.set_linear(0, -1);
/// m.set_quad(0, 1, 2);
/// assert_eq!(m.get_linear(0), -1);
/// assert_eq!(m.get_quad(0, 1), 2);
/// ```
#[derive(Debug, Clone, PartialEq)]
pub struct XqmxModel {
    /// Variable domain.
    pub domain: Domain,
    /// Number of variables.
    pub size: usize,
    /// Sparse linear (bias) terms.
    pub(crate) linear: BTreeMap<usize, i64>,
    /// Sparse quadratic (coupling) terms, keyed by (i, j) with i <= j.
    pub(crate) quadratic: BTreeMap<(usize, usize), i64>,
    /// Grid rows (set by RESIZE).
    pub rows: usize,
    /// Grid columns (set by RESIZE).
    pub cols: usize,
}

impl XqmxModel {
    /// Create a new model with the given domain and number of variables.
    pub fn new(domain: Domain, size: usize) -> Self {
        Self {
            domain,
            size,
            linear: BTreeMap::new(),
            quadratic: BTreeMap::new(),
            rows: 0,
            cols: 0,
        }
    }

    /// Get the linear coefficient for variable `i` (0 if absent).
    pub fn get_linear(&self, i: usize) -> i64 {
        self.linear.get(&i).copied().unwrap_or(0)
    }

    /// Set the linear coefficient for variable `i`.
    pub fn set_linear(&mut self, i: usize, val: i64) {
        if val == 0 {
            let _ = self.linear.remove(&i);
        } else {
            let _ = self.linear.insert(i, val);
        }
    }

    /// Add `delta` to the linear coefficient for variable `i`.
    ///
    /// # Errors
    ///
    /// Returns [`crate::Error::ArithmeticOverflow`] when the resulting
    /// coefficient would leave the signed 64-bit range. The model is left
    /// unchanged, so a rejected mutation cannot half-apply.
    pub fn add_linear(&mut self, i: usize, delta: i64) -> Result<(), crate::Error> {
        let current = self.get_linear(i);
        let updated = current
            .checked_add(delta)
            .ok_or(crate::Error::ArithmeticOverflow { pos: None })?;
        self.set_linear(i, updated);
        Ok(())
    }

    /// Get the quadratic coefficient for the pair (i, j). Returns 0 if absent.
    /// Normalises so that i <= j.
    pub fn get_quad(&self, i: usize, j: usize) -> i64 {
        let key = if i <= j { (i, j) } else { (j, i) };
        self.quadratic.get(&key).copied().unwrap_or(0)
    }

    /// Set the quadratic coefficient for the pair (i, j).
    pub fn set_quad(&mut self, i: usize, j: usize, val: i64) {
        let key = if i <= j { (i, j) } else { (j, i) };
        if val == 0 {
            let _ = self.quadratic.remove(&key);
        } else {
            let _ = self.quadratic.insert(key, val);
        }
    }

    /// Add `delta` to the quadratic coefficient for the pair (i, j).
    ///
    /// # Errors
    ///
    /// Returns [`crate::Error::ArithmeticOverflow`] when the resulting
    /// coefficient would leave the signed 64-bit range. The model is left
    /// unchanged.
    pub fn add_quad(&mut self, i: usize, j: usize, delta: i64) -> Result<(), crate::Error> {
        let current = self.get_quad(i, j);
        let updated = current
            .checked_add(delta)
            .ok_or(crate::Error::ArithmeticOverflow { pos: None })?;
        self.set_quad(i, j, updated);
        Ok(())
    }

    /// Return the number of nonzero linear (bias) terms.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::{XqmxModel, Domain};
    ///
    /// let mut m = XqmxModel::new(Domain::Binary, 4);
    /// m.set_linear(0, 1);
    /// m.set_linear(2, -1);
    /// assert_eq!(m.linear_len(), 2);
    /// ```
    pub fn linear_len(&self) -> usize {
        self.linear.len()
    }

    /// Return the number of nonzero quadratic (coupling) terms.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::{XqmxModel, Domain};
    ///
    /// let mut m = XqmxModel::new(Domain::Binary, 4);
    /// m.set_quad(0, 1, 2);
    /// m.set_quad(1, 2, 3);
    /// assert_eq!(m.quadratic_len(), 2);
    /// ```
    pub fn quadratic_len(&self) -> usize {
        self.quadratic.len()
    }

    /// Iterate the nonzero linear (bias) terms as `(index, coefficient)`.
    pub fn iter_linear(&self) -> impl Iterator<Item = (usize, i64)> + '_ {
        self.linear.iter().map(|(&i, &v)| (i, v))
    }

    /// Iterate the nonzero quadratic (coupling) terms as
    /// `(i, j, coefficient)` with `i <= j`.
    pub fn iter_quadratic(&self) -> impl Iterator<Item = (usize, usize, i64)> + '_ {
        self.quadratic.iter().map(|(&(i, j), &v)| (i, j, v))
    }

    /// Compute the Hamiltonian energy H(s) for a given sample vector.
    ///
    /// H(s) = `sum_i` linear\[i\] * s\[i\] + sum_{i<j} quadratic\[(i,j)\] * s\[i\] * s\[j\]
    ///
    /// # Errors
    ///
    /// Returns [`crate::Error::SizeMismatch`] if `sample.len() != self.size`, or if the
    /// model contains a coefficient at an index that exceeds its declared size (indicating
    /// the model was mutated with an out-of-range index).
    pub fn energy(&self, sample: &[i64]) -> Result<i64, crate::Error> {
        if sample.len() != self.size {
            return Err(crate::Error::SizeMismatch {
                model_size: self.size,
                sample_len: sample.len(),
            });
        }
        // Sorted key order throughout, and every partial sum is checked:
        // once overflow raises, the order in which terms are accumulated
        // decides whether a program errors at all (spec/xqvm/HLF.md).
        let overflow = || crate::Error::ArithmeticOverflow { pos: None };
        let mut h: i64 = 0;
        for (i, coeff) in self.iter_linear() {
            let xi = sample.get(i).copied().ok_or(crate::Error::SizeMismatch {
                model_size: self.size,
                sample_len: i.saturating_add(1),
            })?;
            let term = coeff.checked_mul(xi).ok_or_else(overflow)?;
            h = h.checked_add(term).ok_or_else(overflow)?;
        }
        for (i, j, coeff) in self.iter_quadratic() {
            let xi = sample.get(i).copied().ok_or(crate::Error::SizeMismatch {
                model_size: self.size,
                sample_len: i.saturating_add(1),
            })?;
            let xj = sample.get(j).copied().ok_or(crate::Error::SizeMismatch {
                model_size: self.size,
                sample_len: j.saturating_add(1),
            })?;
            let term = coeff
                .checked_mul(xi)
                .and_then(|partial| partial.checked_mul(xj))
                .ok_or_else(overflow)?;
            h = h.checked_add(term).ok_or_else(overflow)?;
        }
        Ok(h)
    }
}

/// A candidate solution for an XQMX model.
///
/// Samples carry optional grid dimensions (`rows`, `cols`) so that
/// grid-addressed opcodes (`ROWSUM`, `COLSUM`, `ROWFIND`, `COLFIND`,
/// `RESIZE`) can operate on samples the same way they do on models
/// per `spec/xqvm/SPEC.md` §303. Default is `0` / `0` (ungridded); set
/// via `RESIZE`.
///
/// # Examples
///
/// ```rust
/// use xqvm::{XqmxSample, Domain};
///
/// let s = XqmxSample::new(Domain::Binary, vec![0, 1, 0, 1]);
/// assert_eq!(s.values[1], 1);
/// ```
#[derive(Debug, Clone, PartialEq)]
pub struct XqmxSample {
    /// Variable domain.
    pub domain: Domain,
    /// One value per variable.
    pub values: Vec<i64>,
    /// Grid rows (set by `RESIZE`; `0` if ungridded).
    pub rows: usize,
    /// Grid columns (set by `RESIZE`; `0` if ungridded).
    pub cols: usize,
}

impl XqmxSample {
    /// Create a new sample with no grid dimensions.
    pub fn new(domain: Domain, values: Vec<i64>) -> Self {
        Self {
            domain,
            values,
            rows: 0,
            cols: 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Domain, XqmxModel};
    use crate::Error;

    #[test]
    fn binary_contains_only_zero_and_one() {
        assert!(Domain::Binary.contains(0));
        assert!(Domain::Binary.contains(1));
        assert!(!Domain::Binary.contains(-1));
        assert!(!Domain::Binary.contains(2));
    }

    #[test]
    fn spin_contains_the_two_poles_and_not_the_gap() {
        assert!(Domain::Spin.contains(-1));
        assert!(Domain::Spin.contains(1));
        // The whole reason `contains` is not a range test: 0 lies inside
        // the envelope [-1, 1] and is not a spin value.
        assert!(!Domain::Spin.contains(0));
        assert!(!Domain::Spin.contains(2));
    }

    #[test]
    fn integer_contains_zero_through_k_minus_one() {
        let d = Domain::Integer(3);
        assert!(d.contains(0));
        assert!(d.contains(1));
        assert!(d.contains(2));
        // Below the domain, and legal under the centred reading QUI-1150
        // replaced.
        assert!(!d.contains(-1));
        // k itself: the off-by-one a `v <= k` guard admits.
        assert!(!d.contains(3));
    }

    #[test]
    fn default_value_is_always_in_domain() {
        for d in [
            Domain::Binary,
            Domain::Spin,
            Domain::Integer(2),
            Domain::Integer(7),
        ] {
            assert!(
                d.contains(d.default_value()),
                "{d} does not contain its own default {}",
                d.default_value()
            );
        }
    }

    #[test]
    fn degenerate_integer_k_does_not_overflow() {
        // `Domain` is publicly re-exported, so an embedder can build a `k`
        // no allocator would produce. `contains` is defined directly rather
        // than as a range test, so it does not panic on one.
        assert!(!Domain::Integer(i64::MIN).contains(0));
        assert!(!Domain::Integer(0).contains(0));
    }

    #[test]
    fn domain_display_matches_error_message() {
        assert_eq!(format!("{}", Domain::Binary), "binary {0, 1}");
        assert_eq!(format!("{}", Domain::Spin), "spin {-1, +1}");
        assert_eq!(format!("{}", Domain::Integer(3)), "integer {0, ..., 2}");
        // The fault's display string embeds it, so the two move together.
        let err = Error::SampleOutOfDomain {
            pos: 0x25,
            index: 1,
            value: 7,
            domain: Domain::Integer(3),
        };
        assert_eq!(
            format!("{err}"),
            "sample value 7 at variable 1 is outside the integer {0, ..., 2} domain \
             at byte 0x0025"
        );
    }

    #[test]
    fn add_linear_raises_past_i64_max() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_linear(0, i64::MAX);

        let err = m
            .add_linear(0, 1)
            .expect_err("coefficient leaves the range");

        assert!(
            matches!(err, Error::ArithmeticOverflow { .. }),
            "got {err:?}"
        );
        assert_eq!(
            m.get_linear(0),
            i64::MAX,
            "a rejected mutation must not apply"
        );
    }

    #[test]
    fn add_linear_raises_past_i64_min() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_linear(0, i64::MIN);

        let err = m
            .add_linear(0, -1)
            .expect_err("coefficient leaves the range");

        assert!(
            matches!(err, Error::ArithmeticOverflow { .. }),
            "got {err:?}"
        );
        assert_eq!(
            m.get_linear(0),
            i64::MIN,
            "a rejected mutation must not apply"
        );
    }

    #[test]
    fn add_quad_raises_past_i64_max() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_quad(0, 1, i64::MAX);

        let err = m
            .add_quad(0, 1, 1)
            .expect_err("coefficient leaves the range");

        assert!(
            matches!(err, Error::ArithmeticOverflow { .. }),
            "got {err:?}"
        );
        assert_eq!(
            m.get_quad(0, 1),
            i64::MAX,
            "a rejected mutation must not apply"
        );
    }

    #[test]
    fn add_quad_raises_past_i64_min() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_quad(0, 1, i64::MIN);

        let err = m
            .add_quad(0, 1, -1)
            .expect_err("coefficient leaves the range");

        assert!(
            matches!(err, Error::ArithmeticOverflow { .. }),
            "got {err:?}"
        );
        assert_eq!(
            m.get_quad(0, 1),
            i64::MIN,
            "a rejected mutation must not apply"
        );
    }

    #[test]
    fn add_linear_within_range_still_accumulates() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.add_linear(0, 5).expect("in range");
        m.add_linear(0, -2).expect("in range");
        assert_eq!(m.get_linear(0), 3);
    }

    #[test]
    fn energy_raises_when_a_partial_sum_leaves_the_range() {
        // Sorted key order is normative, so the first term is linear[0].
        // i64::MAX + i64::MAX overflows on the second.
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_linear(0, i64::MAX);
        m.set_linear(1, i64::MAX);

        let err = m
            .energy(&[1, 1])
            .expect_err("accumulation leaves the range");

        assert!(
            matches!(err, Error::ArithmeticOverflow { .. }),
            "got {err:?}"
        );
    }
}
