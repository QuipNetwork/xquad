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
Live QPU integration tests for SolverDWaveQPU.

These tests submit real problems to a D-Wave Advantage QPU via the Leap API.
The module is skipped unless dwave-system is installed, which only the
``dwave`` extra provides; individual tests are skipped unless DWAVE_API_TOKEN
is set in the environment.

Run with:
    DWAVE_API_TOKEN=<token> make test-qpu

Prefer that over a direct pytest call. `make test-qpu` goes through
scripts/run-hardware-tests.sh, which syncs the ``dwave`` extra, rebuilds
xqffi, hard-fails on a missing token via scripts/_hwprobe.py, and treats
"collected zero tests" as a failure -- so this module cannot skip itself
into a hollow green.

A direct call must carry the extra, or the module skips at import:

    DWAVE_API_TOKEN=<token> uv run --extra dwave \\
        pytest xqsa/tests/test_qpu_live.py -v

A bare ``uv run`` re-syncs the workspace to the default no-extras
environment, which uninstalls dwave-system and makes the importorskip below
skip every test -- reporting a green run that submitted nothing to the QPU.
"""

from __future__ import annotations

import os
import random

import pytest

pytestmark = pytest.mark.qpu

_HAS_TOKEN = os.environ.get("DWAVE_API_TOKEN") is not None
# True in CI. In CI these tests must run and hard-fail when the token is missing
# (a silent skip would be a green-but-unverified release gate); locally they
# skip without a token.
_IN_CI = os.environ.get("CI") is not None

skip_no_token = pytest.mark.skipif(
    not _IN_CI and not _HAS_TOKEN,
    reason="DWAVE_API_TOKEN not set (skipped locally; runs and hard-fails in CI)",
)

# `dwave.system` is a real module dependency of this file, not merely a
# runtime one: SolverDWaveQPU lazy-imports it inside __init__
# (xqsa/dwave_qpu.py), but every test below constructs one. It ships only in
# xqsa's `dwave` extra, so under `uv sync --extra cuda` / `--extra metal` --
# what hardware:cuda and hardware:metal install -- this module now skips at
# import rather than dragging the xquad umbrella package, and with it xqffi's
# cdylib, into a collection that had already deselected every test in it.
# That import is what turned all three hardware jobs red on main in August
# 2026 (QUI-1191).
pytest.importorskip(
    "dwave.system",
    reason="dwave-system not installed (run `uv sync --extra dwave`)",
)

from xqsa import SolverDWaveQPU, SolverResult
from xquad.cp import Problem, Types, xq_triu
from xquad.types import XQMXDomain
from xquad.vm import VM, VMBackend

# -- helpers (extracted from examples/maxcut/runner.py) --------------------


def _build_maxcut(n: int, seed: int) -> tuple:
    """Build a MaxCut problem and return (programs, n, edges)."""
    rng = random.Random(seed)
    edges = [(i, j, rng.randint(1, 100)) for i in range(n) for j in range(i + 1, n)]

    problem = Problem("MaxCut")
    num_nodes = problem.input("num_nodes", type=Types.Int)
    edges_in = problem.input("edges", type=Types.Vec)
    problem.define_model(size=num_nodes, domain=XQMXDomain.BINARY)

    edge_count = problem.stow("edge_count", edges_in.veclen() // 3)
    with problem.range(0, edge_count) as e:
        offset = e * 3
        i = problem.stow("i", edges_in.get(offset))
        j = problem.stow("j", edges_in.get(offset + 1))
        w = problem.stow("w", edges_in.get(offset + 2))
        problem.model.linear[i].add(-w)
        problem.model.linear[j].add(-w)
        problem.model.quadratic[i, j].add(w * 2)

    partition = problem.output("partition", type=Types.Vec)
    with problem.range(0, num_nodes) as node:
        partition.append(problem.sample.getline(node))

    return problem.compile(), n, edges


def _run_maxcut(programs, n: int, edges, solver) -> tuple[int, int]:
    """Run the full MaxCut pipeline and return (energy, valid)."""
    flat: list[int] = []
    for i, j, w in edges:
        flat.extend((i, j, w))

    vm = VM(backend=VMBackend.PYTHON)
    vm.set_calldata([n, flat])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]

    result = solver.solve(model)

    vm = VM(backend=VMBackend.PYTHON)
    vm.set_calldata([n, flat, model, result.sample])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    return int(outs[0]), int(outs[1])


# -- helpers (extracted from examples/tsp/runner.py) -----------------------


def _build_tsp(n: int, seed: int) -> tuple:
    """Build a TSP problem and return (programs, n, distances)."""
    rng = random.Random(seed)
    distances = [rng.randint(1, 100) for _ in range(n * (n - 1) // 2)]

    problem = Problem("TSP")
    num_cities = problem.input("num_cities", type=Types.Int)
    distance_matrix = problem.input("distance_matrix", type=Types.Vec)
    problem.define_model(
        size=num_cities * num_cities,
        domain=XQMXDomain.BINARY,
        rows=num_cities,
        cols=num_cities,
    )

    with problem.range(0, num_cities - 1) as city_i:
        with problem.range(city_i + 1, num_cities) as city_j:
            dist = problem.stow("dist", distance_matrix.get(xq_triu(city_i, city_j)))
            with problem.range(0, num_cities) as position:
                next_position = (position + 1) % num_cities
                problem.model.quadratic[(city_i, position), (city_j, next_position)].add(dist)
                problem.model.quadratic[(city_j, position), (city_i, next_position)].add(dist)

    with problem.range(0, num_cities) as city:
        problem.model.apply_onehot_row(city, penalty=100)
    with problem.range(0, num_cities) as position:
        problem.model.apply_onehot_col(position, penalty=100)

    tour = problem.output("tour", type=Types.Vec)
    with problem.range(0, num_cities) as position:
        tour.append(problem.sample.colfind(col=position, value=1))

    return problem.compile(), n, distances


def _run_tsp(programs, n: int, distances, solver) -> tuple[int, int]:
    """Run the full TSP pipeline and return (energy, valid)."""
    vm = VM(backend=VMBackend.PYTHON)
    vm.set_calldata([n, distances])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]

    result = solver.solve(model)

    vm = VM(backend=VMBackend.PYTHON)
    vm.set_calldata([n, distances, model, result.sample])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    return int(outs[0]), int(outs[1])


# -- tests -----------------------------------------------------------------


@skip_no_token
class TestQPUConnection:
    """Verify SolverDWaveQPU connects to a real QPU."""

    def test_solver_reports_name(self) -> None:
        """Constructor resolves to an Advantage system."""
        solver = SolverDWaveQPU(num_reads=1, annealing_time=1)
        assert solver._solver_name
        assert "Advantage" in solver._solver_name or "system" in solver._solver_name


@skip_no_token
class TestQPUBareQUBO:
    """Verify the QPU round-trip with a minimal hand-crafted QUBO."""

    def test_antiferromagnetic_2var(self) -> None:
        """Anti-ferromagnetic 2-variable QUBO returns correct ground state."""
        from xqvm_py.xqmx import XQMX

        model = XQMX.binary_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)
        model.set_quadratic(0, 1, 2.0)

        solver = SolverDWaveQPU(num_reads=1, annealing_time=1)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert result.energy == -1
        assert result.timing > 0
        assert result.metadata["solver"]
        assert result.metadata["qpu_timing"] is not None
        assert isinstance(result.metadata["qpu_timing"], dict)
        assert len(result.metadata["qpu_timing"]) > 0


@skip_no_token
class TestQPUMaxCut:
    """MaxCut end-to-end pipeline on real QPU hardware."""

    def test_maxcut_n5_seed42(self) -> None:
        """MaxCut n=5, seed=42: valid solution with energy <= -232."""
        programs, n, edges = _build_maxcut(5, seed=42)
        solver = SolverDWaveQPU(num_reads=10, annealing_time=20)
        energy, valid = _run_maxcut(programs, n, edges, solver)
        assert valid == 1
        assert energy <= -232


@skip_no_token
class TestQPUTSP:
    """TSP end-to-end pipeline on real QPU hardware."""

    def test_tsp_n4_seed42(self) -> None:
        """TSP n=4, seed=42: valid tour."""
        programs, n, distances = _build_tsp(4, seed=42)
        solver = SolverDWaveQPU(num_reads=10, annealing_time=20)
        energy, valid = _run_tsp(programs, n, distances, solver)
        assert valid == 1


@skip_no_token
class TestQPUMetadata:
    """Verify result metadata from real QPU submissions."""

    def test_qpu_timing_populated(self) -> None:
        """result.metadata['qpu_timing'] is a non-empty dict, result.timing > 0."""
        from xqvm_py.xqmx import XQMX

        model = XQMX.binary_model(2)
        model.set_linear(0, -1.0)

        solver = SolverDWaveQPU(num_reads=1, annealing_time=1)
        result = solver.solve(model)

        assert result.timing > 0
        assert result.metadata["qpu_timing"] is not None
        qpu_timing = result.metadata["qpu_timing"]
        assert isinstance(qpu_timing, dict)
        assert "qpu_sampling_time" in qpu_timing
        assert "qpu_access_time" in qpu_timing
        assert qpu_timing["qpu_sampling_time"] > 0


@skip_no_token
class TestQPUErrorPaths:
    """Verify credential error handling (no QPU submissions)."""

    def test_missing_token_raises(self, monkeypatch) -> None:
        """Unset DWAVE_API_TOKEN raises ValueError with clear message."""
        monkeypatch.delenv("DWAVE_API_TOKEN", raising=False)
        with pytest.raises(ValueError, match="DWAVE_API_TOKEN"):
            SolverDWaveQPU()

    def test_bad_token_raises(self, monkeypatch) -> None:
        """Invalid token surfaces a D-Wave authentication error."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "bad-token")
        with pytest.raises(Exception, match="[Uu]nauthorized|[Aa]uthentication|[Aa]ccess denied|[Ii]nvalid token"):
            SolverDWaveQPU()
