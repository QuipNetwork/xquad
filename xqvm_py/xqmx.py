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
XQVM XQMX Types and Operations

XQMX represents quadratic models (QUBO/Ising) with:
- mode: MODEL (for building constraints/objectives) or SAMPLE (for solutions)
- domain: BINARY [0,1], SPIN [-1,+1], or DISCRETE [-k, k-1] (signed, centred)
- Grid operations for row/column indexing
- High-level functions (HLF)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum, auto

from .errors import (
    IndexOutOfBounds,
    InvalidAllocation,
    InvalidDiscreteK,
    InvalidGridDimensions,
    SizeMismatch,
    VecLengthMismatch,
    XQMXModeError,
)
from .limits import check_i64


class XQMXMode(Enum):
    """XQMX matrix mode: model (for building) or sample (for solutions)."""

    MODEL = auto()
    SAMPLE = auto()


class XQMXDomain(Enum):
    """XQMX variable domain types."""

    BINARY = auto()  # [0, 1] - QUBO/BQM
    SPIN = auto()  # [-1, +1] - Ising
    DISCRETE = auto()  # [-k, k-1] - signed centred range, 2k values


@dataclass
class XQMX:
    """
    Sparse quadratic matrix for optimization problems.

    XQMX represents a quadratic model (QUBO/Ising) with:
    - mode: MODEL (for building constraints/objectives) or SAMPLE (for solutions)
    - domain: BINARY [0,1], SPIN [-1,+1], or DISCRETE [-k, k-1] (signed, centred)
    - dimensions: size (total variables), rows, cols (for grid indexing)
    - linear: dict mapping variable index -> linear coefficient
    - quadratic: dict mapping (i, j) tuple -> coupling coefficient (i < j)

    For SAMPLE mode, linear stores the variable assignments (0/1 or -1/+1).
    """

    mode: XQMXMode
    domain: XQMXDomain
    size: int  # Total number of variables
    rows: int = 0  # Grid rows (0 if not grid-indexed)
    cols: int = 0  # Grid cols (0 if not grid-indexed)
    linear: dict[int, int] = field(default_factory=dict)
    quadratic: dict[tuple[int, int], int] = field(default_factory=dict)
    discrete_k: int = 2  # For DISCRETE domain: half-width of the range [-k, k-1]

    def __post_init__(self) -> None:
        if self.size < 0:
            raise InvalidAllocation(self.size)
        if self.rows < 0 or self.cols < 0:
            raise InvalidGridDimensions(self.rows, self.cols)
        if self.domain == XQMXDomain.DISCRETE and self.discrete_k < 2:
            raise InvalidDiscreteK(self.discrete_k)

    @classmethod
    def binary_model(cls, size: int, rows: int = 0, cols: int = 0) -> XQMX:
        """Create a binary [0,1] model XQMX."""
        return cls(
            mode=XQMXMode.MODEL,
            domain=XQMXDomain.BINARY,
            size=size,
            rows=rows,
            cols=cols,
        )

    @classmethod
    def spin_model(cls, size: int, rows: int = 0, cols: int = 0) -> XQMX:
        """Create a spin [-1,+1] model XQMX."""
        return cls(
            mode=XQMXMode.MODEL,
            domain=XQMXDomain.SPIN,
            size=size,
            rows=rows,
            cols=cols,
        )

    @classmethod
    def discrete_model(cls, size: int, k: int, rows: int = 0, cols: int = 0) -> XQMX:
        """Create a discrete model XQMX over the signed centred range [-k, k-1]."""
        return cls(
            mode=XQMXMode.MODEL,
            domain=XQMXDomain.DISCRETE,
            size=size,
            rows=rows,
            cols=cols,
            discrete_k=k,
        )

    @classmethod
    def binary_sample(cls, size: int, rows: int = 0, cols: int = 0) -> XQMX:
        """Create a binary [0,1] sample XQMX."""
        return cls(
            mode=XQMXMode.SAMPLE,
            domain=XQMXDomain.BINARY,
            size=size,
            rows=rows,
            cols=cols,
        )

    @classmethod
    def spin_sample(cls, size: int, rows: int = 0, cols: int = 0) -> XQMX:
        """Create a spin [-1,+1] sample XQMX with all positions at -1.

        Pre-populates `linear` so a fresh sample is a valid spin state,
        matching Rust's `exec_ssmx` (`vec![-1; size]`). Binary and
        discrete samples have a default of 0, which is already the
        sparse-dict fallback — only spin needs pre-population.
        """
        return cls(
            mode=XQMXMode.SAMPLE,
            domain=XQMXDomain.SPIN,
            size=size,
            rows=rows,
            cols=cols,
            linear={i: -1 for i in range(size)},
        )

    @classmethod
    def discrete_sample(cls, size: int, k: int, rows: int = 0, cols: int = 0) -> XQMX:
        """Create a discrete sample XQMX over the signed centred range [-k, k-1].

        Values default to 0, which the centred range always contains.
        """
        return cls(
            mode=XQMXMode.SAMPLE,
            domain=XQMXDomain.DISCRETE,
            size=size,
            rows=rows,
            cols=cols,
            discrete_k=k,
        )

    def is_model(self) -> bool:
        """Check if this is a model (vs sample)."""
        return self.mode == XQMXMode.MODEL

    def is_sample(self) -> bool:
        """Check if this is a sample (vs model)."""
        return self.mode == XQMXMode.SAMPLE

    def get_linear(self, i: int) -> int:
        """Get linear coefficient/value for variable i."""
        return self.linear.get(i, 0)

    def set_linear(self, i: int, value: int) -> None:
        """Set linear coefficient/value for variable i."""
        if i < 0 or i >= self.size:
            raise IndexOutOfBounds(i, self.size)

        check_i64(value, f"linear[{i}]")
        if value == 0:
            self.linear.pop(i, None)
        else:
            self.linear[i] = value

    def add_linear(self, i: int, delta: int) -> None:
        """Add to linear coefficient for variable i."""
        if i < 0 or i >= self.size:
            raise IndexOutOfBounds(i, self.size)

        current = self.linear.get(i, 0)
        new_value = check_i64(current + delta, f"linear[{i}]")

        if new_value == 0:
            self.linear.pop(i, None)
        else:
            self.linear[i] = new_value

    def get_quadratic(self, i: int, j: int) -> int:
        """Get quadratic coefficient for variables i, j."""
        if i > j:
            i, j = j, i
        return self.quadratic.get((i, j), 0)

    def set_quadratic(self, i: int, j: int, value: int) -> None:
        """Set quadratic coefficient for variables i, j."""
        if i < 0 or i >= self.size:
            raise IndexOutOfBounds(i, self.size)
        if j < 0 or j >= self.size:
            raise IndexOutOfBounds(j, self.size)

        if i > j:
            i, j = j, i

        check_i64(value, f"quadratic[{i},{j}]")
        if value == 0:
            self.quadratic.pop((i, j), None)
        else:
            self.quadratic[(i, j)] = value

    def add_quadratic(self, i: int, j: int, delta: int) -> None:
        """Add to quadratic coefficient for variables i, j."""
        if i < 0 or i >= self.size:
            raise IndexOutOfBounds(i, self.size)
        if j < 0 or j >= self.size:
            raise IndexOutOfBounds(j, self.size)

        if i > j:
            i, j = j, i

        current = self.quadratic.get((i, j), 0)
        new_value = check_i64(current + delta, f"quadratic[{i},{j}]")

        if new_value == 0:
            self.quadratic.pop((i, j), None)
        else:
            self.quadratic[(i, j)] = new_value

    def iter_linear(self) -> Iterator[tuple[int, int]]:
        """Iterate nonzero linear terms as ``(index, coefficient)``.

        Terms are visited in sorted key order, which ``spec/xqvm/SPEC.md``
        makes normative for every reduction over a model. The underlying
        dict preserves insertion order -- the order the program's
        SETLINE/ADDLINE instructions happened to run -- which is not
        reproducible from the model alone, so it cannot be the order a
        reduction depends on.
        """
        return iter(sorted(self.linear.items()))

    def iter_quadratic(self) -> Iterator[tuple[tuple[int, int], int]]:
        """Iterate nonzero quadratic terms as ``((i, j), coefficient)``.

        Sorted by ``(i, j)``, for the reason given on
        :meth:`iter_linear`.
        """
        return iter(sorted(self.quadratic.items()))

    def grid_index(self, row: int, col: int) -> int:
        """Convert grid (row, col) to linear variable index."""
        if self.rows == 0 or self.cols == 0:
            raise ValueError("Grid indexing requires non-zero rows and cols")
        if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
            raise IndexError(f"Grid position ({row}, {col}) out of range [{self.rows}, {self.cols}]")

        return row * self.cols + col

    def __repr__(self) -> str:
        return (
            f"XQMX(mode={self.mode.name}, domain={self.domain.name}, "
            f"size={self.size}, linear_terms={len(self.linear)}, "
            f"quadratic_terms={len(self.quadratic)})"
        )


# ============================================================================
# Grid Operations
# ============================================================================


def row_indices(xqmx: XQMX, row: int) -> list[int]:
    """
    Get all variable indices in a given row.

    For a grid with `cols` columns, row `r` contains indices [r*cols, (r+1)*cols).

    Validates through `grid_row_extent`, which is where the row rules are
    stated: `InvalidGridDimensions` for an ungridded XQMX,
    `IndexOutOfBounds` for a row outside `[0, rows)`. Both are the typed
    errors the Rust VM raises for the same faults, so the conformance
    harness can map them.
    """
    cols = grid_row_extent(xqmx, row)
    start = row * cols
    return list(range(start, start + cols))


def col_indices(xqmx: XQMX, col: int) -> list[int]:
    """
    Get all variable indices in a given column.

    For a grid with `cols` columns, column `c` contains indices [c, c+cols, c+2*cols, ...].

    Validates through `grid_col_extent`, the column counterpart:
    `InvalidGridDimensions` for an ungridded XQMX, `IndexOutOfBounds` for a
    column outside `[0, cols)`.
    """
    rows = grid_col_extent(xqmx, col)
    return [col + r * xqmx.cols for r in range(rows)]


def grid_row_extent(xqmx: XQMX, row: int) -> int:
    """
    Validate `row` against the grid and return the number of cells a row
    scan will touch, without materialising anything.

    ROWSUM and ROWFIND walk `cols` cells over an extent the program chose,
    so the executor has to charge the step budget between validating the
    operand and doing the walk (QUI-1056). This is the only place the row
    rules are written down -- `row_indices` calls it rather than repeating
    them -- so charging changes what a program is billed and never which
    error it sees.
    """
    if xqmx.rows == 0 or xqmx.cols == 0:
        raise InvalidGridDimensions(xqmx.rows, xqmx.cols)
    if row < 0 or row >= xqmx.rows:
        raise IndexOutOfBounds(row, xqmx.rows)
    return xqmx.cols


def grid_col_extent(xqmx: XQMX, col: int) -> int:
    """
    Validate `col` and return the number of cells a column scan will touch.
    The column counterpart of `grid_row_extent`, and likewise the only
    statement of the column rules: `col_indices` calls it.
    """
    if xqmx.rows == 0 or xqmx.cols == 0:
        raise InvalidGridDimensions(xqmx.rows, xqmx.cols)
    if col < 0 or col >= xqmx.cols:
        raise IndexOutOfBounds(col, xqmx.cols)
    return xqmx.rows


def row_sum(xqmx: XQMX, row: int) -> int:
    """
    Sum all linear values in a given row.

    Used primarily with SAMPLE mode to count active variables in a row.
    """
    total = 0
    # Checked per partial sum, matching the Rust VM: reductions over
    # coefficients are subject to the SPEC.md overflow rule.
    for i in row_indices(xqmx, row):
        total = check_i64(total + xqmx.get_linear(i), "ROWSUM")
    return total


def col_sum(xqmx: XQMX, col: int) -> int:
    """
    Sum all linear values in a given column.

    Used primarily with SAMPLE mode to count active variables in a column.
    """
    total = 0
    # Checked per partial sum; see row_sum.
    for i in col_indices(xqmx, col):
        total = check_i64(total + xqmx.get_linear(i), "COLSUM")
    return total


def row_find(xqmx: XQMX, row: int, value: int) -> int:
    """
    Find the first column index where the row has the given value.

    Used primarily with SAMPLE mode to find which column is selected in a row.
    Returns -1 if no column has the given value. An ungridded XQMX raises
    `InvalidGridDimensions` (via `row_indices`) rather than returning -1,
    matching the Rust VM.
    """
    indices = row_indices(xqmx, row)
    for col, idx in enumerate(indices):
        if xqmx.get_linear(idx) == value:
            return col
    return -1


def col_find(xqmx: XQMX, col: int, value: int) -> int:
    """
    Find the first row index where the column has the given value.

    Used primarily with SAMPLE mode to find which row is selected in a column.
    Returns -1 if no row has the given value. An ungridded XQMX raises
    `InvalidGridDimensions` (via `col_indices`) rather than returning -1,
    matching the Rust VM.
    """
    indices = col_indices(xqmx, col)
    for row, idx in enumerate(indices):
        if xqmx.get_linear(idx) == value:
            return row
    return -1


# ============================================================================
# Mode Validators
# ============================================================================


def require_model_mode(xqmx: XQMX, operation: str) -> None:
    """
    Require that the XQMX is in MODEL mode.

    Raises XQMXModeError if not in MODEL mode.
    """
    if xqmx.mode != XQMXMode.MODEL:
        raise XQMXModeError(operation, xqmx.mode.name, "MODEL")


def require_sample_mode(xqmx: XQMX, operation: str) -> None:
    """
    Require that the XQMX is in SAMPLE mode.

    Raises XQMXModeError if not in SAMPLE mode.
    """
    if xqmx.mode != XQMXMode.SAMPLE:
        raise XQMXModeError(operation, xqmx.mode.name, "SAMPLE")


# ============================================================================
# High-Level Functions (HLF)
# ============================================================================


def expand_onehot(model: XQMX, indices: list[int], penalty: int) -> None:
    """
    Add a one-hot constraint: exactly one variable in indices must be 1.

    Expands (sum(x) - 1)^2 = sum(x)^2 - 2*sum(x) + 1

    For QUBO:
      - Linear terms: -penalty for each x_i (from -2*sum(x), ignoring constant)
      - Quadratic terms: +2*penalty for each pair (x_i, x_j) where i < j

    Note: x_i^2 = x_i for binary variables, so the quadratic expansion
    contributes to linear terms as well, but the net effect is:
      - linear[i] += -penalty  (the -2 + 1 from expansion simplifies)
      - quadratic[i,j] += 2*penalty
    """
    require_model_mode(model, "ONEHOT")

    # Both scale factors are validated before any term is emitted, so an
    # out-of-range factor raises whether or not the expansion goes on to emit
    # a term that uses it. Rust hoists them the same way (`exec_one_hot_r`,
    # `exec_one_hot_c`), and both implementations already do this in
    # `expand_equality`; evaluating `2 * penalty` lazily inside the pair loop
    # made a one-column ONEHOTR halt here and raise on Rust, which under the
    # pallet is an eight-instruction extrinsic that faults on one node
    # version and succeeds on another.
    neg_penalty = check_i64(-penalty, "ONEHOT -penalty")
    two_penalty = check_i64(2 * penalty, "ONEHOT 2*penalty")

    # Linear terms: -penalty for each variable
    for i in indices:
        model.add_linear(i, neg_penalty)

    # Quadratic terms: +2*penalty for each pair
    n = len(indices)
    for a in range(n):
        for b in range(a + 1, n):
            model.add_quadratic(indices[a], indices[b], two_penalty)


def expand_exclude(model: XQMX, i: int, j: int, penalty: int) -> None:
    """
    Add an exclusion constraint: variables i and j cannot both be 1.

    Adds penalty * x_i * x_j to the model.
    This makes it energetically unfavorable for both to be 1 simultaneously.
    """
    require_model_mode(model, "EXCLUDE")

    model.add_quadratic(i, j, penalty)


def expand_implies(model: XQMX, i: int, j: int, penalty: int) -> None:
    """
    Add an implication constraint: x_i = 1 implies x_j = 1.

    Expands x_i * (1 - x_j) = x_i - x_i * x_j

    This penalizes the case where x_i = 1 and x_j = 0.

    For QUBO:
      - linear[i] += penalty
      - quadratic[i,j] += -penalty
    """
    require_model_mode(model, "IMPLIES")

    model.add_linear(i, penalty)
    model.add_quadratic(i, j, check_i64(-penalty, "IMPLIES -penalty"))


def compute_energy(model: XQMX, sample: XQMX) -> int:
    """
    Compute the energy of a sample with respect to a model.

    E = sum_i(linear[i] * x_i) + sum_{i<j}(quadratic[i,j] * x_i * x_j)

    Where x_i are the sample values (from sample.linear).

    The model provides the coefficients, the sample provides the variable assignments.
    """
    require_model_mode(model, "ENERGY (model)")
    require_sample_mode(sample, "ENERGY (sample)")

    if model.size != sample.size:
        raise SizeMismatch(model.size, sample.size, "ENERGY model vs sample")

    energy = 0

    # Sorted key order throughout: the accumulation order is normative
    # (spec/xqvm/SPEC.md), because it decides which partial sums a
    # checked-arithmetic implementation sees.

    # Linear contribution. Every term product and every partial sum is
    # checked, matching XqmxModel::energy on the Rust VM: with unbounded
    # ints, only per-step checks make the same intermediates fault.
    for i, coeff in model.iter_linear():
        x_i = sample.get_linear(i)
        term = check_i64(coeff * x_i, "ENERGY linear term")
        energy = check_i64(energy + term, "ENERGY")

    # Quadratic contribution, checked the same way: the coefficient is
    # multiplied by x_i first, then by x_j, mirroring the Rust order.
    for (i, j), coeff in model.iter_quadratic():
        x_i = sample.get_linear(i)
        x_j = sample.get_linear(j)
        term = check_i64(coeff * x_i, "ENERGY quadratic term")
        term = check_i64(term * x_j, "ENERGY quadratic term")
        energy = check_i64(energy + term, "ENERGY")

    return energy


def expand_equality(
    model: XQMX,
    indices: list[int],
    coeffs: list[int],
    target: int,
    penalty: int,
) -> None:
    """
    Expand weighted equality constraint P * (sum(a_k * x_k) - b)^2 into QUBO terms.

    For binary variables (x^2 = x):
      linear[idx_k]      += P * a_k * (a_k - 2*b)
      quad[idx_k, idx_m]  += P * 2 * a_k * a_m   for k < m
    """
    require_model_mode(model, "EQUALITY")

    n = len(indices)
    if n != len(coeffs):
        raise VecLengthMismatch("indices", n, "coeffs", len(coeffs))

    # Each intermediate is checked, in the same order the Rust VM checks
    # them, so a product that overflows on the way to an in-range result
    # raises on both implementations rather than only on one.
    two_b = check_i64(2 * target, "EQUALITY 2*target")
    for k in range(n):
        a_k = coeffs[k]
        diff = check_i64(a_k - two_b, "EQUALITY a_k - 2*target")
        scaled = check_i64(a_k * diff, "EQUALITY a_k*(a_k - 2*target)")
        model.add_linear(indices[k], check_i64(penalty * scaled, "EQUALITY linear"))

    two_p = check_i64(2 * penalty, "EQUALITY 2*penalty")
    for k in range(n):
        a_k = coeffs[k]
        for m in range(k + 1, n):
            a_m = coeffs[m]
            partial = check_i64(two_p * a_k, "EQUALITY 2*penalty*a_k")
            model.add_quadratic(indices[k], indices[m], check_i64(partial * a_m, "EQUALITY quad"))


def expand_reduce(model: XQMX, var_a: int, var_b: int, p_aux: int) -> int:
    """
    Rosenberg reduction: replace x_a * x_b with auxiliary variable w.

    Allocates w at model.size, adds enforcement terms, returns w.
    """
    require_model_mode(model, "REDUCE")

    if var_a < 0 or var_a >= model.size:
        raise IndexOutOfBounds(var_a, model.size)
    if var_b < 0 or var_b >= model.size:
        raise IndexOutOfBounds(var_b, model.size)

    w = model.size
    model.size += 1

    minus_two_p = check_i64(-2 * p_aux, "REDUCE -2*p_aux")
    three_p = check_i64(3 * p_aux, "REDUCE 3*p_aux")
    model.add_quadratic(var_a, var_b, p_aux)
    model.add_quadratic(var_a, w, minus_two_p)
    model.add_quadratic(var_b, w, minus_two_p)
    model.add_linear(w, three_p)

    return w


def triu(i: int, j: int) -> int:
    """Upper triangular index: maps (i, j) to a linear index (auto-swaps so i < j)."""
    if i >= j:
        i, j = j, i
    return j * (j - 1) // 2 + i
