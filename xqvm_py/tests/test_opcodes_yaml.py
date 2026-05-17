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

"""YAML-backed opcode parity test.

Complements ``scripts/check-opcode-parity.py`` (which runs in CI and
compares Python opcodes against ``conformance/opcodes.yaml``) with a
pytest-integrated variant so local ``pytest xqvm_py/tests`` catches
drift the same way. The check is kept intentionally narrow — it
compares the fields both schemas describe:

- ``code``            — u8 wire encoding
- ``mnemonic``        — uppercase assembly name
- ``stack_pop`` / ``stack_push`` — stack effect
- operand byte width — sum of ``operands[].width`` in the YAML,
  compared against the Python ``OpcodeMeta.operand_count``
- operand types      — YAML types (register/label/immediate) mapped to
  Python OperandType names (REGISTER/TARGET/IMMEDIATE), with each YAML
  operand expanded by its width to match the per-byte Python tuple.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from xqvm_py.opcodes import Opcode

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "conformance" / "opcodes.yaml"

_YAML_TYPE_TO_PYTHON = {"register": "REGISTER", "label": "TARGET", "immediate": "IMMEDIATE"}


def _load_yaml_rows() -> dict[int, dict]:
    with YAML_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rows: dict[int, dict] = {}
    for entry in data["opcodes"]:
        code = int(entry["code"])
        operand_types: tuple[str, ...] = ()
        for op in entry["operands"]:
            py_type = _YAML_TYPE_TO_PYTHON[op["type"]]
            operand_types += (py_type,) * int(op.get("width", 1))
        rows[code] = {
            "code": code,
            "mnemonic": entry["mnemonic"],
            "stack_pop": int(entry["stack_pop"]),
            "stack_push": int(entry["stack_push"]),
            "operand_byte_width": sum(int(op.get("width", 1)) for op in entry["operands"]),
            "operand_types": operand_types,
        }
    return rows


@pytest.fixture(scope="module")
def yaml_rows() -> dict[int, dict]:
    return _load_yaml_rows()


@pytest.fixture(scope="module")
def py_rows() -> dict[int, dict]:
    rows: dict[int, dict] = {}
    for op in Opcode:
        meta = op.value
        rows[meta.code] = {
            "code": meta.code,
            "mnemonic": op.name,
            "stack_pop": meta.stack_pop,
            "stack_push": meta.stack_push,
            "operand_byte_width": meta.operand_count,
            "operand_types": tuple(t.name for t in meta.operand_types),
        }
    return rows


def test_opcode_counts_match(yaml_rows: dict[int, dict], py_rows: dict[int, dict]) -> None:
    assert len(yaml_rows) == len(py_rows), (
        f"conformance/opcodes.yaml has {len(yaml_rows)} entries; xqvm_py.Opcode has {len(py_rows)}"
    )


def test_opcode_codes_match(yaml_rows: dict[int, dict], py_rows: dict[int, dict]) -> None:
    missing_in_py = sorted(set(yaml_rows) - set(py_rows))
    missing_in_yaml = sorted(set(py_rows) - set(yaml_rows))
    assert not missing_in_py, f"codes in YAML but not Python: {[hex(c) for c in missing_in_py]}"
    assert not missing_in_yaml, f"codes in Python but not YAML: {[hex(c) for c in missing_in_yaml]}"


def test_opcode_fields_match(yaml_rows: dict[int, dict], py_rows: dict[int, dict]) -> None:
    mismatches: list[str] = []
    for code in sorted(yaml_rows):
        yaml_row = yaml_rows[code]
        py_row = py_rows[code]
        if yaml_row != py_row:
            mismatches.append(f"  {code:#04x}: yaml={yaml_row!r}  py={py_row!r}")
    assert not mismatches, "opcode field drift vs conformance/opcodes.yaml:\n" + "\n".join(mismatches)
