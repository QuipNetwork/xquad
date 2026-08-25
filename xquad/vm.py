# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`xquad.vm` — unified VM wrapper with backend selection.

Provides a single `VM` class that dispatches to either the Rust FFI VM
(`xqffi.vm.Vm`) or the pure-Python reference VM (`xqvm_py.Executor`).
All FFI types are converted at the boundary so callers work exclusively
with the canonical Python types from `xquad.types`.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from xqffi.asm import assemble_source as _assemble_source
from xqffi.vm import DEFAULT_STEP_LIMIT as _DEFAULT_STEP_LIMIT
from xqffi.vm import Vm as _RustVm
from xqffi.vm import XqmxModel as ModelFFI
from xqffi.vm import XqmxSample as SampleFFI
from xqvm_py.executor import DEFAULT_MEMORY_LIMIT as _DEFAULT_MEMORY_LIMIT
from xqvm_py.executor import Executor as _PyExecutor
from xqvm_py.program import program_from_bytecode as _program_from_bytecode
from xqvm_py.program import program_from_xqasm as _program_from_xqasm
from xqvm_py.vector import Vec
from xqvm_py.xqmx import XQMX, XQMXDomain

__all__ = ["DEFAULT_STEP_LIMIT", "VM", "VMBackend"]

#: Default step budget, read from the Rust VM rather than restated here so
#: the two cannot drift. Same number as the `xquad run --step-limit`
#: default, which now reads it from the same place.
#:
#: Unbounded execution has to be asked for rather than stumbled into, so
#: `None` is reserved for a caller who writes it. Defaulting to `None` made
#: `Program.from_source("TARGET .0\nNOP\nJUMP .0").session().run()` -- which
#: raised in about a tenth of a second on 0.3.x -- never return.
DEFAULT_STEP_LIMIT: int = _DEFAULT_STEP_LIMIT


class VMBackend(Enum):
    """Selects the interpreter used by `VM`."""

    RUST = auto()
    PYTHON = auto()


# ---------------------------------------------------------------------------
# Domain mapping
# ---------------------------------------------------------------------------

_DOMAIN_TO_STR: dict[XQMXDomain, str] = {
    XQMXDomain.BINARY: "binary",
    XQMXDomain.SPIN: "spin",
    XQMXDomain.DISCRETE: "discrete",
}

_DOMAIN_FROM_STR: dict[str, XQMXDomain] = {v: k for k, v in _DOMAIN_TO_STR.items()}

# ---------------------------------------------------------------------------
# FFI <-> XQMX conversion
# ---------------------------------------------------------------------------


def _xqmx_to_model_ffi(xqmx: XQMX) -> ModelFFI:
    domain_str = _DOMAIN_TO_STR[xqmx.domain]
    k = xqmx.discrete_k if xqmx.domain == XQMXDomain.DISCRETE else None
    model = ModelFFI(domain=domain_str, size=xqmx.size, rows=xqmx.rows, cols=xqmx.cols, k=k)
    for idx, coeff in xqmx.linear.items():
        model.set_linear(idx, coeff)
    for (i, j), coeff in xqmx.quadratic.items():
        model.set_quad(i, j, coeff)
    return model


def _xqmx_to_sample_ffi(xqmx: XQMX) -> SampleFFI:
    domain_str = _DOMAIN_TO_STR[xqmx.domain]
    default = -1 if xqmx.domain == XQMXDomain.SPIN else 0
    values = [xqmx.linear.get(i, default) for i in range(xqmx.size)]
    k = xqmx.discrete_k if xqmx.domain == XQMXDomain.DISCRETE else None
    return SampleFFI(domain=domain_str, values=values, rows=xqmx.rows, cols=xqmx.cols, k=k)


def _model_ffi_to_xqmx(model: ModelFFI) -> XQMX:
    domain = _DOMAIN_FROM_STR[model.domain]
    if domain == XQMXDomain.BINARY:
        xqmx = XQMX.binary_model(model.size, model.rows, model.cols)
    elif domain == XQMXDomain.SPIN:
        xqmx = XQMX.spin_model(model.size, model.rows, model.cols)
    else:
        xqmx = XQMX.discrete_model(model.size, model.k, model.rows, model.cols)
    for idx, coeff in model.linear_items():
        xqmx.linear[idx] = coeff
    for (i, j), coeff in model.quadratic_items():
        xqmx.quadratic[(i, j)] = coeff
    return xqmx


def _sample_ffi_to_xqmx(sample: SampleFFI) -> XQMX:
    domain = _DOMAIN_FROM_STR[sample.domain]
    size = len(sample)
    if domain == XQMXDomain.BINARY:
        xqmx = XQMX.binary_sample(size, sample.rows, sample.cols)
    elif domain == XQMXDomain.SPIN:
        xqmx = XQMX.spin_sample(size, sample.rows, sample.cols)
    else:
        xqmx = XQMX.discrete_sample(size, sample.k, sample.rows, sample.cols)
    for i, v in enumerate(sample.values):
        xqmx.set_linear(i, v)
    return xqmx


# ---------------------------------------------------------------------------
# Calldata preparation
# ---------------------------------------------------------------------------


def _prepare_calldata_rust(data: list) -> list:
    out: list = []
    for item in data:
        if isinstance(item, XQMX):
            if item.is_model():
                out.append(_xqmx_to_model_ffi(item))
            else:
                out.append(_xqmx_to_sample_ffi(item))
        elif isinstance(item, Vec):
            out.append([item.get(i) for i in range(len(item))])
        elif isinstance(item, (int, list)) or item is None:
            out.append(item)
        else:
            raise TypeError(f"unsupported calldata type: {type(item).__name__}")
    return out


def _prepare_calldata_python(data: list) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for i, item in enumerate(data):
        if item is None:
            # An unset slot is kept at its index rather than dropped, so the
            # map stays as long as the caller's sequence. `_prepare_calldata_rust`
            # above passes `None` to `py_to_regval`, which yields `RegVal::Unset`
            # and leaves `calldata.len()` counting it; dropping it here made the
            # same `[None, 5]` two slots on one backend and one on the other, so
            # reading slot 1 succeeded there and raised `CallDataIndex` here.
            result[i] = None
            continue
        if isinstance(item, list):
            result[i] = Vec.from_list(item)
        elif isinstance(item, (int, XQMX, Vec)):
            result[i] = item
        else:
            raise TypeError(f"unsupported calldata type: {type(item).__name__}")
    return result


# ---------------------------------------------------------------------------
# Output conversion
# ---------------------------------------------------------------------------


def _convert_outputs_rust(raw: list) -> list:
    out: list = []
    for item in raw:
        if isinstance(item, ModelFFI):
            out.append(_model_ffi_to_xqmx(item))
        elif isinstance(item, SampleFFI):
            out.append(_sample_ffi_to_xqmx(item))
        else:
            out.append(item)
    return out


def _convert_outputs_python(raw: dict[int, Any], output_slots: int) -> list:
    return [raw.get(i) for i in range(output_slots)]


# ---------------------------------------------------------------------------
# VM
# ---------------------------------------------------------------------------


class VM:
    """Unified XQVM interpreter with backend selection.

    Wraps either the Rust FFI VM or the pure-Python reference VM behind
    a single interface.  All inputs and outputs use canonical Python
    types (`XQMX`, `Vec`, `int`, `list[int]`); FFI conversion is
    handled internally.
    """

    def __init__(self, backend: VMBackend = VMBackend.RUST) -> None:
        self._backend = backend
        self._calldata: list = []
        self._output_slots: int = 0
        self._step_limit: int | None = DEFAULT_STEP_LIMIT
        self._memory_limit: int = _DEFAULT_MEMORY_LIMIT

        if backend == VMBackend.RUST:
            self._rust_vm = _RustVm()
        else:
            self._py_outputs: dict[int, Any] = {}
            self._py_stack: list[int] = []
            self._py_steps: int = 0
            self._py_memory_used: int = 0

    @property
    def backend(self) -> VMBackend:
        return self._backend

    def set_calldata(self, data: list) -> None:
        self._calldata = list(data)

    def set_output_slots(self, n: int) -> None:
        self._output_slots = n

    def set_step_limit(self, limit: int | None) -> None:
        """Cap the instructions a run may execute.

        The limit is exact: `0` permits none at all. `None` is unlimited and
        has to be written by the caller; the default is
        `DEFAULT_STEP_LIMIT`.
        """
        self._step_limit = limit

    def set_memory_limit(self, nbytes: int) -> None:
        """Set the allocation budget in bytes (default 1 GiB).

        Both backends charge the same rates, so both reject the same
        programs at the same instruction.
        """
        self._memory_limit = nbytes

    # -- execution -----------------------------------------------------------

    def run(self, source: str) -> None:
        """Execute `.xqasm` source on the selected backend."""
        if self._backend == VMBackend.RUST:
            bytecode = _assemble_source(source)
            self._run_rust(bytecode)
        else:
            self._run_python(source)

    def run_bytecode(self, bytecode: bytes) -> None:
        """Execute pre-assembled bytecode on the selected backend."""
        if self._backend == VMBackend.RUST:
            self._run_rust(bytecode)
        else:
            self._run_python_bytecode(bytecode)

    # -- results -------------------------------------------------------------

    def outputs(self) -> list:
        if self._backend == VMBackend.RUST:
            return _convert_outputs_rust(list(self._rust_vm.outputs()))
        return _convert_outputs_python(self._py_outputs, self._output_slots)

    def stack(self) -> list[int]:
        if self._backend == VMBackend.RUST:
            return list(self._rust_vm.stack())
        return list(self._py_stack)

    def steps(self) -> int:
        if self._backend == VMBackend.RUST:
            return self._rust_vm.steps()
        return self._py_steps

    def memory_used(self) -> int:
        """Bytes charged against the allocation budget by the last run.

        Both backends charge the same schedule, so this is identical across
        them for any program that runs on both.
        """
        if self._backend == VMBackend.RUST:
            return self._rust_vm.memory_used()
        return self._py_memory_used

    def reset(self) -> None:
        self._calldata = []
        self._output_slots = 0
        self._step_limit = DEFAULT_STEP_LIMIT
        self._memory_limit = _DEFAULT_MEMORY_LIMIT
        if self._backend == VMBackend.RUST:
            self._rust_vm.reset()
        else:
            self._py_outputs = {}
            self._py_stack = []
            self._py_steps = 0
            self._py_memory_used = 0

    # -- private -------------------------------------------------------------

    def _run_rust(self, bytecode: bytes) -> None:
        self._rust_vm.reset()
        cd = _prepare_calldata_rust(self._calldata)
        self._rust_vm.set_calldata(cd)
        self._rust_vm.set_output_slots(self._output_slots)
        if self._step_limit is None:
            self._rust_vm.set_unlimited_steps()
        else:
            self._rust_vm.set_step_limit(self._step_limit)
        self._rust_vm.set_memory_limit(self._memory_limit)
        self._rust_vm.run(bytecode)

    def _run_python(self, source: str) -> None:
        program = _program_from_xqasm(source)
        self._execute_python(program)

    def _run_python_bytecode(self, bytecode: bytes) -> None:
        program = _program_from_bytecode(bytecode)
        self._execute_python(program)

    def _execute_python(self, program) -> None:
        cd = _prepare_calldata_python(self._calldata)
        executor = _PyExecutor()
        self._py_outputs = executor.execute(
            program,
            cd,
            step_limit=self._step_limit,
            memory_limit=self._memory_limit,
            output_slots=self._output_slots,
        )
        self._py_stack = list(executor.state.stack)
        self._py_steps = executor.steps
        self._py_memory_used = executor.memory_used
