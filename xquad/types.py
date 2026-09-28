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

"""`xquad.types` -- the XQuad model, sample and fault types.

Re-exports the Rust-backed types from `xqffi.vm` so downstream code imports
from `xquad.types`: `Domain`, `XqmxModel`, `XqmxSample`, `triu`, the
`XqvmError` fault hierarchy, and the VM limits.
"""

from xqffi.vm import (
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_STEP_LIMIT,
    MAX_ALLOCATION_SIZE,
    ArithmeticOverflow,
    BadJumpTarget,
    BadOpcode,
    CallDataIndex,
    DivisionByZero,
    Domain,
    IndexOutOfBounds,
    InvalidAllocation,
    InvalidGridDimensions,
    InvalidIntegerK,
    InvalidLabel,
    InvalidShift,
    LoopStackOverflow,
    MemoryLimitExceeded,
    NoActiveLoop,
    OutputIndex,
    SampleOutOfDomain,
    SizeMismatch,
    StackOverflow,
    StackUnderflow,
    StepLimitExceeded,
    TraceFailed,
    TruncatedInstruction,
    TypeMismatch,
    UnmatchedLoop,
    UnsetRegister,
    VecLengthMismatch,
    XqmxModel,
    XqmxSample,
    XqvmError,
    triu,
)

__all__ = [
    "DEFAULT_MEMORY_LIMIT",
    "DEFAULT_STEP_LIMIT",
    "MAX_ALLOCATION_SIZE",
    "ArithmeticOverflow",
    "BadJumpTarget",
    "BadOpcode",
    "CallDataIndex",
    "DivisionByZero",
    "Domain",
    "IndexOutOfBounds",
    "InvalidAllocation",
    "InvalidGridDimensions",
    "InvalidIntegerK",
    "InvalidLabel",
    "InvalidShift",
    "LoopStackOverflow",
    "MemoryLimitExceeded",
    "NoActiveLoop",
    "OutputIndex",
    "SampleOutOfDomain",
    "SizeMismatch",
    "StackOverflow",
    "StackUnderflow",
    "StepLimitExceeded",
    "TraceFailed",
    "TruncatedInstruction",
    "TypeMismatch",
    "UnmatchedLoop",
    "UnsetRegister",
    "VecLengthMismatch",
    "XqmxModel",
    "XqmxSample",
    "XqvmError",
    "triu",
]
