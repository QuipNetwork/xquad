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
The public solver classes: :class:`SolverCPU`, :class:`SolverGPU` and
:class:`SolverDWave`.

Each one picks an :class:`~xqsa.solver.Implementation` from its selectors and
passes every other keyword through to it, so an unknown parameter raises
``TypeError`` when the solver is built. ``solve()`` is the
:class:`~xqsa.solver.Solver` template.

The module also holds the registry: :data:`SOLVERS` maps short, CLI-friendly
names to solver factories, and :func:`build_solver` builds one by name, so
example runners and the smoke harness can pick a backend with a single
``--solver`` flag instead of hard-coding a class. Importing the classes is cheap and dependency-free; the
optional deps (cupy, Metal, dwave-system, substrate-interface/quip_signer) are
pulled in only when a solver is constructed or run, so an unavailable backend
fails at build time with an actionable message rather than at import.

``quip`` is the network-mempool backend (:class:`~xqsa.quip.SolverQuip`). Unlike
the local solvers it is configured from environment variables (``QUIP_RPC_URL``
plus a signer; see :mod:`xqsa.quip`), takes no RNG ``seed``, and requires the
``[quip]`` extra (``substrate-interface`` plus the ``quip_signer`` extension).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial
from typing import Any

from .binary import BinaryImplementation
from .inprocess.cpu import InProcessCPU
from .quip import SolverQuip
from .solver import Implementation, Solver


class SolverCPU(Solver):
    """Simulated annealing on CPU.

    ``algorithm`` is ``"sa"``, the only algorithm that runs in-process.

    Examples:

    ```python
    from xqsa import SolverCPU
    from xquad.types import XqmxModel

    model = XqmxModel.binary(4)
    model.set_linear(0, -1)
    model.set_quad(0, 1, 2)

    result = SolverCPU(num_reads=50, seed=42).solve(model)
    print(result.energy, result.metadata["params"]["algorithm"])
    ```
    """

    def __init__(self, *, algorithm: str = "sa", **params: Any) -> None:
        super().__init__(InProcessCPU(algorithm=algorithm, **params), algorithm=algorithm)

    @classmethod
    def _binary(cls, path: str | os.PathLike[str], *, algorithm: str = "sa", **params: Any) -> SolverCPU:
        """Build a SolverCPU on a solver-core binary instead of in-process code. Private: tests only until QUI-1637."""
        self = cls.__new__(cls)
        Solver.__init__(self, BinaryImplementation(path, algorithm=algorithm, **params), algorithm=algorithm)
        return self


class SolverGPU(Solver):
    """Parallel-replica sampling on a GPU.

    ``engine`` is required: ``"cuda"`` (NVIDIA, through CuPy) or ``"metal"``
    (Apple Silicon). ``algorithm`` is ``"sa"`` on both engines, or
    ``"gibbs"`` on Metal.

    Examples:

    ```python
    from xqsa import SolverGPU
    from xquad.types import XqmxModel

    model = XqmxModel.binary(4)
    model.set_linear(0, -1)
    model.set_quad(0, 1, 2)

    solver = SolverGPU(engine="metal", algorithm="gibbs", num_reads=200, seed=42)
    result = solver.solve(model)
    print(result.energy, result.metadata["params"]["engine"])
    ```

    Raises:
        ValueError: if ``engine`` or ``algorithm`` is not supported.
        ImportError: if the engine's optional dependency is not installed.
        RuntimeError: if the engine finds no GPU.
    """

    def __init__(self, *, engine: str, algorithm: str = "sa", **params: Any) -> None:
        impl: type[Implementation]
        if engine == "cuda":
            from .inprocess.cuda import InProcessCuda as impl
        elif engine == "metal":
            from .inprocess.metal import InProcessMetal as impl
        else:
            raise ValueError(f"Unsupported engine {engine!r}. Supported: ['cuda', 'metal']")
        super().__init__(impl(algorithm=algorithm, **params), engine=engine, algorithm=algorithm)


class SolverDWave(Solver):
    """Sampling on D-Wave hardware.

    ``mode`` is ``"qpu"``, a D-Wave Advantage QPU through the Leap cloud API.
    Of the Ocean sampling parameters, only ``chain_strength`` and
    ``auto_scale`` are forwarded; each one set is recorded in ``params``.

    Examples:

    ```python
    import os
    os.environ["DWAVE_API_TOKEN"] = "your-leap-token"

    from xqsa import SolverDWave
    from xquad.types import XqmxModel

    model = XqmxModel.binary(4)
    model.set_linear(0, -1)
    model.set_quad(0, 1, 2)

    result = SolverDWave(chain_strength=2.0).solve(model)
    print(result.energy, result.metadata["params"]["qpu_timing"])
    ```

    Raises:
        ValueError: if ``mode`` is not supported, or no API token is available.
        ImportError: if ``dwave-system`` is not installed.
    """

    def __init__(self, *, mode: str = "qpu", **params: Any) -> None:
        if mode != "qpu":
            raise ValueError(f"Unsupported mode {mode!r}. Supported: ['qpu']; hybrid modes are not available yet.")
        from .inprocess.dwave import InProcessDWave

        super().__init__(InProcessDWave(**params), mode=mode)


# CLI name -> solver factory. ``cpu`` is the default everywhere: it needs no
# extra deps or hardware, so it is the reproducible baseline against which
# the hardware backends are compared. ``cuda`` and ``metal`` both build
# :class:`SolverGPU`, with a larger read and sweep budget than its defaults.
# ``quip`` is env-driven (no seed); see the module docstring.
SOLVERS: dict[str, Callable[..., Solver]] = {
    "cpu": SolverCPU,
    "cuda": partial(SolverGPU, engine="cuda", num_reads=200, num_sweeps=2000),
    "metal": partial(SolverGPU, engine="metal", num_reads=200, num_sweeps=2000),
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
    if name in _SEEDED:
        return factory(seed=seed)
    return factory()
