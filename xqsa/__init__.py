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
XQSA -- Solvers for XQMX quadratic models.

Provides pluggable solvers that take XQMX models and return
optimized samples. The first solver wraps DWave's CPU simulated annealer.
"""

from .cuda_gpu import SolverCudaGPU
from .dwave_cpu import SolverDWaveCPU
from .dwave_qpu import SolverDWaveQPU
from .metal_gpu import SolverMetalGPU
from .quip import (
    QuipConnectionError,
    QuipJobFailedError,
    QuipSubmissionError,
    QuipTimeoutError,
    QuipTopologyError,
    SolverQuip,
)
from .quip_codec import EncodingError, PlacementError, QuipError, QuipMetadataError, QuipSigningError
from .registry import DEFAULT_SOLVER, SOLVERS, build_solver
from .solver import Solver, SolverResult

__all__ = [
    "Solver",
    "SolverResult",
    "SolverCudaGPU",
    "SolverDWaveCPU",
    "SolverDWaveQPU",
    "SolverMetalGPU",
    "SolverQuip",
    "QuipError",
    "QuipConnectionError",
    "QuipMetadataError",
    "QuipSubmissionError",
    "QuipTimeoutError",
    "QuipTopologyError",
    "QuipJobFailedError",
    "QuipSigningError",
    "PlacementError",
    "EncodingError",
    "SOLVERS",
    "DEFAULT_SOLVER",
    "build_solver",
]
