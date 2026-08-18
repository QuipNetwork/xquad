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
XQuad — umbrella namespace for the full toolchain.

`xquad` is a thin meta-package that re-exports the peer distributions
(`xqffi`, `xqcp`, `xqsa`) under a unified surface so users can write
a single set of imports for the full encode → sample → verify → decode
pipeline::

    from xquad import vm, asm, cp, sa

    problem = cp.Problem("TSP")
    bytecode = asm.assemble_source(...)
    v = vm.VM()
    v.run_bytecode(bytecode)
    samples = sa.SolverDWaveCPU().solve(problem.model())

Each subnamespace is a direct re-export of the corresponding peer
package, so `xquad.cp.Problem is xqcp.Problem` (identity, not copy).
Users wanting a subset can still `pip install xqffi xqcp xqsa`
individually; `pip install xquad` is the convenience path.
"""

from xquad import asm, cp, program, sa, types, verifier, vm

__all__ = ["asm", "cp", "program", "sa", "types", "verifier", "vm"]
