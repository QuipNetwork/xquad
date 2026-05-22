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
D-Wave Advantage QPU solver.

Submits XQMX quadratic models to a D-Wave Advantage QPU via the D-Wave Leap
cloud API. Uses EmbeddingComposite for automatic minor embedding onto the
Pegasus qubit topology.

Requires the optional ``dwave`` extra::

    pip install xqsa[dwave]

Credentials are resolved from constructor arguments first, then from the
``DWAVE_API_TOKEN`` and ``DWAVE_API_ENDPOINT`` environment variables.
"""

from __future__ import annotations

import os
import time
from typing import Any

from xqvm_py.xqmx import XQMX

from .solver import Solver, SolverResult


class SolverDWaveQPU(Solver):
    """D-Wave Advantage QPU solver via D-Wave Leap cloud API.

    Wraps ``dwave.system.DWaveSampler`` with ``EmbeddingComposite`` to handle
    automatic minor embedding onto the physical Pegasus qubit topology.

    Examples:

    ```python
    import os
    os.environ["DWAVE_API_TOKEN"] = "your-leap-token"

    from xqsa import SolverDWaveQPU
    from xqvm_py.xqmx import XQMX

    model = XQMX.binary_model(4)
    model.set_linear(0, -1.0)
    model.set_quadratic(0, 1, 2.0)

    solver = SolverDWaveQPU()
    result = solver.solve(model)
    print(result.energy, result.metadata["qpu_timing"])
    ```

    Raises:
        ImportError: if ``dwave-system`` is not installed.
        ValueError: if no API token is available.
    """

    def __init__(
        self,
        token: str | None = None,
        endpoint: str | None = None,
        solver: str | None = None,
        num_reads: int = 100,
        annealing_time: int = 20,
    ) -> None:
        try:
            import dwave.system as _dwave_system
        except ImportError as exc:
            raise ImportError(
                "dwave-system is not installed. Install xqsa with: pip install xqsa[dwave]"
            ) from exc

        resolved_token = token or os.environ.get("DWAVE_API_TOKEN")
        if resolved_token is None:
            raise ValueError("D-Wave API token is required. Pass token= or set DWAVE_API_TOKEN.")

        resolved_endpoint = endpoint or os.environ.get("DWAVE_API_ENDPOINT")

        self.num_reads = num_reads
        self.annealing_time = annealing_time

        solver_filter: str | dict[str, str] = solver if solver is not None else {"topology__type": "pegasus"}

        sampler_kwargs: dict[str, Any] = {
            "token": resolved_token,
            "solver": solver_filter,
        }
        if resolved_endpoint is not None:
            sampler_kwargs["endpoint"] = resolved_endpoint

        raw = _dwave_system.DWaveSampler(**sampler_kwargs)
        self._sampler = _dwave_system.EmbeddingComposite(raw)
        self._solver_name: str = raw.solver.id

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Solve using D-Wave Advantage QPU via EmbeddingComposite.

        Raises:
            ValueError: if the model is not in BINARY or SPIN domain.
        """
        self._validate_model(model)

        num_reads = kwargs.pop("num_reads", self.num_reads)
        annealing_time = kwargs.pop("annealing_time", self.annealing_time)

        bqm = self._model_to_bqm(model)

        t0 = time.perf_counter()
        result = self._sampler.sample(
            bqm,
            num_reads=num_reads,
            annealing_time=annealing_time,
            **kwargs,
        )
        elapsed = time.perf_counter() - t0

        best = result.first
        sample = self._sample_to_xqmx(model, dict(best.sample))

        qpu_timing: dict[str, Any] | None = result.info.get("timing") if hasattr(result, "info") else None

        return SolverResult(
            sample=sample,
            energy=self._recompute_energy(model, sample),
            timing=elapsed,
            metadata={
                "reads": num_reads,
                "solver": self._solver_name,
                "qpu_timing": qpu_timing,
                "params": {
                    "annealing_time": annealing_time,
                    "raw_energy": float(best.energy),
                },
            },
        )
