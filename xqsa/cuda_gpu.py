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
CUDA GPU simulated annealing solver.

Custom CUDA kernels via CuPy RawKernel for solving XQMX quadratic
models on NVIDIA GPUs. Bypasses the D-Wave SDK entirely.

Requires the optional ``cuda`` extra::

    pip install xqsa[cuda]
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import numpy as np

from xqvm_py.xqmx import XQMX

from .solver import Solver, SolverResult

if TYPE_CHECKING:
    import cupy as cp

_SUPPORTED_STRATEGIES = frozenset({"sa"})


class SolverCudaGPU(Solver):
    """Simulated annealing solver using custom CUDA kernels via CuPy.

    Runs parallel replica simulated annealing on an NVIDIA GPU. Each
    replica (controlled by ``num_reads``) executes independently in its
    own CUDA thread block.

    Examples:

    ```python
    from xqsa import SolverCudaGPU
    from xqvm_py.xqmx import XQMX

    model = XQMX.binary_model(4)
    model.set_linear(0, -1.0)
    model.set_quadratic(0, 1, 2.0)

    solver = SolverCudaGPU()
    result = solver.solve(model)
    print(result.energy, result.timing)
    ```

    Raises:
        ImportError: if ``cupy-cuda12x`` is not installed.
        RuntimeError: if no NVIDIA CUDA GPU is detected.
        ValueError: if ``strategy`` is not supported.
    """

    def __init__(
        self,
        strategy: str = "sa",
        num_reads: int = 100,
        num_sweeps: int = 1000,
        beta_range: tuple[float, float] | None = None,
        seed: int | None = None,
    ) -> None:
        try:
            import cupy as _cupy
        except ImportError as exc:
            raise ImportError("cupy is not installed. Install xqsa with: pip install xqsa[cuda]") from exc

        if _cupy.cuda.runtime.getDeviceCount() == 0:
            raise RuntimeError("No NVIDIA CUDA GPU detected. SolverCudaGPU requires an NVIDIA GPU with CUDA support.")

        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported strategy {strategy!r}. Supported: {sorted(_SUPPORTED_STRATEGIES)}")

        self._cp = _cupy
        self.strategy = strategy
        self.num_reads = num_reads
        self.num_sweeps = num_sweeps
        self.beta_range = beta_range
        self.seed = seed

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Solve using parallel-replica simulated annealing on GPU.

        Raises:
            ValueError: if the model is not in BINARY or SPIN domain,
                or if parameter values are invalid.
        """
        self._validate_model(model)

        strategy = kwargs.get("strategy", self.strategy)
        num_reads = kwargs.get("num_reads", self.num_reads)
        num_sweeps = kwargs.get("num_sweeps", self.num_sweeps)
        beta_range = kwargs.get("beta_range", self.beta_range)
        seed = kwargs.get("seed", self.seed)

        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported strategy {strategy!r}. Supported: {sorted(_SUPPORTED_STRATEGIES)}")
        if num_reads < 1:
            raise ValueError("num_reads must be >= 1")
        if num_sweeps < 1:
            raise ValueError("num_sweeps must be >= 1")

        h, j_matrix = self._to_dense_arrays(model)

        t0 = time.perf_counter()
        best_sample_dict, raw_energy = self._run_sa(
            model=model,
            h=h,
            j_matrix=j_matrix,
            num_reads=num_reads,
            num_sweeps=num_sweeps,
            beta_range=beta_range,
            seed=seed,
        )
        elapsed = time.perf_counter() - t0

        sample = self._sample_to_xqmx(model, best_sample_dict)

        return SolverResult(
            sample=sample,
            energy=self._recompute_energy(model, sample),
            timing=elapsed,
            metadata={
                "seed": seed,
                "reads": num_reads,
                "params": {
                    "strategy": strategy,
                    "num_sweeps": num_sweeps,
                    "beta_range": beta_range,
                    "raw_energy": raw_energy,
                },
            },
        )

    def _to_dense_arrays(self, model: XQMX) -> tuple[cp.ndarray, cp.ndarray]:
        """Convert sparse XQMX model to dense GPU arrays.

        Returns:
            (h, J) where h is shape (n,) and J is shape (n, n),
            both float64 on GPU.
        """
        n = model.size
        h_np = np.zeros(n, dtype=np.float64)
        j_np = np.zeros((n, n), dtype=np.float64)

        for idx, coeff in model.linear.items():
            h_np[idx] = coeff

        for (i, j), coeff in model.quadratic.items():
            j_np[i, j] = coeff
            j_np[j, i] = coeff

        return self._cp.asarray(h_np), self._cp.asarray(j_np)

    def _run_sa(
        self,
        model: XQMX,
        h: cp.ndarray,
        j_matrix: cp.ndarray,
        num_reads: int,
        num_sweeps: int,
        beta_range: tuple[float, float] | None,
        seed: int | None,
    ) -> tuple[dict[int, int], float]:
        """Run simulated annealing on GPU.

        Returns:
            (best_sample_dict, raw_energy) -- the best sample found
            across all replicas, and its float energy.
        """
        raise NotImplementedError("CUDA SA kernel not yet implemented")
