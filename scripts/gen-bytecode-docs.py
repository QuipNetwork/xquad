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

"""Regenerate docs/bytecode-semantics.md from conformance/opcodes.yaml.

The YAML is the single source of truth for the XQVM opcode set. This
script derives a concise reference table (one row per opcode, grouped by
category) suitable as a quick look-up alongside spec/xqvm/SPEC.md. The
richer instruction-by-instruction semantics live in
docs/book/src/instructions/*.md and are not generated.

Modes:
  (default)   Overwrite docs/bytecode-semantics.md.
  --check     Render to a string and diff against the committed file;
              exit 1 on any difference. Used by the `docs-build` CI job.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = REPO_ROOT / "conformance" / "opcodes.yaml"
DOC_PATH = REPO_ROOT / "docs" / "bytecode-semantics.md"

# Section ordering and display titles. Matches the spec/xqvm/SPEC.md
# section layout so the generated doc scans in the same order a reader
# would follow the specification.
CATEGORIES: list[tuple[str, str]] = [
    ("control-flow", "Control Flow"),
    ("register-io", "Register I/O"),
    ("stack", "Stack Manipulation"),
    ("arithmetic", "Arithmetic"),
    ("comparison", "Comparison"),
    ("logical", "Logical Boolean"),
    ("bitwise", "Bitwise"),
    ("allocators", "Allocators"),
    ("vector-access", "Vector Access"),
    ("index-math", "Index Math"),
    ("xqmx-access", "XQMX Coefficient Access"),
    ("xqmx-grid", "XQMX Grid"),
    ("xqmx-high-level", "XQMX High-Level Constraints"),
    ("special", "Special"),
]

HEADER = """<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `conformance/opcodes.yaml` by
  `scripts/gen-bytecode-docs.py`. Edit the YAML (and the opcodes! x-macro
  in xqvm/src/bytecode/types/table.rs, which is checked against the YAML
  at compile time), then run `make docs-regen`.

  For the long-form human-readable semantics of each instruction see
  `docs/book/src/instructions/*.md` or `spec/xqvm/SPEC.md`.
-->

# XQVM Bytecode Semantics

Concise reference table for every opcode in the XQVM bytecode format.
Derived directly from [`conformance/opcodes.yaml`](../conformance/opcodes.yaml),
which is kept in sync with the Rust `opcodes!` x-macro (enforced at
compile time by `xqvm/build.rs`) and the Python `Opcode` enum (enforced
by `scripts/check-opcode-parity.py`).

Columns:
- **Code** — wire-encoding byte.
- **Mnemonic** — uppercase assembly name.
- **Operands** — post-opcode operand layout; empty for no-operand instructions.
- **Stack** — stack effect as `pop → push`; `0 → 1` means one value produced.
- **Description** — single-sentence semantic summary.

Reserved wire bytes (rejected by the decoder as illegal): """


def format_operands(operands: list[dict]) -> str:
    if not operands:
        return "—"
    parts: list[str] = []
    for op in operands:
        name = op["name"]
        width = op.get("width", 1)
        kind = op["type"]
        if kind == "register":
            parts.append(f"`{name}: Register`")
        elif kind == "label":
            bits = width * 8
            parts.append(f"`{name}: u{bits}`")
        else:  # immediate
            parts.append(f"`{name}: [u8; {width}]`")
    return ", ".join(parts)


def render(data: dict) -> str:
    by_category: dict[str, list[dict]] = {cat: [] for cat, _ in CATEGORIES}
    for entry in data["opcodes"]:
        by_category.setdefault(entry["category"], []).append(entry)

    lines: list[str] = []
    reserved = ", ".join(f"`0x{c:02X}`" for c in sorted(data.get("reserved", [])))
    lines.append(HEADER + reserved + ".")
    lines.append("")
    lines.append(f"Total: **{len(data['opcodes'])} opcodes**.")
    lines.append("")

    for cat_slug, cat_title in CATEGORIES:
        entries = sorted(by_category.get(cat_slug, []), key=lambda e: int(e["code"]))
        if not entries:
            continue
        lines.append("---")
        lines.append("")
        lines.append(f"## {cat_title}")
        lines.append("")
        lines.append("| Code | Mnemonic | Operands | Stack | Description |")
        lines.append("|------|----------|----------|-------|-------------|")
        for entry in entries:
            code = int(entry["code"])
            mnemonic = entry["mnemonic"]
            operands = format_operands(entry["operands"])
            stack = f"`{entry['stack_pop']} → {entry['stack_push']}`"
            doc = entry["doc"].replace("|", "\\|")
            lines.append(f"| `0x{code:02X}` | `{mnemonic}` | {operands} | {stack} | {doc} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed file matches the generator output.",
    )
    args = parser.parse_args()

    with YAML_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    generated = render(data)

    if args.check:
        existing = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
        if existing == generated:
            print(f"{DOC_PATH.relative_to(REPO_ROOT)}: up to date")
            return 0
        diff_lines = difflib.unified_diff(
            existing.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=str(DOC_PATH.relative_to(REPO_ROOT)) + " (committed)",
            tofile=str(DOC_PATH.relative_to(REPO_ROOT)) + " (regenerated)",
        )
        sys.stderr.write(f"{DOC_PATH.relative_to(REPO_ROOT)} is stale — run `make docs-regen`.\n\n")
        sys.stderr.writelines(diff_lines)
        return 1

    DOC_PATH.write_text(generated, encoding="utf-8")
    print(f"wrote {DOC_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
