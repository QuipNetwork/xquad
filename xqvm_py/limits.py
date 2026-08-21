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

from .errors import ArithmeticOverflow

I64_MIN = -(2**63)
I64_MAX = (2**63) - 1


def check_i64(value: int, context: str = "") -> int:
    """Return ``value`` if it fits in signed 64 bits, else raise.

    Returns the value so a checked computation can be written inline:
    ``check_i64(a * b, "MUL")``.
    """
    if value < I64_MIN or value > I64_MAX:
        raise ArithmeticOverflow(value, context)
    return value
