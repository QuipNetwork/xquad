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

"""Tests for xqvm_py.program — construction and bytecode decoding."""

from __future__ import annotations

from xqffi.asm import assemble_source
from xqvm_py import program_from_bytecode, program_from_xqasm


def test_bytecode_round_trip():
    """Assemble source → bytecode → decode produces the same program as parsing source directly."""
    source = "PUSH 7\nPUSH 5\nADD\nSTOW r0\nPUSH 0\nOUTPUT r0\nHALT\n"
    bytecode = assemble_source(source)

    from_asm = program_from_xqasm(source)
    from_bc = program_from_bytecode(bytecode)

    assert len(from_asm) == len(from_bc)
    for i, (a, b) in enumerate(zip(from_asm.instructions, from_bc.instructions)):
        assert a.opcode == b.opcode, f"instruction {i}: opcode mismatch {a.opcode} != {b.opcode}"
        assert a.operands == b.operands, f"instruction {i}: operands mismatch {a.operands} != {b.operands}"


def _make_xqbc(code: bytes) -> bytes:
    """Build a minimal valid XQBC-encoded byte string for the given code payload."""
    import struct
    import zlib

    crc = zlib.crc32(code) & 0xFFFF_FFFF
    return b"XQBC" + bytes([1, 0, 0]) + struct.pack(">II", len(code), crc) + code


def test_bytecode_decode_empty_program():
    """An empty instruction stream inside a valid XQBC header decodes to an empty program."""
    prog = program_from_bytecode(_make_xqbc(b""))
    assert len(prog) == 0


def test_bytecode_decode_unknown_opcode():
    """Unknown opcode byte in the payload raises InvalidOpcode."""
    import pytest

    from xqvm_py.errors import InvalidOpcode

    with pytest.raises(InvalidOpcode, match="0x0D"):
        program_from_bytecode(_make_xqbc(bytes([0x0D])))


def test_bytecode_decode_truncated():
    """Truncated operands in the payload raise TruncatedInstruction."""
    import pytest

    from xqvm_py.errors import TruncatedInstruction

    with pytest.raises(TruncatedInstruction, match="Truncated instruction"):
        program_from_bytecode(_make_xqbc(bytes([0x11])))  # PUSH1 needs 1 operand byte


def test_bytecode_decode_bad_magic():
    """Non-XQBC bytes raise ValueError."""
    import pytest

    with pytest.raises(ValueError, match="wrong magic"):
        program_from_bytecode(b"\x00" * 15)


def test_bytecode_decode_too_short():
    """Bytes shorter than the 15-byte header raise ValueError."""
    import pytest

    with pytest.raises(ValueError, match="too short"):
        program_from_bytecode(b"XQBC")


def test_bytecode_decode_bad_version():
    """Unsupported version byte raises ValueError."""
    import struct
    import zlib

    import pytest

    code = b"\xff"
    crc = zlib.crc32(code) & 0xFFFF_FFFF
    bad_version = b"XQBC" + bytes([99, 0, 0]) + struct.pack(">II", len(code), crc) + code
    with pytest.raises(ValueError, match="unsupported XQBC version 99"):
        program_from_bytecode(bad_version)


def test_bytecode_decode_length_mismatch():
    """Payload shorter than code_len raises ValueError."""
    import struct
    import zlib

    import pytest

    code = b"\xff"
    crc = zlib.crc32(code) & 0xFFFF_FFFF
    # Claim code_len=5 but provide only 1 byte.
    bad_len = b"XQBC" + bytes([1, 0, 0]) + struct.pack(">II", 5, crc) + code
    with pytest.raises(ValueError, match="length mismatch"):
        program_from_bytecode(bad_len)


def test_bytecode_decode_crc_mismatch():
    """Corrupted payload raises ValueError."""
    import struct
    import zlib

    import pytest

    code = b"\xff"
    crc = zlib.crc32(code) & 0xFFFF_FFFF
    wrong_crc = (crc ^ 0xDEAD_BEEF) & 0xFFFF_FFFF
    corrupted = b"XQBC" + bytes([1, 0, 0]) + struct.pack(">II", len(code), wrong_crc) + code
    with pytest.raises(ValueError, match="CRC-32 mismatch"):
        program_from_bytecode(corrupted)
