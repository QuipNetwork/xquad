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

"""GPU parity suite: the CUDA and Metal solver binaries against XQSA's kernels (QUI-1647).

Each case runs a solver-core binary through XQSA (``SolverGPU._binary``) and,
where XQSA has a kernel for the same engine and algorithm, the in-process
``SolverGPU`` on the same model with the same budget. It gates the removal of
the in-process kernels (QUI-1886).

The binaries are named by environment variables, one per engine and
algorithm: ``XQSA_QUIP_METAL_SA``, ``XQSA_QUIP_METAL_GIBBS``,
``XQSA_QUIP_METAL_MSA``, and the same three for ``CUDA``. A case whose binary
is not named is skipped, in CI too: the hardware jobs do not install the
binaries until QUI-1648 moves them onto the binary manager. A case whose
in-process kernel is missing (no ``msa`` kernel exists, and CUDA has only
``sa``) skips its comparison and keeps its binary-only checks.

Run with ``-m metal`` or ``-m cuda``; ``make test-py`` deselects both.

Where the two paths differ on purpose, the test says so. Where they differ
and QUI-1648 has to settle it, the test is a strict ``xfail`` that names the
difference, so it fails loudly once the difference is gone.
"""

from __future__ import annotations

import itertools
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from xqffi.vm import Domain, XqmxModel, XqmxSample
from xqsa.binary import BinaryImplementation
from xqsa.errors import ModelRejectedError
from xqsa.ising import spins_to_sample, to_ising_milli
from xqsa.solvers import SolverGPU

from .test_gpu_validation import (
    GLASS_TOLERANCE,
    GPU_PARAMS,
    bipartite_maxcut,
    ferro_chain,
    frustrated_triangles,
    random_spin_glass,
)

_IN_CI = os.environ.get("CI") is not None

_ALGORITHMS = ("sa", "gibbs", "msa")
# The algorithms XQSA's in-process kernels run, per engine.
_INPROCESS = {"cuda": frozenset({"sa"}), "metal": frozenset({"sa", "gibbs"})}

# The advertised limits a model is checked against. Every case runs with
# GPU_PARAMS, the budget test_gpu_validation uses, unless it says otherwise.
_LIMITS = ("maxNodes", "maxEdges", "maxDegree", "maxLocalFieldMilli")


@dataclass(frozen=True)
class Target:
    """One solver binary: its engine, its algorithm and where it is."""

    engine: str
    algorithm: str
    path: Path

    def binary(self, **params: Any) -> SolverGPU:
        """The binary through XQSA."""
        return SolverGPU._binary(self.path, engine=self.engine, algorithm=self.algorithm, **(GPU_PARAMS | params))

    def inprocess(self, **params: Any) -> SolverGPU:
        """XQSA's own kernel for the same engine and algorithm, or a skip."""
        if self.algorithm not in _INPROCESS[self.engine]:
            pytest.skip(f"XQSA has no in-process {self.algorithm!r} kernel on {self.engine}")
        try:
            return SolverGPU(engine=self.engine, algorithm=self.algorithm, **(GPU_PARAMS | params))
        except (ImportError, RuntimeError) as e:
            if _IN_CI:
                raise
            pytest.skip(f"in-process {self.engine} is unavailable: {e}")


def _env_name(engine: str, algorithm: str) -> str:
    return f"XQSA_QUIP_{engine.upper()}_{algorithm.upper()}"


@pytest.fixture(
    params=[
        pytest.param((engine, algorithm), marks=getattr(pytest.mark, engine), id=f"{engine}-{algorithm}")
        for engine in ("cuda", "metal")
        for algorithm in _ALGORITHMS
    ]
)
def target(request: pytest.FixtureRequest) -> Target:
    engine, algorithm = request.param
    path = os.environ.get(_env_name(engine, algorithm))
    if not path:
        pytest.skip(f"{_env_name(engine, algorithm)} is not set")
    return Target(engine, algorithm, Path(path))


@pytest.fixture(params=["binary", "inprocess"])
def solver_kind(request: pytest.FixtureRequest) -> str:
    return request.param


def _solver(target: Target, kind: str, **params: Any) -> SolverGPU:
    return target.binary(**params) if kind == "binary" else target.inprocess(**params)


def _brute_force(model: XqmxModel) -> int:
    values = (0, 1) if model.domain == Domain.BINARY else (-1, 1)
    return min(model.energy(XqmxSample(model.domain, list(v))) for v in itertools.product(values, repeat=model.size))


def _quarter_unit_qubo(n: int = 10, seed: int = 3) -> XqmxModel:
    """A BINARY model whose odd couplings become quarter units in Ising form."""
    rng = random.Random(seed)
    model = XqmxModel.binary(n)
    for i in range(n):
        model.set_linear(i, rng.randint(-3, 3))
    for i, j in itertools.combinations(range(n), 2):
        if rng.random() < 0.4:
            model.set_quad(i, j, rng.choice((-3, -1, 1, 3)))
    return model


def _spin_glass(n: int = 12, seed: int = 11, scale: int = 1) -> XqmxModel:
    rng = random.Random(seed)
    model = XqmxModel.spin(n)
    for i, j in itertools.combinations(range(n), 2):
        if rng.random() < 0.5:
            model.set_quad(i, j, scale * rng.choice((-3, -2, -1, 1, 2, 3)))
    return model


def _antiferro_complete(n: int) -> XqmxModel:
    """K_n with every coupling +1. Its ground energy is -(n // 2), at any balanced state."""
    model = XqmxModel.spin(n)
    for i, j in itertools.combinations(range(n), 2):
        model.set_quad(i, j, 1)
    return model


def _fits(caps: dict[str, Any], *, nodes: int, edges: int, degree: int, field_milli: int) -> bool:
    """Whether a model of this shape is inside every limit the binary advertises (0 or absent: no limit)."""
    shape = {"maxNodes": nodes, "maxEdges": edges, "maxDegree": degree, "maxLocalFieldMilli": field_milli}
    return all(not caps.get(key) or shape[key] <= caps[key] for key in _LIMITS)


def _lift_limits(solver: SolverGPU) -> None:
    """Turn off XQSA's pre-spawn limit check, so the binary's own enforcement is what answers."""
    for key in _LIMITS:
        solver._impl.capabilities[key] = 0


class _RecordingBinary(BinaryImplementation):
    """A binary runner that keeps every read the binary returned."""

    reads: list[dict[str, Any]]

    def _parse_reads(self, stdout: str, nodes: int) -> list[dict[str, Any]]:
        self.reads = super()._parse_reads(stdout, nodes)
        return self.reads


class TestArbitraryModels:
    """QUI-78's arbitrary-model cases, through XQSA."""

    def test_quarter_unit_qubo_reaches_brute_force(self, target: Target, solver_kind: str) -> None:
        model = _quarter_unit_qubo()
        assert _solver(target, solver_kind).solve(model).energy == _brute_force(model)

    def test_wide_range_coefficients_solve(self, target: Target) -> None:
        """Fields five orders of magnitude apart, each spin's best value set by its own field.

        Binary only. XQSA's kernels derive their automatic beta range from the largest
        coefficient, (1e-06, 1e-04) here, so the unit-field spins stay near random at the
        coldest beta: the Metal gibbs kernel ends at -400002 with seed 42. The binaries
        reach the ground state, or refuse it before spawning when the largest field is
        above their advertised limit: CUDA msa advertises 16777215 milli.
        """
        model = XqmxModel.spin(8)
        fields = [1 if i % 2 else 100_000 for i in range(8)]
        for i, h in enumerate(fields):
            model.set_linear(i, h if i % 4 < 2 else -h)
        solver = target.binary()
        if _fits(solver._impl.capabilities, nodes=8, edges=0, degree=0, field_milli=1000 * max(fields)):
            assert solver.solve(model).energy == -sum(fields)
        else:
            with pytest.raises(ModelRejectedError) as excinfo:
                solver.solve(model)
            assert excinfo.value.exit_code is None

    def test_same_topology_at_1x_and_1000x(self, target: Target, solver_kind: str) -> None:
        """Scaling every coupling by 1000 scales the ground energy and moves no ground state."""
        small, large = _spin_glass(scale=1), _spin_glass(scale=1000)
        ground = _brute_force(small)
        solver = _solver(target, solver_kind)
        assert solver.solve(small).energy == ground
        assert solver.solve(large).energy == 1000 * ground

    @pytest.mark.parametrize("n", [16, 63, 64, 65, 200])
    def test_complete_graph_solves_or_is_refused_by_its_limits(self, target: Target, n: int) -> None:
        """K_n solves to its ground state inside the advertised limits, and is refused before spawning outside them.

        63 is in the list because Metal advertises a maximum degree of 62: K_63 is the largest
        complete graph it takes.
        """
        solver = target.binary()
        caps = solver._impl.capabilities
        model = _antiferro_complete(n)
        if _fits(caps, nodes=n, edges=n * (n - 1) // 2, degree=n - 1, field_milli=1000 * (n - 1)):
            assert solver.solve(model).energy == -(n // 2)
        else:
            with pytest.raises(ModelRejectedError) as excinfo:
                solver.solve(model)
            assert excinfo.value.exit_code is None

    def test_coefficient_outside_i32_milli_is_refused_before_spawning(self, target: Target) -> None:
        """The advertised limits are lifted so the 32-bit check answers, not a smaller field limit."""
        model = XqmxModel.spin(2)
        model.set_linear(0, 2**31 // 1000 + 1)
        solver = target.binary()
        _lift_limits(solver)
        with pytest.raises(ModelRejectedError, match="32-bit") as excinfo:
            solver.solve(model)
        assert excinfo.value.exit_code is None


class TestReportedEnergy:
    """The energy a binary reports is the model's energy on the spins it reports."""

    @pytest.mark.parametrize("domain", ["binary", "spin"])
    def test_every_read_matches_the_model(self, target: Target, domain: str) -> None:
        model = _quarter_unit_qubo() if domain == "binary" else _spin_glass()
        impl = _RecordingBinary(target.path, algorithm=target.algorithm, num_reads=16, num_sweeps=200, seed=5)
        impl.run(model, **impl.defaults)
        offset = to_ising_milli(model).offset
        for read in impl.reads:
            assert read["energy_milli"] + offset == 1000 * model.energy(spins_to_sample(model, read["spins"]))


class TestCapabilities:
    """What each binary advertises is what it accepts and refuses through XQSA."""

    def test_identity(self, target: Target) -> None:
        caps = target.binary()._impl.capabilities
        assert (caps["backend"], caps["algorithm"], caps["protocolVersion"]) == (target.engine, target.algorithm, 2)
        assert "ISING_SAMPLE" in caps["supportedKinds"]

    def test_degree_limit_is_exact(self, target: Target) -> None:
        """At the advertised maximum degree the binary solves; one above it, the binary itself refuses with exit 65."""
        solver = target.binary(num_reads=4, num_sweeps=50)
        limit = solver._impl.capabilities.get("maxDegree")
        if not limit:
            pytest.skip(f"{target.path.name} advertises no maximum degree")
        at_limit = _antiferro_complete(limit + 1)
        assert solver.solve(at_limit).energy == -((limit + 1) // 2)

        _lift_limits(solver)
        with pytest.raises(ModelRejectedError) as excinfo:
            solver.solve(_antiferro_complete(limit + 2))
        assert excinfo.value.exit_code == 65

    def test_local_field_limit_is_enforced_by_both(self, target: Target) -> None:
        """XQSA refuses a field above the advertised limit without spawning; the binary refuses it too."""
        solver = target.binary(num_reads=4, num_sweeps=50)
        limit = solver._impl.capabilities.get("maxLocalFieldMilli")
        if not limit:
            pytest.skip(f"{target.path.name} advertises no maximum local field")
        model = XqmxModel.spin(2)
        model.set_linear(0, limit // 1000 + 1)

        with pytest.raises(ModelRejectedError) as excinfo:
            solver.solve(model)
        assert excinfo.value.exit_code is None

        _lift_limits(solver)
        with pytest.raises(ModelRejectedError) as excinfo:
            solver.solve(model)
        assert excinfo.value.exit_code == 65

    def test_linear_schedule_is_advertised_and_honoured(self, target: Target) -> None:
        """QUI-1627 and QUI-1628: both GPU binaries take the linear schedule."""
        solver = target.binary(beta_schedule_type="linear")
        assert "linear-schedule" in solver._impl.capabilities["features"]
        assert solver.solve(ferro_chain(16)[0]).metadata["params"]["beta_schedule_type"] == "linear"


class TestSampleQuality:
    """Matched reads and sweeps on the medium models from test_gpu_validation."""

    @pytest.mark.parametrize("build", [ferro_chain, frustrated_triangles, bipartite_maxcut], ids=lambda f: f.__name__)
    def test_known_ground_state(self, target: Target, solver_kind: str, build: Any) -> None:
        model, ground = build()
        assert _solver(target, solver_kind).solve(model).energy == ground

    def test_spin_glass_within_tolerance_of_the_kernel(self, target: Target) -> None:
        model, _ = random_spin_glass()
        kernel = target.inprocess().solve(model).energy
        binary = target.binary().solve(model).energy
        assert binary <= kernel + GLASS_TOLERANCE * abs(kernel)


class TestDefaults:
    """The binary path keeps SolverGPU's defaults: schedule, seeding and metadata."""

    def test_default_schedule_matches_the_kernel(self, target: Target) -> None:
        model = ferro_chain(16)[0]
        kernel = target.inprocess().solve(model).metadata["params"]["beta_schedule_type"]
        assert target.binary().solve(model).metadata["params"]["beta_schedule_type"] == kernel

    def test_same_seed_same_sample(self, target: Target) -> None:
        solver = target.binary(num_reads=8, num_sweeps=100)
        model = _spin_glass()
        assert solver.solve(model, seed=99).sample.values == solver.solve(model, seed=99).sample.values

    def test_drawn_seed_is_reported_and_reproduces(self, target: Target) -> None:
        """Intended difference: an unseeded kernel reports seed None, a binary the seed it drew (INTERFACE.md)."""
        solver = target.binary(num_reads=8, num_sweeps=100, seed=None)
        model = _spin_glass()
        drawn = solver.solve(model)
        assert isinstance(drawn.metadata["seed"], int)
        assert solver.solve(model, seed=drawn.metadata["seed"]).sample.values == drawn.sample.values

    def test_auto_beta_range_is_reported_as_none(self, target: Target) -> None:
        """Intended difference: a kernel reports the range it computed, a binary None when it chose (INTERFACE.md)."""
        result = target.binary(beta_range=None).solve(ferro_chain(16)[0])
        assert result.metadata["params"]["beta_range"] is None

    @pytest.mark.xfail(
        strict=True,
        reason="QUI-1648 settles SolverGPU's params: the binaries add timing and report no num_betas or raw_energy",
    )
    def test_params_keys_match_the_kernel(self, target: Target) -> None:
        model = ferro_chain(16)[0]
        kernel = target.inprocess().solve(model).metadata["params"].keys()
        assert target.binary().solve(model).metadata["params"].keys() == kernel
