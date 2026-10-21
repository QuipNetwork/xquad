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
optimized samples. SolverCPU, SolverGPU and SolverDWave run in-process;
xqsa.quip.SolverQuip submits to the Quip network.
"""

from . import quip as quip
from ._deprecated import flat_quip_dir, flat_quip_getattr
from .solver import Solver, SolverResult
from .solvers import DEFAULT_SOLVER, SOLVERS, SolverCPU, SolverDWave, SolverGPU, build_solver

__all__ = [
    "Solver",
    "SolverResult",
    "SolverCPU",
    "SolverGPU",
    "SolverDWave",
    "SOLVERS",
    "DEFAULT_SOLVER",
    "build_solver",
]

# The Quip names live in xqsa.quip; the flat ones warn until v0.5.0 (QUI-1608).
__getattr__ = flat_quip_getattr(__name__, "xqsa.quip")
__dir__ = flat_quip_dir(__name__)
