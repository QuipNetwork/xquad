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

"""The default step budget is bounded, on every `xquad` entry point.

Unbounded execution has to be asked for rather than stumbled into. Defaulting
`step_limit` to `None` dispatched to `set_unlimited_steps()`, so a program
that raised in about a tenth of a second on 0.3.x never returned -- while
`xqcli` kept `default_value = "10000000"` plus an explicit opt-out. `None`
stays reserved for a caller who writes it; there is no third "unset" state.
"""

from __future__ import annotations

import pytest

from xquad.program import Program
from xquad.vm import DEFAULT_STEP_LIMIT, VM, VMBackend

#: Runs forever unless something bounds it.
RUNAWAY = "TARGET .0\nNOP\nJUMP .0"

TRIVIAL = "PUSH 1\nPUSH 2\nADD\nHALT"


def test_the_default_is_the_documented_bound() -> None:
    assert DEFAULT_STEP_LIMIT == 10_000_000


def test_a_fresh_session_bounds_a_runaway_program() -> None:
    with pytest.raises(Exception, match="StepLimit"):
        Program.from_source(RUNAWAY).session().run()


def test_a_session_takes_the_default_step_limit() -> None:
    session = Program.from_source(TRIVIAL).session()
    assert "step_limit=10000000" in repr(session)


def test_an_explicit_none_is_still_unlimited() -> None:
    session = Program.from_source(TRIVIAL).session(step_limit=None)
    assert "step_limit=unlimited" in repr(session)
    assert session.run().stack == [3]


@pytest.mark.parametrize("backend", [VMBackend.RUST, VMBackend.PYTHON])
def test_a_fresh_vm_bounds_a_runaway_program(backend: VMBackend) -> None:
    vm = VM(backend)
    with pytest.raises(Exception, match="[Ss]tep [Ll]imit|StepLimit"):
        vm.run(RUNAWAY)


@pytest.mark.parametrize("backend", [VMBackend.RUST, VMBackend.PYTHON])
def test_reset_restores_the_bounded_default(backend: VMBackend) -> None:
    """`reset()` must not silently hand the caller an unbounded VM.

    Resetting to `None` was the same defect one level down: a VM the caller
    had deliberately unbounded came back from `reset()` still unbounded, and
    so did one that had never been touched. The end-to-end enforcement of
    that default is pinned above; this asserts `reset()` lands on it rather
    than spending another ten million steps proving the same thing twice.
    """
    vm = VM(backend)
    vm.set_step_limit(None)
    vm.reset()
    assert vm._step_limit == DEFAULT_STEP_LIMIT

    # And a budget the caller did set is still enforced after a reset.
    vm.set_step_limit(2)
    with pytest.raises(Exception, match="[Ss]tep [Ll]imit|StepLimit"):
        vm.run(TRIVIAL)


@pytest.mark.parametrize("backend", [VMBackend.RUST, VMBackend.PYTHON])
def test_reset_then_run_still_executes_an_ordinary_program(backend: VMBackend) -> None:
    vm = VM(backend)
    vm.run(TRIVIAL)
    assert vm.stack() == [3]
    vm.reset()
    vm.run(TRIVIAL)
    assert vm.stack() == [3]
