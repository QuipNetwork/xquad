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

"""Tests for the xqsa solver registry and build_solver factory."""

from __future__ import annotations

from typing import Any

import pytest

import xqsa
from xqsa import registry


class TestRegistry:
    """The name->class registry and its default."""

    def test_expected_solver_names(self) -> None:
        """SOLVERS exposes exactly the four backend names."""
        assert set(xqsa.SOLVERS) == {"dwave-cpu", "dwave-qpu", "cuda-gpu", "metal-gpu"}

    def test_default_solver_is_registered(self) -> None:
        """DEFAULT_SOLVER is a real registry key, so it always resolves."""
        assert xqsa.DEFAULT_SOLVER in xqsa.SOLVERS

    def test_default_is_dwave_cpu(self) -> None:
        """The dependency-free CPU annealer is the default baseline."""
        assert xqsa.DEFAULT_SOLVER == "dwave-cpu"


class TestBuildSolver:
    """The build_solver factory."""

    def test_builds_dwave_cpu_with_seed(self) -> None:
        """A seeded backend receives the seed it was given."""
        solver = xqsa.build_solver("dwave-cpu", seed=7)
        assert isinstance(solver, xqsa.SolverDWaveCPU)
        assert solver.seed == 7

    def test_unknown_name_raises_value_error(self) -> None:
        """An unregistered name fails fast and lists the valid choices."""
        with pytest.raises(ValueError, match="Unknown solver"):
            xqsa.build_solver("does-not-exist")

    def test_seed_forwarded_only_to_seeded_solvers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """seed reaches solvers in _SEEDED and is withheld from the rest.

        Uses fakes so the dispatch is exercised without the optional
        hardware deps the real CUDA/Metal/QPU solvers require.
        """
        captured: dict[str, Any] = {}

        class FakeSeeded:
            def __init__(self, seed: int | None = None) -> None:
                captured["seeded"] = {"seed": seed}

        class FakeUnseeded:
            def __init__(self) -> None:
                captured["unseeded"] = {}

        monkeypatch.setitem(registry.SOLVERS, "fake-seeded", FakeSeeded)
        monkeypatch.setitem(registry.SOLVERS, "fake-unseeded", FakeUnseeded)
        monkeypatch.setattr(registry, "_SEEDED", frozenset({"fake-seeded"}))

        registry.build_solver("fake-seeded", seed=99)
        registry.build_solver("fake-unseeded", seed=99)

        assert captured["seeded"] == {"seed": 99}
        assert captured["unseeded"] == {}
