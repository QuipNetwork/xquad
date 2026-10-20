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
Exact conversion of an XQMX model to an Ising model in milli units.

Solver binaries take Ising problems (spins of -1/+1). A SPIN model passes
through scaled by 1000; a BINARY model is rewritten with ``x = (1 + s) / 2``.
Every coefficient stays an integer number of milli units, so the conversion
is exact: ``1000 * model.energy(sample)`` equals the Ising energy in milli
plus :attr:`IsingMilli.offset`.
"""

from __future__ import annotations

from dataclasses import dataclass

from xqffi.vm import Domain, XqmxModel, XqmxSample


@dataclass(frozen=True)
class IsingMilli:
    """An Ising model with integer milli-unit coefficients.

    ``h`` has one entry per variable, including variables with no terms.
    ``edges`` are the off-diagonal pairs ``(i, j)``, ``i < j``, with ``j``
    holding one coupling per edge. ``offset`` is the constant the Ising form
    drops: ``1000 * model energy == ising energy + offset``.
    """

    h: list[int]
    edges: list[tuple[int, int]]
    j: list[int]
    offset: int


def to_ising_milli(model: XqmxModel) -> IsingMilli:
    """Convert a BINARY or SPIN model to an Ising model in milli units.

    A diagonal term ``(i, i)`` is linear in BINARY (``x * x == x``) and
    constant in SPIN (``s * s == 1``), so it never becomes an edge.

    Raises:
        ValueError: if the model is neither BINARY nor SPIN.
    """
    linear = [0] * model.size
    for i, value in model.linear_items():
        linear[i] = value
    diagonal = 0
    edges: list[tuple[int, int]] = []
    couplings: list[int] = []
    for (i, k), value in model.quadratic_items():
        if i == k:
            if model.domain == Domain.BINARY:
                linear[i] += value
            else:
                diagonal += value
        else:
            edges.append((i, k))
            couplings.append(value)

    if model.domain == Domain.SPIN:
        return IsingMilli(
            h=[1000 * v for v in linear],
            edges=edges,
            j=[1000 * v for v in couplings],
            offset=1000 * diagonal,
        )
    if model.domain != Domain.BINARY:
        raise ValueError(f"Only BINARY and SPIN models convert to Ising, got {model.domain.name}")

    # h_i x_i = h_i/2 + (h_i/2) s_i;  J x_i x_k = J/4 (1 + s_i + s_k + s_i s_k)
    h = [500 * v for v in linear]
    for (i, k), value in zip(edges, couplings, strict=True):
        h[i] += 250 * value
        h[k] += 250 * value
    return IsingMilli(
        h=h,
        edges=edges,
        j=[250 * v for v in couplings],
        offset=500 * sum(linear) + 250 * sum(couplings),
    )


def spins_to_sample(model: XqmxModel, spins: list[int]) -> XqmxSample:
    """Map Ising spins back to a sample in the model's domain and shape.

    Raises:
        ValueError: if the model is neither BINARY nor SPIN, or a spin is not -1/+1.
    """
    if model.domain == Domain.SPIN:
        return XqmxSample.spin(spins, model.rows, model.cols)
    if model.domain != Domain.BINARY:
        raise ValueError(f"Only BINARY and SPIN models convert to Ising, got {model.domain.name}")
    if any(s not in (-1, 1) for s in spins):
        raise ValueError("Ising spins must be -1 or +1")
    return XqmxSample.binary([(s + 1) // 2 for s in spins], model.rows, model.cols)
