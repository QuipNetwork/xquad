#!/usr/bin/env python3
# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
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

"""Cross-check the Python reference opcode table against xqvm/opcodes.yaml.

The Rust side is checked at compile time by xqvm/build.rs. This script is
the Python counterpart: it loads the canonical YAML and compares every
(code, mnemonic, stack_pop, stack_push, stack_reset, operand_count,
operand_types) tuple against xqvm_py/opcodes.py. Any mismatch prints a
diff-style report and the script exits 1.

This side compares stack_pop and stack_push separately. The Rust check
cannot: the opcodes! x-macro stores only the net delta, so xqvm/build.rs
narrows the pair before comparing. The pop/push split is therefore pinned
here and nowhere else.

Intended to be invoked from the `opcode-parity` CI job and the
`make opcode-parity` Makefile target.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from _scriptio import SetupError, load_yaml, require_key, require_mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = REPO_ROOT / "xqvm" / "opcodes.yaml"


YAML_TYPE_TO_PYTHON = {"register": "REGISTER", "label": "TARGET", "immediate": "IMMEDIATE"}


@dataclass(frozen=True)
class Row:
    """Normalised opcode description for comparison."""

    code: int
    mnemonic: str
    stack_pop: int
    stack_push: int
    stack_reset: bool
    operand_byte_width: int
    operand_types: tuple[str, ...]

    def format_line(self) -> str:
        types = ",".join(self.operand_types) if self.operand_types else "-"
        return (
            f"{self.code:#04x} {self.mnemonic:<8} "
            f"pop={self.stack_pop} push={self.stack_push} reset={self.stack_reset} "
            f"operand_bytes={self.operand_byte_width} types={types}"
        )


def load_yaml_rows(path: Path) -> dict[int, Row]:
    data = load_yaml(path)
    opcodes = require_key(data, path, "opcodes", list)
    rows: dict[int, Row] = {}
    for index, value in enumerate(opcodes):
        entry_path = f"{path}: opcodes[{index}]"
        entry = require_mapping(value, entry_path)
        operands = require_key(entry, entry_path, "operands", list)
        code = int(require_key(entry, entry_path, "code", int))
        operand_maps: list[tuple[str, Mapping[str, object]]] = []
        for operand_index, value in enumerate(operands):
            op_path = f"{entry_path}.operands[{operand_index}]"
            operand_maps.append((op_path, require_mapping(value, op_path)))
        operand_byte_width = sum(_operand_width(op, op_path) for op_path, op in operand_maps)
        operand_types: tuple[str, ...] = ()
        for op_path, op in operand_maps:
            py_type = YAML_TYPE_TO_PYTHON[require_key(op, op_path, "type", str)]
            operand_types += (py_type,) * _operand_width(op, op_path)
        rows[code] = Row(
            code=code,
            mnemonic=require_key(entry, entry_path, "mnemonic", str),
            stack_pop=int(require_key(entry, entry_path, "stack_pop", int)),
            stack_push=int(require_key(entry, entry_path, "stack_push", int)),
            stack_reset=_stack_reset(entry, entry_path),
            operand_byte_width=operand_byte_width,
            operand_types=operand_types,
        )
    return rows


def _stack_reset(entry: Mapping[str, object], path: str) -> bool:
    """Read the optional `stack_reset` flag, defaulting to false.

    Optional rather than required so the 92 opcodes with a fixed stack
    effect stay unannotated; only SCLR carries it.
    """
    reset = entry.get("stack_reset", False)
    if not isinstance(reset, bool):
        raise SetupError(f"{path}: optional key `stack_reset` must be bool, got {type(reset).__name__}")
    return reset


def _operand_width(op: Mapping[str, object], path: str) -> int:
    width = op.get("width", 1)
    if not isinstance(width, int):
        raise SetupError(f"{path}: optional key `width` must be int, got {type(width).__name__}")
    return width


def load_python_rows() -> dict[int, Row]:
    """Import xqvm_py's Opcode enum and convert to Row entries."""
    # The repo root sits on sys.path so ``xqvm_py`` resolves to the
    # flat-layout package directory (the repo root / package directory
    # being identical after QUI-440). We don't need the maturin-built
    # ``xqffi`` for this check — only the pure-Python opcode table.
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from xqvm_py.opcodes import Opcode  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    rows: dict[int, Row] = {}
    for op in Opcode:
        meta = op.value
        rows[meta.code] = Row(
            code=meta.code,
            mnemonic=op.name,
            stack_pop=meta.stack_pop,
            stack_push=meta.stack_push,
            stack_reset=meta.stack_reset,
            operand_byte_width=meta.operand_count,
            operand_types=tuple(t.name for t in meta.operand_types),
        )
    return rows


def diff(yaml_rows: dict[int, Row], py_rows: dict[int, Row]) -> list[str]:
    errors: list[str] = []

    yaml_codes = set(yaml_rows)
    py_codes = set(py_rows)
    for code in sorted(yaml_codes - py_codes):
        errors.append(f"MISSING in xqvm_py: {yaml_rows[code].format_line()}")
    for code in sorted(py_codes - yaml_codes):
        errors.append(f"MISSING in opcodes.yaml: {py_rows[code].format_line()}")

    for code in sorted(yaml_codes & py_codes):
        y = yaml_rows[code]
        p = py_rows[code]
        if y != p:
            errors.append(f"MISMATCH at {code:#04x}:")
            errors.append(f"  yaml: {y.format_line()}")
            errors.append(f"  py:   {p.format_line()}")
    return errors


def main() -> int:
    try:
        yaml_rows = load_yaml_rows(YAML_PATH)
    except SetupError as exc:
        print(f"opcode parity setup error: {exc}", file=sys.stderr)
        return 2
    py_rows = load_python_rows()

    errors = diff(yaml_rows, py_rows)
    if errors:
        print("Opcode parity check FAILED:\n", file=sys.stderr)
        for line in errors:
            print(line, file=sys.stderr)
        print(
            f"\nCompared {len(yaml_rows)} yaml entries vs {len(py_rows)} xqvm_py entries.",
            file=sys.stderr,
        )
        return 1

    print(f"opcode parity OK ({len(yaml_rows)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
