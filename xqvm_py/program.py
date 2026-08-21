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

"""
Program and Instruction types, plus construction and execution helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .opcodes import Opcode


@dataclass(frozen=True)
class Instruction:
    """
    A single XQVM instruction.

    Attributes:
        opcode: The operation to perform
        operands: Tuple of operand values (immediates, register indices, target IDs)
        line: Source line number for debugging (0 if unknown)
    """

    opcode: Opcode
    operands: tuple[int, ...] = ()
    line: int = 0


@dataclass
class Program:
    """
    A complete XQVM program.

    Attributes:
        instructions: List of instructions to execute
        name: Optional program name for debugging
        jump_targets: Maps sequential TARGET id to instruction index; pre-built
            at construction time so the executor skips the per-run pre-scan.
    """

    instructions: list[Instruction] = field(default_factory=list)
    name: str = ""
    jump_targets: dict[int, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.instructions)

    def __getitem__(self, index: int) -> Instruction:
        return self.instructions[index]


def _build_jump_targets(instructions: list[Instruction]) -> dict[int, int]:
    """Scan instructions for TARGET opcodes and return the sequential-id map."""
    result: dict[int, int] = {}
    target_id = 0
    for i, instr in enumerate(instructions):
        if instr.opcode == Opcode.TARGET:
            result[target_id] = i
            target_id += 1
    return result


def make_program(instructions: list[Instruction]) -> Program:
    """Build a Program from a list of Instructions."""
    return Program(instructions, jump_targets=_build_jump_targets(instructions))


def run_program(
    instructions: list[Instruction],
    input_data: dict[int, Any] | None = None,
    output_slots: int = 0,
):
    """Build and execute a program, returning executor for state inspection.

    `output_slots` defaults to 0, matching `xqvm::Vm::new()`: a program that
    writes an output has to say how many slots the host reserved.
    """
    from .executor import Executor

    prog = make_program(instructions)
    ex = Executor()
    ex.execute(prog, input_data, output_slots=output_slots)
    return ex


_XQBC_MAGIC = b"XQBC"
_XQBC_VERSION = 1
_XQBC_HEADER_SIZE = 15


def program_from_bytecode(bytecode: bytes, name: str = "") -> Program:
    """Decode ``.xqb`` bytecode into an executable ``Program``.

    Parses and validates the 15-byte XQBC header (magic, version, length,
    CRC-32), then decodes the instruction stream.  No FFI dependency.

    Header layout::

        0..4   b"XQBC"          magic
        4      version: u8      format version (currently 1)
        5      input_slots: u8  count of INPUT instructions
        6      output_slots: u8 count of OUTPUT instructions
        7..11  code_len: u32 BE byte length of instruction stream
        11..15 crc32: u32 BE    CRC-32/ISO-HDLC of instruction stream
        15+    instruction stream

    Raises:
        ValueError: if the header is missing, malformed, or the checksum
            does not match.
    """
    import struct
    import zlib

    if len(bytecode) < _XQBC_HEADER_SIZE:
        raise ValueError(f"not an XQBC file: too short ({len(bytecode)} bytes, need {_XQBC_HEADER_SIZE})")

    magic = bytecode[:4]
    if magic != _XQBC_MAGIC:
        raise ValueError(f"not an XQBC file: wrong magic {magic!r}")

    version = bytecode[4]
    if version != _XQBC_VERSION:
        raise ValueError(f"unsupported XQBC version {version} (expected {_XQBC_VERSION})")

    (code_len,) = struct.unpack_from(">I", bytecode, 7)
    (expected_crc,) = struct.unpack_from(">I", bytecode, 11)

    code = bytecode[_XQBC_HEADER_SIZE:]
    if len(code) != code_len:
        raise ValueError(f"XQBC length mismatch: header says {code_len} bytes, got {len(code)}")

    actual_crc = zlib.crc32(code) & 0xFFFF_FFFF
    if actual_crc != expected_crc:
        raise ValueError(f"XQBC CRC-32 mismatch: expected 0x{expected_crc:08X}, computed 0x{actual_crc:08X}")

    instructions: list[Instruction] = []
    pos = 0
    while pos < len(code):
        opcode_byte = code[pos]
        opcode = Opcode.from_code(opcode_byte)
        if opcode is None:
            raise ValueError(f"unknown opcode 0x{opcode_byte:02X} at byte offset {pos}")
        n = opcode.meta.operand_count
        if pos + 1 + n > len(code):
            raise ValueError(
                f"truncated operands for {opcode.name} at byte offset {pos}: need {n} bytes, have {len(code) - pos - 1}"
            )
        operands = tuple(code[pos + 1 : pos + 1 + n])
        instructions.append(Instruction(opcode, operands))
        pos += 1 + n
    return Program(instructions=instructions, name=name, jump_targets=_build_jump_targets(instructions))


def program_from_xqasm(source: str, name: str = "") -> Program:
    """Parse ``.xqasm`` source via the Rust xqasm crate and wrap the result.

    This is the single entry point xqvm-py uses to turn assembly text
    into an executable ``Program``. The actual parsing + assembly is
    done by ``xqffi.asm.parse_xqasm`` (a pyo3 binding to Rust
    ``xqasm``); this helper just adapts the wire dict into the Python
    dataclass layout the executor expects.

    The ``program_from_xqasm`` / ``Instruction`` / ``Program`` trio
    replaces what used to be ``xqvm.assembler.assemble(src).program``
    — xqvm-py no longer ships its own assembler.
    """
    from xqffi.asm import parse_xqasm

    wire = parse_xqasm(source)
    instructions = [Instruction(Opcode.from_code(code), tuple(ops), int(pc)) for code, ops, pc in wire["instructions"]]
    return Program(instructions=instructions, name=name, jump_targets=_build_jump_targets(instructions))
