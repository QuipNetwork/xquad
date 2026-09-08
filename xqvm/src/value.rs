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

//! Register value types for the XQVM interpreter.

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;
use core::fmt;

pub(crate) use crate::model::{XqmxModel, XqmxSample};

/// Discriminant tag for a [`RegVal`] variant.
///
/// Used in [`IncompatibleTypeError`] to report what kind of value was found in
/// a register and what kind(s) were required, without carrying the value itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegValKind {
    /// Register is unset (`DROP`ped or never written).
    Unset,
    /// Integer (`i64`).
    Int,
    /// Integer vector (`Vec<i64>`).
    VecInt,
    /// XQMX model vector (`Vec<XqmxModel>`).
    VecXqmx,
    /// XQMX model (QUBO/Ising/discrete).
    Model,
    /// XQMX sample.
    Sample,
}

impl RegValKind {
    /// Returns the canonical short name used in error messages.
    ///
    /// These strings match those previously produced by `RegVal::type_name()`
    /// and are kept identical so that user-visible error text is unchanged.
    pub fn kind_name(self) -> &'static str {
        match self {
            Self::Unset => "unset",
            Self::Int => "int",
            Self::VecInt => "vec<int>",
            Self::VecXqmx => "vec<xqmx>",
            Self::Model => "model",
            Self::Sample => "sample",
        }
    }
}

/// Error returned by [`RegVal`] typed accessors when the register holds an
/// incompatible kind.
///
/// `expected` lists every kind that would have been accepted; `actual` is
/// what the register actually held. The slice form of `expected` allows
/// accessors that accept multiple kinds (e.g., the grid accessors accept
/// both `Model` and `Sample`) to declare their full acceptance set.
///
/// # Examples
///
/// ```rust
/// use xqvm::{RegValKind, IncompatibleTypeError};
///
/// let err = IncompatibleTypeError {
///     expected: &[RegValKind::Model],
///     actual: RegValKind::Int,
/// };
/// assert_eq!(err.actual, RegValKind::Int);
/// assert_eq!(err.expected, &[RegValKind::Model]);
/// assert_eq!(err.to_string(), "incompatible types: expected model, got int");
/// ```
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IncompatibleTypeError {
    /// The kinds the accessor would have accepted.
    pub expected: &'static [RegValKind],
    /// The kind the register actually held.
    pub actual: RegValKind,
}

impl fmt::Display for IncompatibleTypeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "incompatible types: expected ")?;
        let mut first = true;
        for kind in self.expected {
            if !first {
                write!(f, "|")?;
            }
            write!(f, "{}", kind.kind_name())?;
            first = false;
        }
        write!(f, ", got {}", self.actual.kind_name())
    }
}

// Manual impl (not `thiserror::Error`) so this type compiles under `no_std`
// without linking against std. `thiserror` generates an `std::error::Error`
// bound internally, which pulls in std even when the feature is not enabled.
impl core::error::Error for IncompatibleTypeError {}

/// A value that can be stored in an XQVM register.
///
/// Registers begin in the `Unset` state. `STOW`, `INPUT`, `LVAL`, `LIDX`, and
/// the allocator instructions (`BQMX`, `VECI`, etc.) transition a register to
/// one of the typed variants. `DROP` returns it to `Unset`. A `LOAD` on an
/// `Unset` register is a runtime fault (`Error::UnsetRegister`).
#[derive(Debug, Clone, PartialEq)]
pub enum RegVal {
    /// Register has no value; `LOAD` on this slot faults.
    Unset,
    /// Integer. Exchanges values with the stack via `LOAD`/`STOW`.
    Int(i64),
    /// Integer vector, created by `VECI` or `VEC`.
    VecInt(Vec<i64>),
    /// XQMX model vector, created by `VECX`.
    VecXqmx(Vec<XqmxModel>),
    /// XQMX model (QUBO/Ising/discrete), created by `BQMX`/`SQMX`/`XQMX`.
    Model(XqmxModel),
    /// XQMX sample, created by `BSMX`/`SSMX`/`XSMX`.
    Sample(XqmxSample),
}

impl Default for RegVal {
    /// An uninitialised register. Returning `Unset` (rather than `Int(0)`)
    /// means any `vec![RegVal::default(); n]` buffer starts in the same
    /// state as a freshly-constructed `Vm`, and a `LOAD` from such a
    /// register faults — matching `spec/xqvm/SPEC.md`'s Machine State
    /// sketch, which treats unwritten slots as absent, not zero.
    fn default() -> Self {
        Self::Unset
    }
}

impl RegVal {
    /// Returns the kind tag for this register value.
    ///
    /// Replaces the old `type_name()` helper: callers that need a display
    /// string should call `.kind().kind_name()`.
    pub fn kind(&self) -> RegValKind {
        match self {
            Self::Unset => RegValKind::Unset,
            Self::Int(_) => RegValKind::Int,
            Self::VecInt(_) => RegValKind::VecInt,
            Self::VecXqmx(_) => RegValKind::VecXqmx,
            Self::Model(_) => RegValKind::Model,
            Self::Sample(_) => RegValKind::Sample,
        }
    }

    /// Return the integer value, or an [`IncompatibleTypeError`].
    pub(crate) fn as_int(&self) -> Result<i64, IncompatibleTypeError> {
        match self {
            Self::Int(n) => Ok(*n),
            other => Err(IncompatibleTypeError {
                expected: &[RegValKind::Int],
                actual: other.kind(),
            }),
        }
    }

    /// Return a mutable reference to the model, or an [`IncompatibleTypeError`].
    pub(crate) fn as_model_mut(&mut self) -> Result<&mut XqmxModel, IncompatibleTypeError> {
        match self {
            Self::Model(m) => Ok(m),
            other => Err(IncompatibleTypeError {
                expected: &[RegValKind::Model],
                actual: other.kind(),
            }),
        }
    }

    /// Return a shared reference to the model, or an [`IncompatibleTypeError`].
    pub(crate) fn as_model(&self) -> Result<&XqmxModel, IncompatibleTypeError> {
        match self {
            Self::Model(m) => Ok(m),
            other => Err(IncompatibleTypeError {
                expected: &[RegValKind::Model],
                actual: other.kind(),
            }),
        }
    }

    /// Return a mutable reference to the int vec, or an [`IncompatibleTypeError`].
    pub(crate) fn as_vec_int_mut(&mut self) -> Result<&mut Vec<i64>, IncompatibleTypeError> {
        match self {
            Self::VecInt(v) => Ok(v),
            other => Err(IncompatibleTypeError {
                expected: &[RegValKind::VecInt],
                actual: other.kind(),
            }),
        }
    }

    /// Return a shared reference to the int vec, or an [`IncompatibleTypeError`].
    pub(crate) fn as_vec_int(&self) -> Result<&Vec<i64>, IncompatibleTypeError> {
        match self {
            Self::VecInt(v) => Ok(v),
            other => Err(IncompatibleTypeError {
                expected: &[RegValKind::VecInt],
                actual: other.kind(),
            }),
        }
    }

    /// Return an immutable XQMX grid view if this register holds a
    /// model or a sample. Used by grid opcodes (`ROWSUM`, `COLSUM`,
    /// `ROWFIND`, `COLFIND`) per `spec/xqvm/SPEC.md` §303, which accept
    /// either XQMX mode.
    pub(crate) fn as_xqmx_grid(&self) -> Result<XqmxGridRef<'_>, IncompatibleTypeError> {
        match self {
            Self::Model(m) => Ok(XqmxGridRef::Model(m)),
            Self::Sample(s) => Ok(XqmxGridRef::Sample(s)),
            other => Err(IncompatibleTypeError {
                expected: &[RegValKind::Model, RegValKind::Sample],
                actual: other.kind(),
            }),
        }
    }

    /// Return a mutable XQMX grid view for `RESIZE` — the only
    /// grid opcode that mutates the register.
    pub(crate) fn as_xqmx_grid_mut(&mut self) -> Result<XqmxGridRefMut<'_>, IncompatibleTypeError> {
        match self {
            Self::Model(m) => Ok(XqmxGridRefMut::Model(m)),
            Self::Sample(s) => Ok(XqmxGridRefMut::Sample(s)),
            other => Err(IncompatibleTypeError {
                expected: &[RegValKind::Model, RegValKind::Sample],
                actual: other.kind(),
            }),
        }
    }
}

/// Immutable view of the grid-addressed surface of an XQMX register.
///
/// Shared by [`RegVal::Model`] and [`RegVal::Sample`]. Models store
/// `linear` sparsely (`BTreeMap<usize, i64>`, 0 for absent entries);
/// samples store `values` densely (`Vec<i64>`, 0 for out-of-bounds).
pub(crate) enum XqmxGridRef<'a> {
    Model(&'a XqmxModel),
    Sample(&'a XqmxSample),
}

impl XqmxGridRef<'_> {
    pub(crate) fn rows(&self) -> usize {
        match self {
            Self::Model(m) => m.rows,
            Self::Sample(s) => s.rows,
        }
    }

    pub(crate) fn cols(&self) -> usize {
        match self {
            Self::Model(m) => m.cols,
            Self::Sample(s) => s.cols,
        }
    }

    /// Size of the linear surface — number of addressable variables.
    /// For models this is `model.size`; for samples it is
    /// `sample.values.len()`.
    pub(crate) fn size(&self) -> usize {
        match self {
            Self::Model(m) => m.size,
            Self::Sample(s) => s.values.len(),
        }
    }

    /// Read `linear[idx]` — sparse lookup for models (0 if absent),
    /// dense lookup for samples (0 if out-of-bounds).
    pub(crate) fn linear(&self, idx: usize) -> i64 {
        match self {
            Self::Model(m) => m.get_linear(idx),
            Self::Sample(s) => s.values.get(idx).copied().unwrap_or(0),
        }
    }
}

/// Mutable view for `RESIZE`.
pub(crate) enum XqmxGridRefMut<'a> {
    Model(&'a mut XqmxModel),
    Sample(&'a mut XqmxSample),
}

impl XqmxGridRefMut<'_> {
    pub(crate) fn set_grid(&mut self, rows: usize, cols: usize) {
        match self {
            Self::Model(m) => {
                m.rows = rows;
                m.cols = cols;
            }
            Self::Sample(s) => {
                s.rows = rows;
                s.cols = cols;
            }
        }
    }

    pub(crate) fn size(&self) -> usize {
        match self {
            Self::Model(m) => m.size,
            Self::Sample(s) => s.values.len(),
        }
    }

    /// Write `linear[idx] = val`. Indexing is sparse for models
    /// (zero values drop the entry) and dense for samples.
    pub(crate) fn linear_set(&mut self, idx: usize, val: i64) {
        match self {
            Self::Model(m) => m.set_linear(idx, val),
            Self::Sample(s) => {
                if let Some(slot) = s.values.get_mut(idx) {
                    *slot = val;
                }
            }
        }
    }

    /// `linear[idx] += delta`. For models this uses the sparse-aware
    /// accumulator; for samples it's a direct in-place add.
    ///
    /// # Errors
    ///
    /// Returns [`crate::Error::ArithmeticOverflow`] when the sum would leave
    /// the signed 64-bit range, leaving the target unchanged.
    pub(crate) fn linear_add(&mut self, idx: usize, delta: i64) -> Result<(), crate::Error> {
        match self {
            Self::Model(m) => m.add_linear(idx, delta),
            Self::Sample(s) => {
                if let Some(slot) = s.values.get_mut(idx) {
                    *slot = slot
                        .checked_add(delta)
                        .ok_or(crate::Error::ArithmeticOverflow { pos: None })?;
                }
                Ok(())
            }
        }
    }
}
