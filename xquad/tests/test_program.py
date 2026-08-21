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

"""Tests for the `xquad.program` surface (Program / Session / RunResult)."""

from __future__ import annotations

import pytest

from xqffi.vm import XqmxModel, XqmxSample
from xquad.program import Program, RunResult

ADD_CALLDATA_SRC = """
PUSH 0
INPUT r0
PUSH 1
INPUT r1
LOAD r0
LOAD r1
ADD
STOW r2
PUSH 0
OUTPUT r2
HALT
"""


class TestProgram:
    def test_from_source_carries_text(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        assert p.source is not None
        assert "ADD" in p.source
        assert p.instruction_count != 0

    def test_load_bytecode(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        bc = p.bytecode()
        p2 = Program.load(bc)
        assert p2.source is None
        assert p2.instruction_count == p.instruction_count
        assert p2.bytecode() == bc

    def test_from_source_rejects_invalid(self):
        with pytest.raises(ValueError):
            Program.from_source("NOT_A_REAL_OPCODE r0")

    def test_run_rejects_malformed_bytecode_at_execution(self):
        p = Program.load(b"\x43")
        s = p.session()
        with pytest.raises(RuntimeError):
            s.run()

    def test_repr_mentions_instruction_count(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        r = repr(p)
        assert "Program" in r
        assert "instructions=" in r


class TestSession:
    def test_run_produces_run_result(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session(output_slots=1)
        s.set_calldata([40, 2])
        result = s.run()
        assert isinstance(result, RunResult)

    def test_outputs_are_dict_keyed_by_slot(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session(output_slots=1)
        s.set_calldata([40, 2])
        result = s.run()
        outputs = dict(result.outputs)
        assert outputs == {0: 42}

    def test_outputs_unset_slots_are_none(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session(output_slots=4)
        s.set_calldata([1, 2])
        result = s.run()
        outputs = dict(result.outputs)
        assert outputs == {0: 3, 1: None, 2: None, 3: None}

    def test_multi_run_isolated(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session(output_slots=1)
        s.set_calldata([10, 20])
        assert dict(s.run().outputs) == {0: 30}
        s.set_calldata([100, 200])
        assert dict(s.run().outputs) == {0: 300}

    def test_step_limit_enforced(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session(output_slots=1)
        s.set_calldata([1, 2])
        s.set_step_limit(3)
        with pytest.raises(RuntimeError, match="StepLimitExceeded"):
            s.run()
        s.set_step_limit(None)
        assert dict(s.run().outputs) == {0: 3}

    def test_zero_step_limit_executes_nothing(self):
        # The limit is exact; `None`, not `0`, is how you ask for no bound.
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session(output_slots=1)
        s.set_calldata([1, 2])
        s.set_step_limit(0)
        with pytest.raises(RuntimeError, match="StepLimitExceeded"):
            s.run()

    def test_stack_and_steps_exposed(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session(output_slots=1)
        s.set_calldata([1, 2])
        r = s.run()
        assert r.stack == []
        assert r.steps > 0

    def test_accepts_xqmx_model_as_calldata(self):
        src = "PUSH 0\nINPUT r0\nHALT\n"
        p = Program.from_source(src)
        s = p.session(output_slots=0)
        m = XqmxModel("binary", size=4)
        s.set_calldata([m])
        r = s.run()
        assert dict(r.outputs) == {}

    def test_accepts_xqmx_sample_as_calldata(self):
        src = "PUSH 0\nINPUT r0\nHALT\n"
        p = Program.from_source(src)
        s = p.session(output_slots=0)
        s.set_calldata([XqmxSample("spin", values=[-1, 1, -1])])
        s.run()

    def test_rejects_unknown_calldata_type(self):
        p = Program.from_source(ADD_CALLDATA_SRC)
        s = p.session()
        with pytest.raises(TypeError):
            s.set_calldata([object()])
