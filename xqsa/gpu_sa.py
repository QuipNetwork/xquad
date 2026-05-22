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
GPU-accelerated simulated annealing solver.

Wraps ``dwave.samplers.SimulatedAnnealingSampler`` with ``use_gpu=True`` to
run thousands of parallel SA replicas on an NVIDIA CUDA GPU.

Requires the optional ``gpu`` extra::

    pip install xqsa[gpu]

A CUDA-capable GPU must be present -- the solver raises ``RuntimeError`` at
construction time if none is detected.
"""

from __future__ import annotations

import time
from typing import Any

from xqvm_py.xqmx import XQMX

from .solver import Solver, SolverResult


class SolverGPUSA(Solver):
    """GPU-accelerated simulated annealing via dwave.samplers on CUDA.

    Runs thousands of independent SA replicas in parallel on an NVIDIA GPU,
    producing better solutions per wall-clock second than ``SolverDWaveCPU`` for
    large QUBO/Ising problems.

    Examples:

    ```python
    from xqsa import SolverGPUSA
    from xqvm_py.xqmx import XQMX

    model = XQMX.binary_model(64)
    # ... set coefficients ...

    solver = SolverGPUSA(num_reads=1000)
    result = solver.solve(model)
    print(result.energy, result.metadata["use_gpu"])
    ```

    Raises:
        ImportError: if ``xqsa[gpu]`` is not installed.
        RuntimeError: if no CUDA-capable GPU is detected.
    """

    def __init__(
        self,
        num_reads: int = 100,
        num_sweeps: int = 1000,
        seed: int | None = None,
    ) -> None:
        try:
            import dwave.samplers as _dwave_samplers
            from numba import cuda as _cuda
        except ImportError as exc:
            raise ImportError(
                "dwave-samplers GPU support is not installed. Install xqsa with: pip install xqsa[gpu]"
            ) from exc

        if not _cuda.is_available():
            raise RuntimeError(
                "No CUDA-capable GPU detected. SolverGPUSA requires an NVIDIA GPU with CUDA support."
            )

        self.num_reads = num_reads
        self.num_sweeps = num_sweeps
        self.seed = seed
        self._sampler = _dwave_samplers.SimulatedAnnealingSampler()

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Solve using GPU-accelerated simulated annealing.

        Raises:
            ValueError: if the model is not in BINARY or SPIN domain.
        """
        self._validate_model(model)

        num_reads = kwargs.pop("num_reads", self.num_reads)
        num_sweeps = kwargs.pop("num_sweeps", self.num_sweeps)
        seed = kwargs.pop("seed", self.seed)

        if num_reads < 1:
            raise ValueError("num_reads must be >= 1")
        if num_sweeps < 1:
            raise ValueError("num_sweeps must be >= 1")

        bqm = self._model_to_bqm(model)

        sample_kwargs: dict[str, Any] = {
            "num_reads": num_reads,
            "num_sweeps": num_sweeps,
            "use_gpu": True,
        }
        if seed is not None:
            sample_kwargs["seed"] = seed

        t0 = time.perf_counter()
        result = self._sampler.sample(bqm, **sample_kwargs, **kwargs)
        elapsed = time.perf_counter() - t0

        best = result.first
        sample = self._sample_to_xqmx(model, dict(best.sample))

        return SolverResult(
            sample=sample,
            energy=self._recompute_energy(model, sample),
            timing=elapsed,
            metadata={
                "seed": seed,
                "reads": num_reads,
                "use_gpu": True,
                "params": {
                    "num_sweeps": num_sweeps,
                    "raw_energy": float(best.energy),
                },
            },
        )
