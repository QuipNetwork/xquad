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

"""Regenerate opcode reference pages from conformance/opcodes.yaml.

The YAML is the single source of truth for the XQVM opcode set. This
script derives concise reference tables (one row per opcode, grouped by
category) suitable as quick look-ups alongside spec/xqvm/SPEC.md. The
richer instruction-by-instruction semantics live in
docs/book/src/xqvm/instructions/*.md and are not generated.

Modes:
  (default)   Overwrite conformance/opcodes.md and docs/book/src/xqvm/opcodes.md.
  --check     Render to strings and diff against the committed files;
              exit 1 on any difference. Setup errors exit 2. Used by the
              docs-generated lint job.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path

from _docsgen import Target, banner, emit
from _scriptio import SetupError, format_setup_error, load_yaml, require_key, require_mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = REPO_ROOT / "conformance" / "opcodes.yaml"
CONFORMANCE_DOC_PATH = REPO_ROOT / "conformance" / "opcodes.md"
BOOK_DOC_PATH = REPO_ROOT / "docs" / "book" / "src" / "xqvm" / "opcodes.md"
GITLAB_BLOB_URL = "https://gitlab.com/quip.network/xquad/-/blob/main"

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
    ("vector-ops", "Vector Operations"),
    ("index-math", "Index Math"),
    ("xqmx-access", "XQMX Coefficient Access"),
    ("xqmx-grid", "XQMX Grid"),
    ("xqmx-high-level", "XQMX High-Level Constraints"),
    ("special", "Special"),
]

CONFORMANCE_HEADER = (
    banner(
        "scripts/gen-bytecode-docs.py",
        "conformance/opcodes.yaml",
        """
        Edit the YAML (and the opcodes! x-macro in
        xqvm/src/bytecode/types/table.rs, which is checked against the YAML at
        compile time), then run `make regen-docs`.

        For the long-form human-readable semantics of each instruction see
        `docs/book/src/xqvm/instructions/*.md` or `spec/xqvm/SPEC.md`.
        """,
    )
    + """

# XQVM Bytecode Semantics

Concise reference table for every opcode in the XQVM bytecode format.
Derived directly from [`conformance/opcodes.yaml`](opcodes.yaml),
which is kept in sync with the Rust `opcodes!` x-macro (enforced at
compile time by `xqvm/build.rs`) and the Python `Opcode` enum (enforced
by `scripts/check-opcode-parity.py`).

Columns:
- **Code** -- wire-encoding byte.
- **Mnemonic** -- uppercase assembly name.
- **Operands** -- post-opcode operand layout; empty for no-operand instructions.
- **Stack** -- stack effect as `pop → push`; `0 → 1` means one value produced.
  `any → 0` marks an instruction that empties the stack outright rather than
  applying a fixed net effect.
- **Description** -- single-sentence semantic summary.

"""
)

BOOK_HEADER = (
    banner(
        "scripts/gen-bytecode-docs.py",
        "conformance/opcodes.yaml",
        """
        Edit the YAML (and the opcodes! x-macro in
        xqvm/src/bytecode/types/table.rs, which is checked against the YAML at
        compile time), then run `make regen-docs`.

        Do not edit this book page directly. For teaching prose about each
        instruction category, edit `docs/book/src/xqvm/instructions/*.md`.
        """,
    )
    + f"""

# Opcode Reference

Concise reference table for every opcode in the XQVM bytecode format.
Derived directly from [`conformance/opcodes.yaml`]({GITLAB_BLOB_URL}/conformance/opcodes.yaml),
which is kept in sync with the Rust [`opcodes!` x-macro]({GITLAB_BLOB_URL}/xqvm/src/bytecode/types/table.rs)
and the Python [`Opcode` enum]({GITLAB_BLOB_URL}/xqvm_py/opcodes.py).

For the normative bytecode specification, see
[`spec/xqvm/SPEC.md`]({GITLAB_BLOB_URL}/spec/xqvm/SPEC.md).

Columns:
- **Code** -- wire-encoding byte.
- **Mnemonic** -- uppercase assembly name.
- **Operands** -- post-opcode operand layout; empty for no-operand instructions.
- **Stack** -- stack effect as `pop → push`; `0 → 1` means one value produced.
  `any → 0` marks an instruction that empties the stack outright rather than
  applying a fixed net effect.
- **Description** -- single-sentence semantic summary.

"""
)


def format_operands(operands: list[object], entry_path: str) -> str:
    if not operands:
        return "--"
    parts: list[str] = []
    for index, value in enumerate(operands):
        op_path = f"{entry_path}.operands[{index}]"
        op = require_mapping(value, op_path)
        name = require_key(op, op_path, "name", str)
        width = op.get("width", 1)
        if not isinstance(width, int):
            raise SetupError(f"{op_path}: optional key `width` must be int, got {type(width).__name__}")
        kind = require_key(op, op_path, "type", str)
        if kind == "register":
            parts.append(f"`{name}: Register`")
        elif kind == "label":
            bits = width * 8
            parts.append(f"`{name}: u{bits}`")
        elif kind == "immediate":
            parts.append(f"`{name}: [u8; {width}]`")
        else:
            raise SetupError(
                f"{op_path}: operand `{name}` has type `{kind}`, which is not one of "
                "register/label/immediate recognised by format_operands in "
                "scripts/gen-bytecode-docs.py; add support there or fix the type in "
                "conformance/opcodes.yaml"
            )
    return ", ".join(parts)


def format_stack_effect(stack_pop: int, stack_push: int, entry: Mapping[str, object], entry_path: str) -> str:
    """Render the Stack column.

    An opcode carrying `stack_reset` has no fixed net effect to print --
    it empties the stack whatever its depth -- so `0 → 0` would read as
    "leaves the stack alone", which is the opposite of what SCLR does.
    """
    reset = entry.get("stack_reset", False)
    if not isinstance(reset, bool):
        raise SetupError(f"{entry_path}: optional key `stack_reset` must be bool, got {type(reset).__name__}")
    if reset:
        return "`any → 0`"
    return f"`{stack_pop} → {stack_push}`"


def _format_reserved_range(start: int, end: int) -> str:
    if start == end:
        return f"`0x{start:02X}`"
    return f"`0x{start:02X}`-`0x{end:02X}`"


def _reserved_ranges(assigned: set[int]) -> list[str]:
    """Collapse the wire bytes with no assigned opcode into ranges.

    Derived by complementing `assigned` against the full 0x00-0xFF byte
    space, not by reading a hand-maintained list, so the reserved set can
    never drift out of step with the opcode table itself.
    """
    gaps = sorted(byte for byte in range(256) if byte not in assigned)
    ranges: list[str] = []
    start: int | None = None
    prev: int | None = None
    for byte in gaps:
        if start is None:
            start = prev = byte
        elif prev is not None and byte == prev + 1:
            prev = byte
        else:
            ranges.append(_format_reserved_range(start, prev))
            start = prev = byte
    if start is not None and prev is not None:
        ranges.append(_format_reserved_range(start, prev))
    return ranges


def render_tables(data: dict) -> str:
    opcodes = require_key(data, YAML_PATH, "opcodes", list)
    known_slugs = {cat for cat, _ in CATEGORIES}
    by_category: dict[str, list[Mapping[str, object]]] = {cat: [] for cat, _ in CATEGORIES}
    for index, value in enumerate(opcodes):
        entry_path = f"{YAML_PATH}: opcodes[{index}]"
        entry = require_mapping(value, entry_path)
        category = require_key(entry, entry_path, "category", str)
        if category not in known_slugs:
            mnemonic = entry.get("mnemonic", "<unknown>")
            raise SetupError(
                f"{entry_path}: opcode `{mnemonic}` has category `{category}`, which is not "
                "in the CATEGORIES list in scripts/gen-bytecode-docs.py; add the slug there "
                "or fix the category in conformance/opcodes.yaml"
            )
        by_category.setdefault(category, []).append(entry)

    assigned_codes = {
        int(require_key(entry, f"{YAML_PATH}: {entry.get('mnemonic', 'opcode entry')}", "code", int))
        for entries in by_category.values()
        for entry in entries
    }

    lines: list[str] = []
    reserved = ", ".join(_reserved_ranges(assigned_codes))
    lines.append(f"Reserved wire bytes (rejected by the decoder as illegal): {reserved}.")
    lines.append("")
    lines.append(f"Total: **{len(opcodes)} opcodes**.")
    lines.append("")

    for cat_slug, cat_title in CATEGORIES:
        entries = sorted(
            by_category.get(cat_slug, []),
            key=lambda e: int(require_key(e, f"{YAML_PATH}: opcode entry", "code", int)),
        )
        if not entries:
            continue
        lines.append("---")
        lines.append("")
        lines.append(f"## {cat_title}")
        lines.append("")
        lines.append("| Code | Mnemonic | Operands | Stack | Description |")
        lines.append("|------|----------|----------|-------|-------------|")
        for entry in entries:
            entry_path = f"{YAML_PATH}: {entry.get('mnemonic', 'opcode entry')}"
            code = require_key(entry, entry_path, "code", int)
            mnemonic = require_key(entry, entry_path, "mnemonic", str)
            operands = format_operands(require_key(entry, entry_path, "operands", list), entry_path)
            stack_pop = require_key(entry, entry_path, "stack_pop", int)
            stack_push = require_key(entry, entry_path, "stack_push", int)
            stack = format_stack_effect(stack_pop, stack_push, entry, entry_path)
            doc = require_key(entry, entry_path, "doc", str).replace("|", "\\|")
            lines.append(f"| `0x{code:02X}` | `{mnemonic}` | {operands} | {stack} | {doc} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_page(data: dict, header: str) -> str:
    return header + render_tables(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed file matches the generator output.",
    )
    args = parser.parse_args()

    try:
        data = load_yaml(YAML_PATH)
        targets = [
            Target(CONFORMANCE_DOC_PATH, render_page(data, CONFORMANCE_HEADER)),
            Target(BOOK_DOC_PATH, render_page(data, BOOK_HEADER)),
        ]
    except SetupError as exc:
        sys.stderr.write(f"docs generation setup error: {format_setup_error(exc, REPO_ROOT)}\n")
        return 2

    return emit(targets, repo_root=REPO_ROOT, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
