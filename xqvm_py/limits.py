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

"""The VM's value range and the check that enforces it.

Python integers are unbounded, so nothing stops an intermediate result from
leaving the signed 64-bit range on its own. Every site that produces a value
the VM can observe -- a stack push, a model coefficient, an accumulation --
checks it here, which is what keeps this implementation in step with the
Rust VM's checked arithmetic.

This lives in its own module rather than in ``state`` because ``xqmx`` needs
it too, and ``state`` already imports ``xqmx``.
"""

from __future__ import annotations

from .errors import ArithmeticOverflow, InvalidAllocation

I64_MIN = -(2**63)
I64_MAX = (2**63) - 1

#: Largest size a model may take, whether it was allocated at that size or
#: grown to it. This is the largest value a 32-bit ``usize`` can hold --
#: ``pallet-xqvm`` runs on wasm32 -- so it is written as a literal rather
#: than derived from either implementation's own integer width: deriving it
#: from ``usize::MAX`` or ``sys.maxsize`` would make the two interpreters
#: agree only on hosts that happen to share a pointer width, not on the
#: target that actually matters. ``xqvm::MAX_ALLOCATION_SIZE`` carries the
#: same literal and ``spec/xqvm/SPEC.md`` states it normatively.
#:
#: It lives here rather than in ``executor`` for the reason ``check_i64``
#: does: ``xqmx`` needs it too, and ``executor`` already imports ``xqmx``.
MAX_ALLOCATION_SIZE = 2**32 - 1


def check_i64(value: int, context: str = "") -> int:
    """Return ``value`` if it fits in signed 64 bits, else raise.

    Returns the value so a checked computation can be written inline:
    ``check_i64(a * b, "MUL")``.
    """
    if value < I64_MIN or value > I64_MAX:
        raise ArithmeticOverflow(value, context)
    return value


def check_model_size(size: int) -> int:
    """Return ``size`` if it is a model size, else raise `InvalidAllocation`.

    Steps 3 and 4 of the allocator validation order in `spec/xqvm/SPEC.md`,
    and the counterpart to Rust's `model_size`. Every path that produces a
    model size shares this bound, not only an allocator's operand: `REDUCE`,
    `ATLEAST` and `ATLEASTW` append variables to a model that already exists,
    and `EQUALITY` grows one to cover the largest index it was handed.

    Python integers are unbounded, so nothing here would overflow on its own.
    The check exists to keep the fault identity equal: Rust performs the same
    addition in the executing target's `usize`, which on wasm32 is 32 bits
    wide, and `MAX_ALLOCATION_SIZE` is exactly `usize::MAX` there. Without
    this, a model grown past the maximum would raise `InvalidAllocation` in
    Rust and carry on here.
    """
    if size > MAX_ALLOCATION_SIZE:
        raise InvalidAllocation(size)
    return size
