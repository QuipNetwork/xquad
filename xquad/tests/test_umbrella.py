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

3. `xquad.vm` has no backend switch: it runs on the Rust VM only.
"""

from __future__ import annotations

import sys

import pytest

import xqcp as _xqcp
import xqffi.vm as _xqffi_vm
import xqsa as _xqsa
import xqsa.quip as _xqsa_quip
import xquad
from xqsa._deprecated import FLAT_QUIP_NAMES
from xquad import asm, cp, sa, types, vm


def test_identity_reexports():
    """xquad re-exports the identical symbols from the peer packages."""
    assert cp.Problem is _xqcp.Problem
    assert cp.Types is _xqcp.Types
    assert sa.SolverDWaveCPU is _xqsa.SolverDWaveCPU
    assert sa.SolverCudaGPU is _xqsa.SolverCudaGPU
    assert sa.SolverMetalGPU is _xqsa.SolverMetalGPU
    assert sa.Solver is _xqsa.Solver
    assert sa.SolverResult is _xqsa.SolverResult
    for name in types.__all__:
        assert getattr(types, name) is getattr(_xqffi_vm, name), name


def test_quip_subpackage_reexports():
    """`xquad.sa.quip` re-exports every `xqsa.quip` name by identity."""
    from xquad.sa import quip

    assert quip.__all__ == _xqsa_quip.__all__
    assert "Solution" in quip.__all__
    for name in _xqsa_quip.__all__:
        assert getattr(quip, name) is getattr(_xqsa_quip, name), name
    assert sa.quip is quip


@pytest.mark.parametrize(("flat", "home"), [(_xqsa, _xqsa_quip), (sa, sa.quip)], ids=["xqsa", "xquad.sa"])
@pytest.mark.parametrize("name", sorted(FLAT_QUIP_NAMES))
def test_flat_quip_name_warns_at_caller(flat, home, name):
    """A flat Quip name on `xqsa` or `xquad.sa` warns at the caller's line and is the `.quip` object."""
    with pytest.warns(DeprecationWarning) as record:
        line = sys._getframe().f_lineno + 1
        obj = getattr(flat, name)
    assert obj is getattr(home, name)
    assert [str(w.message) for w in record] == [
        f"{flat.__name__}.{name} is deprecated; import it from {home.__name__} (removed in v0.5.0)"
    ]
    assert (record[0].filename, record[0].lineno) == (__file__, line)
    assert name not in flat.__all__
    assert name in dir(flat)


def test_top_level_namespace():
    """`import xquad` resolves every subnamespace lazily."""
    assert xquad.vm is vm
    assert xquad.asm is asm
    assert xquad.cp is cp
    assert xquad.sa is sa
    assert xquad.types is types
    assert hasattr(xquad, "program")


def test_end_to_end():
    """assemble → run → read outputs through the umbrella VM."""
    src = "PUSH 7\nPUSH 5\nADD\nSTOW r0\nPUSH 0\nOUTPUT r0\nHALT\n"
    v = vm.VM()
    v.set_output_slots(1)
    v.run(src)
    assert v.outputs() == [12]
    assert v.stack() == []
