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

1. Every aliased subnamespace (`xquad.asm`, `xquad.verifier`, `xquad.cp`,
   `xquad.sa`) *is* its peer module, through attribute access and dotted
   imports alike, and `xquad.types` re-exports the *identical* symbols
   -- no shadow classes, no forks. `import xquad` alone loads no peer.

2. A minimal encode → execute pipeline runs end-to-end through the
   umbrella imports alone, which catches regressions where a peer
   package's public surface changes without the umbrella keeping up.

3. Both VM backends (Rust and Python) produce identical results.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import xqcp as _xqcp
import xqffi as _xqffi
import xqsa as _xqsa
import xqsa.quip as _xqsa_quip
import xquad
import xqvm_py.vector as _xqvm_py_vector
import xqvm_py.xqmx as _xqvm_py_xqmx
from xqsa._deprecated import FLAT_QUIP_NAMES
from xquad import asm, cp, sa, types, vm


def test_aliases_are_the_peer_modules():
    """`xquad.<alias>` is the peer module itself, not a copy."""
    assert cp is _xqcp
    assert sa is _xqsa
    assert sa.quip is _xqsa_quip
    assert asm is _xqffi.asm
    assert xquad.verifier is _xqffi.verifier


def test_dotted_imports_resolve():
    """Each documented dotted import resolves to the peer's objects."""
    import xquad.asm
    import xquad.sa.quip
    from xquad.cp import Problem
    from xquad.sa.quip import SolverQuip
    from xquad.verifier import verify

    assert Problem is _xqcp.Problem
    assert SolverQuip is _xqsa_quip.SolverQuip
    assert verify is _xqffi.verifier.verify
    assert xquad.asm is _xqffi.asm
    assert sys.modules["xquad.sa.quip"] is _xqsa_quip


def test_import_xquad_loads_no_peer():
    """`import xquad` alone leaves the peer packages unimported."""
    code = "import sys, xquad; print(' '.join(m for m in ('xqcp', 'xqsa', 'xqvm_py') if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == ""


def test_dir_lists_every_subnamespace():
    """`dir(xquad)` lists every public name, loaded or not."""
    assert set(xquad.__all__) <= set(dir(xquad))


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        _ = xquad.nope


def test_identity_reexports():
    """xquad re-exports the identical symbols from the peer packages."""
    assert cp.Problem is _xqcp.Problem
    assert cp.Types is _xqcp.Types
    assert sa.SolverDWaveCPU is _xqsa.SolverDWaveCPU
    assert sa.SolverCudaGPU is _xqsa.SolverCudaGPU
    assert sa.SolverMetalGPU is _xqsa.SolverMetalGPU
    assert sa.Solver is _xqsa.Solver
    assert sa.SolverResult is _xqsa.SolverResult
    assert types.XQMX is _xqvm_py_xqmx.XQMX
    assert types.XQMXDomain is _xqvm_py_xqmx.XQMXDomain
    assert types.XQMXMode is _xqvm_py_xqmx.XQMXMode
    assert types.Vec is _xqvm_py_vector.Vec
    assert types.triu is _xqvm_py_xqmx.triu


def test_quip_subpackage_reexports():
    """`xquad.sa.quip` re-exports every `xqsa.quip` name by identity."""
    from xquad.sa import quip

    assert quip.__all__ == _xqsa_quip.__all__
    assert "Solution" in quip.__all__
    for name in _xqsa_quip.__all__:
        assert getattr(quip, name) is getattr(_xqsa_quip, name), name
    assert sa.quip is quip


# `xquad.sa` is `xqsa`, so its warning names `xqsa`; the second case guards the alias path.
@pytest.mark.parametrize(("flat", "home"), [(_xqsa, _xqsa_quip), (sa, sa.quip)], ids=["xqsa", "xquad.sa"])
@pytest.mark.parametrize("name", sorted(FLAT_QUIP_NAMES))
def test_flat_quip_name_warns_at_caller(flat, home, name):
    """A flat Quip name on `xqsa` or `xquad.sa` warns at the caller's line and is the `.quip` object."""
    with pytest.warns(DeprecationWarning) as record:
        line = sys._getframe().f_lineno + 1
        obj = getattr(flat, name)
    assert obj is getattr(home, name)
    message = f"{flat.__name__}.{name} is deprecated; import it from {home.__name__} (removed in v0.5.0)"
    if name in _xqsa_quip._DEPRECATED:  # retired in xqsa.quip too, so it also says why.
        message += f". {_xqsa_quip._DEPRECATED[name]}"
    assert [str(w.message) for w in record] == [message]
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
    assert xquad.verifier is _xqffi.verifier
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
