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
XQVM Opcode Definitions
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class OperandType(Enum):
    """Types of operands that opcodes can accept."""

    IMMEDIATE = auto()  # Literal integer value
    REGISTER = auto()  # Register reference (r1-r16)
    TARGET = auto()  # Jump target ID


@dataclass(frozen=True)
class OpcodeMeta:
    """Metadata for an opcode."""

    code: int  # Numeric opcode value
    stack_pop: int  # Number of values popped from stack
    stack_push: int  # Number of values pushed to stack
    operand_count: int  # Number of operands in assembly
    operand_types: tuple[OperandType, ...]  # Types of each operand
    description: str


class Opcode(Enum):
    """XQVM opcodes."""

    # === Opcode Table Begins ===

    # Control Flow (0x00-0x09)
    TARGET = OpcodeMeta(
        0x00,
        0,
        0,
        0,
        (),
        "Define jump target",
    )
    JUMP1 = OpcodeMeta(
        0x01,
        0,
        0,
        1,
        (OperandType.TARGET,),
        "Unconditional jump to target (u8)",
    )
    JUMPI1 = OpcodeMeta(
        0x02,
        1,
        0,
        1,
        (OperandType.TARGET,),
        "Jump to target if top of stack is non-zero (u8)",
    )
    JUMP2 = OpcodeMeta(
        0x03,
        0,
        0,
        2,
        (OperandType.TARGET, OperandType.TARGET),
        "Unconditional jump to target (u16)",
    )
    JUMPI2 = OpcodeMeta(
        0x04,
        1,
        0,
        2,
        (OperandType.TARGET, OperandType.TARGET),
        "Jump to target if top of stack is non-zero (u16)",
    )
    LIDX = OpcodeMeta(
        0x05,
        0,
        0,
        1,
        (OperandType.REGISTER,),
        "Copy current loop index to register",
    )
    LVAL = OpcodeMeta(
        0x06,
        0,
        0,
        1,
        (OperandType.REGISTER,),
        "Copy current loop value to register",
    )
    NEXT = OpcodeMeta(
        0x07,
        0,
        0,
        0,
        (),
        "Advance loop index, jump back or exit",
    )
    RANGE = OpcodeMeta(
        0x08,
        2,
        0,
        0,
        (),
        "Start range loop: pop count, start → iterate [start, start+count)",
    )
    ITER = OpcodeMeta(
        0x09,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Start vec iteration: pop end_idx, start_idx → iterate vec[start:end]",
    )

    # Register Manipulation (0x0A-0x0F)
    LOAD = OpcodeMeta(
        0x0A,
        0,
        1,
        1,
        (OperandType.REGISTER,),
        "Load register value onto stack",
    )
    STOW = OpcodeMeta(
        0x0B,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Store top of stack into register",
    )
    DROP = OpcodeMeta(
        0x0C,
        0,
        0,
        1,
        (OperandType.REGISTER,),
        "Clear register (reset to unset)",
    )
    INPUT = OpcodeMeta(
        0x0E,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Load input slot into register",
    )
    OUTPUT = OpcodeMeta(
        0x0F,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Write register to output slot",
    )

    # Stack Manipulation — Push (0x11-0x18)
    PUSH1 = OpcodeMeta(
        0x11,
        0,
        1,
        1,
        (OperandType.IMMEDIATE,),
        "Push 1-byte integer to stack",
    )
    PUSH2 = OpcodeMeta(
        0x12,
        0,
        1,
        2,
        (OperandType.IMMEDIATE, OperandType.IMMEDIATE),
        "Push 2-byte integer to stack",
    )
    PUSH3 = OpcodeMeta(
        0x13,
        0,
        1,
        3,
        (OperandType.IMMEDIATE, OperandType.IMMEDIATE, OperandType.IMMEDIATE),
        "Push 3-byte integer to stack",
    )
    PUSH4 = OpcodeMeta(
        0x14,
        0,
        1,
        4,
        (OperandType.IMMEDIATE,) * 4,
        "Push 4-byte integer to stack",
    )
    PUSH5 = OpcodeMeta(
        0x15,
        0,
        1,
        5,
        (OperandType.IMMEDIATE,) * 5,
        "Push 5-byte integer to stack",
    )
    PUSH6 = OpcodeMeta(
        0x16,
        0,
        1,
        6,
        (OperandType.IMMEDIATE,) * 6,
        "Push 6-byte integer to stack",
    )
    PUSH7 = OpcodeMeta(
        0x17,
        0,
        1,
        7,
        (OperandType.IMMEDIATE,) * 7,
        "Push 7-byte integer to stack",
    )
    PUSH8 = OpcodeMeta(
        0x18,
        0,
        1,
        8,
        (OperandType.IMMEDIATE,) * 8,
        "Push 8-byte integer to stack",
    )

    # Stack Manipulation — Operations (0x10, 0x1A-0x1C)
    POP = OpcodeMeta(
        0x10,
        1,
        0,
        0,
        (),
        "Pop and discard top of stack",
    )
    SCLR = OpcodeMeta(
        0x1A,
        0,
        0,
        0,
        (),
        "Clear entire stack",
    )
    SWAP = OpcodeMeta(
        0x1B,
        2,
        2,
        0,
        (),
        "Swap top two stack values",
    )
    COPY = OpcodeMeta(
        0x1C,
        1,
        2,
        0,
        (),
        "Duplicate top of stack",
    )

    # Arithmetic (0x20-0x2B)
    ADD = OpcodeMeta(
        0x20,
        2,
        1,
        0,
        (),
        "Add: push(pop() + pop())",
    )
    SUB = OpcodeMeta(
        0x21,
        2,
        1,
        0,
        (),
        "Subtract: push(second - top)",
    )
    MUL = OpcodeMeta(
        0x22,
        2,
        1,
        0,
        (),
        "Multiply: push(pop() * pop())",
    )
    DIV = OpcodeMeta(
        0x23,
        2,
        1,
        0,
        (),
        "Integer divide: push(second / top)",
    )
    MOD = OpcodeMeta(
        0x24,
        2,
        1,
        0,
        (),
        "Modulo: push(second % top)",
    )
    SQR = OpcodeMeta(
        0x25,
        1,
        1,
        0,
        (),
        "Square: push(top * top)",
    )
    ABS = OpcodeMeta(
        0x26,
        1,
        1,
        0,
        (),
        "Absolute value: push(|top|)",
    )
    NEG = OpcodeMeta(
        0x27,
        1,
        1,
        0,
        (),
        "Negate top value",
    )
    MIN = OpcodeMeta(
        0x28,
        2,
        1,
        0,
        (),
        "Minimum: push(min(second, top))",
    )
    MAX = OpcodeMeta(
        0x29,
        2,
        1,
        0,
        (),
        "Maximum: push(max(second, top))",
    )
    INC = OpcodeMeta(
        0x2A,
        1,
        1,
        0,
        (),
        "Increment: push(top + 1)",
    )
    DEC = OpcodeMeta(
        0x2B,
        1,
        1,
        0,
        (),
        "Decrement: push(top - 1)",
    )
    BITLEN = OpcodeMeta(
        0x2C,
        1,
        1,
        0,
        (),
        "Bit length: push(floor(log2(a))+1), 0 if a<=0",
    )

    # Logical — Comparison (0x30-0x34)
    EQ = OpcodeMeta(
        0x30,
        2,
        1,
        0,
        (),
        "Equal: push(1 if second == top else 0)",
    )
    LT = OpcodeMeta(
        0x31,
        2,
        1,
        0,
        (),
        "Less than: push(1 if second < top else 0)",
    )
    GT = OpcodeMeta(
        0x32,
        2,
        1,
        0,
        (),
        "Greater than: push(1 if second > top else 0)",
    )
    LTE = OpcodeMeta(
        0x33,
        2,
        1,
        0,
        (),
        "Less or equal: push(1 if second <= top else 0)",
    )
    GTE = OpcodeMeta(
        0x34,
        2,
        1,
        0,
        (),
        "Greater or equal: push(1 if second >= top else 0)",
    )

    # Logical — Boolean (0x36-0x39)
    NOT = OpcodeMeta(
        0x36,
        1,
        1,
        0,
        (),
        "Logical NOT: push(1 if top == 0 else 0)",
    )
    AND = OpcodeMeta(
        0x37,
        2,
        1,
        0,
        (),
        "Logical AND: push(1 if both non-zero else 0)",
    )
    OR = OpcodeMeta(
        0x38,
        2,
        1,
        0,
        (),
        "Logical OR: push(1 if either non-zero else 0)",
    )
    XOR = OpcodeMeta(
        0x39,
        2,
        1,
        0,
        (),
        "Logical XOR: push(1 if exactly one non-zero else 0)",
    )

    # Logical — Bitwise (0x3A-0x3F)
    BAND = OpcodeMeta(
        0x3A,
        2,
        1,
        0,
        (),
        "Bitwise AND",
    )
    BOR = OpcodeMeta(
        0x3B,
        2,
        1,
        0,
        (),
        "Bitwise OR",
    )
    BXOR = OpcodeMeta(
        0x3C,
        2,
        1,
        0,
        (),
        "Bitwise XOR",
    )
    BNOT = OpcodeMeta(
        0x3D,
        1,
        1,
        0,
        (),
        "Bitwise NOT (complement)",
    )
    SHL = OpcodeMeta(
        0x3E,
        2,
        1,
        0,
        (),
        "Shift left: push(second << top)",
    )
    SHR = OpcodeMeta(
        0x3F,
        2,
        1,
        0,
        (),
        "Shift right: push(second >> top)",
    )

    # Allocators (0x40-0x4A)
    BQMX = OpcodeMeta(
        0x40,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Create binary model XQMX",
    )
    SQMX = OpcodeMeta(
        0x41,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Create spin model XQMX",
    )
    XQMX = OpcodeMeta(
        0x42,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Create discrete model XQMX",
    )
    BSMX = OpcodeMeta(
        0x43,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Create binary sample XQMX",
    )
    SSMX = OpcodeMeta(
        0x44,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Create spin sample XQMX",
    )
    XSMX = OpcodeMeta(
        0x45,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Create discrete sample XQMX",
    )
    VEC = OpcodeMeta(
        0x4A,
        0,
        0,
        1,
        (OperandType.REGISTER,),
        "Create empty vec (type inferred on first push)",
    )
    VECI = OpcodeMeta(
        0x4B,
        0,
        0,
        1,
        (OperandType.REGISTER,),
        "Create empty vec<int>",
    )
    VECX = OpcodeMeta(
        0x4C,
        0,
        0,
        1,
        (OperandType.REGISTER,),
        "Create empty vec<xqmx>",
    )

    # Vector Access (0x50-0x53)
    VECPUSH = OpcodeMeta(
        0x50,
        1,
        0,
        1,
        (OperandType.REGISTER,),
        "Push value onto vec (infers/validates type)",
    )
    VECGET = OpcodeMeta(
        0x51,
        1,
        1,
        1,
        (OperandType.REGISTER,),
        "Get vec[index]",
    )
    VECSET = OpcodeMeta(
        0x52,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Set vec[index] = value (validates type)",
    )
    VECLEN = OpcodeMeta(
        0x53,
        0,
        1,
        1,
        (OperandType.REGISTER,),
        "Push vec length onto stack",
    )
    SLACK = OpcodeMeta(
        0x54,
        2,
        0,
        2,
        (OperandType.REGISTER, OperandType.REGISTER),
        "Append slack variable indices and power-of-two coefficients to vec pair",
    )

    # Vector Math (0x5A-0x5B)
    IDXGRID = OpcodeMeta(
        0x5A,
        3,
        1,
        0,
        (),
        "Convert (row, col) to flat index using cols",
    )
    IDXTRIU = OpcodeMeta(
        0x5B,
        2,
        1,
        0,
        (),
        "Convert (i, j) to upper triangular index",
    )

    # XQMX Access (0x60-0x65)
    GETLINE = OpcodeMeta(
        0x60,
        1,
        1,
        1,
        (OperandType.REGISTER,),
        "Get linear coefficient",
    )
    SETLINE = OpcodeMeta(
        0x61,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Set linear coefficient",
    )
    ADDLINE = OpcodeMeta(
        0x62,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Add to linear coefficient",
    )
    GETQUAD = OpcodeMeta(
        0x63,
        2,
        1,
        1,
        (OperandType.REGISTER,),
        "Get quadratic coefficient",
    )
    SETQUAD = OpcodeMeta(
        0x64,
        3,
        0,
        1,
        (OperandType.REGISTER,),
        "Set quadratic coefficient",
    )
    ADDQUAD = OpcodeMeta(
        0x65,
        3,
        0,
        1,
        (OperandType.REGISTER,),
        "Add to quadratic coefficient",
    )

    # XQMX Grid (0x66-0x6A)
    RESIZE = OpcodeMeta(
        0x66,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Set grid dimensions",
    )
    ROWFIND = OpcodeMeta(
        0x67,
        2,
        1,
        1,
        (OperandType.REGISTER,),
        "Find first col where row has value",
    )
    COLFIND = OpcodeMeta(
        0x68,
        2,
        1,
        1,
        (OperandType.REGISTER,),
        "Find first row where col has value",
    )
    ROWSUM = OpcodeMeta(
        0x69,
        1,
        1,
        1,
        (OperandType.REGISTER,),
        "Sum all values in row",
    )
    COLSUM = OpcodeMeta(
        0x6A,
        1,
        1,
        1,
        (OperandType.REGISTER,),
        "Sum all values in column",
    )

    # XQMX High Level Functions (0x70-0x7F)
    ONEHOTR = OpcodeMeta(
        0x70,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Add one-hot constraint for row",
    )
    ONEHOTC = OpcodeMeta(
        0x71,
        2,
        0,
        1,
        (OperandType.REGISTER,),
        "Add one-hot constraint for column",
    )
    EXCLUDE = OpcodeMeta(
        0x72,
        3,
        0,
        1,
        (OperandType.REGISTER,),
        "Add exclusion constraint with penalty",
    )
    IMPLIES = OpcodeMeta(
        0x73,
        3,
        0,
        1,
        (OperandType.REGISTER,),
        "Add implication constraint with penalty",
    )
    EQUALITY = OpcodeMeta(
        0x74,
        2,
        0,
        3,
        (OperandType.REGISTER, OperandType.REGISTER, OperandType.REGISTER),
        "Expand weighted equality constraint into QUBO terms",
    )
    ATLEAST = OpcodeMeta(
        0x75,
        2,
        0,
        2,
        (OperandType.REGISTER, OperandType.REGISTER),
        "Expand at-least-k constraint with slack variables",
    )
    ATLEASTW = OpcodeMeta(
        0x76,
        2,
        0,
        3,
        (OperandType.REGISTER, OperandType.REGISTER, OperandType.REGISTER),
        "Expand weighted at-least-k constraint with slack variables",
    )
    REDUCE = OpcodeMeta(
        0x77,
        3,
        1,
        1,
        (OperandType.REGISTER,),
        "Rosenberg reduction: replace product with auxiliary variable",
    )
    ENERGY = OpcodeMeta(
        0x7F,
        0,
        1,
        2,
        (OperandType.REGISTER, OperandType.REGISTER),
        "Compute energy",
    )

    # Special (0xF0, 0xFF)
    NOP = OpcodeMeta(
        0xF0,
        0,
        0,
        0,
        (),
        "No operation",
    )
    HALT = OpcodeMeta(
        0xFF,
        0,
        0,
        0,
        (),
        "Stop execution",
    )

    # === Opcode Table Ends ===

    @property
    def meta(self) -> OpcodeMeta:
        """Get the opcode metadata."""
        return self.value

    @property
    def code(self) -> int:
        """Get the numeric opcode value."""
        return self.value.code

    @classmethod
    def from_code(cls, code: int) -> Opcode | None:
        """Look up an opcode by its numeric code."""
        for op in cls:
            if op.code == code:
                return op
        return None

    @classmethod
    def from_name(cls, name: str) -> Opcode | None:
        """Look up an opcode by name (case-insensitive)."""
        try:
            return cls[name.upper()]
        except KeyError:
            return None


# Verify we have exactly 87 opcodes
assert len(Opcode) == 93, f"Expected 93 opcodes, got {len(Opcode)}"
