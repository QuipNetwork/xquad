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
    """Raised when execution runs past its step budget."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"Step limit exceeded: execution exceeded {limit} steps")


class TargetNotFound(XQVMError):
    """Raised when a jump target does not exist."""

    def __init__(self, target_id: int):
        self.target_id = target_id
        super().__init__(f"Target not found: {target_id}")


class LoopError(XQVMError):
    """Raised for loop-related errors (e.g., NEXT outside loop)."""

    def __init__(self, message: str):
        super().__init__(message)


class XQMXModeError(XQVMError):
    """Raised when an XQMX operation is invalid for the current mode."""

    def __init__(self, operation: str, mode: str, required_mode: str):
        self.operation = operation
        self.mode = mode
        self.required_mode = required_mode

        super().__init__(f"XQMX mode error: {operation} requires {required_mode} mode, but matrix is in {mode} mode")
