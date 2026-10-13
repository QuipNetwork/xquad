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
"""

from __future__ import annotations

from typing import Any

from .dwave_cpu import InProcessCPU
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
            from .cuda_gpu import InProcessCuda as impl
        elif engine == "metal":
            from .metal_gpu import InProcessMetal as impl
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
        from .dwave_qpu import InProcessDWave

        super().__init__(InProcessDWave(**params), mode=mode)
