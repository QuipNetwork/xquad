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

Maps short, CLI-friendly names to solver factories so example runners and the
smoke harness can pick a backend with a single ``--solver`` flag instead of
hard-coding a class. Importing the classes is cheap and dependency-free; the
optional deps (cupy, Metal, dwave-system, substrate-interface/quip_signer) are
pulled in only when a solver is constructed or run, so an unavailable backend
fails at build time with an actionable message rather than at import.

``quip`` is the network-mempool backend (:class:`~xqsa.quip.SolverQuip`). Unlike
the local solvers it is configured from environment variables (``QUIP_RPC_URL``
plus a signer; see :mod:`xqsa.quip`), takes no RNG ``seed``, and requires the
``[quip]`` extra (``substrate-interface`` plus the ``quip_signer`` extension).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from xqsa.quip import SolverQuip
from xqsa.solver import Solver
from xqsa.solvers import SolverCPU, SolverDWave, SolverGPU

# CLI name -> solver factory. ``cpu`` is the default everywhere: it needs no
# extra deps or hardware, so it is the reproducible baseline against which
# the hardware backends are compared. ``cuda`` and ``metal`` both build
# :class:`SolverGPU`. ``quip`` is env-driven (no seed); see the module
# docstring.
SOLVERS: dict[str, Callable[..., Solver]] = {
    "cpu": SolverCPU,
    "cuda": partial(SolverGPU, engine="cuda"),
    "metal": partial(SolverGPU, engine="metal"),
    "dwave": SolverDWave,
    "quip": SolverQuip,
}

DEFAULT_SOLVER = "cpu"

# Solvers whose constructor accepts a `seed`. The classical SA family is
# seedable for reproducibility; the D-Wave QPU is physical hardware with no
# seed, so it is deliberately excluded.
_SEEDED: frozenset[str] = frozenset({"cpu", "cuda", "metal"})


def build_solver(name: str, *, seed: int | None = None) -> Solver:
    """Construct a solver by registry name.

    Args:
        name: A key in :data:`SOLVERS` (e.g. ``"cpu"``, ``"cuda"``).
        seed: Seed for reproducibility, forwarded only to solvers whose
            constructor accepts it. The D-Wave QPU is physical hardware and
            takes no seed; ``quip`` is env-configured and takes no seed -- both
            ignore it.

    Returns:
        A constructed :class:`Solver` instance. ``quip`` reads its endpoint and
        signer from the environment (and needs the ``[quip]`` extra, which
        provides ``substrate-interface`` and the ``quip_signer`` extension), so
        constructing it without that configuration raises from the constructor.

    Raises:
        ValueError: If ``name`` is not a registered solver.
    """
    if name not in SOLVERS:
        raise ValueError(f"Unknown solver {name!r}. Choose one of: {', '.join(sorted(SOLVERS))}.")
    factory = SOLVERS[name]
    if name in ("cuda", "metal"):
        return factory(num_reads=200, num_sweeps=2000, seed=seed)
    if name in _SEEDED:
        return factory(seed=seed)
    return factory()
