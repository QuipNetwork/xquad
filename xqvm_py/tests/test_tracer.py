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
Tests for the XQVM execution tracer.
"""

import pytest

from xqvm_py.executor import Executor
from xqvm_py.opcodes import Opcode
from xqvm_py.program import Instruction, make_program
from xqvm_py.tracer import Tracer

# === Event Collection ===


class TestEventCollection:
    """Test that tracer collects events correctly."""

    def test_silent_collects_events(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.ADD),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        # 3 step events + 1 halt event (TARGET-like NOPs excluded, HALT triggers on_halt)
        step_events = [e for e in tracer.events if "opcode" in e]
        halt_events = [e for e in tracer.events if "halt" in e]
        assert len(step_events) == 3  # PUSH, PUSH, ADD (HALT triggers halt, not step_end)
        assert len(halt_events) == 1

    def test_event_has_opcode(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        assert tracer.events[0]["opcode"] == "PUSH1"
        assert tracer.events[0]["operands"] == (42,)

    def test_event_captures_stack(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (20,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        # After first PUSH: stack was [] -> [10]
        assert tracer.events[0]["stack_before"] == []
        assert tracer.events[0]["stack_after"] == [10]
        # After second PUSH: stack was [10] -> [10, 20]
        assert tracer.events[1]["stack_before"] == [10]
        assert tracer.events[1]["stack_after"] == [10, 20]

    def test_event_captures_register_changes(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (99,)),
                Instruction(Opcode.STOW, (5,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        # STOW event should show r5 changed
        stow_event = tracer.events[1]
        assert 5 in stow_event["changed"]
        assert stow_event["registers"][5] == 99

    def test_no_change_when_register_unchanged(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (2,)),
                Instruction(Opcode.ADD),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        # ADD doesn't touch registers
        add_event = tracer.events[2]
        assert add_event["changed"] == set()


# === Halt Event ===


class TestHaltEvent:
    """Test halt event recording."""

    def test_halt_event_recorded(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (42,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        halt = [e for e in tracer.events if "halt" in e]
        assert len(halt) == 1
        assert halt[0]["final_stack"] == []
        assert halt[0]["final_registers"] == 1

    def test_halt_event_tracks_outputs(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.STOW, (0,)),
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.OUTPUT, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        halt = [e for e in tracer.events if "halt" in e][0]
        assert halt["outputs_written"] == 1


# === Error Event ===


class TestErrorEvent:
    """Test error event recording."""

    def test_error_event_recorded(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.POP),  # Stack underflow
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        with pytest.raises(Exception):
            ex.execute(prog, output_slots=16)
        error_events = [e for e in tracer.events if "error" in e]
        assert len(error_events) == 1
        assert "underflow" in error_events[0]["error"].lower()


# === Formatting ===


class TestFormatting:
    """Test trace output formatting."""

    def test_compact_format(self):
        tracer = Tracer(verbosity=1)
        event = {
            "pc": 0,
            "opcode": "PUSH1",
            "operands": (42,),
            "stack_before": [],
            "stack_after": [42],
            "registers": {},
            "pre_registers": {},
            "changed": set(),
        }
        text = tracer.format_event(event)
        assert "[0000] PUSH1 42" == text

    def test_detailed_format_includes_stack(self):
        tracer = Tracer(verbosity=2)
        event = {
            "pc": 3,
            "opcode": "ADD",
            "operands": (),
            "stack_before": [10, 20],
            "stack_after": [30],
            "registers": {},
            "pre_registers": {},
            "changed": set(),
        }
        text = tracer.format_event(event)
        assert "[0003] ADD" in text
        assert "[10, 20] -> [30]" in text

    def test_detailed_format_includes_register_snapshot(self):
        tracer = Tracer(verbosity=2)
        event = {
            "pc": 1,
            "opcode": "STOW",
            "operands": (0,),
            "stack_before": [42],
            "stack_after": [],
            "registers": {0: 42},
            "pre_registers": {},
            "changed": {0},
        }
        text = tracer.format_event(event)
        assert "r0: int(42)" in text

    def test_format_error_event(self):
        tracer = Tracer(verbosity=1)
        event = {
            "pc": 5,
            "opcode": "POP",
            "operands": (),
            "error": "Stack underflow",
            "stack_before": [],
        }
        text = tracer.format_event(event)
        assert "ERROR" in text
        assert "POP" in text

    def test_format_halt_event(self):
        tracer = Tracer(verbosity=1)
        event = {
            "halt": True,
            "final_stack": [42],
            "final_registers": 3,
            "outputs_written": 1,
        }
        text = tracer.format_event(event)
        assert "halt" in text
        assert "stack_depth=1" in text

    def test_format_trace_all_events(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        text = tracer.format_trace()
        assert "PUSH1" in text
        assert "halt" in text


# === Integration ===


class TestTracerIntegration:
    """End-to-end tracer with real programs."""

    def test_loop_tracing(self):
        tracer = Tracer(verbosity=0)
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (0,)),
                Instruction(Opcode.PUSH1, (3,)),
                Instruction(Opcode.RANGE),
                Instruction(Opcode.LVAL, (0,)),
                Instruction(Opcode.NEXT),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor(tracer=tracer)
        ex.execute(prog, output_slots=16)
        opcodes = [e["opcode"] for e in tracer.events if "opcode" in e]
        # PUSH, PUSH, RANGE, then 3 iterations of (LVAL, NEXT), minus final NEXT exits
        assert "RANGE" in opcodes
        assert "LVAL" in opcodes
        assert "NEXT" in opcodes

    def test_tracer_does_not_affect_execution(self):
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (10,)),
                Instruction(Opcode.PUSH1, (20,)),
                Instruction(Opcode.ADD),
                Instruction(Opcode.HALT),
            ]
        )
        # Without tracer
        ex1 = Executor()
        ex1.execute(prog)
        result1 = ex1.state.peek(0)
        # With tracer
        ex2 = Executor(tracer=Tracer(verbosity=0))
        ex2.execute(prog)
        result2 = ex2.state.peek(0)
        assert result1 == result2 == 30
