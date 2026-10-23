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

"""`xqffi.vm.Vm.run(trace=..., trace_file=...)` writes the Rust tracers' output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xqffi import asm, vm

SOURCE = "PUSH 2\nPUSH 3\nADD\nHALT\n"


def _bytecode() -> bytes:
    return asm.assemble_source(SOURCE)


def test_text_trace_to_file(tmp_path: Path) -> None:
    out = tmp_path / "trace.txt"
    v = vm.Vm()
    v.run(_bytecode(), trace="text", trace_file=str(out))
    lines = out.read_text().splitlines()
    assert lines[0].split()[:3] == ["step", "offset", "instruction"]
    assert len(lines) == 5
    assert v.stack() == [5]


def test_json_trace_to_file(tmp_path: Path) -> None:
    out = tmp_path / "trace.jsonl"
    vm.Vm().run(_bytecode(), trace="json", trace_file=out)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 4


def test_trace_file_without_trace_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="trace_file requires trace"):
        vm.Vm().run(_bytecode(), trace_file=str(tmp_path / "t"))


def test_unknown_trace_format_raises() -> None:
    with pytest.raises(ValueError, match="unknown trace format"):
        vm.Vm().run(_bytecode(), trace="yaml")


def test_run_without_trace_unchanged(capfd: pytest.CaptureFixture[str]) -> None:
    v = vm.Vm()
    v.run(_bytecode())
    assert v.stack() == [5]
    assert capfd.readouterr().err == ""
