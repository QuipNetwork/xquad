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
    DivisionByZero,
    LoopError,
    MemoryLimitExceeded,
    RegisterNotFound,
    StackUnderflow,
    TargetNotFound,
    TypeMismatch,
    XQMXModeError,
)
from xqvm_py.executor import DEFAULT_MEMORY_LIMIT, Executor
from xqvm_py.opcodes import Opcode
from xqvm_py.program import Instruction, make_program, run_program
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
        """ATLEAST with k=0 raises ValueError."""
        with pytest.raises(ValueError, match="ATLEAST"):
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
        """ATLEAST with k > N raises ValueError."""
        with pytest.raises(ValueError, match="ATLEAST"):
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
        """ATLEASTW with mismatched indices/coeffs raises ValueError."""
        with pytest.raises(ValueError, match="ATLEASTW"):
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

    def test_k_zero_raises(self):
        """ATLEASTW with k=0 raises ValueError."""
        with pytest.raises(ValueError, match="ATLEASTW"):
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
        """REDUCE with var_a out of range raises ValueError."""
        with pytest.raises(ValueError, match="REDUCE"):
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
        output = ex.execute(prog, input_data={0: "hello"}, output_slots=16)
        assert output[1] == "hello"

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
        with pytest.raises(ArithmeticOverflow):
            run_program(
                [
                    Instruction(Opcode.PUSH1, (1,)),
                    Instruction(Opcode.PUSH1, (64,)),  # 1 << 64 > I64_MAX
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
        with pytest.raises(ValueError, match="DISCRETE domain requires"):
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
        """RESIZE takes its extents off the stack; ONEHOTR then expands O(cols^2)."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (4,)),
                Instruction(Opcode.BQMX, (0,)),
                Instruction(Opcode.PUSH1, (1,)),  # rows
                Instruction(Opcode.PUSH4, (0, 16, 0, 0)),  # cols = 1 << 20
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
