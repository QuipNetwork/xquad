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

//! `xqffi.vm` -- `PyO3` bindings around [`xqvm::Vm`] and its model types.
//!
//! Exposes:
//!
//! - `Vm` -- the interpreter. Construct, `set_calldata(list)`,
//!   `set_output_slots(n)`, `run(bytecode)`, then read `outputs()` /
//!   `stack()`. A fault raises the `XqvmError` subclass named for it.
//! - `Domain` -- a variable domain: `Domain.BINARY`, `Domain.SPIN`, or
//!   `Domain.integer(k)`.
//! - `XqmxModel` -- a quadratic (QUBO/Ising/integer) optimisation model,
//!   with `energy(sample)` evaluated by the VM's own energy function.
//! - `XqmxSample` -- a candidate solution for a model.
//! - `triu(i, j)` -- the index `IDXTRIU` computes.
//! - `XqvmError` and one subclass per fault (see `fault.rs`).
//! - `DEFAULT_STEP_LIMIT`, `DEFAULT_MEMORY_LIMIT`, `MAX_ALLOCATION_SIZE`.
//!
//! Calldata is heterogeneous: each element may be an `int`, a `list[int]`
//! (mapped to `RegVal::VecInt`), an `XqmxModel`, an `XqmxSample`, or
//! `None`. `outputs()` mirrors the inverse dispatch.

use std::hash::{DefaultHasher, Hash, Hasher};

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;

use xqvm::{Domain, Program, RegVal, Vm, XqmxModel, XqmxSample};

use crate::fault;

/// Python wrapper around [`xqvm::Domain`].
///
/// Immutable and hashable, so a domain can key a dict. Equality is by value:
/// `Domain.integer(3) == Domain.integer(3)`.
#[pyclass(name = "Domain", module = "xqffi.vm", frozen, eq, skip_from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq)]
struct PyDomain {
    inner: Domain,
}

#[pymethods]
impl PyDomain {
    /// The binary domain, `{0, 1}`.
    #[classattr]
    #[pyo3(name = "BINARY")]
    const fn binary() -> Self {
        Self {
            inner: Domain::Binary,
        }
    }

    /// The spin domain, `{-1, +1}`.
    #[classattr]
    #[pyo3(name = "SPIN")]
    const fn spin() -> Self {
        Self {
            inner: Domain::Spin,
        }
    }

    /// The integer domain `{0, ..., k-1}`. Raises `ValueError` for `k < 2`.
    #[staticmethod]
    fn integer(k: i64) -> PyResult<Self> {
        Ok(Self {
            inner: integer_domain(k)?,
        })
    }

    /// `"binary"`, `"spin"`, or `"integer"`.
    #[getter]
    const fn name(&self) -> &'static str {
        domain_name(&self.inner)
    }

    /// The number of values an integer domain holds; `None` otherwise.
    #[getter]
    const fn k(&self) -> Option<i64> {
        domain_k(&self.inner)
    }

    /// The domain and its values, e.g. `"integer {0, ..., 2}"`.
    #[getter]
    fn description(&self) -> String {
        self.inner.to_string()
    }

    /// Whether a sample variable in this domain may hold `value`.
    const fn contains(&self, value: i64) -> bool {
        self.inner.contains(value)
    }

    fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        (domain_name(&self.inner), domain_k(&self.inner)).hash(&mut hasher);
        hasher.finish()
    }

    fn __repr__(&self) -> String {
        match self.inner {
            Domain::Binary => "Domain.BINARY".to_owned(),
            Domain::Spin => "Domain.SPIN".to_owned(),
            Domain::Integer(k) => format!("Domain.integer({k})"),
        }
    }
}

/// Python wrapper around [`xqvm::XqmxModel`].
#[pyclass(name = "XqmxModel", module = "xqffi.vm", skip_from_py_object)]
#[derive(Clone)]
struct PyXqmxModel {
    inner: XqmxModel,
}

#[pymethods]
impl PyXqmxModel {
    /// Construct an empty model of `size` variables over `domain`.
    #[new]
    #[pyo3(signature = (domain, size, rows = 0, cols = 0))]
    fn new(domain: &Bound<'_, PyDomain>, size: usize, rows: usize, cols: usize) -> Self {
        Self::build(domain.get().inner, size, rows, cols)
    }

    /// An empty binary model.
    #[staticmethod]
    #[pyo3(signature = (size, rows = 0, cols = 0))]
    fn binary(size: usize, rows: usize, cols: usize) -> Self {
        Self::build(Domain::Binary, size, rows, cols)
    }

    /// An empty spin model.
    #[staticmethod]
    #[pyo3(signature = (size, rows = 0, cols = 0))]
    fn spin(size: usize, rows: usize, cols: usize) -> Self {
        Self::build(Domain::Spin, size, rows, cols)
    }

    /// An empty integer model over `{0, ..., k-1}`. Raises `ValueError` for
    /// `k < 2`.
    #[staticmethod]
    #[pyo3(signature = (size, k, rows = 0, cols = 0))]
    fn integer(size: usize, k: i64, rows: usize, cols: usize) -> PyResult<Self> {
        Ok(Self::build(integer_domain(k)?, size, rows, cols))
    }

    #[getter]
    const fn domain(&self) -> PyDomain {
        PyDomain {
            inner: self.inner.domain,
        }
    }

    #[getter]
    const fn k(&self) -> Option<i64> {
        domain_k(&self.inner.domain)
    }

    #[getter]
    const fn size(&self) -> usize {
        self.inner.size
    }

    #[getter]
    const fn rows(&self) -> usize {
        self.inner.rows
    }

    #[getter]
    const fn cols(&self) -> usize {
        self.inner.cols
    }

    /// Set the linear coefficient of variable `i`.
    ///
    /// Raises `IndexOutOfBounds` unless `0 <= i < size`, as `SETLINE` does.
    fn set_linear(&mut self, i: i64, value: i64) -> PyResult<()> {
        let i = self.variable(i)?;
        self.inner.set_linear(i, value);
        Ok(())
    }

    /// The linear coefficient of variable `i`.
    ///
    /// Raises `IndexOutOfBounds` unless `0 <= i < size`, as `GETLINE` does.
    fn get_linear(&self, i: i64) -> PyResult<i64> {
        Ok(self.inner.get_linear(self.variable(i)?))
    }

    /// Add `delta` to the linear coefficient of variable `i`.
    ///
    /// Raises `IndexOutOfBounds` unless `0 <= i < size`, and
    /// `ArithmeticOverflow` when the result leaves the signed 64-bit range;
    /// either way the model is left unchanged.
    fn add_linear(&mut self, i: i64, delta: i64) -> PyResult<()> {
        let i = self.variable(i)?;
        self.inner
            .add_linear(i, delta)
            .map_err(|e| fault::vm_error(&e))
    }

    /// Set the quadratic coefficient of the pair `(i, j)`.
    ///
    /// Raises `IndexOutOfBounds` unless both indices lie in `[0, size)`, as
    /// `SETQUAD` does.
    fn set_quad(&mut self, i: i64, j: i64, value: i64) -> PyResult<()> {
        let (i, j) = (self.variable(i)?, self.variable(j)?);
        self.inner.set_quad(i, j, value);
        Ok(())
    }

    /// The quadratic coefficient of the pair `(i, j)`.
    ///
    /// Raises `IndexOutOfBounds` unless both indices lie in `[0, size)`, as
    /// `GETQUAD` does.
    fn get_quad(&self, i: i64, j: i64) -> PyResult<i64> {
        Ok(self.inner.get_quad(self.variable(i)?, self.variable(j)?))
    }

    /// Add `delta` to the quadratic coefficient of the pair `(i, j)`.
    ///
    /// Raises `IndexOutOfBounds` unless both indices lie in `[0, size)`, and
    /// `ArithmeticOverflow` when the result leaves the signed 64-bit range;
    /// either way the model is left unchanged.
    fn add_quad(&mut self, i: i64, j: i64, delta: i64) -> PyResult<()> {
        let (i, j) = (self.variable(i)?, self.variable(j)?);
        self.inner
            .add_quad(i, j, delta)
            .map_err(|e| fault::vm_error(&e))
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

    /// The model's energy at `sample`, computed as `ENERGY` computes it.
    ///
    /// Raises `SizeMismatch` when the sample's length differs from the
    /// model's size, and `ArithmeticOverflow` when a term or partial sum
    /// leaves the signed 64-bit range.
    fn energy(&self, sample: &Bound<'_, PyXqmxSample>) -> PyResult<i64> {
        self.inner
            .energy(&sample.borrow().inner.values)
            .map_err(|e| fault::vm_error(&e))
    }

    fn __repr__(&self) -> String {
        format!(
            "XqmxModel(domain={}, size={})",
            domain_name(&self.inner.domain),
            self.inner.size,
        )
    }
}

impl PyXqmxModel {
    /// `i` as a variable index of this model, or `IndexOutOfBounds`.
    ///
    /// The underlying model stores coefficients sparsely and accepts any
    /// index, so the bound is checked here, at the host boundary, the way
    /// the coefficient opcodes check it inside the VM.
    fn variable(&self, i: i64) -> PyResult<usize> {
        usize::try_from(i)
            .ok()
            .filter(|&index| index < self.inner.size)
            .ok_or_else(|| fault::index_out_of_bounds(i, self.inner.size))
    }

    fn build(domain: Domain, size: usize, rows: usize, cols: usize) -> Self {
        let mut inner = XqmxModel::new(domain, size);
        inner.rows = rows;
        inner.cols = cols;
        Self { inner }
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
    /// Construct a sample over `domain` holding `values`.
    ///
    /// Raises `ValueError` if any value lies outside `domain`.
    #[new]
    #[pyo3(signature = (domain, values, rows = 0, cols = 0))]
    fn new(
        domain: &Bound<'_, PyDomain>,
        values: Vec<i64>,
        rows: usize,
        cols: usize,
    ) -> PyResult<Self> {
        Self::build(domain.get().inner, values, rows, cols)
    }

    /// A binary sample. Raises `ValueError` for a value outside `{0, 1}`.
    #[staticmethod]
    #[pyo3(signature = (values, rows = 0, cols = 0))]
    fn binary(values: Vec<i64>, rows: usize, cols: usize) -> PyResult<Self> {
        Self::build(Domain::Binary, values, rows, cols)
    }

    /// A spin sample. Raises `ValueError` for a value outside `{-1, +1}`.
    #[staticmethod]
    #[pyo3(signature = (values, rows = 0, cols = 0))]
    fn spin(values: Vec<i64>, rows: usize, cols: usize) -> PyResult<Self> {
        Self::build(Domain::Spin, values, rows, cols)
    }

    /// An integer sample over `{0, ..., k-1}`. Raises `ValueError` for
    /// `k < 2` or for a value outside the domain.
    #[staticmethod]
    #[pyo3(signature = (values, k, rows = 0, cols = 0))]
    fn integer(values: Vec<i64>, k: i64, rows: usize, cols: usize) -> PyResult<Self> {
        Self::build(integer_domain(k)?, values, rows, cols)
    }

    #[getter]
    const fn domain(&self) -> PyDomain {
        PyDomain {
            inner: self.inner.domain,
        }
    }

    #[getter]
    const fn k(&self) -> Option<i64> {
        domain_k(&self.inner.domain)
    }

    #[getter]
    fn values(&self) -> Vec<i64> {
        self.inner.values.clone()
    }

    /// The number of variables; the same as `len(sample)`.
    #[getter]
    fn size(&self) -> usize {
        self.inner.values.len()
    }

    #[getter]
    const fn rows(&self) -> usize {
        self.inner.rows
    }

    #[getter]
    const fn cols(&self) -> usize {
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

impl PyXqmxSample {
    fn build(domain: Domain, values: Vec<i64>, rows: usize, cols: usize) -> PyResult<Self> {
        // Independent guards rather than one fused condition, so the extent
        // checks (QUI-1164) drop in beside this one.
        //
        // The VM's own check is on SETLINE and ADDLINE, which a host-supplied
        // sample never passes through: `Vm::set_calldata` is infallible by
        // design and stays the trusted-embedder path. Closing that gap is
        // this constructor's job -- and because the type exposes only
        // getters, a sample that constructs cannot afterwards be mutated out
        // of domain, so nothing downstream has to re-scan it.
        if let Some((index, value)) = values
            .iter()
            .enumerate()
            .find(|(_, v)| !domain.contains(**v))
        {
            return Err(PyValueError::new_err(format!(
                "sample value {value} at variable {index} is outside the {domain} domain"
            )));
        }
        let mut inner = XqmxSample::new(domain, values);
        inner.rows = rows;
        inner.cols = cols;
        Ok(Self { inner })
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
    /// An [`XqmxSample`](PyXqmxSample) reaching here holds only in-domain
    /// values: its constructor rejects the rest, and the type exposes no
    /// setter. `xqvm::Vm::set_calldata` underneath stays infallible and is
    /// the trusted-embedder path, so this boundary is where a host's values
    /// are checked, not the VM.
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

    /// Set the step limit (safety cap against runaway loops).
    ///
    /// A step is a metered cost unit, not an instruction: every instruction
    /// charges a base cost before dispatch, and opcodes whose work scales
    /// with program-controlled data charge more. See `spec/xqvm/METERING.md`.
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
    /// Raises the `XqvmError` subclass named for the fault on any execution
    /// failure (`StackUnderflow`, `ArithmeticOverflow`, `TypeMismatch`,
    /// `StepLimitExceeded`, ...), with the fault's description as its
    /// message. Raises `RuntimeError` when `bytecode` does not decode.
    fn run(&mut self, bytecode: &[u8]) -> PyResult<()> {
        let program = Program::decode(bytecode).map_err(|e| fault::decode_error(&e))?;
        self.inner.run(&program).map_err(|e| fault::vm_error(&e))
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

    /// Total steps charged since construction (or the last `reset`).
    fn steps(&self) -> u64 {
        self.inner.steps()
    }

    /// Total instructions dispatched by the last run.
    ///
    /// Distinct from `steps()`, which counts metered cost units: an opcode
    /// whose work scales with the program's own data charges more than one
    /// step for one dispatch. See `spec/xqvm/METERING.md`.
    fn instructions(&self) -> u64 {
        self.inner.instructions()
    }

    /// Reset internal VM state so the instance can be reused.
    ///
    /// Clears everything a run touches: the value stack, the registers,
    /// the loop stack, the step and memory counters, the output slots,
    /// the calldata and the outputs themselves. The two budgets
    /// (`set_step_limit`, `set_memory_limit`) are settings rather than run
    /// state and survive.
    ///
    /// The calldata and the output slots being cleared is what a caller
    /// has to act on: a host that resets and runs again must call
    /// `set_calldata` and `set_output_slots` again too, exactly as it does
    /// after construction. Skipping them runs the next program against no
    /// calldata and zero output slots, which is a wrong answer rather than
    /// an error. `xquad.vm.VM` reinstalls both on every run, so this is
    /// reachable only from this class directly.
    fn reset(&mut self) {
        self.inner.reset();
    }
}

fn integer_domain(k: i64) -> PyResult<Domain> {
    if k >= 2 {
        Ok(Domain::Integer(k))
    } else {
        Err(PyValueError::new_err(format!(
            "integer domain requires k >= 2, got k={k}"
        )))
    }
}

const fn domain_name(domain: &Domain) -> &'static str {
    match domain {
        Domain::Binary => "binary",
        Domain::Spin => "spin",
        Domain::Integer(_) => "integer",
    }
}

const fn domain_k(domain: &Domain) -> Option<i64> {
    match domain {
        Domain::Integer(k) => Some(*k),
        Domain::Binary | Domain::Spin => None,
    }
}

/// The index `IDXTRIU` pushes for the unordered pair `(i, j)`.
///
/// Raises `ArithmeticOverflow` where the opcode would.
#[pyfunction]
fn triu(i: i64, j: i64) -> PyResult<i64> {
    xqvm::triu_index(i, j)
        .ok_or_else(|| fault::vm_error(&xqvm::Error::ArithmeticOverflow { pos: None }))
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
    m.add_class::<PyDomain>()?;
    m.add_class::<PyXqmxModel>()?;
    m.add_class::<PyXqmxSample>()?;
    m.add_function(wrap_pyfunction!(triu, m)?)?;
    fault::register(m)?;
    // Exported so the Python hosts read the budgets off the VM rather than
    // restating the literals.
    m.add("DEFAULT_STEP_LIMIT", xqvm::DEFAULT_STEP_LIMIT)?;
    m.add("DEFAULT_MEMORY_LIMIT", xqvm::DEFAULT_MEMORY_LIMIT)?;
    m.add("MAX_ALLOCATION_SIZE", xqvm::MAX_ALLOCATION_SIZE)?;
    Ok(())
}
