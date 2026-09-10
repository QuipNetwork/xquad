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

"""Every bytecode-reachable fault is an ``XQVMError``.

``xqvm_py/cli/run.py`` catches ``XQVMError`` and nothing else, so a fault
raised outside the hierarchy kills the process, writes a traceback to
stderr, leaves stdout empty, and makes ``conformance/src/lib.rs`` report
"stdout did not parse as JSON" -- pointing the reader at the runner rather
than at the divergence. The catch is deliberately not broadened to
``except Exception``: a genuine interpreter bug must stay a crash rather
than be reported as a missing fault mapping. These cases are the other
half of that contract.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from xqvm_py.errors import (
    IndexOutOfBounds,
    InvalidAllocation,
    InvalidDiscreteK,
    InvalidOpcode,
    InvalidShift,
    SampleOutOfDomain,
    SizeMismatch,
    TruncatedInstruction,
    TypeMismatch,
    VecLengthMismatch,
    XQVMError,
)
from xqvm_py.program import program_from_bytecode, program_from_xqasm, run_program
from xqvm_py.vector import Vec, VecElem
from xqvm_py.xqmx import XQMX, compute_energy, expand_equality

# Each case is (name, source, expected class). Every one of these raised a
# Python builtin -- ValueError, IndexError, TypeError -- before QUI-1147.
BYTECODE_CASES = [
    # The plan's shortest reproducer: SETLINE past the model's size.
    ("setline_out_of_range", "PUSH 4\nBQMX r0\nPUSH 10\nPUSH 1\nSETLINE r0\nHALT", IndexOutOfBounds),
    ("addquad_out_of_range", "PUSH 2\nBQMX r0\nPUSH 9\nPUSH 0\nPUSH 1\nADDQUAD r0\nHALT", IndexOutOfBounds),
    ("vecget_out_of_range", "VECI r0\nPUSH 7\nVECPUSH r0\nPUSH 3\nVECGET r0\nHALT", IndexOutOfBounds),
    ("vecset_out_of_range", "VECI r0\nPUSH 7\nVECPUSH r0\nPUSH 1\nPUSH 3\nVECSET r0\nHALT", IndexOutOfBounds),
    (
        "atleast_k_zero",
        "PUSH 3\nBQMX r0\nVECI r1\nPUSH 0\nVECPUSH r1\nPUSH 0\nPUSH 10\nATLEAST r0 r1\nHALT",
        IndexOutOfBounds,
    ),
    (
        "atleastw_length_mismatch",
        "PUSH 5\nBQMX r0\nVECI r1\nVECI r2\nPUSH 0\nVECPUSH r1\nPUSH 1\nVECPUSH r1\n"
        "PUSH 1\nVECPUSH r2\nPUSH 1\nPUSH 10\nATLEASTW r0 r1 r2\nHALT",
        VecLengthMismatch,
    ),
    ("reduce_out_of_range", "PUSH 3\nBQMX r0\nPUSH 9\nPUSH 1\nPUSH 10\nREDUCE r0\nHALT", IndexOutOfBounds),
    ("negative_allocation", "PUSH -1\nBQMX r0\nHALT", InvalidAllocation),
    ("discrete_k_below_two", "PUSH 4\nPUSH 1\nXQMX r0\nHALT", InvalidDiscreteK),
    (
        "setline_out_of_domain",
        "PUSH 4\nPUSH 3\nXSMX r0\nPUSH 0\nPUSH 7\nSETLINE r0\nHALT",
        SampleOutOfDomain,
    ),
    (
        "addline_leaves_domain",
        "PUSH 4\nBSMX r0\nPUSH 1\nPUSH 1\nSETLINE r0\nPUSH 1\nPUSH 1\nADDLINE r0\nHALT",
        SampleOutOfDomain,
    ),
    ("negative_shl", "PUSH 1\nPUSH -1\nSHL\nHALT", InvalidShift),
    ("negative_shr", "PUSH 8\nPUSH -1\nSHR\nHALT", InvalidShift),
    # The upper half of Rust's `(0..64)` guard. Before it was closed, the SHL
    # case raised ArithmeticOverflow (a fault-identity divergence) and the SHR
    # case completed with 0 on the stack (a fault-versus-success divergence on
    # a verifier-clean four-instruction program).
    ("oversized_shl", "PUSH 1\nPUSH 64\nSHL\nHALT", InvalidShift),
    ("oversized_shr", "PUSH 8\nPUSH 64\nSHR\nHALT", InvalidShift),
    ("energy_size_mismatch", "PUSH 5\nBQMX r0\nPUSH 3\nBSMX r1\nENERGY r0 r1\nHALT", SizeMismatch),
]


@pytest.mark.parametrize(("name", "source", "expected"), BYTECODE_CASES, ids=[c[0] for c in BYTECODE_CASES])
def test_bytecode_fault_is_in_the_hierarchy(name: str, source: str, expected: type[XQVMError]) -> None:
    """A faulting program raises the mapped class, not a Python builtin."""
    program = program_from_xqasm(source, name=name)
    with pytest.raises(expected) as excinfo:
        run_program(program.instructions)
    assert isinstance(excinfo.value, XQVMError)


def test_decode_faults_are_in_the_hierarchy() -> None:
    """Instruction-stream decode faults carry a fault identity too.

    `program_from_bytecode` runs before `execute`, so `cli/run.py` cannot
    catch these today; typing them is what lets an embedder using
    `xquad.VM(VMBackend.PYTHON)` -- public 0.4.0 API -- catch a malformed
    program with the same `except XQVMError` it already writes.
    """
    body = struct.pack(">4sBBBII", b"XQBC", 1, 0, 0, 1, zlib.crc32(b"\x0d") & 0xFFFF_FFFF) + b"\x0d"
    with pytest.raises(InvalidOpcode):
        program_from_bytecode(body)

    payload = b"\x11"  # PUSH1 with its operand byte missing
    body = struct.pack(">4sBBBII", b"XQBC", 1, 0, 0, 1, zlib.crc32(payload) & 0xFFFF_FFFF) + payload
    with pytest.raises(TruncatedInstruction):
        program_from_bytecode(body)


def test_vec_type_faults_are_in_the_hierarchy() -> None:
    """Vec element-type rejection is a TypeMismatch, not a builtin TypeError."""
    v = Vec.with_capacity(4, VecElem("int"))
    with pytest.raises(TypeMismatch):
        v.push(XQMX.binary_model(2))


def test_expansion_length_mismatch_is_in_the_hierarchy() -> None:
    """The EQUALITY expansion's own length check is typed as well."""
    model = XQMX.binary_model(size=4)
    with pytest.raises(VecLengthMismatch):
        expand_equality(model, [0, 1, 2], [1, 1], target=1, penalty=1)


def test_energy_size_mismatch_is_in_the_hierarchy() -> None:
    """compute_energy is xqsa's authoritative energy; its fault must be catchable."""
    with pytest.raises(SizeMismatch):
        compute_energy(XQMX.binary_model(size=5), XQMX.binary_sample(size=3))
