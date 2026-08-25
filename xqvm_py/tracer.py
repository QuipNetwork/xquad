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
XQVM Execution Tracer: Step-by-step execution logging.

Implements TracerProtocol to capture and format execution traces
with configurable verbosity levels.
"""

from __future__ import annotations

from typing import Any

from .program import Instruction
from .state import Value
from .vector import Vec
from .xqmx import XQMX


class Tracer:
    """
    Execution tracer with configurable verbosity.

    Verbosity levels:
        1 = compact (one line per instruction: pc, opcode, operands)
        2 = detailed (+ stack snapshot and full register state)
    """

    def __init__(self, verbosity: int = 1) -> None:
        self.verbosity = verbosity
        self.events: list[dict[str, Any]] = []
        self._pre_stack: list[int] = []
        self._pre_registers: dict[int, Value] = {}
        self._buffer: list[dict[str, Any]] = []

    def on_step_begin(self, executor: Any, _instr: Instruction) -> None:
        """Capture pre-execution state."""
        state = executor.state
        self._pre_stack = list(state.stack)
        self._pre_registers = dict(state.registers)

    def on_step_end(self, executor: Any, instr: Instruction) -> None:
        """Capture post-execution state, compute diffs, record event."""
        state = executor.state
        post_stack = list(state.stack)
        post_registers = dict(state.registers)

        # Track which registers changed
        changed: set[int] = set()
        all_keys = set(self._pre_registers) | set(post_registers)

        for k in all_keys:
            pre_val = self._pre_registers.get(k)
            post_val = post_registers.get(k)

            if pre_val is not post_val and repr(pre_val) != repr(post_val):
                changed.add(k)

        event = {
            "pc": state.pc - 1 if not state.halted else state.pc,
            "opcode": instr.opcode.name,
            "operands": instr.operands,
            "stack_before": self._pre_stack,
            "stack_after": post_stack,
            "registers": post_registers,
            "pre_registers": dict(self._pre_registers),
            "changed": changed,
        }
        self.events.append(event)

        if self.verbosity >= 1:
            self._buffer.append(event)

    def on_error(self, executor: Any, instr: Instruction, error: Exception) -> None:
        """Record error event with state context."""
        event = {
            "pc": executor.state.pc,
            "opcode": instr.opcode.name,
            "operands": instr.operands,
            "error": str(error),
            "stack_before": self._pre_stack,
        }
        self.events.append(event)

        if self.verbosity >= 1:
            self._buffer.append(event)

    def on_halt(self, executor: Any) -> None:
        """Record halt event with final state summary, flush buffered output.

        The count emitted is `outputs_written`, not `output_slots`.
        `len(state.output)` is how many slots the run actually wrote;
        `MachineState.output_slots` is how many the host reserved. Emitting
        the first under the second's name made a trace read as though a
        program had reserved what it happened to fill.
        """
        state = executor.state
        event = {
            "halt": True,
            "final_stack": list(state.stack),
            "final_registers": len(state.registers),
            "outputs_written": len(state.output),
        }
        self.events.append(event)

        if self.verbosity >= 1:
            self._buffer.append(event)
            self._flush()

    def _flush(self) -> None:
        """Print buffered events with aligned columns."""
        if not self._buffer:
            return

        for line in _format_aligned(self._buffer, self.verbosity):
            print(line)

        self._buffer.clear()

    def format_event(self, event: dict[str, Any]) -> str:
        """Format a single trace event as text (unaligned)."""
        instr_col, stack_col, regs_col = _event_columns(event, self.verbosity)

        if not instr_col:
            return stack_col
        if not stack_col:
            return instr_col

        line = f"{instr_col} | {stack_col} | {regs_col}"
        return line

    def format_trace(self) -> str:
        """Format all collected events as aligned text."""
        if not self.events:
            return ""

        return "\n".join(_format_aligned(self.events, self.verbosity))


# === Value formatting ===


def _fmt_val(val: Value | None) -> str:
    """Format a register value in compact lowercase."""
    if val is None:
        return "none"

    if isinstance(val, int):
        return f"int({val})"

    if isinstance(val, Vec):
        elems = [val.get(i) for i in range(val.length)]
        etype = val.element_type or "?"
        # Use bare values for int elements since type is in the vec<> tag
        if str(etype).lower() == "int":
            items = ", ".join(str(e) for e in elems)
        else:
            items = ", ".join(_fmt_val(e) for e in elems)
        return f"vec<{str(etype).lower()}>({items})"

    if isinstance(val, XQMX):
        mode = val.mode.name.lower()
        domain = val.domain.name.lower()
        nl = len(val.linear)
        nq = len(val.quadratic)
        return f"xqmx({mode}, {domain}, n={val.size}, l={nl}, q={nq})"

    return repr(val)


def _fmt_regs(event: dict[str, Any]) -> str:
    """
    Format register state as active list + touched diffs.

    Shows [r0 r1 r2] for all active registers, then [r1: none -> 6]
    for each register that changed on this step.
    """
    registers: dict[int, Value] = event.get("registers", {})
    pre_registers: dict[int, Value] = event.get("pre_registers", {})
    changed: set[int] = event.get("changed", set())

    parts: list[str] = []
    for k in sorted(registers):
        if k in changed:
            pre_val = pre_registers.get(k)
            post_val = registers.get(k)

            if pre_val is None:
                parts.append(f"[r{k}: {_fmt_val(post_val)}]")
            else:
                parts.append(f"[r{k}: {_fmt_val(pre_val)} -> {_fmt_val(post_val)}]")
        else:
            parts.append(f"[r{k}]")

    return "".join(parts)


# === Column extraction and alignment ===


def _event_columns(event: dict[str, Any], verbosity: int) -> tuple[str, str, str]:
    """
    Extract (instr, stack, regs) column strings for an event.

    Returns empty strings for columns that don't apply.
    """
    if "halt" in event:
        halt_line = (
            f"halt: stack_depth={len(event['final_stack'])}, "
            f"registers={event['final_registers']}, "
            f"outputs_written={event['outputs_written']}"
        )
        return ("", halt_line, "")

    # Instruction column
    ops = " ".join(str(o) for o in event["operands"])
    instr_col = f"[{event['pc']:04d}] {event['opcode']}"
    if ops:
        instr_col += f" {ops}"

    if "error" in event:
        return (f"[{event['pc']:04d}] ERROR {event['opcode']}: {event['error']}", "", "")

    if verbosity < 2:
        return (instr_col, "", "")

    # Stack column
    stack_col = f"{event['stack_before']} -> {event['stack_after']}"

    # Register column (full snapshot with diffs)
    regs_col = _fmt_regs(event)

    return (instr_col, stack_col, regs_col)


def _format_aligned(events: list[dict[str, Any]], verbosity: int) -> list[str]:
    """Format a list of events with aligned instruction, stack, and register columns."""
    rows: list[tuple[str, str, str]] = []
    for event in events:
        rows.append(_event_columns(event, verbosity))

    # Compute max widths for alignment (only across detailed rows)
    max_instr = 0
    max_stack = 0

    for instr_col, stack_col, _ in rows:
        if instr_col and stack_col:
            max_instr = max(max_instr, len(instr_col))
            max_stack = max(max_stack, len(stack_col))

    lines: list[str] = []
    for instr_col, stack_col, regs_col in rows:
        if not instr_col:
            lines.append(stack_col)
            continue

        if not stack_col:
            lines.append(instr_col)
            continue

        line = instr_col.ljust(max_instr)
        line += f" | {stack_col.ljust(max_stack)}"
        line += f" | {regs_col}"

        lines.append(line)

    return lines
