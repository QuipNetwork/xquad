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

"""Cross-check the Python reference opcode table against conformance/opcodes.yaml.

The Rust side is checked at compile time by xqvm/build.rs. This script is
the Python counterpart: it loads the canonical YAML and compares every
(code, mnemonic, stack_pop, stack_push, operand_count, operand_types)
tuple against xqvm_py/opcodes.py. Any mismatch prints a
diff-style report and the script exits 1.

Intended to be invoked from the `opcode-parity` CI job and the
`make opcode-parity` Makefile target.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = REPO_ROOT / "conformance" / "opcodes.yaml"


YAML_TYPE_TO_PYTHON = {"register": "REGISTER", "label": "TARGET", "immediate": "IMMEDIATE"}


@dataclass(frozen=True)
class Row:
    """Normalised opcode description for comparison."""

    code: int
    mnemonic: str
    stack_pop: int
    stack_push: int
    operand_byte_width: int
    operand_types: tuple[str, ...]

    def format_line(self) -> str:
        types = ",".join(self.operand_types) if self.operand_types else "-"
        return (
            f"{self.code:#04x} {self.mnemonic:<8} "
            f"pop={self.stack_pop} push={self.stack_push} "
            f"operand_bytes={self.operand_byte_width} types={types}"
        )


def load_yaml_rows(path: Path) -> dict[int, Row]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rows: dict[int, Row] = {}
    for entry in data["opcodes"]:
        code = int(entry["code"])
        operand_byte_width = sum(int(op.get("width", 1)) for op in entry["operands"])
        operand_types: tuple[str, ...] = ()
        for op in entry["operands"]:
            py_type = YAML_TYPE_TO_PYTHON[op["type"]]
            operand_types += (py_type,) * int(op.get("width", 1))
        rows[code] = Row(
            code=code,
            mnemonic=entry["mnemonic"],
            stack_pop=int(entry["stack_pop"]),
            stack_push=int(entry["stack_push"]),
            operand_byte_width=operand_byte_width,
            operand_types=operand_types,
        )
    return rows


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
    yaml_rows = load_yaml_rows(YAML_PATH)
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
