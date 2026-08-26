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

"""Cross-check the metering constants across all three sources of truth.

`xqvm/src/metering.rs` is the canonical set of `pub const` step-cost
constants. `xqvm_py/metering.py` mirrors them value for value, and
`spec/xqvm/METERING.md` specifies them normatively in its constants table.
Nothing at compile time or import time stops the three from drifting
apart -- this script is that guard. It parses all three, compares every
constant name across the three sources, and prints a diff-style report on
any mismatch or omission.

Intended to be invoked from the `metering-parity` CI job and the
`make metering-parity` Makefile target.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_METERING_PATH = REPO_ROOT / "xqvm" / "src" / "metering.rs"
SPEC_METERING_PATH = REPO_ROOT / "spec" / "xqvm" / "METERING.md"

RUST_CONST_RE = re.compile(r"^pub const (\w+): u64 = (\d+);", re.MULTILINE)
SPEC_TABLE_ROW_RE = re.compile(r"^\|\s*`(\w+)`\s*\|\s*(\d+)\s*\|", re.MULTILINE)

MISSING = "<missing>"


def load_rust_constants(path: Path) -> dict[str, int]:
    """Parse every `pub const NAME: u64 = VALUE;` line."""
    text = path.read_text(encoding="utf-8")
    return {name: int(value) for name, value in RUST_CONST_RE.findall(text)}


def load_python_constants(names: set[str]) -> dict[str, int]:
    """Import `xqvm_py.metering` and read each of `names` off the module.

    Only reads names the Rust side declared -- this is a cross-check of
    shared constants, not an enumeration of everything the module exports.
    """
    # The repo root sits on sys.path so ``xqvm_py`` resolves to the
    # flat-layout package directory, matching scripts/check-opcode-parity.py.
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from xqvm_py import metering  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    result: dict[str, int] = {}
    for name in names:
        value = getattr(metering, name, None)
        if isinstance(value, int):
            result[name] = value
    return result


def load_spec_constants(path: Path) -> dict[str, int]:
    """Parse the `| \\`NAME\\` | value | ... |` rows of the constants table."""
    text = path.read_text(encoding="utf-8")
    return {name: int(value) for name, value in SPEC_TABLE_ROW_RE.findall(text)}


def diff(rust: dict[str, int], py: dict[str, int], spec: dict[str, int]) -> list[str]:
    errors: list[str] = []
    names = sorted(set(rust) | set(py) | set(spec))
    for name in names:
        rust_val = rust.get(name)
        py_val = py.get(name)
        spec_val = spec.get(name)
        if rust_val is not None and rust_val == py_val and rust_val == spec_val:
            continue
        errors.append(
            f"MISMATCH at {name}: "
            f"rust={rust_val if rust_val is not None else MISSING} "
            f"python={py_val if py_val is not None else MISSING} "
            f"spec={spec_val if spec_val is not None else MISSING}"
        )
    return errors


def main() -> int:
    if not RUST_METERING_PATH.is_file():
        print(f"metering parity setup error: {RUST_METERING_PATH} not found", file=sys.stderr)
        return 2
    if not SPEC_METERING_PATH.is_file():
        print(f"metering parity setup error: {SPEC_METERING_PATH} not found", file=sys.stderr)
        return 2

    rust_consts = load_rust_constants(RUST_METERING_PATH)
    if not rust_consts:
        print(
            f"metering parity setup error: no `pub const NAME: u64 = VALUE;` lines found in {RUST_METERING_PATH}",
            file=sys.stderr,
        )
        return 2

    py_consts = load_python_constants(set(rust_consts))
    spec_consts = load_spec_constants(SPEC_METERING_PATH)

    errors = diff(rust_consts, py_consts, spec_consts)
    if errors:
        print("Metering parity check FAILED:\n", file=sys.stderr)
        for line in errors:
            print(line, file=sys.stderr)
        print(
            f"\nCompared {len(rust_consts)} rust constants, {len(py_consts)} python "
            f"constants, {len(spec_consts)} spec table rows.",
            file=sys.stderr,
        )
        return 1

    print(f"metering parity OK ({len(rust_consts)} constants)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
