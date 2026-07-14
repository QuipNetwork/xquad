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
Medium-model (20-100 var) real-GPU validation for the GPU SA solvers (QUI-687).

Models have analytically known ground states (or a seeded CPU reference for
the spin glass); the GPU best-energy is asserted against them, cross-checked
with SolverDWaveCPU on the same model object. Hardware classes follow
test_xqsa.py's guard idiom: skip locally without the device, run and
hard-fail in CI.
"""

import os
import random

import pytest

pytest.importorskip("dimod", reason="dwave-samplers / dimod not installed")

from xqsa import SolverDWaveCPU
from xqvm_py.xqmx import XQMX

# True in CI (GitLab/GitHub set CI). Hardware tests must run and hard-fail on
# a misconfigured CI environment rather than skip silently; locally they skip
# when the device/dependency is absent (no developer has every backend).
_IN_CI = os.environ.get("CI") is not None

_has_cupy = True
try:
    import cupy  # noqa: F401
except ImportError:
    _has_cupy = False

_has_metal = True
try:
    import Metal as _real_metal  # noqa: F401

    _has_metal = _real_metal.MTLCreateSystemDefaultDevice() is not None
except ImportError:
    _has_metal = False

if _has_cupy or _IN_CI:
    from xqsa.cuda_gpu import SolverCudaGPU

if _has_metal or _IN_CI:
    from xqsa.metal_gpu import SolverMetalGPU

# Explicit GPU budgets: QUI-167's sweep showed fixed-depth defaults degrade
# with model size, so these tests never rely on solver defaults.
GPU_PARAMS = {"num_reads": 64, "num_sweeps": 2000, "seed": 42}

# Spin-glass tolerance: GPU best-energy within 2% of the CPU reference.
GLASS_TOLERANCE = 0.02


def ferro_chain(n: int = 64) -> tuple[XQMX, int]:
    """Ferromagnetic Ising chain: J=-1 on consecutive pairs.

    Ground state is all spins aligned; every coupling contributes -1, so
    the exact ground energy is -(n-1).
    """
    model = XQMX.spin_model(n)
    for i in range(n - 1):
        model.set_quadratic(i, i + 1, -1.0)
    return model, -(n - 1)


def frustrated_triangles(n: int = 51) -> tuple[XQMX, int]:
    """Disjoint antiferromagnetic triangles: n//3 independent J=+1 3-cliques.

    Each triangle is frustrated — at best two of its three edges are
    satisfied (energy -1 per triangle) — so the exact ground energy is
    -(n // 3). Triangles force the Gibbs graph-colouring path to use
    >= 3 colours (QUI-687's target), and the independent components keep
    the landscape easy for single-flip SA. (An odd AF *ring* was tried
    first: analytically -(n-2), but SolverDWaveCPU cannot reach it for
    n >= 25 — domain-wall critical slowing — so it cannot serve as a
    reference model.)
    """
    model = XQMX.spin_model(n)
    for t in range(n // 3):
        a, b, c = 3 * t, 3 * t + 1, 3 * t + 2
        model.set_quadratic(a, b, 1.0)
        model.set_quadratic(a, c, 1.0)
        model.set_quadratic(b, c, 1.0)
    return model, -(n // 3)


def bipartite_maxcut(n: int = 20) -> tuple[XQMX, int]:
    """MaxCut QUBO on the complete bipartite graph K(n/2, n/2), unit weights.

    Cut edges contribute -1 (-x_i - x_j + 2 x_i x_j with x_i != x_j),
    uncut edges 0. The bipartition cuts all (n/2)^2 edges, so the exact
    ground energy is -(n/2)^2 = -cut size.
    """
    half = n // 2
    model = XQMX.binary_model(n)
    for i in range(half):
        for j in range(half, n):
            model.add_linear(i, -1.0)
            model.add_linear(j, -1.0)
            model.add_quadratic(i, j, 2.0)
    return model, -(half * half)


def random_spin_glass(n: int = 100, seed: int = 7) -> tuple[XQMX, None]:
    """Seeded random +/-1 spin glass on ~6%-density pairs; no known optimum.

    Ground energy is unknown (returned as None): tests compare the GPU
    best-energy against the SolverDWaveCPU reference on the same model.
    """
    rng = random.Random(seed)
    model = XQMX.spin_model(n)
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.06:
                model.set_quadratic(i, j, rng.choice((-1.0, 1.0)))
    return model, None


def cpu_reference(model: XQMX) -> int:
    """Best energy SolverDWaveCPU finds on ``model`` with the GPU budget.

    Uses GPU_PARAMS so the glass cross-check compares equally-budgeted
    samplers rather than a differently-tuned CPU baseline.
    """
    return SolverDWaveCPU(**GPU_PARAMS).solve(model).energy


class TestMediumModelReferences:
    """Ungated: builders and reference energies hold on every CI run."""

    def test_ferro_chain_cpu_hits_ground_state(self) -> None:
        model, ground = ferro_chain()
        assert ground == -63
        assert cpu_reference(model) == ground

    def test_frustrated_triangles_cpu_hits_ground_state(self) -> None:
        model, ground = frustrated_triangles()
        assert ground == -17
        assert cpu_reference(model) == ground

    def test_bipartite_maxcut_cpu_hits_ground_state(self) -> None:
        model, ground = bipartite_maxcut()
        assert ground == -100
        assert cpu_reference(model) == ground

    def test_random_spin_glass_is_deterministic(self) -> None:
        model_a, ground_a = random_spin_glass()
        model_b, _ = random_spin_glass()
        assert ground_a is None
        assert model_a.size == model_b.size == 100
        assert cpu_reference(model_a) == cpu_reference(model_b)


@pytest.mark.cuda
@pytest.mark.skipif(
    not _IN_CI and not _has_cupy,
    reason="cupy not installed (skipped locally; runs and hard-fails in CI)",
)
class TestCudaGPUMediumModels:
    """SolverCudaGPU on 20-100 var models with known/CPU-referenced optima."""

    def test_ferro_chain_exact(self) -> None:
        model, ground = ferro_chain()
        result = SolverCudaGPU(**GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_frustrated_triangles_exact(self) -> None:
        model, ground = frustrated_triangles()
        result = SolverCudaGPU(**GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_bipartite_maxcut_exact(self) -> None:
        model, ground = bipartite_maxcut()
        result = SolverCudaGPU(**GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_random_spin_glass_within_tolerance_of_cpu(self) -> None:
        model, _ = random_spin_glass()
        cpu = cpu_reference(model)
        gpu = SolverCudaGPU(**GPU_PARAMS).solve(model).energy
        assert gpu <= cpu + GLASS_TOLERANCE * abs(cpu), (
            f"GPU best energy {gpu} more than {GLASS_TOLERANCE:.0%} above CPU reference {cpu}"
        )


@pytest.mark.metal
@pytest.mark.skipif(
    not _IN_CI and not _has_metal,
    reason="Metal GPU not available (skipped locally; runs and hard-fails in CI)",
)
class TestMetalGPUMediumModels:
    """SolverMetalGPU (sa + gibbs) on the same medium models."""

    def test_ferro_chain_exact_sa(self) -> None:
        model, ground = ferro_chain()
        result = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_frustrated_triangles_exact_sa(self) -> None:
        model, ground = frustrated_triangles()
        result = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_bipartite_maxcut_exact_sa(self) -> None:
        model, ground = bipartite_maxcut()
        result = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_frustrated_triangles_exact_gibbs(self) -> None:
        """Gibbs on a >= 3-colour interaction graph (the colouring path's first real test)."""
        model, ground = frustrated_triangles()
        result = SolverMetalGPU(strategy="gibbs", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_bipartite_maxcut_exact_gibbs(self) -> None:
        model, ground = bipartite_maxcut()
        result = SolverMetalGPU(strategy="gibbs", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_random_spin_glass_within_tolerance_of_cpu(self) -> None:
        model, _ = random_spin_glass()
        cpu = cpu_reference(model)
        gpu = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model).energy
        assert gpu <= cpu + GLASS_TOLERANCE * abs(cpu), (
            f"GPU best energy {gpu} more than {GLASS_TOLERANCE:.0%} above CPU reference {cpu}"
        )
