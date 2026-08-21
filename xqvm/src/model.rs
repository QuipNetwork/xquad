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
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Domain {
    /// Binary domain: variables take values in `{0, 1}`.
    Binary,
    /// Spin domain: variables take values in `{-1, 1}`.
    Spin,
    /// Discrete (chromatic) domain: variables take values in the signed
    /// centered range `{-k, -(k-1), ..., k-2, k-1}`.
    ///
    /// `k` is required to be at least 2; the VM rejects `XQMX`/`XSMX`
    /// allocations with smaller `k` via [`crate::Error::InvalidDiscreteK`].
    /// This range matches the `spec/xqvm/SPEC.md` reference and is symmetric
    /// around zero, so the default sample value `0` is always in-domain.
    Discrete(i64),
}

/// A quadratic optimization model (QUBO/Ising/discrete).
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
    pub fn add_linear(&mut self, i: usize, delta: i64) {
        let v = self.linear.entry(i).or_insert(0);
        *v = v.wrapping_add(delta);
        if *v == 0 {
            let _ = self.linear.remove(&i);
        }
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
    pub fn add_quad(&mut self, i: usize, j: usize, delta: i64) {
        let key = if i <= j { (i, j) } else { (j, i) };
        let v = self.quadratic.entry(key).or_insert(0);
        *v = v.wrapping_add(delta);
        if *v == 0 {
            let _ = self.quadratic.remove(&key);
        }
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
        let mut h: i64 = 0;
        for (&i, &coeff) in &self.linear {
            let xi = sample.get(i).copied().ok_or(crate::Error::SizeMismatch {
                model_size: self.size,
                sample_len: i.saturating_add(1),
            })?;
            h = h.wrapping_add(coeff.wrapping_mul(xi));
        }
        for (&(i, j), &coeff) in &self.quadratic {
            let xi = sample.get(i).copied().ok_or(crate::Error::SizeMismatch {
                model_size: self.size,
                sample_len: i.saturating_add(1),
            })?;
            let xj = sample.get(j).copied().ok_or(crate::Error::SizeMismatch {
                model_size: self.size,
                sample_len: j.saturating_add(1),
            })?;
            h = h.wrapping_add(coeff.wrapping_mul(xi).wrapping_mul(xj));
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

    #[test]
    fn add_linear_wraps_past_i64_max() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_linear(0, i64::MAX);

        m.add_linear(0, 1);

        assert_eq!(m.get_linear(0), i64::MIN);
    }

    #[test]
    fn add_linear_wraps_past_i64_min() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_linear(0, i64::MIN);

        m.add_linear(0, -1);

        assert_eq!(m.get_linear(0), i64::MAX);
    }

    #[test]
    fn add_quad_wraps_past_i64_max() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_quad(0, 1, i64::MAX);

        m.add_quad(0, 1, 1);

        assert_eq!(m.get_quad(0, 1), i64::MIN);
    }

    #[test]
    fn add_quad_wraps_past_i64_min() {
        let mut m = XqmxModel::new(Domain::Binary, 2);
        m.set_quad(0, 1, i64::MIN);

        m.add_quad(0, 1, -1);

        assert_eq!(m.get_quad(0, 1), i64::MAX);
    }
}
