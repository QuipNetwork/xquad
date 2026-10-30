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

"""Tests for the BINARY/SPIN to Ising conversion in xqsa.ising."""

from __future__ import annotations

import itertools
import random

import pytest

from xqffi.vm import XqmxModel
from xqsa.ising import IsingMilli, spins_to_sample, to_ising_milli


def _random_model(rng: random.Random, domain: str) -> XqmxModel:
    size = rng.randint(1, 8)
    model = XqmxModel.binary(size) if domain == "binary" else XqmxModel.spin(size)
    for i in range(size):
        if rng.random() < 0.7:
            model.set_linear(i, rng.randint(-50, 50))
    # i <= k, so diagonal terms are covered too
    for i, k in itertools.combinations_with_replacement(range(size), 2):
        if rng.random() < 0.5:
            model.set_quad(i, k, rng.randint(-50, 50))
    return model


def _ising_energy_milli(ising: IsingMilli, spins: tuple[int, ...]) -> int:
    linear = sum(h * s for h, s in zip(ising.h, spins, strict=True))
    return linear + sum(j * spins[a] * spins[b] for (a, b), j in zip(ising.edges, ising.j, strict=True))


@pytest.mark.parametrize("domain", ["binary", "spin"])
def test_energy_matches_for_every_assignment(domain: str) -> None:
    """For every spin assignment, 1000 * model energy == Ising energy + offset."""
    rng = random.Random(1635)
    for _ in range(200):
        model = _random_model(rng, domain)
        ising = to_ising_milli(model)
        assert len(ising.h) == model.size
        assert len(ising.j) == len(ising.edges)
        assert all(a < b for a, b in ising.edges)
        for spins in itertools.product((-1, 1), repeat=model.size):
            sample = spins_to_sample(model, list(spins))
            assert model.energy(sample) * 1000 == _ising_energy_milli(ising, spins) + ising.offset


def test_binary_coefficients() -> None:
    """A worked BINARY example: h' = 500 h + 250 sum J, J' = 250 J."""
    model = XqmxModel.binary(3)
    model.set_linear(0, 2)
    model.set_quad(0, 1, 4)
    model.set_quad(2, 2, 3)
    ising = to_ising_milli(model)
    assert ising == IsingMilli(h=[2000, 1000, 1500], edges=[(0, 1)], j=[1000], offset=3500)


def test_spin_scales_and_moves_diagonal_to_offset() -> None:
    """SPIN scales by 1000; s * s == 1, so a diagonal term is a constant."""
    model = XqmxModel.spin(2)
    model.set_linear(1, -3)
    model.set_quad(0, 1, 2)
    model.set_quad(0, 0, 5)
    ising = to_ising_milli(model)
    assert ising == IsingMilli(h=[0, -3000], edges=[(0, 1)], j=[2000], offset=5000)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (XqmxModel.binary(6, rows=2, cols=3), [1, 0, 0, 1, 1, 0]),
        (XqmxModel.spin(6, rows=3, cols=2), [1, -1, -1, 1, 1, -1]),
    ],
)
def test_spins_to_sample_keeps_shape(model: XqmxModel, expected: list[int]) -> None:
    """Spins map back into the model's domain, keeping rows and cols."""
    sample = spins_to_sample(model, [1, -1, -1, 1, 1, -1])
    assert sample.domain == model.domain
    assert sample.values == expected
    assert (sample.rows, sample.cols) == (model.rows, model.cols)


def test_spins_to_sample_rejects_non_spin_values() -> None:
    with pytest.raises(ValueError):
        spins_to_sample(XqmxModel.binary(2), [1, 0])


def test_integer_model_is_rejected() -> None:
    model = XqmxModel.integer(2, 3)
    with pytest.raises(ValueError, match="BINARY and SPIN"):
        to_ising_milli(model)
    with pytest.raises(ValueError, match="BINARY and SPIN"):
        spins_to_sample(model, [1, -1])
