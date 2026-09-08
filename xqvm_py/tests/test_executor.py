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
Tests for Executor and all 84 opcode handlers.
"""

import pytest

from xqvm_py.errors import (
    ArithmeticOverflow,
    CallDataIndex,
    DivisionByZero,
    IndexOutOfBounds,
    InvalidDiscreteK,
    InvalidShift,
    LoopError,
    MemoryLimitExceeded,
    RegisterNotFound,
    StackUnderflow,
    TargetNotFound,
    TypeMismatch,
    VecLengthMismatch,
    XQMXModeError,
)
from xqvm_py.executor import DEFAULT_MEMORY_LIMIT, VEC_ELEMENT_BYTES, Executor
from xqvm_py.opcodes import Opcode
from xqvm_py.program import Instruction, make_program, run_program
from xqvm_py.program import program_from_xqasm as assemble
from xqvm_py.state import I64_MAX, I64_MIN, MachineState
from xqvm_py.vector import Vec
from xqvm_py.xqmx import XQMX


class TestControlFlow:
    """Tests for control flow opcodes."""

    def test_nop(self):
        """NOP does nothing but advance PC."""
        ex = run_program(
            [
                Instruction(Opcode.NOP),
                Instruction(Opcode.NOP),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.halted is True

    def test_halt(self):
        """HALT stops execution."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.HALT),
                Instruction(Opcode.PUSH1, (2,)),  # Never reached
            ]
        )
        assert ex.state.stack_depth == 1
        assert ex.state.peek(0) == 1

    def test_target_and_jump(self):
        """TARGET defines label, JUMP jumps back to it."""
        # Define target first, then jump back to it
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),  # Counter
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.TARGET, ()),  # Target 0 - loop start
                Instruction(Opcode.LOAD, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.ADD),
                Instruction(Opcode.COPY),
                Instruction(Opcode.STOW, (0,)),  # counter++
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.LT),  # counter < 3?
                Instruction(Opcode.JUMPI1, (0,)),  # Jump back to target 0
                Instruction(Opcode.HALT),
            ]
        )
        # Counter should be 3
        assert ex.state.get_register(0) == 3

    def test_forward_jump(self):
        """JUMP to a TARGET defined later in the program."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.JUMP1, (0,)),  # Forward jump to target 0
                Instruction(Opcode.PUSH1, (99,)),  # Skipped
                Instruction(Opcode.STOW, (0,)),  # Skipped
                Instruction(Opcode.TARGET, ()),  # Target 0 defined after jump
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 42

    def test_forward_jumpi(self):
        """JUMPI forward to a TARGET defined later (break-out-of-loop pattern)."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),  # Non-zero condition
                Instruction(Opcode.JUMPI1, (0,)),  # Forward jump
                Instruction(Opcode.PUSH1, (99,)),  # Skipped
                Instruction(Opcode.STOW, (0,)),  # Skipped
                Instruction(Opcode.TARGET, ()),
                Instruction(Opcode.HALT),
            ]
        )
        assert not ex.state.has_register(0)

    def test_jumpi_taken(self):
        """JUMPI jumps when top of stack is non-zero."""
        # Define target first, then conditionally jump to it
        ex = run_program(
            [
                Instruction(Opcode.TARGET, ()),  # Define target 0 here
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.PUSH1, (1,)),  # Non-zero condition
                Instruction(Opcode.NOT),  # Invert to 0, so we don't jump
                Instruction(Opcode.JUMPI1, (0,)),  # Won't jump (0)
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 42

    def test_jumpi_not_taken(self):
        """JUMPI doesn't jump when top of stack is zero."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (99,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.TARGET, ()),  # Define target first
                Instruction(Opcode.PUSH1, (0,)),  # Zero condition
                Instruction(Opcode.JUMPI1, (0,)),  # Should not jump (0)
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 99

    def test_range_loop(self):
        """RANGE creates loop with values [start, start+count)."""
        # Sum 0+1+2+3+4 = 10
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),  # Sum accumulator
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # Start
                Instruction(Opcode.PUSH1, (5,)),  # Count
                Instruction(Opcode.RANGE),
                Instruction(Opcode.TARGET, ()),  # Loop body
                Instruction(Opcode.LVAL, (1,)),  # i -> r1
                Instruction(Opcode.LOAD, (0,)),  # Load sum
                Instruction(Opcode.LOAD, (1,)),  # Load i
                Instruction(Opcode.ADD),
                Instruction(Opcode.STOW, (0,)),  # Store sum
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 10

    def test_range_count_zero_skips_body(self):
        """RANGE with count=0 skips the loop body entirely."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (7,)),
                Instruction(Opcode.STOW, (0,)),  # r0 = 7 (sentinel)
                Instruction(Opcode.PUSH1, (0,)),  # start
                Instruction(Opcode.PUSH1, (0,)),  # count = 0
                Instruction(Opcode.RANGE),
                Instruction(Opcode.PUSH1, (99,)),
                Instruction(Opcode.STOW, (0,)),  # would overwrite sentinel
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 7

    def test_range_count_negative_skips_body(self):
        """RANGE with negative count skips the loop body entirely."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),  # r0 = 42 (sentinel)
                Instruction(Opcode.PUSH1, (0,)),  # start
                Instruction(Opcode.PUSH1, (0xFD,)),  # count = -3 (signed i8)
                Instruction(Opcode.RANGE),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.STOW, (0,)),  # would zero sentinel
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 42

    def test_range_count_zero_nested_skips(self):
        """Zero-count outer RANGE correctly skips past nested inner loops."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),  # outer start
                Instruction(Opcode.PUSH1, (0,)),  # outer count = 0
                Instruction(Opcode.RANGE),  # outer: should skip to outer NEXT
                Instruction(Opcode.PUSH1, (0,)),  # inner start
                Instruction(Opcode.PUSH1, (3,)),  # inner count
                Instruction(Opcode.RANGE),  # inner
                Instruction(Opcode.PUSH1, (99,)),
                Instruction(Opcode.NEXT),  # inner NEXT
                Instruction(Opcode.NEXT),  # outer NEXT
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 42

    def test_range_count_zero_unmatched_raises(self):
        """RANGE with count=0 and no matching NEXT raises LoopError."""
        with pytest.raises(LoopError, match="unmatched"):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.RANGE),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_range_count_one_executes_body_once(self):
        """RANGE with count=1 executes the loop body exactly once."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.STOW, (0,)),  # r0 = 0 (counter)
                Instruction(Opcode.PUSH1, (5,)),  # start
                Instruction(Opcode.PUSH1, (1,)),  # count = 1
                Instruction(Opcode.RANGE),
                Instruction(Opcode.LOAD, (0,)),
                Instruction(Opcode.INC),
                Instruction(Opcode.STOW, (0,)),  # r0 += 1
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 1

    def test_range_positive_nested_still_works(self):
        """Positive-count nested RANGE loops still work after skip changes."""
        # outer: count=2, inner: count=3 -> body runs 6 times
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.STOW, (0,)),  # r0 = 0 (counter)
                Instruction(Opcode.PUSH1, (0,)),  # outer start
                Instruction(Opcode.PUSH1, (2,)),  # outer count
                Instruction(Opcode.RANGE),
                Instruction(Opcode.PUSH1, (0,)),  # inner start
                Instruction(Opcode.PUSH1, (3,)),  # inner count
                Instruction(Opcode.RANGE),
                Instruction(Opcode.LOAD, (0,)),
                Instruction(Opcode.INC),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.NEXT),  # inner
                Instruction(Opcode.NEXT),  # outer
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 6

    def test_iter_loop(self):
        """ITER iterates over vector elements."""
        ex = run_program(
            [
                # Create vec [10, 20, 30]
                Instruction(Opcode.VECI, (0,)),
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.PUSH1, (20,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.PUSH1, (30,)),
                Instruction(Opcode.VECPUSH, (0,)),
                # Sum accumulator
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.STOW, (1,)),
                # Iterate: start=0, end=3
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.ITER, (0,)),
                Instruction(Opcode.TARGET, ()),
                Instruction(Opcode.LVAL, (2,)),  # val -> r2
                Instruction(Opcode.LOAD, (1,)),  # Load sum
                Instruction(Opcode.LOAD, (2,)),  # Load val
                Instruction(Opcode.ADD),
                Instruction(Opcode.STOW, (1,)),
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(1) == 60

    def test_lval(self):
        """LVAL copies current loop value to register."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),  # Start
                Instruction(Opcode.PUSH1, (1,)),  # Count
                Instruction(Opcode.RANGE),
                Instruction(Opcode.TARGET, ()),
                Instruction(Opcode.LVAL, (0,)),  # Store loop value in r0
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 5


class TestStackRegisterIO:
    """Tests for stack, register, and I/O opcodes."""

    def test_push(self):
        """PUSH puts immediate value on stack."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 42

    def test_pop(self):
        """POP removes top of stack."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.POP),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.stack_depth == 1
        assert ex.state.peek(0) == 1

    def test_copy(self):
        """COPY duplicates top of stack."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.COPY),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.stack_depth == 2
        assert ex.state.peek(0) == 42
        assert ex.state.peek(1) == 42

    def test_swap(self):
        """SWAP exchanges top two stack values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.SWAP),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1
        assert ex.state.peek(1) == 2

    def test_sclr(self):
        """SCLR clears entire stack."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.SCLR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.stack_depth == 0

    def test_sclr_empty_stack(self):
        """SCLR on empty stack is a no-op."""
        ex = run_program(
            [
                Instruction(Opcode.SCLR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.stack_depth == 0

    def test_load(self):
        """LOAD pushes register value onto stack."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (100,)),
                Instruction(Opcode.STOW, (5,)),
                Instruction(Opcode.LOAD, (5,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 100

    def test_stow(self):
        """STOW stores stack top in register."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(0) == 42
        assert ex.state.stack_depth == 0

    def test_input(self):
        """INPUT loads input slot to register."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),  # slot index on stack
                Instruction(Opcode.INPUT, (0,)),  # input[0] -> r0
                Instruction(Opcode.HALT),
            ],
            input_data={0: 42},
        )
        assert ex.state.get_register(0) == 42

    def test_output(self):
        """OUTPUT writes register to output slot."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (99,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # slot index on stack
                Instruction(Opcode.OUTPUT, (0,)),  # r0 -> output[0]
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        output = ex.execute(prog, output_slots=16)
        assert output[0] == 99

    def test_drop_int_register(self):
        """DROP clears an int register."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.DROP, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert not ex.state.has_register(0)

    def test_drop_vec_register(self):
        """DROP clears a vec register."""
        ex = run_program(
            [
                Instruction(Opcode.VEC, (0,)),
                Instruction(Opcode.DROP, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert not ex.state.has_register(0)

    def test_drop_unset_register(self):
        """DROP on unset register is a no-op."""
        ex = run_program(
            [
                Instruction(Opcode.DROP, (5,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert not ex.state.has_register(5)

    def test_drop_then_load_raises(self):
        """LOAD after DROP raises RegisterNotFound."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.DROP, (0,)),
                Instruction(Opcode.LOAD, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(RegisterNotFound):
            Executor().execute(prog)


class TestPushConstant:
    """Tests for PUSH1-PUSH8 push constant opcodes."""

    def test_ldc1_positive(self):
        """PUSH1 loads 1-byte positive constant."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 42

    def test_ldc1_negative(self):
        """PUSH1 loads 1-byte negative constant (two's complement)."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0xFF,)),  # -1
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -1

    def test_ldc1_zero(self):
        """PUSH1 loads zero."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_ldc2(self):
        """PUSH2 loads 2-byte constant."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH2, (0x01, 0x00)),  # 256
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 256

    def test_ldc2_negative(self):
        """PUSH2 loads 2-byte negative constant."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH2, (0xFF, 0xFE)),  # -2
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -2

    def test_ldc3(self):
        """PUSH3 loads 3-byte constant."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH3, (0x01, 0x00, 0x00)),  # 65536
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 65536

    def test_ldc4(self):
        """PUSH4 loads 4-byte constant."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH4, (0x00, 0x01, 0x00, 0x00)),  # 65536
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 65536

    def test_ldc4_max_positive(self):
        """PUSH4 loads max positive 4-byte value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH4, (0x7F, 0xFF, 0xFF, 0xFF)),  # 2^31 - 1
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 2147483647

    def test_ldc4_min_negative(self):
        """PUSH4 loads min negative 4-byte value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH4, (0x80, 0x00, 0x00, 0x00)),  # -2^31
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -2147483648

    def test_ldc8(self):
        """PUSH8 loads 8-byte constant."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH8, (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00)),  # 256
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 256

    def test_ldc8_large_negative(self):
        """PUSH8 loads large negative value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH8, (0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF)),  # -1
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -1


class TestArithmetic:
    """Tests for arithmetic opcodes."""

    def test_add(self):
        """ADD sums top two values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.ADD),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 15

    def test_sub(self):
        """SUB subtracts (second - top)."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.SUB),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 7

    def test_mul(self):
        """MUL multiplies top two values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (6,)),
                Instruction(Opcode.PUSH1, (7,)),
                Instruction(Opcode.MUL),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 42

    def test_div(self):
        """DIV performs integer division."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (20,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.DIV),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 6

    def test_div_by_zero(self):
        """DIV by zero raises DivisionByZero."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.DIV),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(DivisionByZero):
            Executor().execute(prog)

    def test_mod(self):
        """MOD computes remainder."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (17,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.MOD),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 2

    def test_mod_by_zero(self):
        """MOD by zero raises DivisionByZero."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.MOD),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(DivisionByZero):
            Executor().execute(prog)

    def test_neg(self):
        """NEG negates top value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.NEG),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -42

    def test_neg_negative(self):
        """NEG on negative produces positive."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (246,)),
                Instruction(Opcode.NEG),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 10

    def test_sqr(self):
        """SQR squares top value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (7,)),
                Instruction(Opcode.SQR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 49

    def test_sqr_negative(self):
        """SQR of negative is positive."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (253,)),
                Instruction(Opcode.SQR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 9

    def test_sqr_zero(self):
        """SQR of zero is zero."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.SQR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_abs_positive(self):
        """ABS of positive is unchanged."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.ABS),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 5

    def test_abs_negative(self):
        """ABS of negative is positive."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (251,)),
                Instruction(Opcode.ABS),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 5

    def test_abs_zero(self):
        """ABS of zero is zero."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.ABS),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_min(self):
        """MIN pushes the smaller of two values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.MIN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 3

    def test_min_equal(self):
        """MIN with equal values returns that value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.MIN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 5

    def test_min_negative(self):
        """MIN with negative values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (255,)),
                Instruction(Opcode.PUSH1, (251,)),
                Instruction(Opcode.MIN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -5

    def test_max(self):
        """MAX pushes the larger of two values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.MAX),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 10

    def test_max_negative(self):
        """MAX with negative values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (255,)),
                Instruction(Opcode.PUSH1, (251,)),
                Instruction(Opcode.MAX),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -1

    def test_inc(self):
        """INC increments top value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (41,)),
                Instruction(Opcode.INC),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 42

    def test_inc_negative(self):
        """INC on -1 produces 0."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (255,)),
                Instruction(Opcode.INC),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_dec(self):
        """DEC decrements top value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (43,)),
                Instruction(Opcode.DEC),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 42

    def test_dec_zero(self):
        """DEC on 0 produces -1."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.DEC),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == -1


class TestComparison:
    """Tests for comparison opcodes."""

    def test_eq_true(self):
        """EQ returns 1 when equal."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.EQ),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_eq_false(self):
        """EQ returns 0 when not equal."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.EQ),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_lt_true(self):
        """LT returns 1 when second < top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.LT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_lt_false(self):
        """LT returns 0 when second >= top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.LT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_gt_true(self):
        """GT returns 1 when second > top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.GT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_gt_false(self):
        """GT returns 0 when second <= top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.GT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_lte_true_less(self):
        """LTE returns 1 when second < top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.LTE),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_lte_true_equal(self):
        """LTE returns 1 when equal."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.LTE),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_lte_false(self):
        """LTE returns 0 when second > top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (7,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.LTE),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_gte_true_greater(self):
        """GTE returns 1 when second > top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (7,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.GTE),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_gte_true_equal(self):
        """GTE returns 1 when equal."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.GTE),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_gte_false(self):
        """GTE returns 0 when second < top."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.GTE),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0


class TestBoolean:
    """Tests for boolean logic opcodes."""

    def test_not_true_to_false(self):
        """NOT converts non-zero to 0."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.NOT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_not_false_to_true(self):
        """NOT converts 0 to 1."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.NOT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_and_both_true(self):
        """AND of two truthy values is 1."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.AND),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_and_one_false(self):
        """AND with one falsy value is 0."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.AND),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_or_both_true(self):
        """OR of two truthy values is 1."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.OR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_or_one_true(self):
        """OR with one truthy value is 1."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.OR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_or_both_false(self):
        """OR of two falsy values is 0."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.OR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_xor_different(self):
        """XOR of different values is 1."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.XOR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_xor_same(self):
        """XOR of same values is 0."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.XOR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0


class TestBitwise:
    """Tests for bitwise opcodes."""

    def test_band(self):
        """BAND performs bitwise AND."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0b1100,)),
                Instruction(Opcode.PUSH1, (0b1010,)),
                Instruction(Opcode.BAND),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0b1000

    def test_bor(self):
        """BOR performs bitwise OR."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0b1100,)),
                Instruction(Opcode.PUSH1, (0b1010,)),
                Instruction(Opcode.BOR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0b1110

    def test_bxor(self):
        """BXOR performs bitwise XOR."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0b1100,)),
                Instruction(Opcode.PUSH1, (0b1010,)),
                Instruction(Opcode.BXOR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0b0110

    def test_bnot(self):
        """BNOT performs bitwise NOT."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.BNOT),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == ~0

    def test_shl(self):
        """SHL shifts left."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (4,)),
                Instruction(Opcode.SHL),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 16

    def test_shr(self):
        """SHR shifts right."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (16,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.SHR),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 4


class TestAllocators:
    """Tests for allocator opcodes."""

    def test_vec_creates_empty(self):
        """VEC creates empty untyped vector."""
        ex = run_program(
            [
                Instruction(Opcode.VEC, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        v = ex.state.get_register(0)
        assert isinstance(v, Vec)
        assert v.length == 0

    def test_veci_creates_int_vec(self):
        """VECI creates empty int vector."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        v = ex.state.get_register(0)
        assert isinstance(v, Vec)
        assert v.element_type.kind == "int"

    def test_vecx_creates_xqmx_vec(self):
        """VECX creates empty xqmx vector."""
        ex = run_program(
            [
                Instruction(Opcode.VECX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        v = ex.state.get_register(0)
        assert isinstance(v, Vec)
        assert v.element_type.kind == "xqmx"

    def test_bqmx_creates_binary_model(self):
        """BQMX creates binary model XQMX."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),  # size
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert isinstance(x, XQMX)
        assert x.is_model()
        assert x.size == 10

    def test_sqmx_creates_spin_model(self):
        """SQMX creates spin model XQMX."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (8,)),
                Instruction(Opcode.SQMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert x.is_model()
        assert x.size == 8

    def test_xqmx_creates_discrete_model(self):
        """XQMX creates discrete model with k values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),  # size
                Instruction(Opcode.PUSH1, (3,)),  # k
                Instruction(Opcode.XQMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert x.is_model()
        assert x.discrete_k == 3

    def test_bsmx_creates_binary_sample(self):
        """BSMX creates binary sample XQMX."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.BSMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert x.is_sample()

    def test_ssmx_creates_spin_sample(self):
        """SSMX creates spin sample XQMX."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.SSMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert x.is_sample()

    def test_xsmx_creates_discrete_sample(self):
        """XSMX creates discrete sample with k values."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (4,)),
                Instruction(Opcode.XSMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert x.is_sample()
        assert x.discrete_k == 4


class TestVectorAccess:
    """Tests for vector access opcodes."""

    def test_vecpush(self):
        """VECPUSH appends value to vector."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (0,)),
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        v = ex.state.get_register(0)
        assert v.length == 1
        assert v.get(0) == 42

    def test_vecget(self):
        """VECGET gets value at index."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (0,)),
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.PUSH1, (20,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.PUSH1, (1,)),  # index
                Instruction(Opcode.VECGET, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 20

    def test_vecset(self):
        """VECSET sets value at index."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (0,)),
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # index
                Instruction(Opcode.PUSH1, (99,)),  # value
                Instruction(Opcode.VECSET, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        v = ex.state.get_register(0)
        assert v.get(0) == 99

    def test_veclen(self):
        """VECLEN pushes vector length."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.VECLEN, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 3


class TestVectorMath:
    """Tests for vector math opcodes."""

    def test_idxgrid(self):
        """IDXGRID converts (row, col) to flat index."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (2,)),  # row
                Instruction(Opcode.PUSH1, (3,)),  # col
                Instruction(Opcode.PUSH1, (5,)),  # cols
                Instruction(Opcode.IDXGRID),
                Instruction(Opcode.HALT),
            ]
        )
        # index = row * cols + col = 2 * 5 + 3 = 13
        assert ex.state.peek(0) == 13

    def test_idxtriu(self):
        """IDXTRIU converts (i, j) to upper triangular index."""
        # idx = j * (j - 1) // 2 + i (with i < j)
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),  # i
                Instruction(Opcode.PUSH1, (2,)),  # j
                Instruction(Opcode.IDXTRIU),
                Instruction(Opcode.HALT),
            ]
        )
        # With i=0, j=2: idx = 2 * (2-1) / 2 + 0 = 1
        assert ex.state.peek(0) == 1


class TestXQMXAccess:
    """Tests for XQMX coefficient access opcodes."""

    def test_getline(self):
        """GETLINE gets linear coefficient."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (5,)),  # index
                Instruction(Opcode.GETLINE, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        # Default is 0
        assert ex.state.peek(0) == 0

    def test_setline(self):
        """SETLINE sets linear coefficient."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (3,)),  # index
                Instruction(Opcode.PUSH1, (5,)),  # value (as int, will be float)
                Instruction(Opcode.SETLINE, (0,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.GETLINE, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 5

    def test_addline(self):
        """ADDLINE adds to linear coefficient."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.SETLINE, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.ADDLINE, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.GETLINE, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 5

    def test_getquad(self):
        """GETQUAD gets quadratic coefficient."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # i
                Instruction(Opcode.PUSH1, (1,)),  # j
                Instruction(Opcode.GETQUAD, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_setquad(self):
        """SETQUAD sets quadratic coefficient."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # i
                Instruction(Opcode.PUSH1, (1,)),  # j
                Instruction(Opcode.PUSH1, (7,)),  # value
                Instruction(Opcode.SETQUAD, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.GETQUAD, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 7

    def test_addquad(self):
        """ADDQUAD adds to quadratic coefficient."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.SETQUAD, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (4,)),
                Instruction(Opcode.ADDQUAD, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.GETQUAD, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 7


class TestXQMXGrid:
    """Tests for XQMX grid opcodes."""

    def test_resize(self):
        """RESIZE sets grid dimensions."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (25,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (5,)),  # rows
                Instruction(Opcode.PUSH1, (5,)),  # cols
                Instruction(Opcode.RESIZE, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert x.rows == 5
        assert x.cols == 5

    def test_rowfind(self):
        """ROWFIND finds first column with value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (25,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.RESIZE, (0,)),
                # Set value at row 0, col 2 (index 2)
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SETLINE, (0,)),
                # Find in row 0
                Instruction(Opcode.PUSH1, (0,)),  # row
                Instruction(Opcode.PUSH1, (1,)),  # value
                Instruction(Opcode.ROWFIND, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 2

    def test_colfind(self):
        """COLFIND finds first row with value."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (25,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.RESIZE, (0,)),
                # Set value at row 2, col 0 (index 10)
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SETLINE, (0,)),
                # Find in col 0
                Instruction(Opcode.PUSH1, (0,)),  # col
                Instruction(Opcode.PUSH1, (1,)),  # value
                Instruction(Opcode.COLFIND, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 2

    def test_rowsum(self):
        """ROWSUM sums values in row."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (25,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.RESIZE, (0,)),
                # Set values in row 0: indices 0, 1, 2
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SETLINE, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.SETLINE, (0,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.SETLINE, (0,)),
                # Sum row 0
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.ROWSUM, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 6

    def test_colsum(self):
        """COLSUM sums values in column."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (25,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.RESIZE, (0,)),
                # Set values in col 0: indices 0, 5, 10
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SETLINE, (0,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.SETLINE, (0,)),
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.SETLINE, (0,)),
                # Sum col 0
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.COLSUM, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 6


class TestXQMXHighLevel:
    """Tests for XQMX high-level function opcodes."""

    def test_onehotr(self):
        """ONEHOTR adds one-hot constraint for a row."""
        ex = run_program(
            [
                # Create 3x3 grid model (size=9)
                Instruction(Opcode.PUSH1, (9,)),
                Instruction(Opcode.BQMX, (0,)),
                # Set grid dimensions
                Instruction(Opcode.PUSH1, (3,)),  # rows
                Instruction(Opcode.PUSH1, (3,)),  # cols
                Instruction(Opcode.RESIZE, (0,)),
                # Apply one-hot constraint for row 0 with penalty 1
                Instruction(Opcode.PUSH1, (0,)),  # row
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(Opcode.ONEHOTR, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        # Row 0 indices are [0, 1, 2]: should have linear and quadratic terms
        assert len(x.linear) > 0 or len(x.quadratic) > 0

    def test_onehotc(self):
        """ONEHOTC adds one-hot constraint for a column."""
        ex = run_program(
            [
                # Create 3x3 grid model (size=9)
                Instruction(Opcode.PUSH1, (9,)),
                Instruction(Opcode.BQMX, (0,)),
                # Set grid dimensions
                Instruction(Opcode.PUSH1, (3,)),  # rows
                Instruction(Opcode.PUSH1, (3,)),  # cols
                Instruction(Opcode.RESIZE, (0,)),
                # Apply one-hot constraint for col 0 with penalty 1
                Instruction(Opcode.PUSH1, (0,)),  # col
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(Opcode.ONEHOTC, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        # Col 0 indices are [0, 3, 6]: should have linear and quadratic terms
        assert x.linear.get(0) == -1
        assert x.linear.get(3) == -1
        assert x.linear.get(6) == -1
        assert x.quadratic.get((0, 3)) == 2
        assert x.quadratic.get((0, 6)) == 2
        assert x.quadratic.get((3, 6)) == 2

    def test_exclude(self):
        """EXCLUDE adds exclusion constraint."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # i
                Instruction(Opcode.PUSH1, (1,)),  # j
                Instruction(Opcode.PUSH1, (2,)),  # penalty
                Instruction(Opcode.EXCLUDE, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        assert x.get_quadratic(0, 1) == 2

    def test_implies(self):
        """IMPLIES adds implication constraint."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # i
                Instruction(Opcode.PUSH1, (1,)),  # j
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(Opcode.IMPLIES, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        x = ex.state.get_register(0)
        # Should have some coefficients set
        assert len(x.linear) > 0 or len(x.quadratic) > 0

    def test_energy(self):
        """ENERGY computes energy of sample against model."""
        ex = run_program(
            [
                # Create model with linear terms
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SETLINE, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.SETLINE, (0,)),
                # Create sample with values
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.BSMX, (1,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SETLINE, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SETLINE, (1,)),
                # Compute energy
                Instruction(Opcode.ENERGY, (0, 1)),
                Instruction(Opcode.HALT),
            ]
        )
        # Energy = 1*1 + 2*1 = 3
        assert ex.state.peek(0) == 3


class TestBitlen:
    """Tests for BITLEN opcode."""

    def test_positive(self):
        """BITLEN(8) = 4."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (8,)),
                Instruction(Opcode.BITLEN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 4

    def test_one(self):
        """BITLEN(1) = 1."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.BITLEN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 1

    def test_zero(self):
        """BITLEN(0) = 0."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.BITLEN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_negative(self):
        """BITLEN(-1) = 0."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0xFF,)),  # -1 in signed 1-byte
                Instruction(Opcode.BITLEN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 0

    def test_power_of_two(self):
        """BITLEN(16) = 5."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (16,)),
                Instruction(Opcode.BITLEN),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == 5


class TestSlack:
    """Tests for SLACK opcode."""

    def test_basic(self):
        """SLACK with capacity=7, start_index=10 produces 3 vars."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (10,)),  # start_index
                Instruction(Opcode.PUSH1, (7,)),  # capacity
                Instruction(Opcode.SLACK, (1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        idx_vec = ex.state.get_register(1)
        coeff_vec = ex.state.get_register(2)
        assert idx_vec.length == 3
        assert [idx_vec.get(i) for i in range(3)] == [10, 11, 12]
        assert coeff_vec.length == 3
        assert [coeff_vec.get(i) for i in range(3)] == [1, 2, 4]

    def test_capacity_zero(self):
        """SLACK with capacity=0 appends nothing."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (0,)),  # start_index
                Instruction(Opcode.PUSH1, (0,)),  # capacity
                Instruction(Opcode.SLACK, (1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(1).length == 0
        assert ex.state.get_register(2).length == 0

    def test_capacity_negative(self):
        """SLACK with capacity=-1 appends nothing."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (0,)),  # start_index
                Instruction(Opcode.PUSH1, (0xFF,)),  # -1 in signed 1-byte
                Instruction(Opcode.SLACK, (1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.get_register(1).length == 0

    def test_capacity_one(self):
        """SLACK with capacity=1 produces 1 var."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (5,)),  # start_index
                Instruction(Opcode.PUSH1, (1,)),  # capacity
                Instruction(Opcode.SLACK, (1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        idx_vec = ex.state.get_register(1)
        coeff_vec = ex.state.get_register(2)
        assert idx_vec.length == 1
        assert idx_vec.get(0) == 5
        assert coeff_vec.get(0) == 1

    def test_appends_to_existing(self):
        """SLACK appends to vecs that already have elements."""
        ex = run_program(
            [
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (99,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (77,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (0,)),  # start_index
                Instruction(Opcode.PUSH1, (3,)),  # capacity
                Instruction(Opcode.SLACK, (1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        idx_vec = ex.state.get_register(1)
        coeff_vec = ex.state.get_register(2)
        assert idx_vec.length == 3  # 1 existing + 2 slack
        assert idx_vec.get(0) == 99
        assert idx_vec.get(1) == 0
        assert idx_vec.get(2) == 1
        assert coeff_vec.get(0) == 77
        assert coeff_vec.get(1) == 1
        assert coeff_vec.get(2) == 2


class TestEquality:
    """Tests for EQUALITY opcode."""

    def test_matches_onehot(self):
        """EQUALITY with unit coeffs and target=1 matches ONEHOTR."""
        # Build via EQUALITY
        ex_eq = run_program(
            [
                Instruction(Opcode.PUSH1, (9,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (1,)),  # target
                Instruction(Opcode.PUSH1, (10,)),  # penalty
                Instruction(Opcode.EQUALITY, (0, 1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        # Build via ONEHOTR
        ex_oh = run_program(
            [
                Instruction(Opcode.PUSH1, (9,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.RESIZE, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # row
                Instruction(Opcode.PUSH1, (10,)),  # penalty
                Instruction(Opcode.ONEHOTR, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        m_eq = ex_eq.state.get_register(0)
        m_oh = ex_oh.state.get_register(0)
        assert m_eq.linear == m_oh.linear
        assert m_eq.quadratic == m_oh.quadratic

    def test_weighted(self):
        """EQUALITY with weighted coefficients produces correct terms."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (5,)),  # target
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(Opcode.EQUALITY, (0, 1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        m = ex.state.get_register(0)
        # linear[0] = 1 * 2 * (2 - 10) = -16
        # linear[1] = 1 * 3 * (3 - 10) = -21
        assert m.get_linear(0) == -16
        assert m.get_linear(1) == -21
        # quad[0,1] = 1 * 2 * 2 * 3 = 12
        assert m.get_quadratic(0, 1) == 12


class TestAtleast:
    """Tests for ATLEAST opcode."""

    def test_basic(self):
        """ATLEAST with 4 vars, k=2 allocates slack vars and expands."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (4,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (2,)),  # k
                Instruction(Opcode.PUSH1, (10,)),  # penalty
                Instruction(Opcode.ATLEAST, (0, 1)),
                Instruction(Opcode.HALT),
            ]
        )
        m = ex.state.get_register(0)
        # max_excess = 4 - 2 = 2, bit_length(2) = 2 slack vars
        assert m.size == 6  # 4 original + 2 slack
        assert len(m.linear) > 0 or len(m.quadratic) > 0

    def test_k_equals_n(self):
        """ATLEAST with k=N: no slack, just equality on all vars."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (3,)),  # k = N
                Instruction(Opcode.PUSH1, (10,)),  # penalty
                Instruction(Opcode.ATLEAST, (0, 1)),
                Instruction(Opcode.HALT),
            ]
        )
        m = ex.state.get_register(0)
        assert m.size == 3  # no slack variables added

    def test_k_zero_raises(self):
        """ATLEAST with k=0 raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (3,)),
                    Instruction(Opcode.BQMX, (0,)),
                    Instruction(Opcode.VECI, (1,)),
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (0,)),  # k = 0
                    Instruction(Opcode.PUSH1, (10,)),
                    Instruction(Opcode.ATLEAST, (0, 1)),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_k_exceeds_n_raises(self):
        """ATLEAST with k > N raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (3,)),
                    Instruction(Opcode.BQMX, (0,)),
                    Instruction(Opcode.VECI, (1,)),
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (5,)),  # k > N=2
                    Instruction(Opcode.PUSH1, (10,)),
                    Instruction(Opcode.ATLEAST, (0, 1)),
                    Instruction(Opcode.HALT),
                ]
            )


class TestAtleastw:
    """Tests for ATLEASTW opcode."""

    def test_weighted_basic(self):
        """ATLEASTW with 3 vars, weights=[2,3,5], k=4 allocates slack vars."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.VECI, (1,)),
                Instruction(Opcode.VECI, (2,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.VECPUSH, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.VECPUSH, (2,)),
                Instruction(Opcode.PUSH1, (4,)),  # k
                Instruction(Opcode.PUSH1, (10,)),  # penalty
                Instruction(Opcode.ATLEASTW, (0, 1, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        m = ex.state.get_register(0)
        # max_excess = (2+3+5) - 4 = 6, bit_length(6) = 3 slack vars
        assert m.size == 6  # 3 original + 3 slack
        assert len(m.linear) > 0

    def test_length_mismatch_raises(self):
        """ATLEASTW with mismatched indices/coeffs raises VecLengthMismatch."""
        with pytest.raises(VecLengthMismatch, match="indices"):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (5,)),
                    Instruction(Opcode.BQMX, (0,)),
                    Instruction(Opcode.VECI, (1,)),
                    Instruction(Opcode.VECI, (2,)),
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.VECPUSH, (2,)),  # only 1 coeff vs 2 indices
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.PUSH1, (10,)),
                    Instruction(Opcode.ATLEASTW, (0, 1, 2)),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_weight_sum_overflow_raises(self):
        """ATLEASTW with a weight sum past i64::MAX raises ArithmeticOverflow.

        The operands discriminate rather than merely agree: three weights of
        2^62 wrap to -2^62, which makes the excess negative, allocates no
        slack variables and -- with a penalty of 0 -- emits nothing, so a
        wrapping implementation halts with a size-3 all-zero model. A weight
        pair that also faulted when wrapped would not tell the two apart.
        """
        with pytest.raises(ArithmeticOverflow, match=r"\(ATLEASTW weight sum\)"):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (3,)),
                    Instruction(Opcode.BQMX, (0,)),
                    Instruction(Opcode.VECI, (1,)),
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (2,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.VECI, (2,)),
                    _push_i64(1 << 62),
                    Instruction(Opcode.VECPUSH, (2,)),
                    _push_i64(1 << 62),
                    Instruction(Opcode.VECPUSH, (2,)),
                    _push_i64(1 << 62),
                    Instruction(Opcode.VECPUSH, (2,)),
                    _push_i64(1 << 61),  # k
                    Instruction(Opcode.PUSH1, (0,)),  # penalty
                    Instruction(Opcode.ATLEASTW, (0, 1, 2)),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_k_zero_raises(self):
        """ATLEASTW with k=0 raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (3,)),
                    Instruction(Opcode.BQMX, (0,)),
                    Instruction(Opcode.VECI, (1,)),
                    Instruction(Opcode.VECI, (2,)),
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.VECPUSH, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.VECPUSH, (2,)),
                    Instruction(Opcode.PUSH1, (0,)),  # k = 0
                    Instruction(Opcode.PUSH1, (10,)),
                    Instruction(Opcode.ATLEASTW, (0, 1, 2)),
                    Instruction(Opcode.HALT),
                ]
            )


class TestReduce:
    """Tests for REDUCE opcode."""

    def test_basic(self):
        """REDUCE allocates auxiliary and pushes its index."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # var_a
                Instruction(Opcode.PUSH1, (1,)),  # var_b
                Instruction(Opcode.PUSH1, (10,)),  # P_aux
                Instruction(Opcode.REDUCE, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        m = ex.state.get_register(0)
        w = ex.state.peek(0)
        assert w == 3
        assert m.size == 4
        assert m.get_quadratic(0, 1) == 10
        assert m.get_quadratic(0, 3) == -20
        assert m.get_quadratic(1, 3) == -20
        assert m.get_linear(3) == 30

    def test_chaining(self):
        """Two successive REDUCE calls allocate distinct auxiliaries."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (4,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.REDUCE, (0,)),  # w1 = 4
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.REDUCE, (0,)),  # w2 = 5
                Instruction(Opcode.HALT),
            ]
        )
        m = ex.state.get_register(0)
        w2 = ex.state.peek(0)
        # Pop w2 to get w1 underneath
        ex.state.pop()
        w1 = ex.state.peek(0)
        assert w1 == 4
        assert w2 == 5
        assert m.size == 6

    def test_var_out_of_range_raises(self):
        """REDUCE with var_a out of range raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (3,)),
                    Instruction(Opcode.BQMX, (0,)),
                    Instruction(Opcode.PUSH1, (5,)),  # var_a out of range
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.PUSH1, (10,)),
                    Instruction(Opcode.REDUCE, (0,)),
                    Instruction(Opcode.HALT),
                ]
            )


class TestErrorHandling:
    """Tests for error conditions."""

    def test_stack_underflow(self):
        """Stack underflow raises StackUnderflow."""
        prog = make_program(
            [
                Instruction(Opcode.POP),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(StackUnderflow):
            Executor().execute(prog)

    def test_register_not_found(self):
        """Missing register raises RegisterNotFound."""
        prog = make_program(
            [
                Instruction(Opcode.LOAD, (99,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(RegisterNotFound):
            Executor().execute(prog)

    def test_type_mismatch_vec_expected(self):
        """Type mismatch when Vec expected raises TypeMismatch."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),  # r0 = int
                Instruction(Opcode.VECLEN, (0,)),  # Expects Vec
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(TypeMismatch):
            Executor().execute(prog)

    def test_type_mismatch_xqmx_expected(self):
        """Type mismatch when XQMX expected raises TypeMismatch."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),  # r0 = int
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.GETLINE, (0,)),  # Expects XQMX
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(TypeMismatch):
            Executor().execute(prog)

    def test_target_not_found(self):
        """Jump to undefined target raises TargetNotFound."""
        prog = make_program(
            [
                Instruction(Opcode.JUMP1, (99,)),  # Target 99 not defined
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(TargetNotFound):
            Executor().execute(prog)

    def test_loop_error_next_outside_loop(self):
        """NEXT outside loop raises LoopError."""
        prog = make_program(
            [
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(LoopError):
            Executor().execute(prog)

    def test_loop_error_lval_outside_loop(self):
        """LVAL outside loop raises LoopError."""
        prog = make_program(
            [
                Instruction(Opcode.LVAL, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(LoopError):
            Executor().execute(prog)

    def test_xqmx_mode_error(self):
        """HLF on SAMPLE mode raises XQMXModeError."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (5,)),
                Instruction(Opcode.BSMX, (0,)),  # Create SAMPLE
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.EXCLUDE, (0,)),  # Requires MODEL
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(XQMXModeError):
            Executor().execute(prog)


class TestTracerIntegration:
    """Tests for tracer protocol integration."""

    def test_tracer_on_step_begin(self):
        """Tracer receives on_step_begin calls."""
        events = []

        class TestTracer:
            def on_step_begin(self, _executor, instr):
                events.append(("begin", instr.opcode))

            def on_step_end(self, _executor, _instr):
                pass

            def on_error(self, _executor, _instr, _error):
                pass

            def on_halt(self, _executor):
                pass

        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=TestTracer())
        ex.execute(prog, output_slots=16)

        assert ("begin", Opcode.PUSH1) in events
        assert ("begin", Opcode.HALT) in events

    def test_tracer_on_step_end(self):
        """Tracer receives on_step_end calls."""
        events = []

        class TestTracer:
            def on_step_begin(self, _executor, _instr):
                pass

            def on_step_end(self, _executor, instr):
                events.append(("end", instr.opcode))

            def on_error(self, _executor, _instr, _error):
                pass

            def on_halt(self, _executor):
                pass

        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=TestTracer())
        ex.execute(prog, output_slots=16)

        assert ("end", Opcode.PUSH1) in events

    def test_tracer_on_error(self):
        """Tracer receives on_error calls."""
        errors = []

        class TestTracer:
            def on_step_begin(self, _executor, _instr):
                pass

            def on_step_end(self, _executor, _instr):
                pass

            def on_error(self, _executor, _instr, error):
                errors.append(type(error).__name__)

            def on_halt(self, _executor):
                pass

        prog = make_program(
            [
                Instruction(Opcode.POP),  # Will fail - empty stack
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=TestTracer())

        with pytest.raises(StackUnderflow):
            ex.execute(prog, output_slots=16)

        assert "StackUnderflow" in errors

    def test_tracer_on_halt(self):
        """Tracer receives on_halt call."""
        halted = []

        class TestTracer:
            def on_step_begin(self, _executor, _instr):
                pass

            def on_step_end(self, _executor, _instr):
                pass

            def on_error(self, _executor, _instr, _error):
                pass

            def on_halt(self, _executor):
                halted.append(True)

        prog = make_program(
            [
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=TestTracer())
        ex.execute(prog, output_slots=16)

        assert halted == [True]


class TestExecutorHelpers:
    """Tests for Executor helper methods."""

    def test_execute_returns_output(self):
        """execute returns output dictionary."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # slot index
                Instruction(Opcode.OUTPUT, (0,)),  # r0 -> output[0]
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        output = ex.execute(prog, output_slots=16)
        assert output[0] == 42

    def test_execute_with_input_data(self):
        """execute accepts input data."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (0,)),  # input slot
                Instruction(Opcode.INPUT, (0,)),  # input[0] -> r0
                Instruction(Opcode.PUSH1, (1,)),  # output slot
                Instruction(Opcode.OUTPUT, (0,)),  # r0 -> output[1]
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        # An int rather than an opaque marker: `_value_bytes` is exhaustive
        # over the types a register can hold, mirroring `regval_bytes`, so a
        # str is now a charge-schedule gap rather than a convenient payload.
        output = ex.execute(prog, input_data={0: 42}, output_slots=16)
        assert output[1] == 42

    def test_step_returns_continue_flag(self):
        """step returns True to continue, False to stop."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        ex.program = prog
        ex.state = MachineState()

        assert ex.step() is True  # PUSH - continue
        assert ex.step() is False  # HALT - stop


class TestProgramAndInstruction:
    """Tests for Program and Instruction classes."""

    def test_program_length(self):
        """Program length equals instruction count."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert len(prog) == 3

    def test_program_getitem(self):
        """Program supports index access."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.ADD),
                Instruction(Opcode.HALT),
            ]
        )
        assert prog[0].opcode == Opcode.PUSH1
        assert prog[1].opcode == Opcode.ADD
        assert prog[2].opcode == Opcode.HALT

    def test_instruction_operands(self):
        """Instruction stores operands."""
        instr = Instruction(Opcode.PUSH1, (42,))
        assert instr.operands == (42,)

    def test_instruction_no_operands(self):
        """Instruction without operands has empty tuple."""
        instr = Instruction(Opcode.ADD)
        assert instr.operands == ()

    def test_instruction_line_number(self):
        """Instruction stores line number."""
        instr = Instruction(Opcode.NOP, line=10)
        assert instr.line == 10


# === i64 Overflow Tests ===


def _push_i64(value: int) -> Instruction:
    """Build a PUSH8 instruction for an arbitrary signed 64-bit value."""
    encoded = value.to_bytes(8, byteorder="big", signed=True)
    return Instruction(Opcode.PUSH8, tuple(encoded))


class TestI64BoundaryLegal:
    """Values at the i64 boundary are legal and round-trip cleanly."""

    def test_push_i64_max(self):
        ex = run_program([_push_i64(I64_MAX), Instruction(Opcode.HALT)])
        assert ex.state.peek(0) == I64_MAX

    def test_push_i64_min(self):
        ex = run_program([_push_i64(I64_MIN), Instruction(Opcode.HALT)])
        assert ex.state.peek(0) == I64_MIN

    def test_stow_and_load_boundary(self):
        ex = run_program(
            [
                _push_i64(I64_MAX),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.LOAD, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        assert ex.state.peek(0) == I64_MAX


class TestArithmeticOverflow:
    """Arithmetic that leaves the i64 range raises ArithmeticOverflow."""

    def test_add_overflow_positive(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MAX),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.ADD),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_add_overflow_negative(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MIN),
                    Instruction(Opcode.PUSH1, (0xFF,)),  # -1
                    Instruction(Opcode.ADD),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_sub_overflow(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MIN),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.SUB),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_mul_overflow(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MAX),
                    Instruction(Opcode.PUSH1, (2,)),
                    Instruction(Opcode.MUL),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_neg_of_i64_min_overflows(self):
        """Classic case: -I64_MIN = 2^63, which is one past I64_MAX."""
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MIN),
                    Instruction(Opcode.NEG),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_abs_of_i64_min_overflows(self):
        """abs(I64_MIN) = 2^63, which is one past I64_MAX."""
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MIN),
                    Instruction(Opcode.ABS),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_inc_overflow(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MAX),
                    Instruction(Opcode.INC),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_dec_overflow(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(I64_MIN),
                    Instruction(Opcode.DEC),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_sqr_overflow(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(1 << 32),  # (2^32)^2 = 2^64, past I64_MAX
                    Instruction(Opcode.SQR),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_shl_overflow(self):
        """A shift that discards significant bits leaves the i64 range."""
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    _push_i64(1 << 62),
                    Instruction(Opcode.PUSH1, (2,)),  # (2^62) << 2 = 2^64
                    Instruction(Opcode.SHL),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_shl_amount_at_word_size_is_a_shift_fault_not_an_overflow(self):
        """An out-of-range shift amount is rejected before anything shifts.

        This test previously drove `1 << 64` and asserted ArithmeticOverflow,
        which pinned a Python-only behaviour: Rust guards the amount with
        `(0..64)` and raises InvalidShift, so the two implementations
        disagreed on a verifier-clean four-instruction program. The amount
        check now runs first on both, and the overflow case above uses an
        in-range amount so it still exercises what it claims to.
        """
        with pytest.raises(InvalidShift):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.PUSH1, (64,)),
                    Instruction(Opcode.SHL),
                    Instruction(Opcode.HALT),
                ]
            )

    def test_shr_never_overflows(self):
        """SHR is arithmetic right shift — magnitude decreases, never overflows."""
        ex = run_program(
            [
                _push_i64(I64_MIN),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.SHR),
                Instruction(Opcode.HALT),
            ]
        )
        # I64_MIN >> 1 = -(2^62), well within range
        assert ex.state.peek(0) == I64_MIN >> 1


class TestInputSlotBound:
    """INPUT bounds its slot against the calldata the host fixed."""

    def test_slot_past_the_calldata_raises(self):
        """The sharpest shape: empty calldata, so slot 0 does not exist.

        `get_input` returned None with no bounds check, so this program
        charged nothing, stored None in r0 and halted successfully, while
        the Rust VM raised `CallDataIndex`. Pinned across implementations
        by the `input_slot_out_of_range` conformance vector.
        """
        with pytest.raises(CallDataIndex):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.INPUT, (0,)),
                    Instruction(Opcode.HALT),
                ],
            )

    def test_slot_inside_the_calldata_is_accepted(self):
        """The boundary: the last in-range slot is not rejected."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.INPUT, (0,)),
                Instruction(Opcode.HALT),
            ],
            input_data={0: 7, 1: 9},
        )
        assert ex.state.get_register(0) == 9

    def test_slot_one_past_the_end_raises(self):
        """The other side of the same boundary."""
        with pytest.raises(CallDataIndex):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (2,)),
                    Instruction(Opcode.INPUT, (0,)),
                    Instruction(Opcode.HALT),
                ],
                input_data={0: 7, 1: 9},
            )

    def test_negative_slot_raises(self):
        """A negative slot is out of range rather than a Python dict miss."""
        with pytest.raises(CallDataIndex):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (0,)),
                    Instruction(Opcode.DEC, ()),
                    Instruction(Opcode.INPUT, (0,)),
                    Instruction(Opcode.HALT),
                ],
                input_data={0: 7},
            )


class TestInputBoundaryCheck:
    """INPUT validates externally-supplied int values at the VM boundary."""

    def test_input_out_of_range_raises(self):
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (0,)),  # slot 0
                    Instruction(Opcode.INPUT, (5,)),  # into r5
                    Instruction(Opcode.HALT),
                ],
                input_data={0: I64_MAX + 1},
            )

    def test_input_i64_max_is_accepted(self):
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.INPUT, (5,)),
                Instruction(Opcode.HALT),
            ],
            input_data={0: I64_MAX},
        )
        assert ex.state.get_register(5) == I64_MAX

    def test_input_vec_bypasses_int_check(self):
        """Vec inputs pass through; their int contents are validated on use."""
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.INPUT, (5,)),
                Instruction(Opcode.HALT),
            ],
            input_data={0: Vec.from_list([1, 2, 3])},
        )
        assert isinstance(ex.state.get_register(5), Vec)


class TestAllocationBudget:
    """Tests for the allocation budget (QUI-1009).

    The charge rates mirror `xqvm/src/vm.rs` exactly, so these expectations
    are the same numbers the Rust integration tests assert.
    """

    @staticmethod
    def _run(instructions, memory_limit):
        ex = Executor()
        ex.execute(make_program(instructions), memory_limit=memory_limit)
        return ex

    def test_bsmx_beyond_the_budget_allocates_nothing(self):
        """The reported case: a three-instruction program asks for a 1 GiB sample."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH4, (8, 0, 0, 0)),  # 1 << 27
                Instruction(Opcode.BSMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        with pytest.raises(MemoryLimitExceeded) as excinfo:
            ex.execute(prog, memory_limit=1 << 20)
        assert excinfo.value.requested == (1 << 27) * 8
        assert excinfo.value.limit == 1 << 20
        assert not ex.state.has_register(0), "a rejected allocator must not write its register"
        assert ex.memory_used == 0, "a rejected charge must not be kept"

    @pytest.mark.parametrize(
        "instructions",
        [
            pytest.param([Instruction(Opcode.PUSH1, (100,)), Instruction(Opcode.BSMX, (0,))], id="BSMX"),
            pytest.param([Instruction(Opcode.PUSH1, (100,)), Instruction(Opcode.SSMX, (0,))], id="SSMX"),
            pytest.param(
                [
                    Instruction(Opcode.PUSH1, (100,)),
                    Instruction(Opcode.PUSH1, (2,)),
                    Instruction(Opcode.XSMX, (0,)),
                ],
                id="XSMX",
            ),
        ],
    )
    def test_sample_allocators_charge_eight_bytes_per_variable(self, instructions):
        ex = self._run([*instructions, Instruction(Opcode.HALT)], 1 << 20)
        assert ex.memory_used == 800

    def test_model_allocators_charge_their_declared_size(self):
        """A model is sparse, but its declared size is an obligation consumers must meet."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH4, (64, 0, 0, 0)),  # 1 << 30
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(MemoryLimitExceeded):
            Executor().execute(prog, memory_limit=1 << 20)

    def test_discrete_k_is_rejected_before_the_budget_is_charged(self):
        """Error precedence: an invalid domain wins over the allocation charge."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH4, (64, 0, 0, 0)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.XSMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(InvalidDiscreteK, match="requires k >= 2"):
            Executor().execute(prog, memory_limit=8)

    def test_vec_push_is_charged_on_growth(self):
        """Four elements fit in a 64-byte budget at 16 bytes each; the fifth does not."""
        instructions = [Instruction(Opcode.VECI, (0,))]
        for i in range(5):
            instructions.append(Instruction(Opcode.PUSH1, (i,)))
            instructions.append(Instruction(Opcode.VECPUSH, (0,)))
        instructions.append(Instruction(Opcode.HALT))

        ex = Executor()
        with pytest.raises(MemoryLimitExceeded):
            ex.execute(make_program(instructions), memory_limit=64)
        assert ex.state.get_register(0).length == 4, "the four charged pushes should have landed"

    def test_equality_expansion_is_charged_before_it_expands(self):
        """EQUALITY writes one quadratic term per pair, charged up front."""
        n = 200
        instructions = [
            Instruction(Opcode.PUSH2, (0, n)),
            Instruction(Opcode.BQMX, (0,)),
            Instruction(Opcode.VECI, (1,)),
            Instruction(Opcode.VECI, (2,)),
        ]
        for i in range(n):
            instructions.append(Instruction(Opcode.PUSH2, (i >> 8, i & 0xFF)))
            instructions.append(Instruction(Opcode.VECPUSH, (1,)))
            instructions.append(Instruction(Opcode.PUSH1, (1,)))
            instructions.append(Instruction(Opcode.VECPUSH, (2,)))
        instructions.append(Instruction(Opcode.PUSH1, (1,)))
        instructions.append(Instruction(Opcode.PUSH1, (1,)))
        instructions.append(Instruction(Opcode.EQUALITY, (0, 1, 2)))
        instructions.append(Instruction(Opcode.HALT))

        ex = Executor()
        with pytest.raises(MemoryLimitExceeded):
            ex.execute(make_program(instructions), memory_limit=1 << 14)
        assert ex.state.get_register(0).quadratic == {}, "a rejected expansion must not write coefficients"

    def test_one_hot_r_over_a_huge_grid_is_rejected(self):
        """ONEHOTR expands O(cols^2) terms in a single step.

        The grid is legitimately oversized rather than degenerate: the model
        declares 4096 variables and the grid describes exactly those 4096
        cells, so RESIZE accepts it and the budget is what stops the
        expansion. A size-4 model resized to `1 x 2^20` is now rejected at
        RESIZE, one instruction earlier and with a better identity.
        """
        prog = make_program(
            [
                Instruction(Opcode.PUSH2, (16, 0)),  # size = 4096
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (1,)),  # rows
                Instruction(Opcode.PUSH2, (16, 0)),  # cols = 4096
                Instruction(Opcode.RESIZE, (0,)),
                Instruction(Opcode.PUSH1, (0,)),  # row
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(Opcode.ONEHOTR, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(MemoryLimitExceeded):
            Executor().execute(prog, memory_limit=1 << 20)

    def test_the_budget_is_cumulative_across_reallocations(self):
        """Overwriting the same register still spends budget; charges are never refunded."""
        instructions = [
            Instruction(Opcode.PUSH1, (0,)),
            Instruction(Opcode.PUSH1, (10,)),
            Instruction(Opcode.RANGE),
            Instruction(Opcode.PUSH1, (50,)),
            Instruction(Opcode.BSMX, (0,)),
            Instruction(Opcode.NEXT),
            Instruction(Opcode.HALT),
        ]
        ex = Executor()
        with pytest.raises(MemoryLimitExceeded):
            ex.execute(make_program(instructions), memory_limit=1000)
        assert ex.memory_used == 800, "two 400-byte samples should have been charged before the third failed"

    def test_reentrant_iter_copies_are_charged(self):
        """A frame is only popped by NEXT, so a back-edge piles up slice copies."""
        instructions = [Instruction(Opcode.VECI, (0,))]
        for i in range(64):
            instructions.append(Instruction(Opcode.PUSH1, (i,)))
            instructions.append(Instruction(Opcode.VECPUSH, (0,)))
        instructions.append(Instruction(Opcode.TARGET))
        instructions.append(Instruction(Opcode.PUSH1, (0,)))
        instructions.append(Instruction(Opcode.PUSH1, (64,)))
        instructions.append(Instruction(Opcode.ITER, (0,)))
        instructions.append(Instruction(Opcode.JUMP1, (0,)))
        instructions.append(Instruction(Opcode.HALT))

        ex = Executor()
        with pytest.raises(MemoryLimitExceeded):
            ex.execute(make_program(instructions), memory_limit=4096)
        assert ex.memory_used > 1024, "the charge should cover more than the 64 vec pushes"

    def test_each_execute_starts_with_a_fresh_budget(self):
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (100,)),
                Instruction(Opcode.BSMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        ex.execute(prog, memory_limit=1000)
        assert ex.memory_used == 800
        ex.execute(prog, memory_limit=1000)
        assert ex.memory_used == 800, "a run must not inherit the previous run's charge"

    def test_the_default_budget_admits_ordinary_programs(self):
        """A 100,000-variable sample is 800 KB: comfortably inside the 1 GiB default."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH4, (0, 1, 134, 160)),  # 100_000
                Instruction(Opcode.BSMX, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        ex.execute(prog, output_slots=16)
        assert ex.memory_limit == DEFAULT_MEMORY_LIMIT == 1 << 30
        assert ex.memory_used == 800_000


class TestStepLimit:
    """Tests for the executor's step budget."""

    def test_step_limit_raises_an_xqvm_error(self):
        """Exhausting the budget raises StepLimitExceeded, not a bare RuntimeError.

        The conformance harness identifies faults by exception class, so a
        budget overrun has to be part of the XQVMError hierarchy like every
        other VM fault.
        """
        from xqvm_py.errors import StepLimitExceeded

        ex = Executor()
        program = make_program(
            [
                Instruction(Opcode.NOP),
                Instruction(Opcode.NOP),
                Instruction(Opcode.NOP),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(StepLimitExceeded) as excinfo:
            ex.execute(program, step_limit=2)
        assert excinfo.value.limit == 2

    def test_step_limit_not_reached_runs_to_completion(self):
        ex = Executor()
        program = make_program(
            [
                Instruction(Opcode.NOP),
                Instruction(Opcode.HALT),
            ]
        )
        ex.execute(program, step_limit=10)
        assert ex.state.halted is True

    def test_energy_charges_for_the_model_it_evaluates(self):
        """ENERGY walks every coefficient; one step for that is QUI-1056."""
        from xqvm_py.metering import (
            BASE_STEPS,
            COEFF_WRITE_STEPS,
            MODEL_TERM_STEPS,
            SAMPLE_COPY_STEPS,
        )

        def steps_for(terms: int) -> int:
            source = [f"PUSH {terms}", "BQMX r0"]
            for i in range(terms):
                source += [f"PUSH {i}", "PUSH 1", "SETLINE r0"]
            source += [f"PUSH {terms}", "BSMX r1", "ENERGY r0 r1", "HALT"]
            ex = Executor()
            ex.execute(assemble("\n".join(source)))
            return ex.steps

        # Mirrors `xqvm/tests/integration.rs::energy_charges_for_the_model_it_evaluates`:
        # four more setup instructions' worth of coefficient writes, four
        # more sample elements charged when BSMX fills the buffer, four more
        # sample elements to copy during ENERGY's own read, four more terms
        # to accumulate.
        delta = steps_for(8) - steps_for(4)
        assert delta == (
            4 * (3 * BASE_STEPS + COEFF_WRITE_STEPS)
            + 4 * SAMPLE_COPY_STEPS
            + 4 * SAMPLE_COPY_STEPS
            + 4 * MODEL_TERM_STEPS
        )

    def test_grid_scan_charges_for_its_extent(self):
        """A row scan walks `cols` cells; one step for that is QUI-1056."""
        from xqvm_py.metering import GRID_CELL_STEPS

        def steps_for(cols: int) -> int:
            source = [
                f"PUSH {cols}",
                "BQMX r0",
                "PUSH 1",
                f"PUSH {cols}",
                "RESIZE r0",
                "PUSH 0",
                "ROWSUM r0",
                "HALT",
            ]
            ex = Executor()
            ex.execute(assemble("\n".join(source)))
            return ex.steps

        # Mirrors `xqvm/tests/integration.rs::a_grid_scan_charges_for_its_extent`.
        # Instruction count is identical between the two runs, so the whole
        # difference is the scan charge.
        assert steps_for(64) - steps_for(4) == 60 * GRID_CELL_STEPS

    def test_grid_scan_is_refused_before_it_walks_the_grid(self):
        """The charge lands before the scan, so a program that cannot pay
        does no work -- the same discipline the allocation budget uses."""
        from xqvm_py.errors import StepLimitExceeded
        from xqvm_py.metering import GRID_CELL_STEPS

        cols = 4096
        source = [
            f"PUSH {cols}",
            "BQMX r0",
            "PUSH 1",
            f"PUSH {cols}",
            "RESIZE r0",
            "PUSH 0",
            "ROWSUM r0",
            "HALT",
        ]
        ex = Executor()
        with pytest.raises(StepLimitExceeded) as excinfo:
            ex.execute(assemble("\n".join(source)), step_limit=64)
        # The refused charge is the whole row, not a partial walk.
        assert excinfo.value.requested == cols * GRID_CELL_STEPS
        assert excinfo.value.limit == 64

    def test_grid_scan_charges_nothing_extra_for_a_bad_operand(self):
        """Validation precedes the charge, so an out-of-range row faults for
        its own reason and is billed only the base cost of the dispatch."""
        from xqvm_py.metering import BASE_STEPS

        source = [
            "PUSH 8",
            "BQMX r0",
            "PUSH 2",
            "PUSH 4",
            "RESIZE r0",
            "PUSH 99",
            "ROWSUM r0",
            "HALT",
        ]
        ex = Executor()
        with pytest.raises(IndexOutOfBounds):
            ex.execute(assemble("\n".join(source)))
        # Seven dispatches, none charging beyond the base cost: the ROWSUM
        # faulted before its scan charge landed.
        assert ex.steps == 7 * BASE_STEPS

    def test_energy_validates_its_model_register_before_its_sample_register(self):
        """`ENERGY r0 r1` validates r0 completely -- XQMX-ness and mode --
        before it looks at r1, per spec/xqvm/METERING.md (Conformance).

        With a sample-mode XQMX in r0 and an int in r1, both registers are
        ill-typed. Checking XQMX-ness across both operands before checking
        either one's mode would fault on r1; the conforming fault is r0's.
        """
        ex = Executor()
        with pytest.raises(XQMXModeError) as excinfo:
            ex.execute(assemble("PUSH 4\nBSMX r0\nPUSH 5\nSTOW r1\nENERGY r0 r1\nHALT"))
        assert "ENERGY (model)" in str(excinfo.value)

    def test_instructions_and_steps_are_separate_counters(self):
        ex = Executor()
        ex.execute(assemble("PUSH 2\nBQMX r0\nPUSH 0\nPUSH 3\nSETLINE r0\nHALT"))
        assert ex.instructions == 6
        assert ex.steps > ex.instructions

    def test_charge_steps_saturates_rather_than_overflowing(self):
        """Python ints don't overflow on their own, so an unlimited step
        budget could let the stored total grow past what Rust's `u64`
        counter would report for the same program -- exactly the
        divergence `_saturating` in `xqvm_py/metering.py` exists to
        prevent. Mirrors `expansion_saturates_rather_than_wrapping` in
        `xqvm/src/metering.rs`, but at the counter itself rather than at
        one cost formula.
        """
        from xqvm_py.metering import U64_MAX

        # Explicitly unlimited: `execute` defaults to DEFAULT_STEP_LIMIT, and
        # this test is about the stored total saturating, not the budget.
        ex = Executor()
        ex.execute(assemble("HALT"), step_limit=None)
        ex._charge_steps(U64_MAX)
        ex._charge_steps(U64_MAX)
        assert ex.steps == U64_MAX

    def test_step_can_be_driven_directly_by_a_charging_opcode(self):
        """`step()` is public, so `_step_limit` must exist before `execute()`.

        Any opcode reaching `_charge_steps` without a limit argument reads
        `self._step_limit`; before QUI-1056 only `execute()` set it, so
        driving `step()` by hand raised AttributeError instead of an
        XQVMError. PUSH and HALT charge nothing, which is why the existing
        direct-`step()` test missed it -- this one uses SETLINE, which does.
        """
        from xqvm_py.metering import COEFF_WRITE_STEPS

        ex = Executor()
        ex.program = assemble("PUSH 2\nBQMX r0\nPUSH 0\nPUSH 3\nSETLINE r0\nHALT")
        for _ in range(5):
            assert ex.step() is True
        assert ex.steps == COEFF_WRITE_STEPS
        assert ex.step() is False

    def test_skipping_an_empty_loop_charges_for_the_scan(self):
        """A skipped loop body is still decoded, one instruction at a time.

        RANGE with count <= 0 scans forward to the matching NEXT; that scan
        is O(body length), so every instruction it consumes pays BASE_STEPS
        (QUI-1056). Mirrors
        `xqvm/tests/integration.rs::skipping_an_empty_loop_charges_for_the_scan`.
        """
        from xqvm_py.metering import BASE_STEPS

        def steps_for(body: int) -> int:
            source = ["PUSH 0", "PUSH 0", "RANGE"]
            source += ["NOP"] * body
            source += ["NEXT", "HALT"]
            ex = Executor()
            ex.execute(assemble("\n".join(source)))
            return ex.steps

        # PUSH, PUSH, RANGE and HALT are dispatched; the NEXT is consumed by
        # the scan.
        assert steps_for(0) == 5 * BASE_STEPS
        assert steps_for(64) - steps_for(0) == 64 * BASE_STEPS

    def test_a_skipped_body_is_metered_but_not_dispatched(self):
        """The scan charges steps without dispatching, so the two counters
        part company by exactly the number of instructions it consumed.
        """
        source = ["PUSH 0", "PUSH 0", "RANGE"] + ["NOP"] * 10 + ["NEXT", "HALT"]
        ex = Executor()
        ex.execute(assemble("\n".join(source)))
        assert ex.instructions == 4
        assert ex.steps == 4 + 11

    def test_a_skip_scan_that_cannot_pay_does_not_finish(self):
        """The scan charges before it decodes, so a program that cannot pay
        for the whole scan is refused partway through.
        """
        from xqvm_py.errors import StepLimitExceeded

        source = ["PUSH 0", "PUSH 0", "RANGE"] + ["NOP"] * 100 + ["NEXT", "HALT"]
        ex = Executor()
        with pytest.raises(StepLimitExceeded):
            ex.execute(assemble("\n".join(source)), step_limit=10)

    def test_constraint_helpers_pay_for_the_coefficients_they_write(self):
        """EXCLUDE writes one quadratic term, IMPLIES a linear and a
        quadratic, REDUCE three quadratics and a linear -- each priced at the
        same COEFF_WRITE_STEPS a bare SETQUAD pays (QUI-1056). Mirrors
        `constraint_helpers_pay_for_the_coefficients_they_write` in
        `xqvm/tests/integration.rs`.
        """
        from xqvm_py.metering import BASE_STEPS, COEFF_WRITE_STEPS

        def steps_for(*body: str) -> int:
            ex = Executor()
            ex.execute(assemble("\n".join(["PUSH 8", "BQMX r0", *body, "HALT"])))
            return ex.steps

        baseline = steps_for()
        # Three PUSHes plus the constraint opcode's own dispatch.
        dispatch = 4 * BASE_STEPS

        exclude = steps_for("PUSH 0", "PUSH 1", "PUSH 5", "EXCLUDE r0")
        assert exclude - baseline == dispatch + COEFF_WRITE_STEPS

        implies = steps_for("PUSH 0", "PUSH 1", "PUSH 5", "IMPLIES r0")
        assert implies - baseline == dispatch + 2 * COEFF_WRITE_STEPS

        reduce_ = steps_for("PUSH 0", "PUSH 1", "PUSH 5", "REDUCE r0")
        assert reduce_ - baseline == dispatch + 4 * COEFF_WRITE_STEPS

    def test_copying_a_vec_costs_the_sum_over_its_elements(self):
        """`value_copy_steps` on a vec recurses per element, so a vec of
        samples is charged the same here as ITER charges copying the same
        elements one at a time (QUI-1056).
        """
        from xqvm_py.metering import SAMPLE_COPY_STEPS, value_copy_steps
        from xqvm_py.vector import Vec, VecElem
        from xqvm_py.xqmx import XQMX

        vec = Vec(VecElem("xqmx"))
        for _ in range(3):
            vec.push(XQMX.binary_sample(4))
        assert value_copy_steps(vec) == sum(value_copy_steps(vec.get(i)) for i in range(3))
        assert value_copy_steps(vec) == 3 * 4 * SAMPLE_COPY_STEPS


class TestCalldataChargeParity:
    """A calldata value costs the same to copy here as on the Rust VM."""

    def test_a_raw_list_is_charged_as_a_vec(self):
        """`execute` converts a list to a Vec, so INPUT charges the vec rate.

        Stored unconverted the list fell through `_value_bytes`'s recursion
        to the per-int rate, charging 8 bytes an element against
        `regval_bytes`'s 16 for the same `vec<int>`, so a program tuned near
        the memory limit passed here and faulted on Rust. Only the direct
        Executor API was exposed -- `xquad.vm` already converted at its own
        boundary.
        """
        ex = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.INPUT, (0,)),
                Instruction(Opcode.HALT),
            ],
            input_data={0: [1, 2, 3]},
        )
        assert ex.memory_used == 3 * VEC_ELEMENT_BYTES
        assert isinstance(ex.state.get_register(0), Vec)

    def test_a_vec_and_the_list_it_came_from_cost_the_same(self):
        """The conversion is what makes the two paths agree."""
        as_list = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.INPUT, (0,)),
                Instruction(Opcode.HALT),
            ],
            input_data={0: [4, 5]},
        )
        as_vec = run_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.INPUT, (0,)),
                Instruction(Opcode.HALT),
            ],
            input_data={0: Vec.from_list([4, 5])},
        )
        assert as_list.memory_used == as_vec.memory_used

    def test_a_value_with_no_rate_is_rejected_rather_than_priced(self):
        """`_value_bytes` is exhaustive, mirroring `regval_bytes`.

        Rust's match over `RegVal` has no wildcard, so an added variant is
        a compile error. The trailing fallback here priced anything at the
        per-variable rate and stored it, which is a divergence an
        implementation cannot discover from the schedule.
        """
        ex = Executor()
        with pytest.raises(TypeMismatch):
            ex._value_bytes(object())


# ---------------------------------------------------------------------------
# Error precedence
# ---------------------------------------------------------------------------

# Every opcode that names a register and also pops. spec/xqvm/SPEC.md states
# the order normatively: "operand pops happen first (StackUnderflow), then the
# allocation charge, then type and range validation."
#
# All twenty-two resolved their register before popping, so a short stack plus
# a wrong-typed register raised TypeMismatch here and StackUnderflow on the
# Rust VM. Both VMs rejected the program, but the Faults table makes the
# identity itself normative, so two names for one program is a divergence.
#
# Driven off the opcode table rather than a hand-written list: an opcode added
# to this family is covered without anyone remembering to add it here.
_REGISTER_AND_POP_OPCODES = [
    Opcode.VECPUSH,
    Opcode.VECGET,
    Opcode.VECSET,
    Opcode.GETLINE,
    Opcode.SETLINE,
    Opcode.ADDLINE,
    Opcode.GETQUAD,
    Opcode.SETQUAD,
    Opcode.ADDQUAD,
    Opcode.RESIZE,
    Opcode.ROWFIND,
    Opcode.COLFIND,
    Opcode.ROWSUM,
    Opcode.COLSUM,
    Opcode.ONEHOTR,
    Opcode.ONEHOTC,
    Opcode.EXCLUDE,
    Opcode.IMPLIES,
    Opcode.EQUALITY,
    Opcode.ATLEAST,
    Opcode.ATLEASTW,
    Opcode.REDUCE,
]


def _int_registers_and_stack(opcode, pops):
    """Build a program whose registers hold ints and whose stack holds `pops`.

    Every register operand is stowed an int, so the opcode's type check must
    fail whenever it is reached.
    """
    meta = opcode.meta
    instructions = []
    for reg in range(meta.operand_count):
        instructions += [
            Instruction(Opcode.PUSH1, (5,)),
            Instruction(Opcode.STOW, (reg,)),
        ]
    instructions += [Instruction(Opcode.PUSH1, (1,))] * pops
    instructions += [
        Instruction(opcode, tuple(range(meta.operand_count))),
        Instruction(Opcode.HALT),
    ]
    return make_program(instructions)


class TestErrorPrecedence:
    """The pops come first, then the charge, then type and range validation."""

    @pytest.mark.parametrize("opcode", _REGISTER_AND_POP_OPCODES, ids=lambda o: o.name)
    def test_a_short_stack_beats_a_wrong_typed_register(self, opcode):
        """StackUnderflow, not TypeMismatch, for one operand too few."""
        prog = _int_registers_and_stack(opcode, opcode.meta.stack_pop - 1)
        with pytest.raises(StackUnderflow):
            Executor().execute(prog)

    @pytest.mark.parametrize("opcode", _REGISTER_AND_POP_OPCODES, ids=lambda o: o.name)
    def test_the_pops_are_satisfied_before_the_register_is_read(self, opcode):
        """With the stack full, the same programs reach the type check.

        The companion to the case above: it pins that the reordering moved
        the pops ahead of the resolution rather than removing the check.
        MemoryLimitExceeded is the correct answer for the four opcodes that
        charge unconditionally before discriminating the register, which is
        the second clause of the same precedence rule.
        """
        prog = _int_registers_and_stack(opcode, opcode.meta.stack_pop)
        with pytest.raises((TypeMismatch, MemoryLimitExceeded)):
            Executor().execute(prog)


# The three HLF runners that take a model register and one or two vec
# registers resolve their operands in a different order from the Rust VM.
# `xqvm/src/vm.rs` resolves the vec operands and validates their shape and
# range *before* it charges the allocation budget, and discriminates the
# model register *last*, after the charge -- `exec_equality` even reads the
# model's size for the charge basis through a peek that does not
# discriminate the register (`RegVal::Model(m) => m.size, _ => 0`). Putting
# the model register first meant whatever fault it would raise pre-empted
# the vec rule Rust fires first, so one program had two names.
#
# `_int_registers_and_stack` above cannot reach any of this: it stows an int
# in every register operand including the vec ones, so it never gets past
# the first resolution. These cases are written out instead.


def _vec_of(reg, count):
    """Instructions allocating vec `reg` and appending `count` ones."""
    return [Instruction(Opcode.VEC, (reg,))] + [
        Instruction(Opcode.PUSH1, (1,)),
        Instruction(Opcode.VECPUSH, (reg,)),
    ] * count


class TestConstraintCheckOrder:
    """EQUALITY, ATLEAST and ATLEASTW check their vecs before their model."""

    def test_equality_length_mismatch_beats_the_charge(self):
        """A mismatch outranks a budget the expansion could not pay.

        200 indices against no coeffs costs 961600 bytes to expand, so
        charging first answered MemoryLimitExceeded where the Rust VM had
        already returned VecLengthMismatch.
        """
        prog = make_program(
            [Instruction(Opcode.PUSH1, (4,)), Instruction(Opcode.BQMX, (0,))]
            + _vec_of(2, 200)
            + _vec_of(3, 0)
            + [
                Instruction(Opcode.PUSH1, (0,)),  # target
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(Opcode.EQUALITY, (0, 2, 3)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(VecLengthMismatch):
            Executor().execute(prog, memory_limit=20000)

    def test_equality_length_mismatch_beats_the_model_type(self):
        """A mismatch outranks an int in the model register."""
        prog = make_program(
            [Instruction(Opcode.PUSH1, (0,)), Instruction(Opcode.STOW, (0,))]
            + _vec_of(2, 1)
            + _vec_of(3, 0)
            + [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.EQUALITY, (0, 2, 3)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(VecLengthMismatch):
            Executor().execute(prog)

    def test_equality_charges_before_it_reads_an_unset_model(self):
        """The charge outranks an unset model register.

        Rust's charge basis comes from a peek that returns 0 for a slot
        holding no model at all, so the budget is spent before the register
        is discriminated. Reading the slot through `get_register` instead
        raised RegisterNotFound and pre-empted a charge Rust takes.
        """
        prog = make_program(
            _vec_of(2, 200)
            + _vec_of(3, 200)
            + [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.EQUALITY, (0, 2, 3)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(MemoryLimitExceeded):
            Executor().execute(prog, memory_limit=20000)

    def test_atleast_k_range_beats_the_register_mode(self):
        """`k` is range-checked before the model register is discriminated.

        `exec_at_least` never reaches its register check for k = 0, so the
        disagreement was over which rule fired rather than what to call the
        mode fault -- outside SPEC.md's XqmxMode carve-out.
        """
        prog = make_program(
            [Instruction(Opcode.PUSH1, (4,)), Instruction(Opcode.BSMX, (0,))]
            + _vec_of(2, 1)
            + [
                Instruction(Opcode.PUSH1, (0,)),  # k
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(Opcode.ATLEAST, (0, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(IndexOutOfBounds):
            Executor().execute(prog)

    def test_atleast_charges_before_it_reads_an_unset_model(self):
        """The charge outranks an unset model register; see EQUALITY."""
        prog = make_program(
            _vec_of(2, 200)
            + [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.ATLEAST, (0, 2)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(MemoryLimitExceeded):
            Executor().execute(prog, memory_limit=20000)

    def test_atleastw_length_mismatch_beats_the_register_mode(self):
        """The vec lengths are compared before the model is discriminated."""
        prog = make_program(
            [Instruction(Opcode.PUSH1, (4,)), Instruction(Opcode.BSMX, (0,))]
            + _vec_of(2, 1)
            + _vec_of(3, 0)
            + [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.ATLEASTW, (0, 2, 3)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(VecLengthMismatch):
            Executor().execute(prog)


class TestFaultOrdering:
    """Every runner pops its operands before it resolves a register.

    `spec/xqvm/SPEC.md` fixes the order of work within one instruction:
    pops, then the allocation charge, then validation. A runner that reads
    its register first raises `TypeMismatch` where the Rust VM raises the
    operand fault -- a fault-identity divergence, which on a chain runtime
    is a consensus split (QUI-1178).
    """

    #: Prefix of every accessor that resolves a register --
    #: `_get_register_as_xqmx`, `_get_register_as_vec`,
    #: `_get_register_as_model`, `_get_register_as_int` today. Matched by
    #: prefix rather than by an explicit set because a runner reaching a
    #: register through an accessor the set did not name is silently exempt
    #: rather than flagged: `_first_positions` finds no read at all, so
    #: `read < pop` is never evaluated. A fifth accessor is the same hazard
    #: this guard exists to catch, one level up.
    #:
    #: The prefix deliberately excludes the non-faulting peeks
    #: (`_peek_model_size`, `_peek_model_grid`). They read a slot without
    #: discriminating it, which is what lets the charge precede the type
    #: error, so they are not the resolution this guard orders against pops.
    REGISTER_READ_PREFIX = "_get_register_as"
    POPS = frozenset({"pop", "pop_n"})

    #: Loads calldata slot 0 into r0 as an int. `INPUT` writes a register of
    #: unknown kind, which is what lets the verifier admit these programs --
    #: the same shape the route-B conformance vectors use.
    PROLOGUE = [Instruction(Opcode.PUSH1, (0,)), Instruction(Opcode.INPUT, (0,))]

    @staticmethod
    def _first_positions(func):
        """Source position of the first register read and the first pop.

        Returns `(read_pos, pop_pos)`, either of which is `None` when the
        runner does not do that thing. Positions are `(lineno, col_offset)`
        so a read and a pop on one line still compare left to right.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        reads, pops = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
            pos = (node.lineno, node.col_offset)
            if name is not None and name.startswith(TestFaultOrdering.REGISTER_READ_PREFIX):
                reads.append(pos)
            elif name in TestFaultOrdering.POPS:
                pops.append(pos)
        return (min(reads) if reads else None, min(pops) if pops else None)

    @staticmethod
    def _run(instructions, memory_limit=DEFAULT_MEMORY_LIMIT, registers=(0,)):
        """Run `PROLOGUE`-loaded registers plus `instructions` to completion.

        Every slot in `registers` is loaded from calldata with an int, so a
        runner that type-checks its register faults where a runner that pops
        first does not.
        """
        prologue = []
        for slot in registers:
            prologue += [
                Instruction(Opcode.PUSH1, (slot,)),
                Instruction(Opcode.INPUT, (slot,)),
            ]
        prog = make_program(prologue + list(instructions) + [Instruction(Opcode.HALT)])
        ex = Executor()
        ex.execute(
            prog,
            input_data={slot: 5 for slot in registers},
            memory_limit=memory_limit,
        )
        return ex

    @staticmethod
    def _prologue_budget(registers=(0,)):
        """Bytes the prologue alone costs.

        `INPUT` charges for the copy it makes, so a test that wants the
        *next* charge to fail sets the budget to exactly this. Derived from a
        run rather than hard-coded, so a change to the charge rate does not
        silently turn these tests into no-ops.
        """
        ex = Executor()
        prologue = []
        for slot in registers:
            prologue += [
                Instruction(Opcode.PUSH1, (slot,)),
                Instruction(Opcode.INPUT, (slot,)),
            ]
        ex.execute(
            make_program(prologue + [Instruction(Opcode.HALT)]),
            input_data={slot: 5 for slot in registers},
        )
        return ex.memory_used

    def test_pops_precede_the_register_read(self):
        offenders = set()
        for runner in Executor()._build_dispatch_table().values():
            read, pop = self._first_positions(runner)
            if read is not None and pop is not None and read < pop:
                offenders.add(runner.__name__)
        assert offenders == set(), (
            "these runners resolve a register before popping their operands, "
            "which raises TypeMismatch where the Rust VM raises the operand "
            "fault -- a consensus-visible divergence "
            f"(spec/xqvm/SPEC.md, QUI-1178): {sorted(offenders)}"
        )

    def test_vecpush_charges_before_it_reads_its_register(self):
        """A budget too small to hold the element faults before the type check.

        `exec_vec_push` calls `charge` before it resolves the register, so
        an int in r0 under an exhausted budget is MemoryLimitExceeded in
        Rust, not TypeMismatch.
        """
        with pytest.raises(MemoryLimitExceeded):
            self._run(
                [
                    Instruction(Opcode.PUSH1, (7,)),
                    Instruction(Opcode.VECPUSH, (0,)),
                ],
                memory_limit=self._prologue_budget(),
            )

    @staticmethod
    def _onehot_model_setup(dim):
        """Instructions building a `dim` x `dim` grid model in r0.

        A square grid so the same setup drives both ONEHOTR (charges on
        `cols`) and ONEHOTC (charges on `rows`) with the same nonzero
        magnitude. Mirrors `conformance/vectors/constraints/onehotr_coeff`.
        """
        return [
            Instruction(Opcode.PUSH1, (dim * dim,)),
            Instruction(Opcode.BQMX, (0,)),
            Instruction(Opcode.PUSH1, (dim,)),  # rows
            Instruction(Opcode.PUSH1, (dim,)),  # cols
            Instruction(Opcode.RESIZE, (0,)),
        ]

    @classmethod
    def _onehot_model_setup_budget(cls, dim):
        """Bytes the model setup alone costs, the way `_prologue_budget` does.

        `BQMX` is the only charging instruction here (`RESIZE` charges
        nothing), so this is exactly `dim * dim * VARIABLE_BYTES` -- derived
        from a run rather than hard-coded so a change to the charge rate
        cannot silently turn the test below into a no-op.
        """
        ex = Executor()
        ex.execute(make_program(cls._onehot_model_setup(dim) + [Instruction(Opcode.HALT)]))
        return ex.memory_used

    @pytest.mark.parametrize("opcode", [Opcode.ONEHOTR, Opcode.ONEHOTC])
    def test_onehot_charges_before_it_expands(self, opcode):
        """A model sized to exhaust the budget faults on the charge itself.

        The register here holds a real model, which is the case both VMs
        agree on: `exec_one_hot_r` peeks `cols` off it and charges before
        `as_model_mut` and `grid_row_index`, and `_runner_ONEHOTR` charges
        after `_get_register_as_xqmx` but still before `row_indices` and
        `expand_onehot`. A budget that covers building the grid but not the
        one-hot expansion must therefore raise MemoryLimitExceeded, not the
        (passing) dimension check or anything downstream of it -- and a
        regression that dropped the charge call entirely would let this
        program run to completion instead.

        A register holding something other than a model charges nothing
        at all; see `test_onehot_does_not_charge_for_a_sample`.
        """
        dim = 3
        budget = self._onehot_model_setup_budget(dim)
        prog = make_program(
            self._onehot_model_setup(dim)
            + [
                Instruction(Opcode.PUSH1, (0,)),  # row/col index
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(opcode, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(MemoryLimitExceeded):
            Executor().execute(prog, memory_limit=budget)

    @pytest.mark.parametrize("opcode", [Opcode.ONEHOTR, Opcode.ONEHOTC])
    def test_onehot_does_not_charge_for_a_sample(self, opcode):
        """A sample sizes the charge at zero, as Rust's peek does (QUI-1202).

        `_peek_model_grid` is `is_model()`-gated, so a sample contributes
        nothing to the charge and the run reaches the mode check with the
        budget untouched -- mirroring `exec_one_hot_r`'s `RegVal::Model(m) =>
        m.cols, _ => 0`, which charges nothing and falls through to
        `as_model_mut`'s RegisterType.

        The budget below has no headroom past the setup, so the assertion is
        that the fault is *not* MemoryLimitExceeded: before the fix the
        register resolved first and a sample was billed 240 bytes for its
        real 3x3 extent, which raised MemoryLimitExceeded here where the Rust
        VM raised its type error -- and it is verifier-clean, since `INPUT`
        and a `BQMX`/`BSMX` branch join both write `RegType::Any`, which
        satisfies ONEHOT's `R::Model` requirement. Both routes are covered:
        the in-program `BSMX` below pins the runner, and the calldata sample
        after it pins the surface an embedder reaches, since
        `Vm::set_calldata` takes any `RegVal` -- samples included, as its own
        docs say.

        Which identity the surviving fault carries is deliberately not
        asserted beyond Python's own: `spec/xqvm/SPEC.md`'s Faults table
        records `XqmxMode` as its one unresolved row -- `xqvm_py`
        raises it where the Rust VM raises `TypeMismatch`, no `xqvm::Error`
        maps to it, and the spec says neither identity is safe to write a
        vector against until that is settled. What this pins is the charge,
        which is settled: the same fault now comes out at any budget.

        An int in the register cannot pin this. `_get_register_as_xqmx`
        rejects it before the charge either way, so a test written against
        one passes whatever the charge ordering is.
        """
        dim = 3
        setup = [
            Instruction(Opcode.PUSH1, (dim * dim,)),
            Instruction(Opcode.BSMX, (0,)),  # a SAMPLE, not a model
            Instruction(Opcode.PUSH1, (dim,)),
            Instruction(Opcode.PUSH1, (dim,)),
            Instruction(Opcode.RESIZE, (0,)),  # RESIZE accepts model|sample
        ]
        ex = Executor()
        ex.execute(make_program(setup + [Instruction(Opcode.HALT)]))
        budget = ex.memory_used

        prog = make_program(
            setup
            + [
                Instruction(Opcode.PUSH1, (0,)),  # row/col index
                Instruction(Opcode.PUSH1, (1,)),  # penalty
                Instruction(opcode, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        with pytest.raises(XQMXModeError):
            Executor().execute(prog, memory_limit=budget)
        # Same fault with the budget out of the picture: the charge is what
        # the fix removed, so it can no longer decide the outcome.
        with pytest.raises(XQMXModeError):
            Executor().execute(prog)

        # The same register kind delivered the way a host delivers it. This
        # is the shorter route to the runner and the one an embedder
        # controls; `--calldata` parses i64s and cannot express it, which is
        # what makes it easy to miss from the command line.
        def calldata_sample() -> XQMX:
            sample = XQMX.binary_sample(dim * dim)
            sample.rows, sample.cols = dim, dim
            return sample

        input_prologue = [
            Instruction(Opcode.PUSH1, (0,)),
            Instruction(Opcode.INPUT, (0,)),
        ]
        ex = Executor()
        ex.execute(
            make_program(input_prologue + [Instruction(Opcode.HALT)]),
            input_data={0: calldata_sample()},
        )
        via_input = make_program(
            input_prologue
            + [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(opcode, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        for limit in (ex.memory_used, DEFAULT_MEMORY_LIMIT):
            with pytest.raises(XQMXModeError):
                Executor().execute(
                    via_input,
                    input_data={0: calldata_sample()},
                    memory_limit=limit,
                )

    def test_reduce_charges_before_it_reads_its_register(self):
        """`exec_reduce` charges before it resolves the model register."""
        with pytest.raises(MemoryLimitExceeded):
            self._run(
                [
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.REDUCE, (0,)),
                ],
                memory_limit=self._prologue_budget(),
            )

    @pytest.mark.parametrize(
        "opcode,operands",
        [
            (Opcode.EQUALITY, (0, 1, 2)),
            (Opcode.ATLEAST, (0, 1)),
            (Opcode.ATLEASTW, (0, 1, 2)),
        ],
    )
    def test_multi_register_opcodes_read_their_inputs_before_the_model(self, opcode, operands):
        """The indices register is validated before the model register.

        `exec_equality`, `exec_at_least` and `exec_at_least_w` all resolve
        `indices` with `as_vec_int` before they reach `reg_mut(model)`.
        With both r0 (model) and r1 (indices) holding ints, the reported
        register must be r1 in both VMs. (Where in that sequence the
        charges fall differs per opcode -- but neither register is a model
        here, so nothing is charged either way.)
        """
        with pytest.raises(TypeMismatch) as excinfo:
            self._run(
                [
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(opcode, operands),
                ],
                registers=(0, 1, 2),
            )
        assert "r1" in str(excinfo.value)
