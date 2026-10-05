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
#
# Fault docstring guard (QUI-1506).
#
# `xqffi/python/xqffi/vm.pyi` restates, by hand, the docstring of every
# fault class `xqffi/src/fault.rs` declares. `stubtest` (the other half of
# `make check-stubs`) compares signatures and leaves docstrings alone, so
# without this check a fault reworded in `fault.rs` would leave type
# checkers and IDEs showing the old text with nothing failing.
#
# The runtime side is the built `xqffi.vm` module rather than `fault.rs`
# itself: `create_exception!` copies each doc literal into the class's
# `__doc__`, so the module is `fault.rs` after the compiler has parsed it,
# and reading it here needs no Rust macro parser. `make check-stubs`
# rebuilds the extension first, so the module is the current tree's.
#
# Line wrapping is not compared. A Rust literal joined with `\` and a
# triple-quoted stub docstring wrap the same prose differently, so both
# sides are reduced to their paragraphs with whitespace runs collapsed;
# the words and the paragraph breaks must match exactly.

"""Check that the fault docstrings in `vm.pyi` match the built `xqffi.vm`."""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
STUB = REPO_ROOT / "xqffi" / "python" / "xqffi" / "vm.pyi"
BASE = "XqvmError"


def normalise(doc: str) -> str:
    """Reduce `doc` to its paragraphs, each with whitespace runs collapsed."""

    paragraphs = inspect.cleandoc(doc).split("\n\n")
    return "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())


def stub_docstrings(source: str) -> dict[str, str]:
    """Map `XqvmError` and each class the stub derives from it to its docstring.

    Only direct subclasses are found, which is every fault `fault.rs`
    declares. A class with no docstring maps to the empty string.

    Raises `SyntaxError` when `source` does not parse.
    """

    docs: dict[str, str] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {base.id for base in node.bases if isinstance(base, ast.Name)}
        if node.name == BASE or BASE in bases:
            docs[node.name] = ast.get_docstring(node) or ""
    return docs


def runtime_docstrings(module: ModuleType) -> dict[str, str]:
    """Map `XqvmError` and every exported subclass of it to its `__doc__`."""

    base = getattr(module, BASE)
    return {
        name: obj.__doc__ or "" for name, obj in vars(module).items() if isinstance(obj, type) and issubclass(obj, base)
    }


def mismatches(stub: dict[str, str], runtime: dict[str, str]) -> list[str]:
    """Describe every fault whose docstring differs, is missing, or is empty."""

    problems: list[str] = []
    for name in sorted(stub.keys() | runtime.keys()):
        if name not in stub:
            problems.append(f"{name}: raised by xqffi.vm but has no class in vm.pyi")
        elif name not in runtime:
            problems.append(f"{name}: declared in vm.pyi but not exported by xqffi.vm")
        elif not normalise(runtime[name]):
            problems.append(f"{name}: has no docstring in xqffi/src/fault.rs")
        elif normalise(stub[name]) != normalise(runtime[name]):
            problems.append(
                f"{name}: vm.pyi docstring differs from xqffi/src/fault.rs\n"
                f"    stub:    {normalise(stub[name])!r}\n"
                f"    runtime: {normalise(runtime[name])!r}"
            )
    return problems


def main() -> int:
    """Compare the stub against the built module; return the exit status."""

    import xqffi.vm

    try:
        stub = stub_docstrings(STUB.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as error:
        print(f"check-fault-docstrings: cannot read {STUB}: {error}", file=sys.stderr)
        return 1

    problems = mismatches(stub, runtime_docstrings(xqffi.vm))
    for problem in problems:
        print(f"check-fault-docstrings: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"check-fault-docstrings: {len(stub)} fault docstrings match xqffi/src/fault.rs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
