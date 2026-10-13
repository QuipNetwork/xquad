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
In-process CPU simulated annealing implementation.

Wraps dwave-samplers' SimulatedAnnealingSampler to sample XQMX quadratic
models on CPU. :class:`xqsa.SolverCPU` runs it.
"""

from __future__ import annotations

from typing import Any

from dwave.samplers import SimulatedAnnealingSampler

from xqffi.vm import XqmxModel

from .solver import Implementation, RunOutput, _check_algorithm, _check_anneal_params, model_to_bqm, sample_to_xqmx

_SUPPORTED_ALGORITHMS = frozenset({"sa"})


class InProcessCPU(Implementation):
    """Simulated annealing using dwave-samplers (CPU)."""

    def __init__(
        self,
        algorithm: str = "sa",
        num_reads: int = 100,
        num_sweeps: int = 1000,
        num_sweeps_per_beta: int = 1,
        beta_range: tuple[float, float] | None = None,
        seed: int | None = None,
    ) -> None:
        _check_algorithm(algorithm, _SUPPORTED_ALGORITHMS, "CPU")
        super().__init__(
            num_reads=num_reads,
            num_sweeps=num_sweeps,
            num_sweeps_per_beta=num_sweeps_per_beta,
            beta_range=beta_range,
            seed=seed,
        )

    def run(
        self,
        model: XqmxModel,
        *,
        num_reads: int,
        num_sweeps: int,
        num_sweeps_per_beta: int,
        beta_range: tuple[float, float] | None,
        seed: int | None,
    ) -> RunOutput:
        """Sample using simulated annealing via dwave-samplers."""

        # Enforce locally for a consistent cross-backend error; dwave-samplers
        # retains the same check as a backstop.
        num_betas = _check_anneal_params(num_reads, num_sweeps, num_sweeps_per_beta)

        sample_kwargs: dict[str, Any] = {
            "num_reads": num_reads,
            "num_sweeps": num_sweeps,
            "num_sweeps_per_beta": num_sweeps_per_beta,
        }
        if beta_range is not None:
            sample_kwargs["beta_range"] = beta_range
        if seed is not None:
            sample_kwargs["seed"] = seed

        result = SimulatedAnnealingSampler().sample(model_to_bqm(model), **sample_kwargs)
        best = result.first

        return RunOutput(
            sample=sample_to_xqmx(model, dict(best.sample)),
            seed=seed,
            reads=num_reads,
            params={
                "num_sweeps": num_sweeps,
                "num_sweeps_per_beta": num_sweeps_per_beta,
                "num_betas": num_betas,
                "beta_range": beta_range,
                "num_occurrences": int(best.num_occurrences),
                "raw_energy": float(best.energy),
            },
        )
