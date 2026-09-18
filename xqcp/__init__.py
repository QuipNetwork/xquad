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
Constraint Programming DSL for XQVM.

Compiles high-level problem descriptions into three XQVM assembly
programs: encoder, verifier, and decoder.
"""

from __future__ import annotations

from .expression import (
    BinOp,
    BitLenExpr,
    ColFindExpr,
    ColSumExpr,
    CompareOp,
    Expr,
    GetLineExpr,
    GetQuadExpr,
    GridExpr,
    Literal,
    RegLoad,
    RowFindExpr,
    RowSumExpr,
    SqrExpr,
    TriuExpr,
    Types,
    UnaryOp,
    VecGetExpr,
    VecLenExpr,
    coerce,
)
from .problem import Action, CompiledPrograms, Domain, Problem
from .symbols import (
    CoefficientRef,
    InputRef,
    LinearProxy,
    LoopVar,
    ModelRef,
    OutputRef,
    QuadraticProxy,
    SampleRef,
    VecRef,
)

# ---------------------------------------------------------------------------
# Free functions — xq_* prefix
# ---------------------------------------------------------------------------


def xq_triu(i: Expr | int, j: Expr | int) -> TriuExpr:
    """Upper triangular index: compiles to IDXTRIU opcode."""
    return TriuExpr(coerce(i), coerce(j))


def xq_grid(row: Expr | int, col: Expr | int, cols: Expr | int) -> GridExpr:
    """Grid index (row * cols + col): compiles to IDXGRID opcode."""
    return GridExpr(coerce(row), coerce(col), coerce(cols))


def xq_sqr(val: Expr | int) -> SqrExpr:
    """Square: compiles to SQR opcode."""
    return SqrExpr(coerce(val))


def xq_abs(val: Expr | int) -> UnaryOp:
    """Absolute value: compiles to ABS opcode."""
    return UnaryOp("ABS", coerce(val))


def xq_min(a: Expr | int, b: Expr | int) -> BinOp:
    """Minimum of two values: compiles to MIN opcode."""
    return BinOp("MIN", coerce(a), coerce(b))


def xq_max(a: Expr | int, b: Expr | int) -> BinOp:
    """Maximum of two values: compiles to MAX opcode."""
    return BinOp("MAX", coerce(a), coerce(b))


# -- Logical operators (free functions since Python keywords can't be overloaded) --


def xq_not(cond: Expr | int) -> UnaryOp:
    """Logical NOT: compiles to NOT opcode."""
    return UnaryOp("NOT", coerce(cond))


def xq_and(a: Expr | int, b: Expr | int) -> BinOp:
    """Logical AND: compiles to AND opcode."""
    return BinOp("AND", coerce(a), coerce(b))


def xq_or(a: Expr | int, b: Expr | int) -> BinOp:
    """Logical OR: compiles to OR opcode."""
    return BinOp("OR", coerce(a), coerce(b))


def xq_xor(a: Expr | int, b: Expr | int) -> BinOp:
    """Logical XOR: compiles to XOR opcode."""
    return BinOp("XOR", coerce(a), coerce(b))


def xq_bnot(val: Expr | int) -> UnaryOp:
    """Bitwise NOT: compiles to BNOT opcode."""
    return UnaryOp("BNOT", coerce(val))


def xq_bitlen(val: Expr | int) -> BitLenExpr:
    """Bit length (floor(log2(value)) + 1): compiles to BITLEN opcode."""
    return BitLenExpr(coerce(val))


__all__ = [
    # DSL entry points
    "Problem",
    "Domain",
    "Types",
    "CompiledPrograms",
    # Free functions
    "xq_triu",
    "xq_grid",
    "xq_sqr",
    "xq_abs",
    "xq_min",
    "xq_max",
    "xq_not",
    "xq_and",
    "xq_or",
    "xq_xor",
    "xq_bnot",
    "xq_bitlen",
    # Expression types (for advanced use / testing)
    "Expr",
    "Literal",
    "RegLoad",
    "BinOp",
    "UnaryOp",
    "CompareOp",
    "SqrExpr",
    "GridExpr",
    "VecGetExpr",
    "TriuExpr",
    "ColFindExpr",
    "RowFindExpr",
    "RowSumExpr",
    "ColSumExpr",
    "VecLenExpr",
    "BitLenExpr",
    "GetLineExpr",
    "GetQuadExpr",
    # Symbolic references
    "InputRef",
    "LoopVar",
    "SampleRef",
    "ModelRef",
    "OutputRef",
    "CoefficientRef",
    "LinearProxy",
    "QuadraticProxy",
    "VecRef",
    # Internals
    "Action",
]
