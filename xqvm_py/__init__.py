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

"""xqvm_py — Python reference VM for the XQuad toolchain.

Flat-layout package: all core modules (executor, state, opcodes, program,
vector, xqmx, errors, tracer) live directly under ``xqvm_py``. Re-exports
below mirror the old ``xqvm.core`` aggregation so existing imports of the
form ``from xqvm_py import Executor, Opcode, ...`` continue to resolve.

xqvm-py is transitional (the Rust ``xqvm`` crate is the production
runtime); see the package README for the full status note.
"""

from __future__ import annotations

__version__ = "0.3.0rc1"

from .errors import (
    DivisionByZero,
    InvalidOpcode,
    LoopError,
    RegisterNotFound,
    StackOverflow,
    StackUnderflow,
    TargetNotFound,
    TypeMismatch,
    XQMXModeError,
    XQVMError,
)
from .executor import Executor
from .opcodes import Opcode, OpcodeMeta, OperandType
from .program import (
    Instruction,
    Program,
    make_program,
    program_from_bytecode,
    program_from_xqasm,
    run_program,
)
from .state import JumpControl, MachineState, Value
from .tracer import Tracer
from .vector import Vec, VecElem
from .xqmx import (
    XQMX,
    XQMXDomain,
    XQMXMode,
    col_find,
    col_indices,
    col_sum,
    compute_energy,
    expand_exclude,
    expand_implies,
    expand_onehot,
    require_model_mode,
    require_sample_mode,
    row_find,
    row_indices,
    row_sum,
    triu,
)

__all__ = [
    "XQMX",
    "DivisionByZero",
    "Executor",
    "Instruction",
    "InvalidOpcode",
    "JumpControl",
    "LoopError",
    "MachineState",
    "Opcode",
    "OpcodeMeta",
    "OperandType",
    "Program",
    "RegisterNotFound",
    "StackOverflow",
    "StackUnderflow",
    "TargetNotFound",
    "Tracer",
    "TypeMismatch",
    "Value",
    "Vec",
    "VecElem",
    "XQMXDomain",
    "XQMXMode",
    "XQMXModeError",
    "XQVMError",
    "__version__",
    "col_find",
    "col_indices",
    "col_sum",
    "compute_energy",
    "expand_exclude",
    "expand_implies",
    "expand_onehot",
    "make_program",
    "program_from_bytecode",
    "program_from_xqasm",
    "require_model_mode",
    "require_sample_mode",
    "row_find",
    "row_indices",
    "row_sum",
    "run_program",
    "triu",
]
