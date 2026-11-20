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

"""Type stubs for `xqffi.asm`, the bindings around the Rust `xqasm` crate."""

__all__ = ["parse_xqasm", "assemble_source", "disassemble", "instruction_count"]

def parse_xqasm(source: str) -> dict[str, object]:
    """Assemble `source` into `{"instructions": [...], "jump_targets": {...}}`.

    Raises `ValueError` on any parse or assembly failure.
    """

def assemble_source(source: str) -> bytes:
    """Assemble `source` and return the wire-format bytes.

    Raises `ValueError` on any parse or assembly failure.
    """

def disassemble(bytecode: bytes) -> str:
    """Return a human-readable listing of `bytecode`, for display only.

    Raises `ValueError` if `bytecode` does not decode.
    """

def instruction_count(bytecode: bytes) -> int:
    """Count the instructions in `bytecode`.

    Raises `ValueError` if `bytecode` does not decode.
    """
