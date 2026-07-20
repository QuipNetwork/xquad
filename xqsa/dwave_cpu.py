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
DWave CPU simulated annealing solver.

Wraps dwave-samplers' SimulatedAnnealingSampler to solve XQMX
quadratic models via simulated annealing on CPU.
"""

from __future__ import annotations

import time
from typing import Any

from dwave.samplers import SimulatedAnnealingSampler

from xqvm_py.xqmx import XQMX

from .solver import Solver, SolverResult


class SolverDWaveCPU(Solver):
    """Simulated annealing solver using dwave-samplers (CPU)."""

    def __init__(
        self,
        num_reads: int = 100,
        num_sweeps: int = 1000,
        num_sweeps_per_beta: int = 1,
        beta_range: tuple[float, float] | None = None,
        seed: int | None = None,
    ) -> None:
        self.num_reads = num_reads
        self.num_sweeps = num_sweeps
        self.num_sweeps_per_beta = num_sweeps_per_beta
        self.beta_range = beta_range
        self.seed = seed

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Solve using simulated annealing via dwave-samplers."""
        self._validate_model(model)

        num_reads = kwargs.get("num_reads", self.num_reads)
        num_sweeps = kwargs.get("num_sweeps", self.num_sweeps)
        num_sweeps_per_beta = kwargs.get("num_sweeps_per_beta", self.num_sweeps_per_beta)
        beta_range = kwargs.get("beta_range", self.beta_range)
        seed = kwargs.get("seed", self.seed)

        if num_reads < 1:
            raise ValueError(f"num_reads must be >= 1, got {num_reads}")
        if num_sweeps < 1:
            raise ValueError(f"num_sweeps must be >= 1, got {num_sweeps}")
        if not isinstance(num_sweeps_per_beta, int):
            raise ValueError(f"num_sweeps_per_beta must be an int, got {type(num_sweeps_per_beta).__name__}")
        if num_sweeps_per_beta < 1:
            raise ValueError(f"num_sweeps_per_beta must be >= 1, got {num_sweeps_per_beta}")
        num_betas, rem = divmod(num_sweeps, num_sweeps_per_beta)
        if rem != 0:
            raise ValueError(
                f"num_sweeps ({num_sweeps}) must be divisible by num_sweeps_per_beta ({num_sweeps_per_beta})"
            )

        bqm = self._model_to_bqm(model)
        sampler = SimulatedAnnealingSampler()

        # Enforce locally for a consistent cross-backend error; dwave-samplers
        # retains the same check as a backstop.
        sample_kwargs: dict[str, Any] = {
            "num_reads": num_reads,
            "num_sweeps": num_sweeps,
            "num_sweeps_per_beta": num_sweeps_per_beta,
        }
        if beta_range is not None:
            sample_kwargs["beta_range"] = beta_range
        if seed is not None:
            sample_kwargs["seed"] = seed

        t0 = time.perf_counter()
        result = sampler.sample(bqm, **sample_kwargs)
        elapsed = time.perf_counter() - t0

        best = result.first
        raw_sample = dict(best.sample)
        sample = self._sample_to_xqmx(model, raw_sample)

        return SolverResult(
            sample=sample,
            energy=self._recompute_energy(model, sample),
            timing=elapsed,
            metadata={
                "seed": seed,
                "reads": num_reads,
                "params": {
                    "num_sweeps": num_sweeps,
                    "num_sweeps_per_beta": num_sweeps_per_beta,
                    "num_betas": num_betas,
                    "beta_range": beta_range,
                    "num_occurrences": int(best.num_occurrences),
                    "raw_energy": float(best.energy),
                },
            },
        )
