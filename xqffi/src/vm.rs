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

//! `xqffi.vm` — `PyO3` bindings around [`xqvm::Vm`].
//!
//! Exposes three Python classes:
//!
//! - `Vm` — the interpreter. Construct, `set_calldata(list)`,
//!   `set_output_slots(n)`, `run(bytecode)`, then read `outputs()` /
//!   `stack()`.
//! - `XqmxModel` — a quadratic (QUBO/Ising/discrete) optimisation
//!   model. Passed into `Vm.set_calldata` for programs whose
//!   `INPUT` reads a model slot, and returned from `Vm.outputs()`
//!   when the final register held a [`RegVal::Model`].
//! - `XqmxSample` — a candidate solution for a model. Same plumbing.
//!
//! Calldata is now heterogeneous: each element may be an `int`, a
//! `list[int]` (mapped to `RegVal::VecInt`), an `XqmxModel`, or an
//! `XqmxSample`. `outputs()` mirrors the inverse dispatch.

use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;

use xqvm::{Domain, Program, RegVal, Vm, XqmxModel, XqmxSample};

/// Python wrapper around [`xqvm::XqmxModel`].
#[pyclass(name = "XqmxModel", module = "xqffi.vm", skip_from_py_object)]
#[derive(Clone)]
struct PyXqmxModel {
    inner: XqmxModel,
}

#[pymethods]
impl PyXqmxModel {
    /// Construct a fresh model. `domain` is `"binary"`, `"spin"`, or
    /// `"discrete"` (the latter requires `k`).
    #[new]
    #[pyo3(signature = (domain, size, rows = 0, cols = 0, k = None))]
    fn new(domain: &str, size: usize, rows: usize, cols: usize, k: Option<i64>) -> PyResult<Self> {
        let dom = domain_from_str(domain, k)?;
        let mut inner = XqmxModel::new(dom, size);
        inner.rows = rows;
        inner.cols = cols;
        Ok(Self { inner })
    }

    #[getter]
    fn domain(&self) -> &'static str {
        domain_name(&self.inner.domain)
    }

    #[getter]
    fn k(&self) -> Option<i64> {
        match &self.inner.domain {
            Domain::Discrete(k) => Some(*k),
            _ => None,
        }
    }

    #[getter]
    fn size(&self) -> usize {
        self.inner.size
    }

    #[getter]
    fn rows(&self) -> usize {
        self.inner.rows
    }

    #[getter]
    fn cols(&self) -> usize {
        self.inner.cols
    }

    fn set_linear(&mut self, i: usize, value: i64) {
        self.inner.set_linear(i, value);
    }

    fn get_linear(&self, i: usize) -> i64 {
        self.inner.get_linear(i)
    }

    fn set_quad(&mut self, i: usize, j: usize, value: i64) {
        self.inner.set_quad(i, j, value);
    }

    fn get_quad(&self, i: usize, j: usize) -> i64 {
        self.inner.get_quad(i, j)
    }

    /// Return the sparse linear terms as `list[(index, coefficient)]`.
    fn linear_items(&self) -> Vec<(usize, i64)> {
        self.inner.iter_linear().collect()
    }

    /// Return the sparse quadratic terms as `list[((i, j), coefficient)]`
    /// with `i <= j`.
    fn quadratic_items(&self) -> Vec<((usize, usize), i64)> {
        self.inner
            .iter_quadratic()
            .map(|(i, j, v)| ((i, j), v))
            .collect()
    }

    fn __repr__(&self) -> String {
        format!(
            "XqmxModel(domain={}, size={})",
            domain_name(&self.inner.domain),
            self.inner.size,
        )
    }
}

/// Python wrapper around [`xqvm::XqmxSample`].
#[pyclass(name = "XqmxSample", module = "xqffi.vm", skip_from_py_object)]
#[derive(Clone)]
struct PyXqmxSample {
    inner: XqmxSample,
}

#[pymethods]
impl PyXqmxSample {
    #[new]
    #[pyo3(signature = (domain, values, rows = 0, cols = 0, k = None))]
    fn new(
        domain: &str,
        values: Vec<i64>,
        rows: usize,
        cols: usize,
        k: Option<i64>,
    ) -> PyResult<Self> {
        let dom = domain_from_str(domain, k)?;
        let mut inner = XqmxSample::new(dom, values);
        inner.rows = rows;
        inner.cols = cols;
        Ok(Self { inner })
    }

    #[getter]
    fn domain(&self) -> &'static str {
        domain_name(&self.inner.domain)
    }

    #[getter]
    fn k(&self) -> Option<i64> {
        match &self.inner.domain {
            Domain::Discrete(k) => Some(*k),
            _ => None,
        }
    }

    #[getter]
    fn values(&self) -> Vec<i64> {
        self.inner.values.clone()
    }

    #[getter]
    fn rows(&self) -> usize {
        self.inner.rows
    }

    #[getter]
    fn cols(&self) -> usize {
        self.inner.cols
    }

    fn __len__(&self) -> usize {
        self.inner.values.len()
    }

    fn __repr__(&self) -> String {
        format!(
            "XqmxSample(domain={}, len={})",
            domain_name(&self.inner.domain),
            self.inner.values.len(),
        )
    }
}

/// Python wrapper around [`xqvm::Vm`].
#[pyclass(name = "Vm", module = "xqffi.vm")]
struct PyVm {
    inner: Vm,
}

#[pymethods]
impl PyVm {
    /// Construct a fresh VM.
    #[new]
    fn new() -> Self {
        Self { inner: Vm::new() }
    }

    /// Set the calldata slots. Each entry may be one of:
    ///
    /// - Python `int` → [`RegVal::Int`]
    /// - Python `list[int]` (or any sequence of ints) → [`RegVal::VecInt`]
    /// - [`XqmxModel`](PyXqmxModel) → [`RegVal::Model`]
    /// - [`XqmxSample`](PyXqmxSample) → [`RegVal::Sample`]
    ///
    /// # Errors
    ///
    /// Raises `TypeError` for any other element type.
    fn set_calldata(&mut self, data: &Bound<'_, PyList>) -> PyResult<()> {
        let mut calldata: Vec<RegVal> = Vec::with_capacity(data.len());
        for item in data.iter() {
            calldata.push(py_to_regval(&item)?);
        }
        let _ = self.inner.set_calldata(calldata);
        Ok(())
    }

    /// Set the number of output slots reserved.
    fn set_output_slots(&mut self, n: usize) {
        let _ = self.inner.set_output_slots(n);
    }

    /// Set the instruction-step limit (safety cap against runaway loops).
    ///
    /// Exact: `0` permits no instructions. Use `set_unlimited_steps()` for an
    /// unbounded run.
    fn set_step_limit(&mut self, limit: u64) {
        let _ = self.inner.set_step_limit(limit);
    }

    /// Remove the step limit, allowing the program to run to completion.
    fn set_unlimited_steps(&mut self) {
        let _ = self.inner.set_unlimited_steps();
    }

    /// Set the allocation budget in bytes (safety cap against oversized
    /// models, samples and vectors). Defaults to 1 GiB.
    fn set_memory_limit(&mut self, bytes: u64) {
        let _ = self.inner.set_memory_limit(bytes);
    }

    /// Return the bytes charged against the allocation budget by the last run.
    fn memory_used(&self) -> u64 {
        self.inner.memory_used()
    }

    /// Execute `bytecode` (raw wire-format bytes) on this VM.
    ///
    /// # Errors
    ///
    /// Raises `RuntimeError` with a formatted VM error on any execution
    /// failure (stack under/overflow, arithmetic overflow, type
    /// mismatch, unset register, etc.).
    fn run(&mut self, bytecode: &[u8]) -> PyResult<()> {
        let program = Program::decode(bytecode)
            .map_err(|e| PyRuntimeError::new_err(format!("decode error: {e:?}")))?;
        self.inner
            .run(&program)
            .map_err(|e| PyRuntimeError::new_err(format!("{e:?}")))
    }

    /// Return the current output slots as a `list` of typed Python
    /// objects (`int`, `list[int]`, `XqmxModel`, `XqmxSample`, or
    /// `None` for unset slots).
    fn outputs<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let list = PyList::empty(py);
        for rv in self.inner.outputs() {
            list.append(regval_to_py(py, rv)?)?;
        }
        Ok(list)
    }

    /// Return the residual stack as `list[int]` (bottom to top).
    fn stack(&self) -> Vec<i64> {
        self.inner.stack().to_vec()
    }

    /// Total steps executed since construction (or the last `reset`).
    fn steps(&self) -> u64 {
        self.inner.steps()
    }

    /// Reset internal VM state so the instance can be reused.
    fn reset(&mut self) {
        self.inner.reset();
    }
}

fn domain_from_str(domain: &str, k: Option<i64>) -> PyResult<Domain> {
    match (domain, k) {
        ("binary", None) => Ok(Domain::Binary),
        ("spin", None) => Ok(Domain::Spin),
        ("discrete", Some(k)) if k >= 2 => Ok(Domain::Discrete(k)),
        ("discrete", Some(k)) => Err(PyValueError::new_err(format!(
            "discrete domain requires k >= 2, got k={k}"
        ))),
        ("discrete", None) => Err(PyValueError::new_err("discrete domain requires k argument")),
        ("binary" | "spin", Some(_)) => Err(PyValueError::new_err(format!(
            "domain={domain:?} does not take k"
        ))),
        _ => Err(PyValueError::new_err(format!(
            "unknown domain {domain:?}; expected \"binary\", \"spin\", or \"discrete\""
        ))),
    }
}

fn domain_name(domain: &Domain) -> &'static str {
    match domain {
        Domain::Binary => "binary",
        Domain::Spin => "spin",
        Domain::Discrete(_) => "discrete",
    }
}

fn py_to_regval(obj: &Bound<'_, PyAny>) -> PyResult<RegVal> {
    if let Ok(model) = obj.extract::<PyRef<'_, PyXqmxModel>>() {
        return Ok(RegVal::Model(model.inner.clone()));
    }
    if let Ok(sample) = obj.extract::<PyRef<'_, PyXqmxSample>>() {
        return Ok(RegVal::Sample(sample.inner.clone()));
    }
    if let Ok(n) = obj.extract::<i64>() {
        return Ok(RegVal::Int(n));
    }
    if let Ok(vec) = obj.extract::<Vec<i64>>() {
        return Ok(RegVal::VecInt(vec));
    }
    if obj.is_none() {
        return Ok(RegVal::Unset);
    }
    Err(PyTypeError::new_err(format!(
        "unsupported calldata element type: {}; expected int, list[int], XqmxModel, XqmxSample, or None",
        obj.get_type()
            .name()
            .map_or_else(|_| "<unknown>".into(), |s| s.to_string()),
    )))
}

fn regval_to_py<'py>(py: Python<'py>, rv: &RegVal) -> PyResult<Bound<'py, PyAny>> {
    match rv {
        RegVal::Unset => Ok(py.None().into_bound(py)),
        RegVal::Int(n) => Ok(n.into_pyobject(py)?.into_any()),
        RegVal::VecInt(v) => Ok(v.clone().into_pyobject(py)?.into_any()),
        RegVal::VecXqmx(v) => {
            let list = PyList::empty(py);
            for m in v {
                let wrapped = Py::new(py, PyXqmxModel { inner: m.clone() })?;
                list.append(wrapped)?;
            }
            Ok(list.into_any())
        }
        RegVal::Model(m) => Ok(Py::new(py, PyXqmxModel { inner: m.clone() })?
            .into_bound(py)
            .into_any()),
        RegVal::Sample(s) => Ok(Py::new(py, PyXqmxSample { inner: s.clone() })?
            .into_bound(py)
            .into_any()),
    }
}

pub(crate) fn register(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyVm>()?;
    m.add_class::<PyXqmxModel>()?;
    m.add_class::<PyXqmxSample>()?;
    // Exported so the Python hosts read the budget off the VM rather than
    // restating the literal. `xquad.vm` and the conformance harness both
    // carried their own copy with a comment claiming to match this one.
    m.add("DEFAULT_STEP_LIMIT", xqvm::DEFAULT_STEP_LIMIT)?;
    Ok(())
}
