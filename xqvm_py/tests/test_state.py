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
Tests for MachineState and related components.
"""

import pytest

from xqvm_py.errors import (
    CallDataIndex,
    NoActiveLoop,
    OutputIndex,
    RegisterNotFound,
    StackOverflow,
    StackUnderflow,
)
from xqvm_py.state import MAX_STACK_SIZE, JumpControl
from xqvm_py.vector import Vec
from xqvm_py.xqmx import XQMX


class TestStackOperations:
    """Tests for stack push/pop operations."""

    def test_push_single_value(self, empty_state):
        """Push single value onto stack."""
        empty_state.push(42)
        assert empty_state.stack_depth == 1

    def test_pop_single_value(self, empty_state):
        """Pop returns pushed value."""
        empty_state.push(100)
        value = empty_state.pop()
        assert value == 100
        assert empty_state.stack_depth == 0

    def test_push_pop_multiple(self, empty_state):
        """Push and pop multiple values."""
        empty_state.push(1)
        empty_state.push(2)
        empty_state.push(3)

        assert empty_state.pop() == 3
        assert empty_state.pop() == 2
        assert empty_state.pop() == 1

    def test_pop_n_values(self, empty_state):
        """pop_n returns list of values (top first)."""
        empty_state.push(1)
        empty_state.push(2)
        empty_state.push(3)

        values = empty_state.pop_n(2)
        assert values == [3, 2]
        assert empty_state.stack_depth == 1

    def test_pop_n_all_values(self, empty_state):
        """pop_n can pop all values."""
        empty_state.push(10)
        empty_state.push(20)
        empty_state.push(30)

        values = empty_state.pop_n(3)
        assert values == [30, 20, 10]
        assert empty_state.stack_depth == 0

    def test_peek_top(self, empty_state):
        """peek(0) returns top without removing."""
        empty_state.push(1)
        empty_state.push(2)
        empty_state.push(3)

        assert empty_state.peek(0) == 3
        assert empty_state.stack_depth == 3

    def test_peek_depth(self, empty_state):
        """peek at various depths."""
        empty_state.push(1)
        empty_state.push(2)
        empty_state.push(3)

        assert empty_state.peek(0) == 3
        assert empty_state.peek(1) == 2
        assert empty_state.peek(2) == 1

    def test_stack_underflow_on_empty_pop(self, empty_state):
        """Popping empty stack raises StackUnderflow."""
        with pytest.raises(StackUnderflow):
            empty_state.pop()

    def test_stack_underflow_on_pop_n(self, empty_state):
        """pop_n with insufficient values raises StackUnderflow."""
        empty_state.push(1)
        with pytest.raises(StackUnderflow):
            empty_state.pop_n(5)

    def test_stack_underflow_on_peek(self, empty_state):
        """Peeking beyond stack depth raises StackUnderflow."""
        empty_state.push(1)
        with pytest.raises(StackUnderflow):
            empty_state.peek(5)

    def test_stack_overflow(self, empty_state):
        """Exceeding MAX_STACK_SIZE raises StackOverflow."""
        # Fill stack to capacity
        for i in range(MAX_STACK_SIZE):
            empty_state.push(i)

        with pytest.raises(StackOverflow):
            empty_state.push(0)

    def test_stack_depth_property(self, empty_state):
        """stack_depth returns current depth."""
        assert empty_state.stack_depth == 0
        empty_state.push(1)
        assert empty_state.stack_depth == 1
        empty_state.push(2)
        assert empty_state.stack_depth == 2
        empty_state.pop()
        assert empty_state.stack_depth == 1


class TestRegisterOperations:
    """Tests for register storage and retrieval."""

    def test_store_retrieve_int(self, empty_state):
        """Store and retrieve integer."""
        empty_state.set_register(0, 42)
        assert empty_state.get_register(0) == 42

    def test_store_retrieve_vec(self, empty_state):
        """Store and retrieve Vec."""
        v = Vec.from_list([1, 2, 3])
        empty_state.set_register(5, v)
        retrieved = empty_state.get_register(5)
        assert retrieved.length == 3

    def test_store_retrieve_xqmx(self, empty_state):
        """Store and retrieve XQMX."""
        x = XQMX.binary_model(10)
        empty_state.set_register(10, x)
        retrieved = empty_state.get_register(10)
        assert retrieved.size == 10

    def test_has_register_true(self, empty_state):
        """has_register returns True for set register."""
        empty_state.set_register(0, 100)
        assert empty_state.has_register(0) is True

    def test_has_register_false(self, empty_state):
        """has_register returns False for unset register."""
        assert empty_state.has_register(99) is False

    def test_clear_register(self, empty_state):
        """clear_register removes register."""
        empty_state.set_register(0, 42)
        empty_state.clear_register(0)
        assert empty_state.has_register(0) is False

    def test_clear_nonexistent_register(self, empty_state):
        """clear_register on nonexistent register is no-op."""
        empty_state.clear_register(999)  # Should not raise

    def test_register_not_found(self, empty_state):
        """Accessing missing register raises RegisterNotFound."""
        with pytest.raises(RegisterNotFound):
            empty_state.get_register(42)

    def test_overwrite_register(self, empty_state):
        """Setting register overwrites previous value."""
        empty_state.set_register(0, 10)
        empty_state.set_register(0, 20)
        assert empty_state.get_register(0) == 20

    def test_multiple_registers(self, empty_state):
        """Multiple registers can be used independently."""
        empty_state.set_register(0, 1)
        empty_state.set_register(1, 2)
        empty_state.set_register(100, 100)

        assert empty_state.get_register(0) == 1
        assert empty_state.get_register(1) == 2
        assert empty_state.get_register(100) == 100


class TestJumpControl:
    """Tests for JumpControl jump targets and loops."""

    def test_define_target(self):
        """Define a jump target."""
        jc = JumpControl()
        jc.define_target(0, 10)
        assert jc.resolve_target(0) == 10

    def test_resolve_undefined_target(self):
        """Resolving undefined target returns None."""
        jc = JumpControl()
        assert jc.resolve_target(99) is None

    def test_multiple_targets(self):
        """Multiple targets can be defined."""
        jc = JumpControl()
        jc.define_target(0, 5)
        jc.define_target(1, 15)
        jc.define_target(2, 25)

        assert jc.resolve_target(0) == 5
        assert jc.resolve_target(1) == 15
        assert jc.resolve_target(2) == 25


class TestLoopRangeOperations:
    """Tests for RANGE loop operations."""

    def test_push_loop_range_basic(self):
        """push_loop_range creates correct values."""
        jc = JumpControl()
        jc.push_loop_range(target=0, start=0, count=5)

        assert jc.in_loop is True
        assert jc.current_loop_value() == 0

    def test_push_loop_range_does_not_materialise_the_iteration_space(self):
        """A huge RANGE is held lazily, so pushing it is O(1) and allocation-free."""
        jc = JumpControl()
        jc.push_loop_range(target=0, start=0, count=10**9)

        frame = jc.current_loop()
        assert frame is not None
        assert not isinstance(frame.values, list)
        assert len(frame.values) == 10**9
        assert jc.current_loop_value() == 0
        assert jc.advance_loop() is True
        assert jc.current_loop_value() == 1

    def test_range_loop_values(self):
        """Range loop generates correct sequence."""
        jc = JumpControl()
        jc.push_loop_range(target=0, start=0, count=3)

        values = []
        while True:
            values.append(jc.current_loop_value())
            if not jc.advance_loop():
                break

        assert values == [0, 1, 2]

    def test_range_loop_with_start_offset(self):
        """Range loop with non-zero start."""
        jc = JumpControl()
        jc.push_loop_range(target=0, start=10, count=3)

        values = []
        while True:
            values.append(jc.current_loop_value())
            if not jc.advance_loop():
                break

        assert values == [10, 11, 12]

    def test_advance_loop_returns_continue(self):
        """advance_loop returns True while values remain."""
        jc = JumpControl()
        jc.push_loop_range(target=0, start=0, count=2)

        assert jc.advance_loop() is True  # More values
        assert jc.advance_loop() is False  # Done

    def test_loop_depth(self):
        """loop_depth tracks nesting level."""
        jc = JumpControl()
        assert jc.loop_depth == 0

        jc.push_loop_range(target=0, start=0, count=5)
        assert jc.loop_depth == 1

        jc.push_loop_range(target=1, start=0, count=3)
        assert jc.loop_depth == 2


class TestLoopIterOperations:
    """Tests for ITER loop operations."""

    def test_push_loop_iter_basic(self):
        """push_loop_iter copies vec slice."""
        jc = JumpControl()
        v = Vec.from_list([10, 20, 30, 40, 50])
        jc.push_loop_iter(target=0, vec=v, start_idx=0, end_idx=3)

        assert jc.in_loop is True
        assert jc.current_loop_value() == 10

    def test_iter_loop_values(self):
        """Iter loop iterates over vec slice."""
        jc = JumpControl()
        v = Vec.from_list([100, 200, 300])
        jc.push_loop_iter(target=0, vec=v, start_idx=0, end_idx=3)

        values = []
        while True:
            values.append(jc.current_loop_value())
            if not jc.advance_loop():
                break

        assert values == [100, 200, 300]

    def test_iter_loop_partial_slice(self):
        """Iter loop with partial slice."""
        jc = JumpControl()
        v = Vec.from_list([1, 2, 3, 4, 5])
        jc.push_loop_iter(target=0, vec=v, start_idx=1, end_idx=4)

        values = []
        while True:
            values.append(jc.current_loop_value())
            if not jc.advance_loop():
                break

        assert values == [2, 3, 4]


class TestNestedLoops:
    """Tests for nested loop operations."""

    def test_nested_range_loops(self):
        """Nested range loops work correctly."""
        jc = JumpControl()
        jc.push_loop_range(target=0, start=0, count=2)  # Outer
        jc.push_loop_range(target=1, start=0, count=3)  # Inner

        # Inner loop values
        assert jc.current_loop_value() == 0
        jc.advance_loop()
        assert jc.current_loop_value() == 1
        jc.advance_loop()
        assert jc.current_loop_value() == 2
        jc.advance_loop()  # Inner done, pops

        # Back to outer loop
        assert jc.loop_depth == 1
        assert jc.current_loop_value() == 0

    def test_current_loop_returns_frame(self):
        """current_loop returns current frame without removing."""
        jc = JumpControl()
        jc.push_loop_range(target=5, start=0, count=3)

        frame = jc.current_loop()
        assert frame is not None
        assert frame.target == 5
        assert jc.loop_depth == 1  # Still there


class TestLoopErrors:
    """Tests for loop error conditions."""

    def test_current_loop_value_outside_loop(self):
        """current_loop_value outside loop raises NoActiveLoop."""
        jc = JumpControl()
        with pytest.raises(NoActiveLoop):
            jc.current_loop_value()

    def test_advance_loop_outside_loop(self):
        """advance_loop outside loop raises NoActiveLoop."""
        jc = JumpControl()
        with pytest.raises(NoActiveLoop):
            jc.advance_loop()

    def test_pop_loop_outside_loop(self):
        """pop_loop outside loop raises NoActiveLoop."""
        jc = JumpControl()
        with pytest.raises(NoActiveLoop):
            jc.pop_loop()

    def test_in_loop_false_initially(self):
        """in_loop is False when no loops active."""
        jc = JumpControl()
        assert jc.in_loop is False

    def test_current_loop_returns_none_outside(self):
        """current_loop returns None outside loop."""
        jc = JumpControl()
        assert jc.current_loop() is None


class TestIOOperations:
    """Tests for input/output slot operations."""

    def test_set_get_input(self, empty_state):
        """Set and get input slot."""
        empty_state.input_slots = 4
        empty_state.set_input(0, 42)
        assert empty_state.get_input(0) == 42

    def test_get_input_past_the_slot_count_raises(self, empty_state):
        """A slot outside the count is a program error, not a null result.

        The mirror of `set_output`: the host fixes both counts before the
        run, so reading past the calldata is a fault rather than None.
        Returning None let `PUSH 0 / INPUT r0 / HALT` on empty calldata
        store None and halt successfully here while the Rust VM raised
        `CallDataIndex`.
        """
        empty_state.input_slots = 4
        with pytest.raises(CallDataIndex):
            empty_state.get_input(99)

    def test_get_input_with_no_calldata_raises(self, empty_state):
        """Zero slots is the default, so any read is out of range."""
        with pytest.raises(CallDataIndex):
            empty_state.get_input(0)

    def test_set_get_output(self, empty_state):
        """Set and get output slot."""
        empty_state.output_slots = 4
        empty_state.set_output(0, "result")
        assert empty_state.get_output(0) == "result"

    def test_get_missing_output(self, empty_state):
        """Getting missing output returns None."""
        assert empty_state.get_output(99) is None

    def test_multiple_io_slots(self, empty_state):
        """Multiple I/O slots work independently."""
        empty_state.input_slots = 4
        empty_state.set_input(0, "a")
        empty_state.set_input(1, "b")
        empty_state.output_slots = 4
        empty_state.set_output(0, "x")
        empty_state.set_output(1, "y")

        assert empty_state.get_input(0) == "a"
        assert empty_state.get_input(1) == "b"
        assert empty_state.get_output(0) == "x"
        assert empty_state.get_output(1) == "y"

    def test_io_can_hold_complex_values(self, empty_state):
        """I/O slots can hold any value type."""
        v = Vec.from_list([1, 2, 3])
        x = XQMX.binary_model(5)

        empty_state.input_slots = 4
        empty_state.set_input(0, v)
        empty_state.output_slots = 4
        empty_state.set_output(0, x)

        assert empty_state.get_input(0).length == 3
        assert empty_state.get_output(0).size == 5


class TestControlFlow:
    """Tests for PC and execution control."""

    def test_advance_pc(self, empty_state):
        """advance_pc increments PC."""
        assert empty_state.pc == 0
        empty_state.advance_pc()
        assert empty_state.pc == 1
        empty_state.advance_pc()
        assert empty_state.pc == 2

    def test_jump_to(self, empty_state):
        """jump_to sets PC to target."""
        empty_state.jump_to(100)
        assert empty_state.pc == 100

    def test_halt(self, empty_state):
        """halt sets halted flag."""
        assert empty_state.halted is False
        empty_state.halt()
        assert empty_state.halted is True


class TestReset:
    """Tests for state reset functionality."""

    def test_reset_clears_stack(self, empty_state):
        """reset clears the stack."""
        empty_state.push(1)
        empty_state.push(2)
        empty_state.reset()
        assert empty_state.stack_depth == 0

    def test_reset_clears_registers(self, empty_state):
        """reset clears registers."""
        empty_state.set_register(0, 42)
        empty_state.reset()
        assert empty_state.has_register(0) is False

    def test_reset_clears_pc(self, empty_state):
        """reset resets PC to 0."""
        empty_state.advance_pc()
        empty_state.advance_pc()
        empty_state.reset()
        assert empty_state.pc == 0

    def test_reset_clears_halted(self, empty_state):
        """reset clears halted flag."""
        empty_state.halt()
        empty_state.reset()
        assert empty_state.halted is False

    def test_reset_clears_io(self, empty_state):
        """reset clears I/O slots and both slot counts."""
        empty_state.input_slots = 4
        empty_state.set_input(0, "in")
        empty_state.output_slots = 4
        empty_state.set_output(0, "out")
        empty_state.reset()
        # `input_slots` goes back to 0 with everything else, so the read is
        # out of range rather than a surviving None.
        with pytest.raises(CallDataIndex):
            empty_state.get_input(0)
        assert empty_state.get_output(0) is None

    def test_reset_clears_the_output_slot_count(self, empty_state):
        """reset restores initial conditions, `output_slots` included.

        Leaving the slot count standing meant a reset state still described
        the previous run's host contract -- the mirror of the Rust VM leaving
        its outputs, calldata and slot count in place.
        """
        empty_state.output_slots = 4
        empty_state.set_output(0, "out")
        empty_state.reset()
        assert empty_state.output_slots == 0
        with pytest.raises(OutputIndex):
            empty_state.set_output(0, "again")

    def test_reset_clears_jump_control(self, empty_state):
        """reset clears jump targets and loops."""
        empty_state.jc.define_target(0, 10)
        empty_state.jc.push_loop_range(target=0, start=0, count=5)
        empty_state.reset()
        assert empty_state.jc.resolve_target(0) is None
        assert empty_state.jc.in_loop is False


class TestSnapshot:
    """Tests for state snapshot functionality."""

    def test_snapshot_structure(self, empty_state):
        """snapshot returns dict with expected keys."""
        empty_state.push(42)
        empty_state.set_register(0, 100)
        empty_state.advance_pc()

        snap = empty_state.snapshot()

        assert "stack" in snap
        assert "registers" in snap
        assert "pc" in snap
        assert "halted" in snap

    def test_snapshot_captures_stack(self, empty_state):
        """snapshot captures stack values."""
        empty_state.push(1)
        empty_state.push(2)

        snap = empty_state.snapshot()
        assert snap["stack"] == [1, 2]

    def test_snapshot_captures_pc(self, empty_state):
        """snapshot captures PC value."""
        empty_state.advance_pc()
        empty_state.advance_pc()

        snap = empty_state.snapshot()
        assert snap["pc"] == 2
