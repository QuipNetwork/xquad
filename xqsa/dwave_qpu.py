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
In-process D-Wave Advantage QPU implementation.

Submits XQMX quadratic models to a D-Wave Advantage QPU via the D-Wave Leap
cloud API. Uses EmbeddingComposite for automatic minor embedding onto the
Pegasus qubit topology. :class:`xqsa.SolverDWave` runs it.

Requires the optional ``dwave`` extra::

    pip install xqsa[dwave]

Credentials are resolved from constructor arguments first, then from the
``DWAVE_API_TOKEN`` and ``DWAVE_API_ENDPOINT`` environment variables.
"""

from __future__ import annotations

import os
from typing import Any

from xqffi.vm import XqmxModel

from .solver import Implementation, RunOutput, model_to_bqm, sample_to_xqmx


class InProcessDWave(Implementation):
    """D-Wave Advantage QPU sampling via D-Wave Leap cloud API.

    Wraps ``dwave.system.DWaveSampler`` with ``EmbeddingComposite`` to handle
    automatic minor embedding onto the physical Pegasus qubit topology.

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
        chain_strength: float | None = None,
        auto_scale: bool | None = None,
    ) -> None:
        try:
            import dwave.system as _dwave_system
        except ImportError as exc:
            raise ImportError("dwave-system is not installed. Install xqsa with: pip install xqsa[dwave]") from exc

        resolved_token = token or os.environ.get("DWAVE_API_TOKEN")
        if resolved_token is None:
            raise ValueError("D-Wave API token is required. Pass token= or set DWAVE_API_TOKEN.")

        resolved_endpoint = endpoint or os.environ.get("DWAVE_API_ENDPOINT")

        super().__init__(
            num_reads=num_reads,
            annealing_time=annealing_time,
            chain_strength=chain_strength,
            auto_scale=auto_scale,
        )

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

    def run(
        self,
        model: XqmxModel,
        *,
        num_reads: int,
        annealing_time: int,
        chain_strength: float | None,
        auto_scale: bool | None,
    ) -> RunOutput:
        """Sample on the D-Wave Advantage QPU via EmbeddingComposite."""
        # Ocean sampling parameters forwarded when set; the constructor rejects any other.
        ocean = {
            key: value
            for key, value in {"chain_strength": chain_strength, "auto_scale": auto_scale}.items()
            if value is not None
        }

        result = self._sampler.sample(
            model_to_bqm(model),
            num_reads=num_reads,
            annealing_time=annealing_time,
            **ocean,
        )
        best = result.first
        qpu_timing: dict[str, Any] | None = result.info.get("timing") if hasattr(result, "info") else None

        return RunOutput(
            sample=sample_to_xqmx(model, dict(best.sample)),
            seed=None,
            reads=num_reads,
            params={
                "solver": self._solver_name,
                "qpu_timing": qpu_timing,
                "annealing_time": annealing_time,
                **ocean,
                "raw_energy": float(best.energy),
            },
        )
