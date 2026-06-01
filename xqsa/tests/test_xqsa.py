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
Tests for the XQSA solver package.
"""

import sys

import numpy as np
import pytest

dimod = pytest.importorskip("dimod", reason="dwave-samplers / dimod not installed")

from xqsa import Solver, SolverDWaveCPU, SolverResult
from xqvm_py.xqmx import XQMX, XQMXMode, compute_energy

# ---------------------------------------------------------------------------
# SolverResult
# ---------------------------------------------------------------------------


class TestSolverResult:
    """Tests for the SolverResult dataclass."""

    def test_construction(self) -> None:
        """SolverResult stores sample, energy, timing, metadata."""
        sample = XQMX.binary_sample(2)
        result = SolverResult(sample=sample, energy=-5, timing=0.1, metadata={"k": "v"})
        assert result.sample is sample
        assert result.energy == -5
        assert result.timing == 0.1
        assert result.metadata == {"k": "v"}

    def test_frozen(self) -> None:
        """SolverResult is immutable."""
        sample = XQMX.binary_sample(2)
        result = SolverResult(sample=sample, energy=0, timing=0.0)
        with pytest.raises(AttributeError):
            result.energy = 1

    def test_default_metadata(self) -> None:
        """Metadata defaults to empty dict."""
        sample = XQMX.binary_sample(2)
        result = SolverResult(sample=sample, energy=0, timing=0.0)
        assert result.metadata == {}


# ---------------------------------------------------------------------------
# Solver ABC
# ---------------------------------------------------------------------------


class TestSolver:
    """Tests for the abstract Solver base class."""

    def test_cannot_instantiate(self) -> None:
        """Solver is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Solver()

    def test_validate_rejects_sample_mode(self) -> None:
        """_validate_model rejects SAMPLE mode."""

        class DummySolver(Solver):
            def solve(self, model, **kwargs):
                pass

        solver = DummySolver()
        sample = XQMX.binary_sample(2)
        with pytest.raises(ValueError, match="MODEL"):
            solver._validate_model(sample)

    def test_validate_rejects_discrete_domain(self) -> None:
        """_validate_model rejects DISCRETE domain."""

        class DummySolver(Solver):
            def solve(self, model, **kwargs):
                pass

        solver = DummySolver()
        model = XQMX.discrete_model(2, k=3)
        with pytest.raises(ValueError, match="DISCRETE"):
            solver._validate_model(model)

    def test_validate_accepts_binary_model(self) -> None:
        """_validate_model accepts BINARY MODEL."""

        class DummySolver(Solver):
            def solve(self, model, **kwargs):
                pass

        solver = DummySolver()
        model = XQMX.binary_model(2)
        solver._validate_model(model)  # should not raise

    def test_validate_accepts_spin_model(self) -> None:
        """_validate_model accepts SPIN MODEL."""

        class DummySolver(Solver):
            def solve(self, model, **kwargs):
                pass

        solver = DummySolver()
        model = XQMX.spin_model(2)
        solver._validate_model(model)  # should not raise


# ---------------------------------------------------------------------------
# SolverDWaveCPU
# ---------------------------------------------------------------------------


class TestSolverDWaveCPU:
    """Tests for the DWave CPU simulated annealing solver."""

    def test_default_params(self) -> None:
        """SolverDWaveCPU stores default parameters."""
        solver = SolverDWaveCPU()
        assert solver.num_reads == 100
        assert solver.num_sweeps == 1000
        assert solver.beta_range is None
        assert solver.seed is None

    def test_custom_params(self) -> None:
        """SolverDWaveCPU accepts custom parameters."""
        solver = SolverDWaveCPU(num_reads=50, num_sweeps=500, seed=42)
        assert solver.num_reads == 50
        assert solver.num_sweeps == 500
        assert solver.seed == 42

    def test_solve_trivial_binary(self) -> None:
        """Solve a trivial 2-variable QUBO: minimize x0 + x1."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverDWaveCPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert isinstance(result.sample, XQMX)
        assert result.sample.mode == XQMXMode.SAMPLE
        assert result.sample.size == 2
        assert result.energy == 0
        assert isinstance(result.energy, int)
        assert result.timing > 0.0
        assert result.metadata["reads"] == 10
        assert result.metadata["seed"] == 42
        assert "num_sweeps" in result.metadata["params"]

    def test_solve_antiferromagnetic(self) -> None:
        """Solve x0*x1 with positive coupling: optimal is x0 != x1."""
        model = XQMX.binary_model(2)
        model.set_quadratic(0, 1, 1.0)

        solver = SolverDWaveCPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        x0 = result.sample.get_linear(0)
        x1 = result.sample.get_linear(1)
        assert x0 * x1 == 0  # at least one must be 0

    def test_solve_preserves_grid(self) -> None:
        """Solver preserves rows/cols from the model."""
        model = XQMX.binary_model(4, rows=2, cols=2)
        model.set_linear(0, 1.0)

        solver = SolverDWaveCPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert result.sample.rows == 2
        assert result.sample.cols == 2

    def test_solve_rejects_sample_mode(self) -> None:
        """Solve rejects SAMPLE mode input."""
        sample = XQMX.binary_sample(2)
        solver = SolverDWaveCPU()
        with pytest.raises(ValueError, match="MODEL"):
            solver.solve(sample)

    def test_kwargs_override(self) -> None:
        """Per-call kwargs override constructor defaults."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = SolverDWaveCPU(num_reads=100, seed=1)
        result = solver.solve(model, num_reads=5, seed=99)

        assert result.metadata["reads"] == 5
        assert result.metadata["seed"] == 99
        assert result.metadata["params"]["num_sweeps"] == 1000

    def test_energy_matches_compute_energy(self) -> None:
        """Solver-reported energy matches compute_energy."""
        model = XQMX.binary_model(3)
        model.set_linear(0, -2.0)
        model.set_linear(1, -3.0)
        model.set_quadratic(0, 1, 5.0)

        solver = SolverDWaveCPU(num_reads=50, num_sweeps=500, seed=42)
        result = solver.solve(model)

        expected_energy = compute_energy(model, result.sample)
        assert result.energy == expected_energy
        assert isinstance(result.energy, int)

    def test_solve_ising_basic(self) -> None:
        """Solve a trivial Ising model: minimize -s0 - s1 (ground state: +1, +1)."""
        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = SolverDWaveCPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert isinstance(result.energy, int)
        assert result.sample.size == 2
        for i in range(2):
            assert result.sample.get_linear(i) in (-1, 1)

    def test_beta_range_passthrough(self) -> None:
        """beta_range is recorded in result metadata."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = SolverDWaveCPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model, beta_range=(0.1, 5.0))

        assert result.metadata["params"]["beta_range"] == (0.1, 5.0)

    def test_num_reads_validation(self) -> None:
        """num_reads < 1 raises ValueError."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverDWaveCPU()
        with pytest.raises(ValueError, match="num_reads"):
            solver.solve(model, num_reads=0)

    def test_num_sweeps_validation(self) -> None:
        """num_sweeps < 1 raises ValueError."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverDWaveCPU()
        with pytest.raises(ValueError, match="num_sweeps"):
            solver.solve(model, num_sweeps=0)

    def test_model_to_bqm_qubo(self) -> None:
        """_model_to_bqm produces correct BQM for a QUBO model."""
        model = XQMX.binary_model(3)
        model.set_linear(0, -2.0)
        model.set_linear(1, 3.0)
        model.set_quadratic(0, 2, 1.5)

        solver = SolverDWaveCPU()
        bqm = solver._model_to_bqm(model)

        assert bqm.vartype is dimod.BINARY
        assert len(bqm.variables) == 3
        assert bqm.get_linear(0) == pytest.approx(-2.0)
        assert bqm.get_linear(1) == pytest.approx(3.0)
        assert bqm.get_quadratic(0, 2) == pytest.approx(1.5)

    def test_model_to_bqm_ising(self) -> None:
        """_model_to_bqm produces correct BQM for an Ising model."""
        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_quadratic(0, 1, 2.0)

        solver = SolverDWaveCPU()
        bqm = solver._model_to_bqm(model)

        assert bqm.vartype is dimod.SPIN
        assert bqm.get_linear(0) == pytest.approx(-1.0)
        assert bqm.get_quadratic(0, 1) == pytest.approx(2.0)

    def test_sample_to_xqmx(self) -> None:
        """_sample_to_xqmx converts a raw sample dict to an XQMX sample."""
        model = XQMX.binary_model(3, rows=1, cols=3)

        solver = SolverDWaveCPU()
        sample = solver._sample_to_xqmx(model, {0: 1, 1: 0, 2: 1})

        assert sample.mode == XQMXMode.SAMPLE
        assert sample.size == 3
        assert sample.rows == 1
        assert sample.cols == 3
        assert sample.get_linear(0) == 1
        assert sample.get_linear(1) == 0
        assert sample.get_linear(2) == 1


# ---------------------------------------------------------------------------
# SolverCudaGPU -- mocked tests (no GPU required, runs everywhere)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cupy_env(monkeypatch):
    """Inject a fake cupy module backed by numpy for CI without GPU."""
    import types as _types

    fake_cupy = _types.ModuleType("cupy")
    fake_cupy.float64 = np.float64
    fake_cupy.int32 = np.int32
    fake_cupy.asarray = np.asarray
    fake_cupy.asnumpy = np.asarray
    fake_cupy.zeros = np.zeros
    fake_cupy.abs = np.abs
    fake_cupy.max = np.max

    fake_runtime = _types.ModuleType("cupy.cuda.runtime")
    fake_runtime.getDeviceCount = lambda: 1

    class _FakeDevice:
        def synchronize(self):
            pass

    fake_cuda = _types.ModuleType("cupy.cuda")
    fake_cuda.runtime = fake_runtime
    fake_cuda.Device = _FakeDevice
    fake_cupy.cuda = fake_cuda

    fake_random = _types.ModuleType("cupy.random")
    fake_random.default_rng = np.random.default_rng
    fake_cupy.random = fake_random

    def _make_sa_kernel(name):
        """Return a callable that simulates SA on CPU via numpy."""

        def _kernel(grid, block, args):
            h, J, samples, energies, randoms, n_val, ns_val, bs, be = args
            n = int(n_val)
            num_sweeps = int(ns_val)
            beta_start = float(bs)
            beta_end = float(be)
            num_reads = samples.shape[0]
            is_binary = name == "sa_binary"

            for rid in range(num_reads):
                x = samples[rid]
                energy = 0.0
                if is_binary:
                    for i in range(n):
                        if x[i] == 0:
                            continue
                        energy += h[i]
                        for j in range(i + 1, n):
                            if x[j] == 0:
                                continue
                            energy += J[i, j]
                else:
                    for i in range(n):
                        energy += h[i] * float(x[i])
                        for j in range(i + 1, n):
                            energy += J[i, j] * float(x[i]) * float(x[j])

                for sweep in range(num_sweeps):
                    if num_sweeps <= 1:
                        beta = beta_start
                    else:
                        beta = beta_start + (beta_end - beta_start) * (sweep / (num_sweeps - 1))

                    for i in range(n):
                        if is_binary:
                            local = h[i]
                            for j in range(n):
                                if j == i:
                                    continue
                                local += J[i, j] * float(x[j])
                            delta_e = local * float(1 - 2 * x[i])
                        else:
                            local = h[i]
                            for j in range(n):
                                if j == i:
                                    continue
                                local += J[i, j] * float(x[j])
                            delta_e = -2.0 * float(x[i]) * local

                        r = randoms[rid, sweep, i]
                        if delta_e <= 0.0 or r < np.exp(-delta_e * beta):
                            if is_binary:
                                x[i] = 1 - x[i]
                            else:
                                x[i] = -x[i]
                            energy += delta_e

                energies[rid] = energy

        return _kernel

    fake_cupy.RawKernel = lambda code, name: _make_sa_kernel(name)

    monkeypatch.setitem(sys.modules, "cupy", fake_cupy)
    monkeypatch.setitem(sys.modules, "cupy.cuda", fake_cuda)
    monkeypatch.setitem(sys.modules, "cupy.cuda.runtime", fake_runtime)
    monkeypatch.setitem(sys.modules, "cupy.random", fake_random)

    return fake_cupy


class TestSolverCudaGPUMocked:
    """Tests for SolverCudaGPU with mocked CuPy (no GPU required)."""

    def test_solve_trivial_binary_mocked(self, mock_cupy_env) -> None:
        """Solve trivial QUBO through mocked pipeline."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert result.energy == 0
        assert result.energy == compute_energy(model, result.sample)

    def test_solve_trivial_spin_mocked(self, mock_cupy_env) -> None:
        """Solve trivial Ising through mocked pipeline."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert result.energy == -2
        assert result.energy == compute_energy(model, result.sample)

    def test_energy_matches_compute_energy_mocked(self, mock_cupy_env) -> None:
        """Solver-reported energy matches compute_energy (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(3)
        model.set_linear(0, -2.0)
        model.set_linear(1, -3.0)
        model.set_quadratic(0, 1, 5.0)

        solver = _Solver(num_reads=50, num_sweeps=500, seed=42)
        result = solver.solve(model)

        assert result.energy == compute_energy(model, result.sample)
        assert isinstance(result.energy, int)

    def test_kwargs_override_mocked(self, mock_cupy_env) -> None:
        """Per-call kwargs override constructor defaults (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=100, seed=1)
        result = solver.solve(model, num_reads=5, seed=99)

        assert result.metadata["reads"] == 5
        assert result.metadata["seed"] == 99

    def test_metadata_schema_mocked(self, mock_cupy_env) -> None:
        """Metadata has exactly {seed, reads, params} keys (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert set(result.metadata.keys()) == {"seed", "reads", "params"}

    def test_missing_cupy_raises(self, monkeypatch) -> None:
        """ImportError with install hint when cupy is not installed."""
        monkeypatch.setitem(sys.modules, "cupy", None)
        with pytest.raises(ImportError, match="pip install xqsa"):
            from xqsa.cuda_gpu import SolverCudaGPU as _Solver

            _Solver()

    def test_gpu_not_found(self, mock_cupy_env, monkeypatch) -> None:
        """RuntimeError when no GPU is detected."""
        mock_cupy_env.cuda.runtime.getDeviceCount = lambda: 0
        with pytest.raises(RuntimeError, match="No NVIDIA CUDA GPU"):
            from xqsa.cuda_gpu import SolverCudaGPU as _Solver

            _Solver()


# ---------------------------------------------------------------------------
# SolverCudaGPU -- real GPU tests (requires cupy + NVIDIA GPU)
# ---------------------------------------------------------------------------


_has_cupy = True
try:
    import cupy  # noqa: F401
except ImportError:
    _has_cupy = False

if _has_cupy:
    from xqsa.cuda_gpu import SolverCudaGPU


@pytest.mark.skipif(not _has_cupy, reason="cupy not installed")
class TestSolverCudaGPU:
    """Tests for the CUDA GPU simulated annealing solver (real hardware)."""

    def test_default_params(self) -> None:
        """SolverCudaGPU stores default parameters."""
        solver = SolverCudaGPU()
        assert solver.strategy == "sa"
        assert solver.num_reads == 100
        assert solver.num_sweeps == 1000
        assert solver.beta_range is None
        assert solver.seed is None

    def test_custom_params(self) -> None:
        """SolverCudaGPU accepts custom parameters."""
        solver = SolverCudaGPU(strategy="sa", num_reads=50, num_sweeps=500, seed=42)
        assert solver.num_reads == 50
        assert solver.num_sweeps == 500
        assert solver.seed == 42

    def test_solve_trivial_binary(self) -> None:
        """Solve a trivial 2-variable QUBO: minimize x0 + x1."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverCudaGPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert isinstance(result.sample, XQMX)
        assert result.sample.mode == XQMXMode.SAMPLE
        assert result.sample.size == 2
        assert result.energy == 0
        assert isinstance(result.energy, int)
        assert result.timing > 0.0
        assert result.metadata["reads"] == 10
        assert result.metadata["seed"] == 42
        assert "strategy" in result.metadata["params"]

    def test_solve_trivial_spin(self) -> None:
        """Solve a trivial Ising model: minimize -s0 - s1."""
        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = SolverCudaGPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert isinstance(result.energy, int)
        assert result.energy == -2
        assert result.sample.size == 2
        for i in range(2):
            assert result.sample.get_linear(i) in (-1, 1)

    def test_solve_antiferromagnetic(self) -> None:
        """Solve x0*x1 with positive coupling: optimal is x0 != x1."""
        model = XQMX.binary_model(2)
        model.set_quadratic(0, 1, 1.0)

        solver = SolverCudaGPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        x0 = result.sample.get_linear(0)
        x1 = result.sample.get_linear(1)
        assert x0 * x1 == 0

    def test_solve_preserves_grid(self) -> None:
        """Solver preserves rows/cols from the model."""
        model = XQMX.binary_model(4, rows=2, cols=2)
        model.set_linear(0, 1.0)

        solver = SolverCudaGPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert result.sample.rows == 2
        assert result.sample.cols == 2

    def test_solve_rejects_sample_mode(self) -> None:
        """Solve rejects SAMPLE mode input."""
        sample = XQMX.binary_sample(2)
        solver = SolverCudaGPU()
        with pytest.raises(ValueError, match="MODEL"):
            solver.solve(sample)

    def test_kwargs_override(self) -> None:
        """Per-call kwargs override constructor defaults."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = SolverCudaGPU(num_reads=100, seed=1)
        result = solver.solve(model, num_reads=5, seed=99)

        assert result.metadata["reads"] == 5
        assert result.metadata["seed"] == 99
        assert result.metadata["params"]["num_sweeps"] == 1000

    def test_energy_matches_compute_energy(self) -> None:
        """Solver-reported energy matches compute_energy."""
        model = XQMX.binary_model(3)
        model.set_linear(0, -2.0)
        model.set_linear(1, -3.0)
        model.set_quadratic(0, 1, 5.0)

        solver = SolverCudaGPU(num_reads=50, num_sweeps=500, seed=42)
        result = solver.solve(model)

        expected_energy = compute_energy(model, result.sample)
        assert result.energy == expected_energy
        assert isinstance(result.energy, int)

    def test_num_reads_validation(self) -> None:
        """num_reads < 1 raises ValueError."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverCudaGPU()
        with pytest.raises(ValueError, match="num_reads"):
            solver.solve(model, num_reads=0)

    def test_num_sweeps_validation(self) -> None:
        """num_sweeps < 1 raises ValueError."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverCudaGPU()
        with pytest.raises(ValueError, match="num_sweeps"):
            solver.solve(model, num_sweeps=0)

    def test_strategy_validation_init(self) -> None:
        """Unsupported strategy in __init__ raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported strategy"):
            SolverCudaGPU(strategy="gibbs")

    def test_strategy_validation_kwargs(self) -> None:
        """Unsupported strategy in solve() kwargs raises ValueError."""
        model = XQMX.binary_model(2)
        solver = SolverCudaGPU()
        with pytest.raises(ValueError, match="Unsupported strategy"):
            solver.solve(model, strategy="gibbs")

    def test_beta_range_passthrough(self) -> None:
        """beta_range is recorded in result metadata."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = SolverCudaGPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model, beta_range=(0.1, 5.0))

        assert result.metadata["params"]["beta_range"] == (0.1, 5.0)

    def test_metadata_schema(self) -> None:
        """Metadata has exactly {seed, reads, params} top-level keys."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = SolverCudaGPU(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert set(result.metadata.keys()) == {"seed", "reads", "params"}
        assert set(result.metadata["params"].keys()) == {
            "strategy",
            "num_sweeps",
            "beta_range",
            "raw_energy",
        }
