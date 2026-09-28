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
Abstract solver base class and result types for XQMX quadratic models.

Solvers implement the solve() method to find low-energy solutions
for XQMX models using different optimization strategies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import dimod

from xqffi.vm import Domain, XqmxModel, XqmxSample


@dataclass(frozen=True)
class SolverResult:
    """Result from a solver."""

    sample: XqmxSample
    energy: int
    timing: float
    metadata: dict[str, Any] = field(default_factory=dict)


class Solver(ABC):
    """Abstract solver base class for XQMX quadratic models."""

    @abstractmethod
    def solve(self, model: XqmxModel, **kwargs: Any) -> SolverResult:
        """Solve a quadratic model, returning the best solution found."""
        ...

    def _validate_model(self, model: XqmxModel) -> None:
        """Validate that the model is solvable."""
        if not isinstance(model, XqmxModel):
            raise ValueError(f"Expected an XqmxModel, got {type(model).__name__}")
        if model.domain not in (Domain.BINARY, Domain.SPIN):
            raise ValueError(f"Unsupported domain for solving: {model.domain.name}")

    def _model_to_bqm(self, model: XqmxModel) -> dimod.BinaryQuadraticModel:
        """Convert an XQMX model to a dimod BQM."""
        vartype = dimod.BINARY if model.domain == Domain.BINARY else dimod.SPIN
        return dimod.BinaryQuadraticModel(
            dict(model.linear_items()),
            dict(model.quadratic_items()),
            0.0,
            vartype,
        )

    def _sample_to_xqmx(self, model: XqmxModel, raw_sample: dict[int, int]) -> XqmxSample:
        """Convert a dimod sample dict to an XQMX sample.

        dimod omits variables with no terms; those keep the domain default.
        """
        sample = XqmxSample.default(model.domain, model.size, model.rows, model.cols)
        for var_idx, value in raw_sample.items():
            sample.set_linear(int(var_idx), int(value))

        return sample

    def _recompute_energy(self, model: XqmxModel, sample: XqmxSample) -> int:
        """Compute authoritative integer energy for a model-sample pair."""
        return model.energy(sample)
