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
XQVM Virtual Machine State
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import (
    CallDataIndex,
    LoopError,
    LoopStackOverflow,
    OutputIndex,
    RegisterNotFound,
    StackOverflow,
    StackUnderflow,
)
from .limits import I64_MAX, I64_MIN, check_i64
from .vector import Vec
from .xqmx import XQMX

# Type alias for values that can be stored in registers
Value = int | Vec | XQMX

# Maximum stack size to prevent runaway programs
MAX_STACK_SIZE = 8192  # 2^13

# Maximum loop nesting depth. RANGE and ITER each push a frame and only NEXT
# pops one, so a program that jumps back over a loop header without running
# its NEXT grows the loop stack without bound. Mirrors MAX_STACK_SIZE and
# `Vm::LOOP_LIMIT` in `xqvm/src/vm.rs`.
MAX_LOOP_DEPTH = 8192  # 2^13

# Signed 64-bit bounds and the range check live in `limits`, and are
# re-exported here because both predate that module and callers import them
# from `state`. Producing a value outside the range -- via arithmetic, a
# shift, INPUT, or a model coefficient -- raises ArithmeticOverflow.
__all__ = ["I64_MAX", "I64_MIN", "LoopFrame", "MachineState", "check_i64"]


@dataclass
class LoopFrame:
    """Loop iteration frame supporting RANGE and ITER paradigms."""

    target: int  # PC to jump back to
    #: All loop values, as a plain sequence rather than a Vec. RANGE stores a
    #: lazy `range` so the iteration space is never materialised -- `xqvm`'s
    #: `exec_range` likewise keeps only `current`/`end`. ITER stores a real
    #: list, because it copies the elements it iterates. Both are read only
    #: through `len()` and a single subscript, which are O(1) either way.
    values: Sequence[Value]
    index: int = 0  # Current position in values
    start_offset: int = 0  # Base index for LIDX (original start value)


@dataclass
class JumpControl:
    """
    Jump control state: targets and loop stack.

    Targets map target IDs to instruction indices.
    The loop stack tracks nested loop state for RANGE/ITER/NEXT operations.
    """

    targets: dict[int, int] = field(default_factory=dict)
    loop_stack: list[LoopFrame] = field(default_factory=list)

    def define_target(self, target_id: int, pc: int) -> None:
        """Define a target at the given program counter."""
        self.targets[target_id] = pc

    def resolve_target(self, target_id: int) -> int | None:
        """Resolve a target ID to its program counter. Returns None if not found."""
        return self.targets.get(target_id)

    def _check_depth(self) -> None:
        """Reject a frame that would exceed the nesting limit."""
        if len(self.loop_stack) >= MAX_LOOP_DEPTH:
            raise LoopStackOverflow(MAX_LOOP_DEPTH)

    def push_loop_range(self, target: int, start: int, count: int) -> None:
        """Push a RANGE loop frame.

        The iteration space is held lazily as a `range`, not materialised into
        a list: `count` is bounded only by i64, so a list would let a
        four-instruction program exhaust host memory while charging nothing
        against the VM's memory budget. `xqvm`'s `exec_range` stores the same
        two integers.
        """
        self._check_depth()
        values = range(start, start + count)
        self.loop_stack.append(LoopFrame(target=target, values=values, start_offset=start))

    def push_loop_iter(self, target: int, vec: Vec, start_idx: int, end_idx: int) -> None:
        """Push an ITER loop frame. Copies elements for immutability."""
        self._check_depth()
        values = [vec.get(i) for i in range(start_idx, end_idx)]
        self.loop_stack.append(LoopFrame(target=target, values=values, start_offset=start_idx))

    def pop_loop(self) -> LoopFrame:
        """Pop the current loop frame. Raises LoopError if no active loop."""
        if not self.loop_stack:
            raise LoopError("No active loop")
        return self.loop_stack.pop()

    def current_loop(self) -> LoopFrame | None:
        """Get the current loop frame without removing it."""
        return self.loop_stack[-1] if self.loop_stack else None

    def advance_loop(self) -> bool:
        """Advance loop index. Returns True if loop should continue."""
        if not self.loop_stack:
            raise LoopError("No active loop to advance")

        frame = self.loop_stack[-1]
        frame.index += 1

        if frame.index >= len(frame.values):
            self.loop_stack.pop()
            return False

        return True

    def current_loop_value(self) -> Value:
        """Get current loop value. Raises LoopError if no active loop."""
        if not self.loop_stack:
            raise LoopError("No active loop")
        frame = self.loop_stack[-1]
        return frame.values[frame.index]

    def current_loop_index(self) -> int:
        """Get current loop index (offset-adjusted). Raises LoopError if no active loop."""
        if not self.loop_stack:
            raise LoopError("No active loop")
        frame = self.loop_stack[-1]
        return frame.index + frame.start_offset

    @property
    def in_loop(self) -> bool:
        """Check if currently inside a loop."""
        return len(self.loop_stack) > 0

    @property
    def loop_depth(self) -> int:
        """Get current loop nesting depth."""
        return len(self.loop_stack)


@dataclass
class MachineState:
    """
    Complete XQVM virtual machine state.

    The state includes:
    - stack: Integer-only operand stack
    - registers: Unified register file holding int, Vec, or XQMX values
    - pc: Program counter (current instruction index)
    - jc: Jump control (targets and loop stack)
    - input: Input data provided to the program
    - output: Output data produced by the program
    - halted: Whether execution has stopped
    """

    stack: list[int] = field(default_factory=list)
    registers: dict[int, Value] = field(default_factory=dict)
    pc: int = 0
    jc: JumpControl = field(default_factory=JumpControl)
    input: dict[int, Any] = field(default_factory=dict)
    output: dict[int, Any] = field(default_factory=dict)
    #: Number of allocated output slots. Zero by default, matching
    #: `xqvm::Vm::new()`, so a program that writes an output without the host
    #: reserving slots is rejected on both implementations.
    output_slots: int = 0
    #: Number of calldata slots the host fixed before the run. Zero by
    #: default, matching an empty `Vm::set_calldata`. The counterpart to
    #: `output_slots` on the other side of the boundary: `input` is a dict
    #: for convenience, but the slot space it stands for is a dense
    #: sequence, so reading outside `[0, input_slots)` is a program error
    #: rather than a null result. Inside it, a slot the host left unset is
    #: legal and reads as unset.
    input_slots: int = 0
    halted: bool = False
    steps: int = 0

    # === Stack Operations ===

    def push(self, value: int) -> None:
        """Push an integer onto the stack."""
        if len(self.stack) >= MAX_STACK_SIZE:
            raise StackOverflow(MAX_STACK_SIZE)
        check_i64(value, "stack push")
        self.stack.append(value)

    def pop(self) -> int:
        """Pop an integer from the stack."""
        if not self.stack:
            raise StackUnderflow(required=1, available=0)
        return self.stack.pop()

    def peek(self, depth: int = 0) -> int:
        """Peek at a stack value without removing it. depth=0 is top."""
        if depth >= len(self.stack):
            raise StackUnderflow(required=depth + 1, available=len(self.stack))
        return self.stack[-(depth + 1)]

    def pop_n(self, n: int) -> list[int]:
        """Pop n values from stack. Returns in pop order (top first)."""
        if len(self.stack) < n:
            raise StackUnderflow(required=n, available=len(self.stack))

        result = []
        for _ in range(n):
            result.append(self.stack.pop())

        return result

    @property
    def stack_depth(self) -> int:
        """Current stack depth."""
        return len(self.stack)

    # === Register Operations ===

    def get_register(self, slot: int) -> Value:
        """Get a register value. Raises RegisterNotFound if not set."""
        if slot not in self.registers:
            raise RegisterNotFound(slot)
        return self.registers[slot]

    def set_register(self, slot: int, value: Value) -> None:
        """Set a register value."""
        self.registers[slot] = value

    def has_register(self, slot: int) -> bool:
        """Check if a register exists."""
        return slot in self.registers

    def clear_register(self, slot: int) -> None:
        """Clear a register. No error if it doesn't exist."""
        self.registers.pop(slot, None)

    # === I/O Operations ===

    def get_input(self, slot: int) -> Any:
        """Get an input slot value, or None if the host left it unset.

        The slot count is fixed before the run, so a slot outside it is a
        program error rather than a null result, mirroring `set_output` on
        the other side of the boundary. Returning None for an out-of-range
        slot let `PUSH 0 / INPUT r0 / HALT` on empty calldata store None and
        halt successfully here while the Rust VM raised `CallDataIndex`.

        None inside the count is a different thing and stays: it is an
        in-range slot the host did not fill, the Python spelling of a
        `RegVal::Unset` entry in `Vm::set_calldata`'s vec. `_runner_INPUT`
        turns it into an unset register rather than a stored null.
        """
        if slot < 0 or slot >= self.input_slots:
            raise CallDataIndex(slot, self.input_slots)
        return self.input.get(slot)

    def set_input(self, slot: int, value: Any) -> None:
        """Set an input slot value."""
        self.input[slot] = value

    def set_output(self, slot: int, value: Any) -> None:
        """Set an output slot value.

        The slot count is fixed before the run, so a slot outside it is a
        program error rather than a request to grow the map.
        """
        if slot < 0 or slot >= self.output_slots:
            raise OutputIndex(slot, self.output_slots)
        self.output[slot] = value

    def get_output(self, slot: int) -> Any:
        """Get an output slot value. Returns None if not set."""
        return self.output.get(slot)

    # === Control Flow ===

    def advance_pc(self) -> None:
        """Advance program counter by one."""
        self.pc += 1

    def jump_to(self, target: int) -> None:
        """Set program counter to target instruction index."""
        self.pc = target

    def halt(self) -> None:
        """Stop execution."""
        self.halted = True

    # === State Management ===

    def reset(self) -> None:
        """Reset machine state to initial conditions.

        Every field a run touches goes back to the value a
        freshly-constructed `MachineState` carries, both slot counts
        included.
        Leaving the slot count standing meant a reset state still described
        the previous run's host contract, which is the mirror of the Rust
        VM leaving its outputs, calldata and slot count in place.
        """
        self.stack.clear()
        self.registers.clear()
        self.pc = 0
        self.jc = JumpControl()
        self.input.clear()
        self.output.clear()
        self.output_slots = 0
        self.input_slots = 0
        self.halted = False
        self.steps = 0

    def snapshot(self) -> dict[str, Any]:
        """Create a snapshot of current state for debugging."""
        return {
            "stack": list(self.stack),
            "registers": {k: repr(v) for k, v in self.registers.items()},
            "pc": self.pc,
            "targets": dict(self.jc.targets),
            "loop_depth": self.jc.loop_depth,
            "halted": self.halted,
        }

    def __repr__(self) -> str:
        return (
            f"MachineState(pc={self.pc}, stack_depth={len(self.stack)}, "
            f"registers={len(self.registers)}, halted={self.halted})"
        )
