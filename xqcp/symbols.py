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

"""
Symbolic references for the constraint programming DSL.

These types represent inputs, loop variables, models, samples,
and outputs as symbolic objects that record operations for later
compilation into assembly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

from xqvm_py import XQMXDomain

from .expression import (
    ColFindExpr,
    ColSumExpr,
    Expr,
    GetLineExpr,
    GetQuadExpr,
    RegLoad,
    RowFindExpr,
    RowSumExpr,
    Types,
    VecGetExpr,
    VecLenExpr,
    _ExprOps,
    coerce,
    line,
)

if TYPE_CHECKING:
    from .problem import Problem


def _require_2d(is_2d: bool, op: str) -> None:
    """Reject a grid-only operation on a flat (non-2D) model.

    Grid opcodes read the XQMX rows/cols shape, which stays 0x0 unless
    define_model() was given both rows= and cols=.  Without this check the
    program compiles and verifies clean, then faults at run time with
    InvalidGridDimensions far from the call that caused it.
    """
    if not is_2d:
        raise ValueError(f"{op}() requires a 2D model; pass rows= and cols= to define_model()")


# ---------------------------------------------------------------------------
# InputRef
# ---------------------------------------------------------------------------


class InputRef(Expr, _ExprOps):
    """Symbolic reference to a declared input."""

    def __init__(self, reg: int, name: str, type_: Types) -> None:
        self.reg = reg
        self.name = name
        self.type_ = type_

    def emit(self, lines: list[str], indent: int) -> None:
        lines.append(line(f"LOAD r{self.reg}", indent))

    def get(self, index_expr: Expr | int) -> VecGetExpr:
        """Access an element of a Vec input."""
        if self.type_ != Types.Vec:
            raise TypeError(f"Cannot index into {self.type_.value} input '{self.name}'")
        return VecGetExpr(self.reg, coerce(index_expr))

    def veclen(self) -> VecLenExpr:
        """Get the length of a Vec input."""
        if self.type_ != Types.Vec:
            raise TypeError(f"Cannot get length of {self.type_.value} input '{self.name}'")
        return VecLenExpr(self.reg)


# ---------------------------------------------------------------------------
# LoopVar
# ---------------------------------------------------------------------------


class LoopVar(Expr, _ExprOps):
    """Symbolic loop variable."""

    def __init__(self, reg: int, name: str) -> None:
        self.reg = reg
        self.name = name

    def emit(self, lines: list[str], indent: int) -> None:
        lines.append(line(f"LOAD r{self.reg}", indent))


# ---------------------------------------------------------------------------
# SampleRef
# ---------------------------------------------------------------------------


class SampleRef:
    """Symbolic reference to the sample (counterpart of the model)."""

    def __init__(self, reg: int, is_2d: bool) -> None:
        self.reg = reg
        self.is_2d = is_2d

    def colfind(self, col: Expr | int, value: int) -> ColFindExpr:
        """Find the row where the given column has the specified value (2D models)."""
        _require_2d(self.is_2d, "colfind")
        return ColFindExpr(self.reg, coerce(col), value)

    def rowfind(self, row: Expr | int, value: int) -> RowFindExpr:
        """Find the column where the given row has the specified value (2D models)."""
        _require_2d(self.is_2d, "rowfind")
        return RowFindExpr(self.reg, coerce(row), value)

    def getline(self, index: Expr | int) -> GetLineExpr:
        """Read a sample variable by index."""
        return GetLineExpr(self.reg, coerce(index))

    def rowsum(self, row: Expr | int) -> RowSumExpr:
        """Sum all values in a row (2D models)."""
        _require_2d(self.is_2d, "rowsum")
        return RowSumExpr(self.reg, coerce(row))

    def colsum(self, col: Expr | int) -> ColSumExpr:
        """Sum all values in a column (2D models)."""
        _require_2d(self.is_2d, "colsum")
        return ColSumExpr(self.reg, coerce(col))


# ---------------------------------------------------------------------------
# CoefficientRef — returned by model.linear[i] / model.quadratic[i, j]
# ---------------------------------------------------------------------------


class CoefficientRef(Expr, _ExprOps):
    """Proxy for a single coefficient, usable as an Expr (emits GET) or target (.add)."""

    def __init__(
        self,
        problem: Problem,
        model: ModelRef,
        kind: str,
        coord: Any,
        coord_b: Any = None,
    ) -> None:
        self._problem = problem
        self._model = model
        self._kind = kind  # "linear" or "quadratic"
        self._coord = coord
        self._coord_b = coord_b

    def emit(self, lines: list[str], indent: int) -> None:
        """Emit GETLINE or GETQUAD."""
        if self._kind == "linear":
            # Emit as GetLineExpr
            inner = GetLineExpr(self._model.reg, coerce(self._coord))
            inner.emit(lines, indent)
        else:
            inner = GetQuadExpr(self._model.reg, coerce(self._coord), coerce(self._coord_b))
            inner.emit(lines, indent)

    def add(self, weight: Expr | int) -> None:
        """Record an ADDLINE/ADDQUAD action."""
        if self._kind == "linear":
            self._problem._record_add_linear(self._model, self._coord, weight)
        else:
            self._problem._record_add_quadratic(self._model, self._coord, self._coord_b, weight)


# ---------------------------------------------------------------------------
# LinearProxy / QuadraticProxy — model.linear / model.quadratic
# ---------------------------------------------------------------------------


class LinearProxy:
    """Proxy for model.linear[i] subscript access."""

    def __init__(self, problem: Problem, model: ModelRef) -> None:
        self._problem = problem
        self._model = model

    def __getitem__(self, coord: Any) -> CoefficientRef:
        return CoefficientRef(self._problem, self._model, "linear", coord)

    def __setitem__(self, coord: Any, weight: Expr | int) -> None:
        self._problem._record_set_linear(self._model, coord, weight)


class QuadraticProxy:
    """Proxy for model.quadratic[i, j] subscript access."""

    def __init__(self, problem: Problem, model: ModelRef) -> None:
        self._problem = problem
        self._model = model

    def __getitem__(self, coords: tuple[Any, Any]) -> CoefficientRef:
        if not isinstance(coords, tuple) or len(coords) != 2:
            raise TypeError(f"Expected (i, j) tuple, got {coords!r}")
        return CoefficientRef(self._problem, self._model, "quadratic", coords[0], coords[1])

    def __setitem__(self, coords: tuple[Any, Any], weight: Expr | int) -> None:
        if not isinstance(coords, tuple) or len(coords) != 2:
            raise TypeError(f"Expected (i, j) tuple, got {coords!r}")
        self._problem._record_set_quadratic(self._model, coords[0], coords[1], weight)


# ---------------------------------------------------------------------------
# ModelRef
# ---------------------------------------------------------------------------


class ModelRef:
    """Symbolic reference to the XQMX model."""

    def __init__(
        self,
        problem: Problem,
        reg: int,
        domain: XQMXDomain,
        cols_reg: int | None,
        is_2d: bool,
    ) -> None:
        self._problem = problem
        self.reg = reg
        self.domain = domain
        self.cols_reg = cols_reg
        self.is_2d = is_2d
        self._linear_proxy: LinearProxy | None = None
        self._quadratic_proxy: QuadraticProxy | None = None

    @property
    def linear(self) -> LinearProxy:
        """Access linear coefficients via model.linear[i]."""
        if self._linear_proxy is None:
            self._linear_proxy = LinearProxy(self._problem, self)
        return self._linear_proxy

    @property
    def quadratic(self) -> QuadraticProxy:
        """Access quadratic coefficients via model.quadratic[i, j]."""
        if self._quadratic_proxy is None:
            self._quadratic_proxy = QuadraticProxy(self._problem, self)
        return self._quadratic_proxy

    def apply_onehot_row(self, row: Expr | int, penalty: int) -> None:
        """Apply one-hot constraint on a row."""
        _require_2d(self.is_2d, "apply_onehot_row")
        self._problem._record_onehot_row(self, row, penalty)

    def apply_onehot_col(self, col: Expr | int, penalty: int) -> None:
        """Apply one-hot constraint on a column."""
        _require_2d(self.is_2d, "apply_onehot_col")
        self._problem._record_onehot_col(self, col, penalty)

    def apply_exclude(self, coord_a: Any, coord_b: Any, penalty: int) -> None:
        """Apply mutual exclusion constraint between two variables."""
        self._problem._record_exclude(self, coord_a, coord_b, penalty)

    def apply_implies(self, coord_a: Any, coord_b: Any, penalty: int) -> None:
        """Apply implication constraint (a implies b)."""
        self._problem._record_implies(self, coord_a, coord_b, penalty)

    def apply_equality(
        self,
        indices: VecRef,
        coeffs: VecRef,
        target: Expr | int,
        penalty: int,
    ) -> None:
        """Apply weighted equality constraint: P*(sum(a_k*x_k) - b)^2."""
        self._problem._record_equality(self, indices, coeffs, target, penalty)

    def apply_atleast(self, indices: VecRef, k: Expr | int, penalty: int) -> None:
        """Apply at-least-k constraint with unit weights."""
        self._problem._record_atleast(self, indices, k, penalty)

    def apply_atleastw(
        self,
        indices: VecRef,
        coeffs: VecRef,
        k: Expr | int,
        penalty: int,
    ) -> None:
        """Apply weighted at-least-k constraint."""
        self._problem._record_atleastw(self, indices, coeffs, k, penalty)

    def apply_inequality(
        self,
        indices: VecRef,
        coeffs: VecRef,
        target: Expr | int,
        capacity: Expr | int,
        penalty: int,
    ) -> None:
        """Apply inequality constraint via SLACK + EQUALITY composition."""
        self._problem._record_inequality(self, indices, coeffs, target, capacity, penalty)

    def reduce(self, var_a: Expr | int, var_b: Expr | int, p_aux: Expr | int) -> RegLoad:
        """HOBO degree reduction: replaces x_a*x_b with auxiliary variable w.

        Allocates one auxiliary variable, records the REDUCE action, and
        returns a RegLoad so the caller can chain further reductions.
        """
        stow_reg = self._problem._alloc.alloc()
        self._problem._record_reduce(self, var_a, var_b, p_aux, stow_reg)
        return RegLoad(stow_reg)


# ---------------------------------------------------------------------------
# OutputRef
# ---------------------------------------------------------------------------


class OutputRef:
    """Symbolic reference to a declared output."""

    def __init__(self, problem: Problem, slot: int, reg: int, name: str, type_: Types) -> None:
        self._problem = problem
        self.slot = slot
        self.reg = reg
        self.name = name
        self.type_ = type_

    def append(self, value_expr: Expr | int) -> None:
        """Append a value to the output vector.

        Problem.output() rejects any type other than Types.Vec, so every
        OutputRef is a vector by construction and no type check is needed here.
        """
        self._problem._record_output_append(self, value_expr)

    def __getitem__(self, index: Expr | int) -> NoReturn:
        """Reading from an output is not supported; outputs are write-only."""
        raise TypeError(
            f"Reading from output '{self.name}' is not supported; outputs are write-only via .append(value)"
        )

    def __setitem__(self, index: Expr | int, value: Expr | int) -> NoReturn:
        """Random-access writes to an output are not supported; use .append(value)."""
        raise TypeError(
            f"Random-access write to output '{self.name}' is not supported; use {self.name}.append(value) instead"
        )


# ---------------------------------------------------------------------------
# VecRef
# ---------------------------------------------------------------------------


class VecRef:
    """Symbolic reference to a user-constructed vector register."""

    def __init__(self, problem: Problem, reg: int) -> None:
        self._problem = problem
        self.reg = reg

    def push(self, value: Expr | int) -> None:
        """Append a value to the vector (emits VECPUSH)."""
        self._problem._record_vec_push(self, value)

    def get(self, index: Expr | int) -> VecGetExpr:
        """Access an element by index (emits VECGET)."""
        return VecGetExpr(self.reg, coerce(index))

    def veclen(self) -> VecLenExpr:
        """Get the length of the vector (emits VECLEN)."""
        return VecLenExpr(self.reg)
