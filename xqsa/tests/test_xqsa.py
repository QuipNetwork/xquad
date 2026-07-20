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

import os
import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

# True in CI (GitLab/GitHub set CI). Hardware tests must run and hard-fail on a
# misconfigured CI environment rather than skip silently; locally they skip when
# the device/dependency is absent (no developer has every backend).
_IN_CI = os.environ.get("CI") is not None

dimod = pytest.importorskip("dimod", reason="dwave-samplers / dimod not installed")

from xqsa import Solver, SolverDWaveCPU, SolverDWaveQPU, SolverResult
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
        assert solver.num_sweeps_per_beta == 1
        assert solver.beta_range is None
        assert solver.seed is None

    def test_custom_params(self) -> None:
        """SolverDWaveCPU accepts custom parameters."""
        solver = SolverDWaveCPU(num_reads=50, num_sweeps=500, num_sweeps_per_beta=5, seed=42)
        assert solver.num_reads == 50
        assert solver.num_sweeps == 500
        assert solver.num_sweeps_per_beta == 5
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

    def test_num_sweeps_per_beta_validation(self) -> None:
        """num_sweeps_per_beta < 1 raises, and indivisible counts raise."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverDWaveCPU()
        with pytest.raises(ValueError, match="num_sweeps_per_beta"):
            solver.solve(model, num_sweeps_per_beta=0)
        # Local validation keeps this message independent of upstream wording.
        with pytest.raises(ValueError, match="divisible"):
            solver.solve(model, num_sweeps=100, num_sweeps_per_beta=7)
        with pytest.raises(ValueError, match="must be an int, got float"):
            solver.solve(model, num_sweeps_per_beta=2.5)

    def test_num_sweeps_per_beta_run(self) -> None:
        """num_sweeps_per_beta > 1 solves and is recorded in metadata."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverDWaveCPU(num_reads=10, num_sweeps=100, num_sweeps_per_beta=10, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["num_sweeps_per_beta"] == 10
        assert result.metadata["params"]["num_betas"] == 10

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
# SolverDWaveQPU
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_dwave_system(monkeypatch):
    """Inject a fake dwave.system module to avoid hardware dependency in tests."""
    mock_first = MagicMock()
    mock_first.sample = {0: 0, 1: 0}
    mock_first.energy = 0.0
    mock_first.num_occurrences = 1

    mock_sampleset = MagicMock()
    mock_sampleset.first = mock_first
    mock_sampleset.info = {"timing": {"qpu_sampling_time": 1000}}

    mock_composite = MagicMock()
    mock_composite.sample.return_value = mock_sampleset

    mock_raw = MagicMock()
    mock_raw.solver.id = "Advantage_system5.4"

    fake_dwave_system = types.ModuleType("dwave.system")
    fake_dwave_system.DWaveSampler = MagicMock(return_value=mock_raw)
    fake_dwave_system.EmbeddingComposite = MagicMock(return_value=mock_composite)

    fake_dwave = types.ModuleType("dwave")
    fake_dwave.system = fake_dwave_system

    monkeypatch.setitem(sys.modules, "dwave", fake_dwave)
    monkeypatch.setitem(sys.modules, "dwave.system", fake_dwave_system)

    return {
        "dwave_system": fake_dwave_system,
        "composite": mock_composite,
        "raw": mock_raw,
        "sampleset": mock_sampleset,
        "first": mock_first,
    }


class TestSolverDWaveQPU:
    """Tests for the D-Wave QPU solver (hardware mocked)."""

    def test_default_params(self, mock_dwave_system, monkeypatch) -> None:
        """SolverDWaveQPU stores default parameters."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "test-token")
        solver = SolverDWaveQPU()
        assert solver.num_reads == 100
        assert solver.annealing_time == 20

    def test_custom_params(self, mock_dwave_system, monkeypatch) -> None:
        """SolverDWaveQPU accepts custom parameters."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "test-token")
        solver = SolverDWaveQPU(num_reads=50, annealing_time=100)
        assert solver.num_reads == 50
        assert solver.annealing_time == 100

    def test_solve_binary(self, mock_dwave_system, monkeypatch) -> None:
        """Solve a trivial 2-variable QUBO with mocked sampler."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "test-token")
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverDWaveQPU()
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert isinstance(result.sample, XQMX)
        assert result.sample.mode == XQMXMode.SAMPLE
        assert result.energy == 0  # x0=0, x1=0 -> energy 0
        assert isinstance(result.energy, int)
        assert result.timing >= 0.0
        assert result.metadata["solver"] == "Advantage_system5.4"
        assert result.metadata["qpu_timing"] == {"qpu_sampling_time": 1000}

    def test_solve_spin(self, mock_dwave_system, monkeypatch) -> None:
        """Solve a 2-variable Ising model with mocked sampler."""
        mock_dwave_system["first"].sample = {0: -1, 1: -1}
        mock_dwave_system["first"].energy = -2.0
        monkeypatch.setenv("DWAVE_API_TOKEN", "test-token")
        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = SolverDWaveQPU()
        result = solver.solve(model)

        assert result.sample.mode == XQMXMode.SAMPLE
        # _recompute_energy: -1*(-1) + -1*(-1) = -2
        expected = compute_energy(model, result.sample)
        assert result.energy == expected
        assert isinstance(result.energy, int)

    def test_solve_kwargs_override(self, mock_dwave_system, monkeypatch) -> None:
        """Per-call kwargs override constructor defaults."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "test-token")
        model = XQMX.binary_model(2)

        solver = SolverDWaveQPU(num_reads=100, annealing_time=20)
        result = solver.solve(model, num_reads=50, annealing_time=40)

        assert result.metadata["reads"] == 50
        assert result.metadata["params"]["annealing_time"] == 40

    def test_solve_rejects_sample_mode(self, mock_dwave_system, monkeypatch) -> None:
        """SolverDWaveQPU raises ValueError for SAMPLE mode input."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "test-token")
        sample = XQMX.binary_sample(2)
        solver = SolverDWaveQPU()
        with pytest.raises(ValueError, match="MODEL"):
            solver.solve(sample)

    def test_missing_token_raises(self, mock_dwave_system, monkeypatch) -> None:
        """ValueError raised when token absent from constructor and env."""
        monkeypatch.delenv("DWAVE_API_TOKEN", raising=False)
        with pytest.raises(ValueError, match="DWAVE_API_TOKEN"):
            SolverDWaveQPU()

    def test_token_from_env(self, mock_dwave_system, monkeypatch) -> None:
        """Token resolved from DWAVE_API_TOKEN env var."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "env-token")
        SolverDWaveQPU()
        call_kwargs = mock_dwave_system["dwave_system"].DWaveSampler.call_args.kwargs
        assert call_kwargs["token"] == "env-token"

    def test_missing_dwave_system_raises(self, monkeypatch) -> None:
        """ImportError with install hint when dwave-system is not installed."""
        monkeypatch.setenv("DWAVE_API_TOKEN", "test-token")
        monkeypatch.setitem(sys.modules, "dwave", None)
        monkeypatch.setitem(sys.modules, "dwave.system", None)
        with pytest.raises(ImportError, match="pip install xqsa"):
            SolverDWaveQPU()


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
        @property
        def mem_info(self):
            # (free, total) -- 8 GB fake GPU for test purposes.
            return (8 * 1024**3, 8 * 1024**3)

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
            h, J, samples, energies, randoms, betas, n_val, ns_val = args
            n = int(n_val)
            num_sweeps = int(ns_val)
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
                    beta = float(betas[sweep])

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
        assert set(result.metadata["params"].keys()) == {
            "strategy",
            "num_sweeps",
            "num_sweeps_per_beta",
            "num_betas",
            "beta_range",
            "beta_schedule_type",
            "raw_energy",
        }

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

    def test_oom_guard(self, mock_cupy_env) -> None:
        """ValueError when random buffer exceeds GPU memory."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        # Override mem_info to report only 1 KB free.
        class _TinyDevice:
            @property
            def mem_info(self):
                return (1024, 1024)

            def synchronize(self):
                pass

        mock_cupy_env.cuda.Device = _TinyDevice

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, seed=42)
        with pytest.raises(ValueError, match="GPU memory"):
            solver.solve(model)

    def test_compute_beta_schedule_mocked(self, mock_cupy_env) -> None:
        """_compute_beta_schedule returns num_betas float64 points for both types."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        solver = _Solver()
        for schedule_type in ("geometric", "linear"):
            schedule = solver._compute_beta_schedule(20, (0.05, 5.0), schedule_type)
            assert schedule.shape == (20,)
            assert schedule.dtype == np.float64

    def test_compute_beta_schedule_linear_bit_identical_mocked(self, mock_cupy_env) -> None:
        """The linear schedule reproduces the retired in-kernel ramp bit-for-bit."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        solver = _Solver()
        schedule = solver._compute_beta_schedule(200, (0.05, 5.0), "linear")
        expected = 0.05 + (5.0 - 0.05) * (np.arange(200, dtype=np.float64) / np.float64(199))
        np.testing.assert_array_equal(schedule, expected)

    def test_compute_beta_schedule_single_level_mocked(self, mock_cupy_env) -> None:
        """A single temperature level anneals at beta_end (coldest quench)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        solver = _Solver()
        for schedule_type in ("geometric", "linear"):
            schedule = solver._compute_beta_schedule(1, (0.05, 5.0), schedule_type)
            assert schedule.shape == (1,)
            assert schedule[0] == np.float64(5.0)

    def test_num_sweeps_per_beta_validation_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta < 1 raises, and indivisible counts raise (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match="num_sweeps_per_beta"):
            _Solver().solve(model, num_sweeps_per_beta=0)
        with pytest.raises(ValueError, match="divisible"):
            _Solver().solve(model, num_sweeps=200, num_sweeps_per_beta=7)
        with pytest.raises(ValueError, match="must be an int"):
            _Solver().solve(model, num_sweeps_per_beta=2.0)

    def test_range_errors_carry_offending_value_mocked(self, mock_cupy_env) -> None:
        """num_reads / num_sweeps range errors name the rejected value (QUI-685)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match=r"num_reads must be >= 1, got 0"):
            _Solver().solve(model, num_reads=0)
        with pytest.raises(ValueError, match=r"num_sweeps must be >= 1, got -3"):
            _Solver().solve(model, num_sweeps=-3)

    def test_beta_schedule_type_validation_mocked(self, mock_cupy_env) -> None:
        """Unsupported beta_schedule_type raises in constructor and solve (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match="beta_schedule_type"):
            _Solver(beta_schedule_type="exponential")
        with pytest.raises(ValueError, match="beta_schedule_type"):
            _Solver().solve(model, beta_schedule_type="exponential")

    def test_num_sweeps_per_beta_run_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta > 1 solves and records the schedule split (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=200, num_sweeps_per_beta=10, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["num_sweeps"] == 200
        assert result.metadata["params"]["num_sweeps_per_beta"] == 10
        assert result.metadata["params"]["num_betas"] == 20
        assert result.metadata["params"]["beta_schedule_type"] == "linear"

    @staticmethod
    def _capture_kernel_args(solver) -> dict:
        """Shadow the cached binary kernel with a wrapper that records call args."""
        captured: dict = {}
        real_kernel = solver._binary_kernel

        def wrapper(grid, block, args):
            captured["grid"] = grid
            captured["block"] = block
            captured["args"] = args
            return real_kernel(grid, block, args)

        solver.__dict__["_binary_kernel"] = wrapper
        return captured

    def test_launch_shape_parallel_reduction_mocked(self, mock_cupy_env) -> None:
        """Each replica's block launches _THREADS_PER_REPLICA threads (QUI-852)."""
        from xqsa.cuda_gpu import _THREADS_PER_REPLICA
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=7, num_sweeps=10, seed=1)
        captured = self._capture_kernel_args(solver)
        solver.solve(model)

        assert captured["grid"] == (7,)
        assert captured["block"] == (_THREADS_PER_REPLICA,)
        assert _THREADS_PER_REPLICA == 256

    def test_num_sweeps_per_beta_schedule_buffer_mocked(self, mock_cupy_env) -> None:
        """The kernel receives a per-sweep buffer holding each level for N sweeps.

        Verifies the divide semantics end-to-end: buffer length num_sweeps,
        constant within each num_sweeps_per_beta block, and the levels are
        20 distinct, strictly increasing betas (a collapsed-constant schedule
        would fail the uniqueness assertions).
        """
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=5, num_sweeps=200, num_sweeps_per_beta=10, beta_range=(0.05, 5.0), seed=7)
        captured = self._capture_kernel_args(solver)
        solver.solve(model)

        betas = np.asarray(captured["args"][5])
        assert betas.shape == (200,)
        levels = betas.reshape(20, 10)
        for level in levels:
            assert np.all(level == level[0])
        assert np.unique(levels[:, 0]).size == 20
        assert np.all(np.diff(levels[:, 0]) > 0)

    def test_default_schedule_bit_identical_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta=1 + linear reproduces the retired kernel ramp bit-for-bit."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=5, num_sweeps=200, beta_range=(0.05, 5.0), seed=7)
        captured = self._capture_kernel_args(solver)
        solver.solve(model)

        betas = np.asarray(captured["args"][5])
        expected = 0.05 + (5.0 - 0.05) * (np.arange(200, dtype=np.float64) / np.float64(199))
        np.testing.assert_array_equal(betas, expected)

    def test_single_beta_level_uses_beta_end_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta == num_sweeps runs a cold quench at beta_end.

        Matches SolverDWaveCPU and SolverMetalGPU (QUI-685); the retired
        in-kernel ramp used beta_start for this degenerate case.
        """
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=5, num_sweeps=50, num_sweeps_per_beta=50, beta_range=(0.05, 5.0), seed=7)
        captured = self._capture_kernel_args(solver)
        result = solver.solve(model)

        betas = np.asarray(captured["args"][5])
        assert betas.shape == (50,)
        assert np.all(betas == np.float64(5.0))
        assert result.metadata["params"]["num_betas"] == 1

    def test_geometric_schedule_mocked(self, mock_cupy_env) -> None:
        """The geometric schedule solves a trivial model and is recorded (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, beta_schedule_type="geometric", seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["beta_schedule_type"] == "geometric"

    def test_geometric_schedule_shape_mocked(self, mock_cupy_env) -> None:
        """The geometric schedule has a constant level ratio and spans beta_range."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(
            num_reads=5,
            num_sweeps=200,
            num_sweeps_per_beta=10,
            beta_range=(0.05, 5.0),
            beta_schedule_type="geometric",
            seed=7,
        )
        captured = self._capture_kernel_args(solver)
        solver.solve(model)

        betas = np.asarray(captured["args"][5])
        levels = betas.reshape(20, 10)[:, 0]
        ratios = np.diff(np.log(levels))
        assert np.allclose(ratios, ratios[0])
        assert levels[0] == pytest.approx(0.05)
        assert levels[-1] == pytest.approx(5.0)

    def test_num_sweeps_per_beta_spin_run_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta > 1 solves a spin model through the spin kernel (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, num_sweeps_per_beta=10, seed=42)
        result = solver.solve(model)

        assert result.energy == -2
        assert result.metadata["params"]["num_betas"] == 10

    def test_num_sweeps_per_beta_kwargs_override_mocked(self, mock_cupy_env) -> None:
        """Per-call kwargs override non-default constructor values (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, num_sweeps_per_beta=10, seed=1)
        result = solver.solve(model, num_sweeps_per_beta=5, beta_schedule_type="geometric")

        assert result.metadata["params"]["num_sweeps_per_beta"] == 5
        assert result.metadata["params"]["num_betas"] == 20
        assert result.metadata["params"]["beta_schedule_type"] == "geometric"


# ---------------------------------------------------------------------------
# SolverCudaGPU -- real GPU tests (requires cupy + NVIDIA GPU)
# ---------------------------------------------------------------------------


_has_cupy = True
try:
    import cupy  # noqa: F401
except ImportError:
    _has_cupy = False

if _has_cupy or _IN_CI:
    from xqsa.cuda_gpu import SolverCudaGPU


@pytest.mark.cuda
@pytest.mark.skipif(
    not _IN_CI and not _has_cupy,
    reason="cupy not installed (skipped locally; runs and hard-fails in CI)",
)
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
        assert solver.num_sweeps_per_beta == 1
        assert solver.beta_schedule_type == "linear"

    def test_custom_params(self) -> None:
        """SolverCudaGPU accepts custom parameters."""
        solver = SolverCudaGPU(strategy="sa", num_reads=50, num_sweeps=500, num_sweeps_per_beta=5, seed=42)
        assert solver.num_reads == 50
        assert solver.num_sweeps == 500
        assert solver.num_sweeps_per_beta == 5
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
            "num_sweeps_per_beta",
            "num_betas",
            "beta_range",
            "beta_schedule_type",
            "raw_energy",
        }

    def test_num_sweeps_per_beta_validation(self) -> None:
        """num_sweeps_per_beta < 1 raises, and indivisible counts raise."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverCudaGPU()
        with pytest.raises(ValueError, match="num_sweeps_per_beta"):
            solver.solve(model, num_sweeps_per_beta=0)
        with pytest.raises(ValueError, match="divisible"):
            solver.solve(model, num_sweeps=200, num_sweeps_per_beta=7)

    def test_num_sweeps_per_beta_run(self) -> None:
        """num_sweeps_per_beta > 1 solves and records the schedule split."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverCudaGPU(num_reads=20, num_sweeps=200, num_sweeps_per_beta=10, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["num_sweeps"] == 200
        assert result.metadata["params"]["num_sweeps_per_beta"] == 10
        assert result.metadata["params"]["num_betas"] == 20

    def test_geometric_schedule(self) -> None:
        """The geometric beta schedule solves a trivial model on real hardware."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverCudaGPU(num_reads=20, num_sweeps=200, beta_schedule_type="geometric", seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["beta_schedule_type"] == "geometric"

    def test_reduction_consistency_integer_n512(self) -> None:
        """Kernel-accumulated energy matches the exact host energy at n=512.

        n > _THREADS_PER_REPLICA exercises multi-element strides plus the full
        block-wide reduction on real hardware: the bit-identical trajectory
        gate ran at n in {64, 256}, where the strided loop gives each thread
        at most one term, so the intra-thread multi-term accumulation was
        never verified integer-exact. Integer coefficients keep every float64
        sum exact, so any reduction race, mis-partition, or missing barrier
        surfaces as a mismatch between the incrementally accumulated kernel
        energy and the host recompute.
        """
        rng = np.random.default_rng(0)
        n = 512
        model = XQMX.binary_model(n)
        for i in range(n):
            for j in range(i + 1, n):
                if rng.random() < 0.25:
                    model.set_quadratic(i, j, 1.0)

        solver = SolverCudaGPU(strategy="sa", num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert float(result.metadata["params"]["raw_energy"]) == float(result.energy)


# ---------------------------------------------------------------------------
# SolverMetalGPU -- mocked tests (no Apple GPU required, runs everywhere)
# ---------------------------------------------------------------------------


def _cpu_sa(h, j_matrix, beta, samples, energies, n, num_sweeps, is_spin, base_seed):
    """Reference CPU simulated annealing matching the Metal SA kernel ABI."""
    rng = np.random.default_rng(base_seed)
    for rid in range(samples.shape[0]):
        x = samples[rid]
        energy = 0.0
        for i in range(n):
            xi = float(x[i])
            energy += h[i] * xi
            for j in range(i + 1, n):
                energy += j_matrix[i, j] * xi * float(x[j])

        for sweep in range(num_sweeps):
            b = float(beta[sweep])
            for i in range(n):
                local = h[i]
                for j in range(n):
                    if j == i:
                        continue
                    local += j_matrix[i, j] * float(x[j])
                delta_e = (-2.0 * float(x[i]) * local) if is_spin else (local * float(1 - 2 * x[i]))
                r = rng.random()
                if delta_e <= 0.0 or r < np.exp(-delta_e * b):
                    x[i] = -x[i] if is_spin else 1 - x[i]
                    energy += delta_e
        energies[rid] = energy


def _cpu_gibbs(
    h, j_matrix, beta, samples, energies, starts, counts, nodes, n, num_sweeps, num_colors, is_spin, base_seed
):
    """Reference CPU block Gibbs sampler matching the Metal Gibbs kernel ABI."""
    rng = np.random.default_rng(base_seed)
    for rid in range(samples.shape[0]):
        x = samples[rid]
        for sweep in range(num_sweeps):
            b = float(beta[sweep])
            for c in range(num_colors):
                start = int(starts[c])
                count = int(counts[c])
                for k in range(count):
                    i = int(nodes[start + k])
                    h_eff = h[i]
                    for j in range(n):
                        if j == i:
                            continue
                        h_eff += j_matrix[i, j] * float(x[j])
                    r = rng.random()
                    if is_spin:
                        p_plus = 1.0 / (1.0 + np.exp(2.0 * b * h_eff))
                        x[i] = 1 if r < p_plus else -1
                    else:
                        p_one = 1.0 / (1.0 + np.exp(b * h_eff))
                        x[i] = 1 if r < p_one else 0

        energy = 0.0
        for i in range(n):
            xi = float(x[i])
            energy += h[i] * xi
            for j in range(i + 1, n):
                energy += j_matrix[i, j] * xi * float(x[j])
        energies[rid] = energy


@pytest.fixture
def mock_metal_env(monkeypatch):
    """Inject a fake ``Metal`` module backed by numpy CPU references.

    The fake reproduces the slice of the pyobjc-Metal API that
    ``SolverMetalGPU`` drives (device, library, function, pipeline, queue,
    command buffer, encoder, buffers). ``waitUntilCompleted`` decodes the
    buffers the solver bound -- by the documented kernel ABI -- and runs the
    corresponding CPU SA/Gibbs reference, writing results back into the
    shared sample and energy buffers.

    This validates the host orchestration and buffer ABI (which buffer and
    scalar lands at which index), schedule/colouring construction, and energy
    bookkeeping. It does NOT validate the MSL kernels themselves: the kernel
    source is never compiled, and the CPU reference uses numpy's RNG rather
    than the on-device xorshift32, so acceptance/sampling decisions differ
    from real hardware. Kernel correctness is covered by ``TestSolverMetalGPU``
    (real Apple GPU), which is skipped without hardware.
    """
    import types as _types

    def _i32(raw):
        return int(np.frombuffer(raw, dtype=np.int32, count=1)[0])

    def _u32(raw):
        return int(np.frombuffer(raw, dtype=np.uint32, count=1)[0])

    class _Contents:
        def __init__(self, raw):
            self._raw = raw

        def as_buffer(self, length):
            return memoryview(self._raw)[:length]

    class _Buffer:
        def __init__(self, raw):
            self._raw = bytearray(raw)

        def contents(self):
            return _Contents(self._raw)

        def length(self):
            return len(self._raw)

    class _Function:
        def __init__(self, name):
            self._name = name

    class _Library:
        def newFunctionWithName_(self, name):
            return _Function(name)

    class _Pipeline:
        def __init__(self, name):
            self._name = name

        def maxTotalThreadsPerThreadgroup(self):
            return 1024

    class _Encoder:
        def __init__(self):
            self.buffers = {}
            self.scalars = {}
            self.function = None
            self.num_reads = 0

        def setComputePipelineState_(self, pipeline):
            self.function = pipeline._name

        def setBuffer_offset_atIndex_(self, buf, offset, index):
            self.buffers[index] = buf

        def setBytes_length_atIndex_(self, data, length, index):
            self.scalars[index] = bytes(data)

        def dispatchThreadgroups_threadsPerThreadgroup_(self, grid, per_group):
            self.num_reads = int(grid[0])
            self.threads_per_group = tuple(int(v) for v in per_group)

        def endEncoding(self):
            pass

        def run(self):
            if self.function == "sa_metal":
                self._run_sa()
            elif self.function == "gibbs_metal":
                self._run_gibbs()
            else:
                raise AssertionError(f"unexpected kernel {self.function!r}")

        def _decode_common(self, n_index):
            n = _i32(self.scalars[n_index])
            num_reads = self.num_reads
            h = np.frombuffer(self.buffers[0]._raw, dtype=np.float32, count=n)
            j_matrix = np.frombuffer(self.buffers[1]._raw, dtype=np.float32, count=n * n).reshape(n, n)
            beta = np.frombuffer(self.buffers[2]._raw, dtype=np.float32)
            samples = np.frombuffer(self.buffers[3]._raw, dtype=np.int32).reshape(num_reads, n)
            energies = np.frombuffer(self.buffers[4]._raw, dtype=np.float32)
            return n, num_reads, h, j_matrix, beta, samples, energies

        def _run_sa(self):
            n, _num_reads, h, j_matrix, beta, samples, energies = self._decode_common(5)
            num_sweeps = _i32(self.scalars[6])
            base_seed = _u32(self.scalars[7])
            is_spin = bool(_i32(self.scalars[8]))
            _cpu_sa(h, j_matrix, beta, samples, energies, n, num_sweeps, is_spin, base_seed)

        def _run_gibbs(self):
            n, _num_reads, h, j_matrix, beta, samples, energies = self._decode_common(8)
            num_sweeps = _i32(self.scalars[9])
            base_seed = _u32(self.scalars[10])
            num_colors = _i32(self.scalars[11])
            is_spin = bool(_i32(self.scalars[12]))
            starts = np.frombuffer(self.buffers[5]._raw, dtype=np.int32)
            counts = np.frombuffer(self.buffers[6]._raw, dtype=np.int32)
            nodes = np.frombuffer(self.buffers[7]._raw, dtype=np.int32)
            _cpu_gibbs(
                h,
                j_matrix,
                beta,
                samples,
                energies,
                starts,
                counts,
                nodes,
                n,
                num_sweeps,
                num_colors,
                is_spin,
                base_seed,
            )

    class _CommandBuffer:
        def __init__(self):
            self._encoder = None

        def computeCommandEncoder(self):
            self._encoder = _Encoder()
            return self._encoder

        def commit(self):
            pass

        def waitUntilCompleted(self):
            self._encoder.run()

        def status(self):
            return 4  # MTLCommandBufferStatusCompleted

        def error(self):
            return None

    class _Queue:
        def commandBuffer(self):
            return _CommandBuffer()

    class _Device:
        def name(self):
            return "MockMetal"

        def newLibraryWithSource_options_error_(self, source, options, error):
            return (_Library(), None)

        def newComputePipelineStateWithFunction_error_(self, function, error):
            return (_Pipeline(function._name), None)

        def newCommandQueue(self):
            return _Queue()

        def newBufferWithBytes_length_options_(self, data, length, options):
            return _Buffer(data)

        def newBufferWithLength_options_(self, length, options):
            return _Buffer(bytearray(length))

    fake_metal = _types.ModuleType("Metal")
    fake_metal.MTLResourceStorageModeShared = 0
    fake_metal.MTLCommandBufferStatusError = 5
    fake_metal.MTLCreateSystemDefaultDevice = lambda: _Device()
    fake_metal.MTLSizeMake = lambda a, b, c: (a, b, c)

    monkeypatch.setitem(sys.modules, "Metal", fake_metal)
    return fake_metal


class TestSolverMetalGPUMocked:
    """Tests for SolverMetalGPU with a mocked Metal framework (no GPU)."""

    def test_solve_trivial_binary_sa_mocked(self, mock_metal_env) -> None:
        """Solve a trivial QUBO with the SA strategy through the mock."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(strategy="sa", num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert result.energy == 0
        assert result.energy == compute_energy(model, result.sample)

    def test_solve_trivial_spin_sa_mocked(self, mock_metal_env) -> None:
        """Solve a trivial Ising with the SA strategy through the mock."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = _Solver(strategy="sa", num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert result.energy == -2
        assert result.energy == compute_energy(model, result.sample)

    def test_solve_trivial_binary_gibbs_mocked(self, mock_metal_env) -> None:
        """Solve a trivial QUBO with the Gibbs strategy through the mock."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(strategy="gibbs", num_reads=50, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.energy == compute_energy(model, result.sample)

    def test_solve_trivial_spin_gibbs_mocked(self, mock_metal_env) -> None:
        """Solve a trivial Ising with the Gibbs strategy through the mock."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = _Solver(strategy="gibbs", num_reads=50, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert result.energy == -2
        assert result.energy == compute_energy(model, result.sample)

    def test_solve_antiferromagnetic_gibbs_mocked(self, mock_metal_env) -> None:
        """Gibbs on coupled vars exercises graph colouring (two colours)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_quadratic(0, 1, 1.0)

        solver = _Solver(strategy="gibbs", num_reads=50, num_sweeps=200, seed=42)
        result = solver.solve(model)

        x0 = result.sample.get_linear(0)
        x1 = result.sample.get_linear(1)
        assert x0 * x1 == 0
        assert result.energy == compute_energy(model, result.sample)

    def test_energy_matches_compute_energy_mocked(self, mock_metal_env) -> None:
        """Solver-reported energy matches compute_energy (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(3)
        model.set_linear(0, -2.0)
        model.set_linear(1, -3.0)
        model.set_quadratic(0, 1, 5.0)

        solver = _Solver(num_reads=50, num_sweeps=300, seed=42)
        result = solver.solve(model)

        assert result.energy == compute_energy(model, result.sample)
        assert isinstance(result.energy, int)

    def test_kwargs_override_mocked(self, mock_metal_env) -> None:
        """Per-call kwargs override constructor defaults (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=100, seed=1)
        result = solver.solve(model, num_reads=5, seed=99)

        assert result.metadata["reads"] == 5
        assert result.metadata["seed"] == 99
        assert result.metadata["params"]["num_sweeps"] == 1000

    def test_metadata_schema_mocked(self, mock_metal_env) -> None:
        """Metadata has exactly {seed, reads, params} keys (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert set(result.metadata.keys()) == {"seed", "reads", "params"}
        assert set(result.metadata["params"].keys()) == {
            "strategy",
            "num_sweeps",
            "num_sweeps_per_beta",
            "num_betas",
            "beta_range",
            "beta_schedule_type",
            "raw_energy",
        }

    def test_strategy_validation_mocked(self, mock_metal_env) -> None:
        """Unsupported strategy raises ValueError (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        with pytest.raises(ValueError, match="Unsupported strategy"):
            _Solver(strategy="metropolis")

    def test_schedule_validation_mocked(self, mock_metal_env) -> None:
        """Unsupported beta_schedule_type raises ValueError (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        with pytest.raises(ValueError, match="beta_schedule_type"):
            _Solver(beta_schedule_type="exponential")

    def test_strategy_validation_kwargs_mocked(self, mock_metal_env) -> None:
        """Unsupported strategy in solve() kwargs raises ValueError (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        solver = _Solver()
        with pytest.raises(ValueError, match="Unsupported strategy"):
            solver.solve(model, strategy="metropolis")

    def test_beta_range_passthrough_mocked(self, mock_metal_env) -> None:
        """beta_range is recorded in result metadata (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model, beta_range=(0.1, 5.0))

        assert result.metadata["params"]["beta_range"] == (0.1, 5.0)

    def test_missing_metal_raises(self, monkeypatch) -> None:
        """ImportError with install hint when Metal is not installed."""
        monkeypatch.setitem(sys.modules, "Metal", None)
        with pytest.raises(ImportError, match="pip install xqsa"):
            from xqsa.metal_gpu import SolverMetalGPU as _Solver

            _Solver()

    def test_no_gpu_raises(self, mock_metal_env) -> None:
        """RuntimeError when no Metal device is present."""
        mock_metal_env.MTLCreateSystemDefaultDevice = lambda: None
        with pytest.raises(RuntimeError, match="No Metal GPU"):
            from xqsa.metal_gpu import SolverMetalGPU as _Solver

            _Solver()

    def test_num_reads_validation_mocked(self, mock_metal_env) -> None:
        """num_reads < 1 raises ValueError (host-side, no GPU needed)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match="num_reads"):
            _Solver().solve(model, num_reads=0)

    def test_num_sweeps_validation_mocked(self, mock_metal_env) -> None:
        """num_sweeps < 1 raises ValueError (host-side, no GPU needed)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match="num_sweeps"):
            _Solver().solve(model, num_sweeps=0)

    def test_num_sweeps_per_beta_validation_mocked(self, mock_metal_env) -> None:
        """num_sweeps_per_beta < 1 raises, and indivisible counts raise (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match="num_sweeps_per_beta"):
            _Solver().solve(model, num_sweeps_per_beta=0)
        with pytest.raises(ValueError, match="divisible"):
            _Solver().solve(model, num_sweeps=200, num_sweeps_per_beta=7)
        with pytest.raises(ValueError, match="must be an int, got float"):
            _Solver().solve(model, num_sweeps_per_beta=2.5)

    def _capture_beta_buffer(self, solver, model):
        """Run ``solve`` and return the beta buffer bound to the Metal encoder."""
        captured: dict = {}
        original_queue = solver._device.newCommandQueue

        def capturing_queue():
            queue = original_queue()
            original_buffer = queue.commandBuffer

            def capturing_buffer():
                command_buffer = original_buffer()
                original_encoder = command_buffer.computeCommandEncoder

                def capturing_encoder():
                    encoder = original_encoder()
                    captured["encoder"] = encoder
                    return encoder

                command_buffer.computeCommandEncoder = capturing_encoder
                return command_buffer

            queue.commandBuffer = capturing_buffer
            return queue

        solver._device.newCommandQueue = capturing_queue
        result = solver.solve(model)
        beta = np.frombuffer(captured["encoder"].buffers[2]._raw, dtype=np.float32)
        return result, beta

    def test_num_sweeps_per_beta_run_mocked(self, mock_metal_env) -> None:
        """num_sweeps_per_beta > 1 expands the schedule and records the split (mocked).

        Verifies the divide semantics end-to-end: the beta buffer bound to the
        kernel (ABI index 2) is the per-sweep schedule of length ``num_sweeps``,
        holding each of the ``num_betas`` levels for ``num_sweeps_per_beta``
        consecutive sweeps.
        """
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(strategy="sa", num_reads=10, num_sweeps=200, num_sweeps_per_beta=10, seed=42)
        result, beta = self._capture_beta_buffer(solver, model)

        assert result.energy == 0
        assert result.metadata["params"]["num_sweeps"] == 200
        assert result.metadata["params"]["num_sweeps_per_beta"] == 10
        assert result.metadata["params"]["num_betas"] == 20

        assert beta.shape == (200,)
        # Each temperature level is held for num_sweeps_per_beta consecutive sweeps.
        levels = beta.reshape(20, 10)
        for level in levels:
            assert np.all(level == level[0])
        assert np.unique(levels[:, 0]).size == 20
        assert np.all(np.diff(levels[:, 0]) > 0)

    def test_default_beta_schedule_is_bit_identical_mocked(self, mock_metal_env) -> None:
        """The default one-beta-per-sweep buffer matches the legacy schedule."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)
        solver = _Solver(strategy="sa", num_reads=10, num_sweeps=200, seed=42)
        result, beta = self._capture_beta_buffer(solver, model)

        expected = solver._compute_beta_schedule(
            200, result.metadata["params"]["beta_range"], result.metadata["params"]["beta_schedule_type"]
        )
        np.testing.assert_array_equal(beta, expected)

    def test_single_temperature_level_uses_beta_end_mocked(self, mock_metal_env) -> None:
        """A single temperature level binds beta_end for every sweep."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)
        solver = _Solver(strategy="sa", num_reads=10, num_sweeps=50, num_sweeps_per_beta=50, seed=42)
        result, beta = self._capture_beta_buffer(solver, model)

        beta_end = result.metadata["params"]["beta_range"][-1]
        assert result.metadata["params"]["num_betas"] == 1
        np.testing.assert_array_equal(beta, np.full(50, beta_end, dtype=np.float32))

    def test_linear_schedule_mocked(self, mock_metal_env) -> None:
        """The linear beta schedule solves a trivial model (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(num_reads=20, num_sweeps=200, beta_schedule_type="linear", seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["beta_schedule_type"] == "linear"

    def test_single_sweep_mocked(self, mock_metal_env) -> None:
        """num_sweeps == 1 exercises the schedule special case (mocked)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(num_reads=50, num_sweeps=1, seed=42)
        result = solver.solve(model)

        assert result.energy == compute_energy(model, result.sample)

    def test_single_sweep_uses_beta_end(self, mock_metal_env) -> None:
        """num_sweeps == 1 anneals at beta_end, matching dwave-samplers."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        solver = _Solver()
        beta_start, beta_end = 0.05, 5.0
        for schedule_type in ("geometric", "linear"):
            schedule = solver._compute_beta_schedule(1, (beta_start, beta_end), schedule_type)
            assert schedule.shape == (1,)
            assert schedule[0] == pytest.approx(beta_end)

    def test_compute_beta_schedule_length_mocked(self, mock_metal_env) -> None:
        """_compute_beta_schedule returns one beta per temperature level."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        solver = _Solver()
        for schedule_type in ("geometric", "linear"):
            schedule = solver._compute_beta_schedule(20, (0.05, 5.0), schedule_type)
            assert schedule.shape == (20,)

    def test_command_buffer_error_raises_mocked(self, mock_metal_env) -> None:
        """A GPU command-buffer error surfaces as RuntimeError, not silently."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=5, num_sweeps=10, seed=42)
        # Patch the device's queue so dispatched command buffers report failure.
        original_queue = solver._device.newCommandQueue

        def failing_queue():
            queue = original_queue()
            original_buffer = queue.commandBuffer

            def failing_buffer():
                cb = original_buffer()
                cb.status = lambda: mock_metal_env.MTLCommandBufferStatusError
                cb.error = lambda: "simulated GPU failure"
                return cb

            queue.commandBuffer = failing_buffer
            return queue

        solver._device.newCommandQueue = failing_queue
        with pytest.raises(RuntimeError, match="command buffer failed"):
            solver.solve(model)

    def test_graph_coloring_is_valid_mocked(self, mock_metal_env) -> None:
        """Greedy colouring partitions all nodes and gives no edge one colour."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        # Triangle + a pendant: forces >= 3 colours and a non-trivial layout.
        model = XQMX.binary_model(4)
        for i, j in [(0, 1), (1, 2), (0, 2), (2, 3)]:
            model.set_quadratic(i, j, 1.0)

        solver = _Solver()
        starts, counts, nodes, num_colors = solver._compute_graph_coloring(model)

        # Every node appears exactly once across the colour slices.
        seen = sorted(int(nodes[starts[c] + k]) for c in range(num_colors) for k in range(int(counts[c])))
        assert seen == list(range(model.size))

        # color[node] lookup; no edge may join two same-colour nodes.
        color_of = {int(nodes[starts[c] + k]): c for c in range(num_colors) for k in range(int(counts[c]))}
        for i, j in model.quadratic:
            assert color_of[i] != color_of[j]

    def test_sa_dispatch_width_mocked(self, mock_metal_env) -> None:
        """The sa pipeline dispatches _SA_THREADGROUP_WIDTH threads per replica (QUI-852)."""
        from xqsa.metal_gpu import _SA_THREADGROUP_WIDTH
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(strategy="sa", num_reads=5, num_sweeps=10, seed=1)
        captured: dict = {}
        original_queue = solver._device.newCommandQueue

        def capturing_queue():
            queue = original_queue()
            original_buffer = queue.commandBuffer

            def capturing_buffer():
                cb = original_buffer()
                original_encoder = cb.computeCommandEncoder

                def capturing_encoder():
                    encoder = original_encoder()
                    captured["encoder"] = encoder
                    return encoder

                cb.computeCommandEncoder = capturing_encoder
                return cb

            queue.commandBuffer = capturing_buffer
            return queue

        solver._device.newCommandQueue = capturing_queue
        solver.solve(model)

        assert _SA_THREADGROUP_WIDTH == 256
        # Mock pipeline reports maxTotalThreadsPerThreadgroup == 1024, so the
        # clamp resolves to the full width.
        assert captured["encoder"].threads_per_group == (256, 1, 1)

    def test_gibbs_dispatch_width_mocked(self, mock_metal_env) -> None:
        """The gibbs pipeline keeps one thread per replica (out of QUI-852 scope)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(strategy="gibbs", num_reads=5, num_sweeps=10, seed=1)
        captured: dict = {}
        original_queue = solver._device.newCommandQueue

        def capturing_queue():
            queue = original_queue()
            original_buffer = queue.commandBuffer

            def capturing_buffer():
                cb = original_buffer()
                original_encoder = cb.computeCommandEncoder

                def capturing_encoder():
                    encoder = original_encoder()
                    captured["encoder"] = encoder
                    return encoder

                cb.computeCommandEncoder = capturing_encoder
                return cb

            queue.commandBuffer = capturing_buffer
            return queue

        solver._device.newCommandQueue = capturing_queue
        solver.solve(model)

        assert captured["encoder"].threads_per_group == (1, 1, 1)

    def test_sa_dispatch_width_down_clamps_mocked(self, mock_metal_env) -> None:
        """The sa dispatch clamps to the largest power of two <= the pipeline max (QUI-852)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(strategy="sa", num_reads=5, num_sweeps=10, seed=1)
        # Force the cached_property to compile and cache the sa pipeline now, so
        # patching its class below is visible to the (already-cached) instance
        # that solve() will use.
        type(solver._sa_pipeline).maxTotalThreadsPerThreadgroup = lambda self: 96

        captured: dict = {}
        original_queue = solver._device.newCommandQueue

        def capturing_queue():
            queue = original_queue()
            original_buffer = queue.commandBuffer

            def capturing_buffer():
                cb = original_buffer()
                original_encoder = cb.computeCommandEncoder

                def capturing_encoder():
                    encoder = original_encoder()
                    captured["encoder"] = encoder
                    return encoder

                cb.computeCommandEncoder = capturing_encoder
                return cb

            queue.commandBuffer = capturing_buffer
            return queue

        solver._device.newCommandQueue = capturing_queue
        solver.solve(model)

        # 96 is not a power of two; the largest power of two <= 96 is 64.
        assert captured["encoder"].threads_per_group == (64, 1, 1)


# ---------------------------------------------------------------------------
# SolverMetalGPU -- real GPU tests (requires macOS + Metal)
# ---------------------------------------------------------------------------


_has_metal = True
try:
    import Metal as _real_metal  # noqa: F401

    _has_metal = _real_metal.MTLCreateSystemDefaultDevice() is not None
except ImportError:
    _has_metal = False

if _has_metal or _IN_CI:
    from xqsa.metal_gpu import SolverMetalGPU


@pytest.mark.metal
@pytest.mark.skipif(
    not _IN_CI and not _has_metal,
    reason="Metal GPU not available (skipped locally; runs and hard-fails in CI)",
)
class TestSolverMetalGPU:
    """Tests for the Metal GPU solver (real Apple hardware)."""

    def test_default_params(self) -> None:
        """SolverMetalGPU stores default parameters."""
        solver = SolverMetalGPU()
        assert solver.strategy == "sa"
        assert solver.num_reads == 100
        assert solver.num_sweeps == 1000
        assert solver.num_sweeps_per_beta == 1
        assert solver.beta_range is None
        assert solver.beta_schedule_type == "geometric"
        assert solver.seed is None

    def test_custom_params(self) -> None:
        """SolverMetalGPU accepts custom parameters."""
        solver = SolverMetalGPU(strategy="gibbs", num_reads=50, num_sweeps=500, num_sweeps_per_beta=5, seed=42)
        assert solver.strategy == "gibbs"
        assert solver.num_reads == 50
        assert solver.num_sweeps == 500
        assert solver.num_sweeps_per_beta == 5
        assert solver.seed == 42

    def test_solve_trivial_binary_sa(self) -> None:
        """Solve a trivial 2-variable QUBO with SA: minimize x0 + x1."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverMetalGPU(strategy="sa", num_reads=20, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert isinstance(result, SolverResult)
        assert result.sample.mode == XQMXMode.SAMPLE
        assert result.energy == 0
        assert isinstance(result.energy, int)
        assert result.timing > 0.0
        assert result.metadata["reads"] == 20
        assert result.metadata["params"]["strategy"] == "sa"

    def test_solve_trivial_spin_sa(self) -> None:
        """Solve a trivial Ising with SA: minimize -s0 - s1 (ground +1,+1)."""
        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = SolverMetalGPU(strategy="sa", num_reads=20, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert result.energy == -2
        for i in range(2):
            assert result.sample.get_linear(i) in (-1, 1)

    def test_solve_trivial_binary_gibbs(self) -> None:
        """Solve a trivial QUBO with the Gibbs strategy."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverMetalGPU(strategy="gibbs", num_reads=50, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.energy == compute_energy(model, result.sample)

    def test_solve_trivial_spin_gibbs(self) -> None:
        """Solve a trivial Ising with the Gibbs strategy."""
        model = XQMX.spin_model(2)
        model.set_linear(0, -1.0)
        model.set_linear(1, -1.0)

        solver = SolverMetalGPU(strategy="gibbs", num_reads=50, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert result.energy == -2
        assert result.energy == compute_energy(model, result.sample)

    def test_solve_antiferromagnetic(self) -> None:
        """Solve x0*x1 with positive coupling: optimal is x0 != x1."""
        model = XQMX.binary_model(2)
        model.set_quadratic(0, 1, 1.0)

        solver = SolverMetalGPU(num_reads=20, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert result.sample.get_linear(0) * result.sample.get_linear(1) == 0

    def test_solve_preserves_grid(self) -> None:
        """Solver preserves rows/cols from the model."""
        model = XQMX.binary_model(4, rows=2, cols=2)
        model.set_linear(0, 1.0)

        solver = SolverMetalGPU(num_reads=20, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert result.sample.rows == 2
        assert result.sample.cols == 2

    def test_solve_rejects_sample_mode(self) -> None:
        """Solve rejects SAMPLE mode input."""
        sample = XQMX.binary_sample(2)
        solver = SolverMetalGPU()
        with pytest.raises(ValueError, match="MODEL"):
            solver.solve(sample)

    def test_energy_matches_compute_energy(self) -> None:
        """Solver-reported energy matches compute_energy."""
        model = XQMX.binary_model(3)
        model.set_linear(0, -2.0)
        model.set_linear(1, -3.0)
        model.set_quadratic(0, 1, 5.0)

        solver = SolverMetalGPU(num_reads=50, num_sweeps=500, seed=42)
        result = solver.solve(model)

        assert result.energy == compute_energy(model, result.sample)
        assert isinstance(result.energy, int)

    def test_num_reads_validation(self) -> None:
        """num_reads < 1 raises ValueError."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverMetalGPU()
        with pytest.raises(ValueError, match="num_reads"):
            solver.solve(model, num_reads=0)

    def test_num_sweeps_validation(self) -> None:
        """num_sweeps < 1 raises ValueError."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverMetalGPU()
        with pytest.raises(ValueError, match="num_sweeps"):
            solver.solve(model, num_sweeps=0)

    def test_strategy_validation_init(self) -> None:
        """Unsupported strategy in __init__ raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported strategy"):
            SolverMetalGPU(strategy="metropolis")

    def test_num_sweeps_per_beta_validation(self) -> None:
        """num_sweeps_per_beta < 1 raises, and indivisible counts raise."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverMetalGPU()
        with pytest.raises(ValueError, match="num_sweeps_per_beta"):
            solver.solve(model, num_sweeps_per_beta=0)
        with pytest.raises(ValueError, match="divisible"):
            solver.solve(model, num_sweeps=200, num_sweeps_per_beta=7)
        with pytest.raises(ValueError, match="must be an int, got float"):
            solver.solve(model, num_sweeps_per_beta=2.5)

    def test_single_temperature_level_uses_beta_end(self) -> None:
        """A single Metal temperature level uses the coldest beta."""
        solver = SolverMetalGPU()
        beta_start, beta_end = 0.05, 5.0
        for schedule_type in ("geometric", "linear"):
            schedule = solver._compute_beta_schedule(1, (beta_start, beta_end), schedule_type)
            assert schedule.shape == (1,)
            assert schedule[0] == pytest.approx(beta_end)

    def test_num_sweeps_per_beta_run(self) -> None:
        """num_sweeps_per_beta > 1 solves and records the schedule split."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverMetalGPU(strategy="sa", num_reads=20, num_sweeps=200, num_sweeps_per_beta=10, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["num_sweeps"] == 200
        assert result.metadata["params"]["num_sweeps_per_beta"] == 10
        assert result.metadata["params"]["num_betas"] == 20

    def test_linear_schedule(self) -> None:
        """The linear beta schedule also solves a trivial model."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverMetalGPU(num_reads=20, num_sweeps=200, beta_schedule_type="linear", seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["beta_schedule_type"] == "linear"

    def test_metadata_schema(self) -> None:
        """Metadata has exactly {seed, reads, params} top-level keys."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = SolverMetalGPU(num_reads=20, num_sweeps=200, seed=42)
        result = solver.solve(model)

        assert set(result.metadata.keys()) == {"seed", "reads", "params"}
        assert set(result.metadata["params"].keys()) == {
            "strategy",
            "num_sweeps",
            "num_sweeps_per_beta",
            "num_betas",
            "beta_range",
            "beta_schedule_type",
            "raw_energy",
        }

    def test_reduction_consistency_integer_n512(self) -> None:
        """Kernel-accumulated energy matches the exact host energy at n=512.

        n > _SA_THREADGROUP_WIDTH exercises multi-element strides plus the full
        threadgroup reduction on real hardware (the other metal tests use tiny
        n where most lanes are idle). Integer coefficients keep every float32
        sum exact below 2**24, so any reduction race, mis-partition, or missing
        barrier surfaces as a mismatch between the incrementally accumulated
        kernel energy and the host recompute.
        """
        rng = np.random.default_rng(0)
        n = 512
        model = XQMX.binary_model(n)
        for i in range(n):
            for j in range(i + 1, n):
                if rng.random() < 0.25:
                    model.set_quadratic(i, j, 1.0)

        solver = SolverMetalGPU(strategy="sa", num_reads=10, num_sweeps=100, seed=42)
        result = solver.solve(model)

        assert float(result.metadata["params"]["raw_energy"]) == float(result.energy)
