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
import types
from unittest.mock import MagicMock

import pytest

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
