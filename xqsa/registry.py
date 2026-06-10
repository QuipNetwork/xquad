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
Solver registry and factory for selecting an XQSA backend by name.

Maps short, CLI-friendly names to solver classes so example runners and the
smoke harness can pick a backend with a single ``--solver`` flag instead of
hard-coding a class. Importing the classes is cheap and dependency-free; the
optional hardware deps (cupy, Metal, dwave-system) are pulled in only when a
solver is constructed or run, so an unavailable backend fails at build time
with an actionable message rather than at import.
"""

from __future__ import annotations

from xqsa.cuda_gpu import SolverCudaGPU
from xqsa.dwave_cpu import SolverDWaveCPU
from xqsa.dwave_qpu import SolverDWaveQPU
from xqsa.metal_gpu import SolverMetalGPU
from xqsa.solver import Solver

# CLI name -> solver class. ``dwave-cpu`` is the default everywhere: it needs
# no extra deps or hardware, so it is the reproducible baseline against which
# the hardware backends are compared.
SOLVERS: dict[str, type[Solver]] = {
    "dwave-cpu": SolverDWaveCPU,
    "dwave-qpu": SolverDWaveQPU,
    "cuda": SolverCudaGPU,
    "metal": SolverMetalGPU,
}

DEFAULT_SOLVER = "dwave-cpu"

# Solvers whose constructor accepts a `seed`. The classical SA family is
# seedable for reproducibility; the D-Wave QPU is physical hardware with no
# seed, so it is deliberately excluded.
_SEEDED: frozenset[str] = frozenset({"dwave-cpu", "cuda", "metal"})


def build_solver(name: str, *, seed: int | None = None) -> Solver:
    """Construct a solver by registry name.

    Args:
        name: A key in :data:`SOLVERS` (e.g. ``"dwave-cpu"``, ``"cuda"``).
        seed: Seed for reproducibility, forwarded only to solvers whose
            constructor accepts it. The D-Wave QPU is physical hardware and
            takes no seed, so it is ignored there.

    Returns:
        A constructed :class:`Solver` instance.

    Raises:
        ValueError: If ``name`` is not a registered solver.
    """
    if name not in SOLVERS:
        raise ValueError(f"Unknown solver {name!r}. Choose one of: {', '.join(sorted(SOLVERS))}.")
    cls = SOLVERS[name]
    if name in _SEEDED:
        return cls(seed=seed)
    return cls()
