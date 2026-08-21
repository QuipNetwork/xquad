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

"""Implementation of the ``python -m xqvm_py run`` subcommand.

Supports both ``.xqasm`` source and ``.xqb`` bytecode input. Both paths
go through the Rust assembler via ``xqffi.asm``:

* ``.xqasm`` — text is passed directly to ``program_from_xqasm``.
* ``.xqb``  — bytes are first disassembled back to ``.xqasm`` text (via
  ``xqffi.asm.disassemble``) and then parsed. The disassembly output
  is not fully round-trippable as source today (it carries pc offsets
  and ``.N`` labels); programmatic ``.xqb`` support will be revisited
  alongside the Phase 5 xqffi work.

xqvm-py ships no Python assembler of its own — the previous
``xqvm.assembler`` tree was removed in QUI-440.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from xqvm_py import Executor, Program, program_from_bytecode, program_from_xqasm
from xqvm_py.errors import XQVMError


def _load_program(path: Path, *, text: bool) -> Program:
    """Load ``.xqasm`` text or ``.xqb`` bytecode."""
    if text or path.suffix == ".xqasm":
        source = path.read_text(encoding="utf-8")
        return program_from_xqasm(source, name=path.stem)
    if path.suffix == ".xqb":
        return program_from_bytecode(path.read_bytes(), name=path.stem)
    raise SystemExit(
        f"xqvm-py: unknown file extension {path.suffix!r}; pass a .xqasm "
        "source (or use --text to force text interpretation)."
    )


def _build_input_data(args: argparse.Namespace) -> dict[int, Any]:
    if args.inputs is not None:
        with Path(args.inputs).open(encoding="utf-8") as f:
            inputs_doc = json.load(f)
        calldata = inputs_doc.get("calldata", [])
    else:
        calldata = list(args.calldata)
    return dict(enumerate(calldata))


def _serialise_value(value: Any) -> Any:
    """Convert a register value to JSON-ready primitives.

    Non-scalar types (``Vec``, ``XQMX``) fall back to ``repr()`` — this
    is a defensive last resort; conformance vectors stick to integer
    outputs.
    """
    if isinstance(value, int):
        return value
    return repr(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m xqvm_py",
        description="Run XQVM programs using the Python reference VM.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Execute an XQVM program")
    run.add_argument("file", type=Path, help="Program file (.xqasm or .xqb)")
    run.add_argument(
        "--text",
        action="store_true",
        help="Force interpretation of FILE as assembly text.",
    )
    run.add_argument(
        "--calldata",
        type=lambda s: [int(x) for x in s.split(",") if x],
        default=[],
        help="Comma-separated i64 calldata values passed to INPUT slots.",
    )
    run.add_argument(
        "--inputs",
        type=Path,
        help='JSON file with {"calldata": [...]} (overrides --calldata).',
    )
    run.add_argument(
        "--outputs",
        type=int,
        default=16,
        help="Number of output slots reserved (outputs beyond this index are null).",
    )
    run.add_argument(
        "--step-limit",
        type=int,
        default=None,
        help=(
            "Maximum steps before the run is stopped. The limit is exact, so 0 "
            "executes nothing; omit the flag to run unbounded."
        ),
    )

    args = parser.parse_args(argv)

    if args.command != "run":
        parser.error(f"unknown command: {args.command}")

    program = _load_program(args.file, text=args.text)
    input_data = _build_input_data(args)

    executor = Executor()
    try:
        output_map = executor.execute(program, input_data=input_data, step_limit=args.step_limit)
    except XQVMError as exc:
        # A faulting program is a result, not a crash: report the fault's
        # identity as JSON so callers (the conformance harness above all)
        # can compare it against the other implementation instead of
        # scraping a traceback off stderr.
        json.dump(
            {"error": {"type": type(exc).__name__, "message": str(exc)}},
            sys.stdout,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 2

    # Unset slots are reported as null (None) to match the spec's sparse-map
    # semantics: output slots never written by the program are absent.
    outputs: list[Any] = [
        _serialise_value(output_map[slot]) if slot in output_map else None for slot in range(args.outputs)
    ]
    final_stack = [_serialise_value(v) for v in executor.state.stack]

    json.dump(
        {"outputs": outputs, "final_stack": final_stack, "steps": executor.steps},
        sys.stdout,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
