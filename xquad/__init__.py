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

`xquad` is a thin meta-package that puts the peer distributions
(`xqffi`, `xqcp`, `xqsa`) under one surface so users can write a single
set of imports for the full encode → sample → verify → decode
pipeline::

    from xquad import vm, asm, cp, sa

    problem = cp.Problem("TSP")
    bytecode = asm.assemble_source(...)
    v = vm.VM()
    v.run_bytecode(bytecode)
    samples = sa.SolverCPU().solve(problem.model())

`cp`, `sa`, `asm` and `verifier` are aliases, not copies: `xquad.cp` *is*
`xqcp`, `xquad.sa` *is* `xqsa`, `xquad.asm` *is* `xqffi.asm` and
`xquad.verifier` *is* `xqffi.verifier`. An import finder maps every
dotted import under an alias to its target, so `import xquad.cp` and
`from xquad.sa.quip import SolverQuip` resolve to the same module
objects. `types`, `vm` and `program` are this package's own modules.

Nothing loads until it is first used: `import xquad` imports none of
the peer packages, which keeps the `xquad` command's startup short.
Users wanting a subset can still `pip install xqffi xqcp xqsa`
individually; `pip install xquad` is the convenience path.
"""

from __future__ import annotations

import importlib
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import xqcp as cp
    import xqsa as sa
    from xqffi import asm, verifier
    from xquad import program, types, vm

_ALIASES = {"cp": "xqcp", "sa": "xqsa", "asm": "xqffi.asm", "verifier": "xqffi.verifier"}
_LAZY = ("types", "vm", "program")

__all__ = ["asm", "cp", "program", "sa", "types", "verifier", "vm"]


class _AliasLoader:
    """Return the target module itself, so `xquad.<alias>[.rest]` *is* `<target>[.rest]`."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        module = importlib.import_module(self._target)
        self._target_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # The import system has just set the alias's spec on the target; give it its own back, or
        # `importlib.reload` and `importlib.resources` see a spec with no origin and no real loader.
        # The PyO3 submodules (`xqffi.asm`, `xqffi.verifier`) have none, so they keep the alias spec
        # for `find_spec`; `importlib.reload` does not work on them either way.
        if self._target_spec is not None:
            module.__spec__ = self._target_spec


class _AliasFinder:
    """Map `xquad.<alias>[.rest]` to `<target>[.rest]`; every other name falls through."""

    @staticmethod
    def find_spec(fullname: str, path: object = None, target: object = None) -> ModuleSpec | None:
        head, _, rest = fullname.removeprefix(f"{__name__}.").partition(".")
        if not fullname.startswith(f"{__name__}.") or head not in _ALIASES:
            return None
        return ModuleSpec(fullname, _AliasLoader(_ALIASES[head] + (f".{rest}" if rest else "")))


# First on the path: the default finder would otherwise load `xquad.sa.quip`
# a second time from `xqsa.__path__` instead of returning `xqsa.quip`.
if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())


def __getattr__(name: str) -> ModuleType:
    """Import a subnamespace on first access (PEP 562)."""
    if name in _ALIASES or name in _LAZY:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | {n for n in globals() if n.startswith("__")})
