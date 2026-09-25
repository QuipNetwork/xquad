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

"""Typed VM faults raised by `xqffi.vm.Vm.run` (QUI-1479).

Each `xqvm::Error` variant surfaces as the `XqvmError` subclass named for
its fault in the conformance vocabulary, carrying the error's Display text.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable

import pytest

import xqffi.vm as ffi
from xqffi.asm import assemble_source, instruction_count
from xqffi.verifier import verify

#: The conformance fault vocabulary (`conformance/src/lib.rs`, `Fault`),
#: spelled as the exception classes spell it.
FAULTS = [
    "StackUnderflow",
    "StackOverflow",
    "TypeMismatch",
    "UnsetRegister",
    "DivisionByZero",
    "IndexOutOfBounds",
    "NoActiveLoop",
    "UnmatchedLoop",
    "BadJumpTarget",
    "InvalidLabel",
    "BadOpcode",
    "TruncatedInstruction",
    "CallDataIndex",
    "OutputIndex",
    "SizeMismatch",
    "VecLengthMismatch",
    "ArithmeticOverflow",
    "StepLimitExceeded",
    "MemoryLimitExceeded",
    "InvalidShift",
    "InvalidGridDimensions",
    "InvalidIntegerK",
    "SampleOutOfDomain",
    "TraceFailed",
    "InvalidAllocation",
    "LoopStackOverflow",
]

I64_MAX = 2**63 - 1


def _exported_faults() -> set[str]:
    return {
        name
        for name in dir(ffi)
        if isinstance(getattr(ffi, name), type)
        and issubclass(getattr(ffi, name), ffi.XqvmError)
        and name != "XqvmError"
    }


def test_every_fault_in_the_vocabulary_is_exported() -> None:
    assert _exported_faults() == set(FAULTS)


def test_the_base_is_a_runtime_error() -> None:
    # A host that caught `RuntimeError` before the faults were typed still
    # catches every one of them.
    assert issubclass(ffi.XqvmError, RuntimeError)


@pytest.mark.parametrize("name", FAULTS)
def test_each_fault_is_an_xqvm_error_in_xqffi_vm(name: str) -> None:
    cls = getattr(ffi, name)
    assert issubclass(cls, ffi.XqvmError)
    assert cls.__module__ == "xqffi.vm"
    assert cls.__doc__


# One program per fault reachable from assembled source. `BadOpcode` and
# `TruncatedInstruction` are raised below from hand-built bytecode.
# `BadJumpTarget` and `InvalidLabel` need a jump table the decoder would
# accept and the run would not, and `TraceFailed` needs a tracer, which
# `xqffi` does not expose; their mapping is pinned at compile time by the
# exhaustive match in `xqffi/src/fault.rs`.
RAISED = [
    ("StackUnderflow", "ADD\nHALT", []),
    ("StackOverflow", "TARGET .0\nPUSH 1\nJUMP .0", []),
    ("TypeMismatch", "PUSH 0\nINPUT r0\nPUSH 0\nGETLINE r0\nHALT", [5]),
    ("UnsetRegister", "LOAD r0\nHALT", []),
    ("DivisionByZero", "PUSH 1\nPUSH 0\nDIV\nHALT", []),
    ("ArithmeticOverflow", f"PUSH {I64_MAX}\nPUSH 1\nADD\nHALT", []),
    ("IndexOutOfBounds", "PUSH 2\nBQMX r0\nPUSH 5\nGETLINE r0\nHALT", []),
    ("NoActiveLoop", "NEXT\nHALT", []),
    ("UnmatchedLoop", "PUSH 0\nPUSH 0\nRANGE\nHALT", []),
    ("CallDataIndex", "PUSH 3\nINPUT r0\nHALT", []),
    ("OutputIndex", "PUSH 1\nSTOW r0\nPUSH 5\nOUTPUT r0\nHALT", []),
    (
        "VecLengthMismatch",
        "PUSH 0\nINPUT r0\nPUSH 1\nINPUT r1\nPUSH 2\nBQMX r2\nPUSH 1\nPUSH 1\nATLEASTW r2 r0 r1\nHALT",
        [[0, 1], [1]],
    ),
    ("InvalidShift", "PUSH 8\nPUSH 64\nSHR\nHALT", []),
    ("InvalidGridDimensions", "PUSH 4\nBQMX r0\nPUSH 0\nPUSH 1\nONEHOTC r0\nHALT", []),
    ("InvalidIntegerK", "PUSH 4\nPUSH 1\nXSMX r0\nHALT", []),
    ("SampleOutOfDomain", "PUSH 4\nPUSH 3\nXSMX r0\nPUSH 1\nPUSH 3\nSETLINE r0\nHALT", []),
    ("InvalidAllocation", "PUSH -1\nBQMX r0\nHALT", []),
    ("LoopStackOverflow", "TARGET .0\nPUSH 0\nPUSH 2\nRANGE\nJUMP .0", []),
]


def _run(source: str, calldata: list, *, step_limit: int | None = None, memory_limit: int | None = None) -> None:
    vm = ffi.Vm()
    vm.set_calldata(calldata)
    vm.set_output_slots(1)
    if step_limit is not None:
        vm.set_step_limit(step_limit)
    if memory_limit is not None:
        vm.set_memory_limit(memory_limit)
    vm.run(assemble_source(source))


@pytest.mark.parametrize(("name", "source", "calldata"), RAISED, ids=[case[0] for case in RAISED])
def test_run_raises_the_fault_named_for_the_error(name: str, source: str, calldata: list) -> None:
    with pytest.raises(ffi.XqvmError) as excinfo:
        _run(source, calldata)
    assert type(excinfo.value) is getattr(ffi, name)


def _xqbc(payload: bytes) -> bytes:
    """Wrap `payload` in the 15-byte XQBC header of `spec/xqvm/ENCODING.md`."""
    code_len = len(payload).to_bytes(4, "big")
    crc = zlib.crc32(payload).to_bytes(4, "big")
    return b"XQBC\x01\x00\x00" + code_len + crc + payload


# `PUSH1 1` (two bytes) followed by the faulting instruction at byte 2:
# `0x0D` is the table's reserved gap, and `PUSH2` (`0x12`) with one of its
# two operand bytes runs off the end of the stream.
HAND_BUILT = [
    ("BadOpcode", b"\x11\x01\x0d"),
    ("TruncatedInstruction", b"\x11\x01\x12\x00"),
]


@pytest.mark.parametrize(("name", "payload"), HAND_BUILT, ids=[case[0] for case in HAND_BUILT])
def test_hand_built_bytecode_raises_the_fault_named_for_the_error(name: str, payload: bytes) -> None:
    with pytest.raises(ffi.XqvmError) as excinfo:
        ffi.Vm().run(_xqbc(payload))
    assert type(excinfo.value) is getattr(ffi, name)
    assert excinfo.value.offset == 2


def test_the_offset_is_the_faulting_instruction() -> None:
    # PUSH1 1 and PUSH1 0 take two bytes each, so DIV sits at byte 4.
    with pytest.raises(ffi.DivisionByZero) as excinfo:
        _run("PUSH 1\nPUSH 0\nDIV\nHALT", [])
    assert excinfo.value.offset == 4


def test_a_fault_with_no_instruction_has_no_offset() -> None:
    with pytest.raises(ffi.CallDataIndex) as excinfo:
        _run("PUSH 3\nINPUT r0\nHALT", [])
    assert excinfo.value.offset is None


def test_size_mismatch_from_energy() -> None:
    model = ffi.XqmxModel.binary(2)
    sample = ffi.XqmxSample.binary([0, 1, 1])
    with pytest.raises(ffi.SizeMismatch):
        _run("PUSH 0\nINPUT r0\nPUSH 1\nINPUT r1\nENERGY r0 r1\nHALT", [model, sample])


def test_step_limit_exceeded() -> None:
    with pytest.raises(ffi.StepLimitExceeded):
        _run("PUSH 1\nPUSH 2\nADD\nHALT", [], step_limit=2)


def test_memory_limit_exceeded() -> None:
    with pytest.raises(ffi.MemoryLimitExceeded):
        _run("PUSH 1000\nBQMX r0\nHALT", [], memory_limit=10)


def test_the_message_is_the_display_text() -> None:
    # Display, not Debug: no struct braces and no field names.
    with pytest.raises(ffi.StepLimitExceeded) as excinfo:
        _run("PUSH 1\nPUSH 2\nADD\nHALT", [], step_limit=2)
    assert str(excinfo.value) == "step charge of 1 exceeds the step limit of 2 (2 steps already charged)"


def test_an_undecodable_program_is_not_a_vm_fault() -> None:
    # Decoding happens before the VM runs.
    with pytest.raises(RuntimeError, match="decode error") as excinfo:
        ffi.Vm().run(b"\x43")
    assert not isinstance(excinfo.value, ffi.XqvmError)
    # Display, like the faults: the message, not the variant name.
    assert str(excinfo.value) == "decode error: XQBC header is truncated"


@pytest.mark.parametrize("decode", [instruction_count, verify], ids=["instruction_count", "verify"])
def test_host_decoders_report_the_display_text(decode: Callable[[bytes], object]) -> None:
    with pytest.raises(ValueError) as excinfo:
        decode(b"\x43")
    assert str(excinfo.value) == "decode error: XQBC header is truncated"
