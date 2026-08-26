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

"""Execution cost units and the functions that count them.

This module mirrors `xqvm/src/metering.rs` value for value and formula for
formula. It is specified normatively in `spec/xqvm/METERING.md`.

The VM meters execution in *steps*. A step is not an instruction: it is a
unit of work, calibrated so that one step is one `NOP` dispatch and no step
is cheaper than the operation it is charged for. Every instruction pays
`BASE_STEPS` before dispatch; the opcodes whose work scales with data the
program controls -- evaluating a model, expanding a constraint, copying a
register -- charge the extra units counted here before they do that work.

The constants are consensus-visible: `xqvm/src/metering.rs` is the source of
truth, `scripts/check-metering-parity.py` enforces the match, and
`spec/xqvm/METERING.md` specifies them normatively. Changing one is a
breaking change to observable behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import Value

#: Upper bound of the u64 range the Rust VM's counters live in.
U64_MAX = (2**64) - 1

#: Cost of dispatching one instruction, charged before every dispatch.
#:
#: This is the unit: one `NOP`.
BASE_STEPS = 1

#: Cost of writing one coefficient into a model's sparse map.
#:
#: Calibrated against the read-modify-write form (a lookup plus an insert),
#: not the insert-only form: every constraint expansion writes through the
#: read-modify-write path.
COEFF_WRITE_STEPS = 13

#: Cost of accumulating one model term into a Hamiltonian energy.
MODEL_TERM_STEPS = 1

#: Cost of reading one cell of a grid's linear surface.
#:
#: ROWSUM, COLSUM, ROWFIND and COLFIND walk a whole row or column, one
#: lookup per cell, over an extent the program chose: RESIZE bounds
#: `rows * cols` by the register's declared size, which is an allocation
#: bound and not a work-per-step bound. Calibrated against the model
#: surface -- a sparse-map lookup -- rather than the sample surface's list
#: index, so the constant is not cheaper than the dearer of the two.
GRID_CELL_STEPS = 3

#: Cost of writing one element of a sample buffer.
SAMPLE_COPY_STEPS = 1

#: Cost of copying one element out of a vec.
ELEMENT_COPY_STEPS = 1


def _saturating(value: int) -> int:
    """Clamp to the u64 range the Rust VM's counters live in.

    Python integers do not overflow, so a charge that Rust saturates to
    ``u64::MAX`` would otherwise grow past it here and the two VMs would
    disagree about which programs are affordable.
    """
    return min(max(value, 0), U64_MAX)


def equality_expansion_steps(n: int) -> int:
    """Worst-case cost of an equality expansion over ``n`` terms.

    One linear term per index and one quadratic term per unordered pair,
    saturating throughout so an ``n`` large enough to overflow the pair
    count prices out rather than wrapping into a charge the program can
    afford.
    """
    n = _saturating(n)
    pairs = _saturating(n * (n - 1)) // 2
    return _saturating(_saturating(n + pairs) * COEFF_WRITE_STEPS)


def model_eval_steps(sample_len: int, terms: int) -> int:
    """Cost of evaluating a model with ``terms`` nonzero coefficients against
    a sample of ``sample_len`` values, including the copy of the sample.
    """
    sample_len = _saturating(sample_len)
    terms = _saturating(terms)
    copy = _saturating(sample_len * SAMPLE_COPY_STEPS)
    accumulate = _saturating(terms * MODEL_TERM_STEPS)
    return _saturating(copy + accumulate)


def value_copy_steps(value: Value) -> int:
    """Cost of copying a register value.

    Scalars are free: the copy is a machine word. Everything else is
    charged for what it actually holds, because cloning a model clones its
    coefficient maps.
    """
    from .vector import Vec
    from .xqmx import XQMX

    if isinstance(value, int):
        return 0
    if isinstance(value, Vec):
        if value.element_type.kind == "xqmx":
            # Recurse per element rather than inlining the model formula, so
            # that a vec holding samples is charged the same here as `ITER`
            # charges when it copies the same elements one at a time.
            total = 0
            for elem in value:
                total = _saturating(total + value_copy_steps(elem))
            return total
        return _saturating(value.length * ELEMENT_COPY_STEPS)
    if isinstance(value, XQMX):
        if value.is_sample():
            return _saturating(value.size * SAMPLE_COPY_STEPS)
        terms = _saturating(len(value.linear) + len(value.quadratic))
        return _saturating(terms * COEFF_WRITE_STEPS)
    return 0
