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

"""Smoke tests for the xquad umbrella meta-package.

Guards three invariants:

1. Every umbrella subnamespace (`xquad.vm`, `xquad.asm`, `xquad.cp`,
   `xquad.sa`, `xquad.types`) re-exports the *identical* symbols from
   its peer package — no shadow classes, no forks.

2. A minimal encode → execute pipeline runs end-to-end through the
   umbrella imports alone, which catches regressions where a peer
   package's public surface changes without the umbrella keeping up.

3. Both VM backends (Rust and Python) produce identical results.
"""

from __future__ import annotations

import xqcp as _xqcp
import xqsa as _xqsa
import xquad
import xqvm_py.vector as _xqvm_py_vector
import xqvm_py.xqmx as _xqvm_py_xqmx
from xquad import asm, cp, sa, types, vm


def test_identity_reexports():
    """xquad re-exports the identical symbols from the peer packages."""
    assert cp.Problem is _xqcp.Problem
    assert cp.Types is _xqcp.Types
    assert sa.SolverDWaveCPU is _xqsa.SolverDWaveCPU
    assert sa.SolverCudaGPU is _xqsa.SolverCudaGPU
    assert sa.Solver is _xqsa.Solver
    assert sa.SolverResult is _xqsa.SolverResult
    assert types.XQMX is _xqvm_py_xqmx.XQMX
    assert types.XQMXDomain is _xqvm_py_xqmx.XQMXDomain
    assert types.XQMXMode is _xqvm_py_xqmx.XQMXMode
    assert types.Vec is _xqvm_py_vector.Vec
    assert types.triu is _xqvm_py_xqmx.triu


def test_top_level_namespace():
    """`import xquad` resolves every subnamespace lazily."""
    assert xquad.vm is vm
    assert xquad.asm is asm
    assert xquad.cp is cp
    assert xquad.sa is sa
    assert xquad.types is types
    assert hasattr(xquad, "program")


def test_end_to_end_rust():
    """assemble → run → read outputs through the Rust backend."""
    src = "PUSH 7\nPUSH 5\nADD\nSTOW r0\nPUSH 0\nOUTPUT r0\nHALT\n"
    v = vm.VM(backend=vm.VMBackend.RUST)
    v.set_output_slots(1)
    v.run(src)
    assert v.outputs() == [12]
    assert v.stack() == []


def test_end_to_end_python():
    """parse → run → read outputs through the Python backend."""
    src = "PUSH 7\nPUSH 5\nADD\nSTOW r0\nPUSH 0\nOUTPUT r0\nHALT\n"
    v = vm.VM(backend=vm.VMBackend.PYTHON)
    v.set_output_slots(1)
    v.run(src)
    assert v.outputs() == [12]
