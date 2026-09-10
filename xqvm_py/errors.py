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
XQVM Exception Hierarchy
"""

from typing import Any


class XQVMError(Exception):
    """Base exception for all XQVM errors."""

    pass


class StackUnderflow(XQVMError):
    """Raised when attempting to pop from an empty stack."""

    def __init__(self, required: int = 1, available: int = 0):
        self.required = required
        self.available = available
        super().__init__(f"Stack underflow: need {required}, have {available}")


class StackOverflow(XQVMError):
    """Raised when stack exceeds maximum capacity."""

    def __init__(self, max_size: int):
        self.max_size = max_size
        super().__init__(f"Stack overflow: maximum size {max_size} exceeded")


class TypeMismatch(XQVMError):
    """Raised when an operation receives an unexpected type."""

    def __init__(self, expected: str, got: str, context: str = ""):
        self.expected = expected
        self.got = got
        self.context = context

        msg = f"Type mismatch: expected {expected}, got {got}"
        if context:
            msg += f" in {context}"

        super().__init__(msg)


class RegisterNotFound(XQVMError):
    """Raised when accessing a non-existent register slot."""

    def __init__(self, slot: int):
        self.slot = slot
        super().__init__(f"Register not found: r{slot}")


class InvalidOpcode(XQVMError):
    """Raised when encountering an unknown opcode."""

    def __init__(self, opcode: Any):
        self.opcode = opcode
        super().__init__(f"Invalid opcode: {opcode}")


class DivisionByZero(XQVMError):
    """Raised when attempting to divide by zero."""

    def __init__(self):
        super().__init__("Division by zero")


class ArithmeticOverflow(XQVMError):
    """Raised when a value outside the signed 64-bit range would enter the VM."""

    def __init__(self, value: int, context: str = ""):
        self.value = value
        self.context = context
        msg = f"Arithmetic overflow: {value} is outside signed 64-bit range"
        if context:
            msg += f" ({context})"
        super().__init__(msg)


class MemoryLimitExceeded(XQVMError):
    """Raised when an allocating instruction exceeds the allocation budget.

    Distinct from the step-limit error so an embedder can tell a runaway
    loop from an oversized allocation. Mirrors Rust's
    `xqvm::Error::MemoryLimitExceeded`.
    """

    def __init__(self, requested: int, used: int, limit: int):
        self.requested = requested
        self.used = used
        self.limit = limit
        super().__init__(
            f"Memory limit exceeded: allocation of {requested} bytes exceeds the "
            f"memory limit ({used} of {limit} bytes already charged)"
        )


class InvalidAllocation(XQVMError):
    """Raised when an allocator is handed a size that is not an allocation.

    Mirrors Rust's `xqvm::Error::InvalidAllocation`. The six XQMX allocators
    take their size straight off the value stack, where a negative value is
    one PUSH away; a size that is not an allocation is a program error, not a
    request for an empty model. Rust additionally raises this for a size too
    large for the executing target to address, which cannot arise here
    because Python integers are unbounded.
    """

    def __init__(self, size: int):
        self.size = size
        super().__init__(f"Invalid allocation size: {size}")


class OutputIndex(XQVMError):
    """Raised when OUTPUT addresses a slot beyond the allocated count.

    Mirrors Rust's `xqvm::Error::OutputIndex`. The slot count is fixed
    before the run; writing past it is a program error, not a request to
    grow the output map.
    """

    def __init__(self, index: int, length: int):
        self.index = index
        self.length = length
        super().__init__(f"Output index {index} out of range (len {length})")


class CallDataIndex(XQVMError):
    """Raised when INPUT addresses a calldata slot that does not exist.

    Mirrors Rust's `xqvm::Error::CallDataIndex`. The counterpart to
    `OutputIndex` on the other side of the host boundary: the host fixes
    the calldata before the run, so a slot outside it is a program error
    and not a null result. The bound is the slot count, not the set of
    populated slots -- `spec/xqvm/ISA.md`'s INPUT row admits an in-range
    slot the host left unset, and reading one leaves the register unset
    rather than raising this.
    """

    def __init__(self, index: int, length: int):
        self.index = index
        self.length = length
        super().__init__(f"Calldata index {index} out of range (len {length})")


class IndexOutOfBounds(XQVMError):
    """Raised when an index falls outside its valid range.

    Mirrors Rust's `xqvm::Error::IndexOutOfBounds`, which is what
    `spec/xqvm/ISA.md` names for a grid opcode whose row or column index
    lies outside `[0, extent)`.
    """

    def __init__(self, index: int, length: int):
        self.index = index
        self.length = length
        super().__init__(f"Index {index} out of range [0, {length})")


class InvalidDiscreteK(XQVMError):
    """Raised when a discrete XQMX is allocated with a half-width below 2.

    Mirrors Rust's `xqvm::Error::InvalidDiscreteK`. `XQMX` and `XSMX` take
    `k` off the value stack, and a domain of `[-k, k-1]` needs at least two
    values to be a domain at all.
    """

    def __init__(self, k: int):
        self.k = k
        super().__init__(f"Invalid discrete k: {k} (requires k >= 2)")


class InvalidShift(XQVMError):
    """Raised when SHL or SHR is given a shift amount outside `[0, 64)`.

    Mirrors Rust's `xqvm::Error::InvalidShift`.
    """

    def __init__(self, amount: int):
        self.amount = amount
        super().__init__(f"Invalid shift amount: {amount} (requires 0 <= amount < 64)")


class SizeMismatch(XQVMError):
    """Raised when two XQMX registers that must agree on `size` do not.

    Mirrors Rust's `xqvm::Error::SizeMismatch`, which is what `ENERGY`
    raises for a sample whose variable count differs from the model's.
    """

    def __init__(self, a: int, b: int, context: str = ""):
        self.a = a
        self.b = b
        self.context = context
        msg = f"Size mismatch: {a} vs {b}"
        if context:
            msg += f" ({context})"
        super().__init__(msg)


class VecLengthMismatch(XQVMError):
    """Raised when two operand lists that must be the same length are not.

    Mirrors Rust's `xqvm::Error::VecLengthMismatch`, which is what the
    weighted constraint expansions raise for an `indices`/`coeffs` pair of
    unequal length.
    """

    def __init__(self, what: str, a: int, other: str, b: int):
        self.what = what
        self.a = a
        self.other = other
        self.b = b
        super().__init__(f"Vec length mismatch: {what} has {a}, {other} has {b}")


class TruncatedInstruction(XQVMError):
    """Raised when an instruction's operands run past the end of the stream.

    Mirrors Rust's `xqvm::Error::TruncatedInstruction`.
    """

    def __init__(self, offset: int, needed: int, available: int):
        self.offset = offset
        self.needed = needed
        self.available = available
        super().__init__(
            f"Truncated instruction at byte offset {offset}: need {needed} operand bytes, have {available}"
        )


class InvalidGridDimensions(XQVMError):
    """Raised when grid dimensions are absent or non-positive.

    Mirrors Rust's `xqvm::Error::InvalidGridDimensions`, which is what
    `spec/xqvm/ISA.md` names for RESIZE with a non-positive extent and for
    the grid constraints when no grid is set.
    """

    def __init__(self, rows: int, cols: int):
        self.rows = rows
        self.cols = cols
        super().__init__(f"Invalid grid dimensions: {rows}x{cols}")


class StepLimitExceeded(XQVMError):
    """Raised when execution runs past its step budget.

    All three quantities are required. The charge site is the only place
    that can know them, and a default would render a message that
    contradicts itself -- a `requested` of 0 reads as a charge of nothing
    exceeding a non-empty budget.
    """

    def __init__(self, limit: int, requested: int, used: int):
        self.limit = limit
        self.requested = requested
        self.used = used
        super().__init__(f"step charge of {requested} exceeds the step limit of {limit} ({used} steps already charged)")


class TargetNotFound(XQVMError):
    """Raised when a jump target does not exist."""

    def __init__(self, target_id: int):
        self.target_id = target_id
        super().__init__(f"Target not found: {target_id}")


class LoopStackOverflow(XQVMError):
    """Raised when loop nesting exceeds the maximum depth.

    RANGE and ITER each push a frame and only NEXT pops one, so a program
    that jumps back over a loop header without running its NEXT grows the
    loop stack without bound. Mirrors Rust's
    `xqvm::Error::LoopStackOverflow`.
    """

    def __init__(self, max_depth: int):
        self.max_depth = max_depth
        super().__init__(f"Loop stack overflow: maximum nesting depth {max_depth} exceeded")


class NoActiveLoop(XQVMError):
    """Raised when NEXT, LVAL or LIDX runs with no frame on the loop stack.

    Mirrors Rust's `xqvm::Error::NoActiveLoop`. This and `UnmatchedLoop`
    were one `LoopError` class until QUI-1289: the conformance harness
    resolves a Python exception to a fault identity by class name, so a
    single class for two spec faults left both unassertable.

    The mnemonic is required. Only the raise site knows which instruction
    asked, and the harness surfaces the message as the whole detail of a
    fault mismatch.
    """

    def __init__(self, op: str):
        self.op = op
        super().__init__(f"No active loop: {op} requires an open loop frame")


class UnmatchedLoop(XQVMError):
    """Raised when the empty-loop skip runs off the end of the stream.

    A RANGE with a non-positive count, or an ITER over an empty slice,
    skips its body by scanning forward for the matching NEXT. Reaching the
    end of the program without finding one means the loop was never
    closed. Mirrors Rust's `xqvm::Error::UnmatchedLoop`.
    """

    def __init__(self, pc: int):
        self.pc = pc
        super().__init__(f"Unmatched loop header at {pc}: no matching NEXT found")


class XQMXModeError(XQVMError):
    """Raised when an XQMX operation is invalid for the current mode."""

    def __init__(self, operation: str, mode: str, required_mode: str):
        self.operation = operation
        self.mode = mode
        self.required_mode = required_mode

        super().__init__(f"XQMX mode error: {operation} requires {required_mode} mode, but matrix is in {mode} mode")
