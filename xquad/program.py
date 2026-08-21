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

"""`xquad.program` — interactive multi-run API (Program / Session / RunResult).

Pure-Python convenience layer over `xqffi.asm` and `xqffi.vm`. A `Program`
loads once from bytecode or source, a `Session` carries mutable calldata and
output-slot configuration for repeated runs, and each `RunResult` presents
one execution's outputs as a dict keyed by slot index.
"""

from __future__ import annotations

from xqffi.asm import assemble_source
from xqffi.vm import Vm, XqmxModel, XqmxSample
from xqvm_py.executor import DEFAULT_MEMORY_LIMIT as _DEFAULT_MEMORY_LIMIT

__all__ = ["Program", "RunResult", "Session"]


def _validate_calldata_element(item: object) -> None:
    if item is None or isinstance(item, (int, XqmxModel, XqmxSample)):
        return
    if isinstance(item, list):
        for x in item:
            if not isinstance(x, int):
                raise TypeError(
                    f"unsupported calldata element type: {type(item).__name__}; "
                    "expected int, list[int], XqmxModel, XqmxSample, or None"
                )
        return
    raise TypeError(
        f"unsupported calldata element type: {type(item).__name__}; "
        "expected int, list[int], XqmxModel, XqmxSample, or None"
    )


class Program:
    """An immutable program loaded from bytecode or assembled from source."""

    __slots__ = ("_bytecode", "_source")

    def __init__(self, bytecode: bytes, source: str | None) -> None:
        self._bytecode = bytecode
        self._source = source

    @staticmethod
    def load(bytecode: bytes) -> Program:
        return Program(bytes(bytecode), None)

    @staticmethod
    def from_source(source: str) -> Program:
        bc = bytes(assemble_source(source))
        return Program(bc, source)

    @property
    def source(self) -> str | None:
        return self._source

    def bytecode(self) -> bytes:
        return self._bytecode

    @property
    def instruction_count(self) -> int:
        try:
            from xqffi.asm import instruction_count as _ic

            return _ic(self._bytecode)
        except ImportError:
            return -1

    def session(self, output_slots: int = 16, step_limit: int | None = None) -> Session:
        return Session(self, output_slots, step_limit)

    def __repr__(self) -> str:
        tag = "from_source" if self._source is not None else "load"
        return f"Program({tag}, instructions={self.instruction_count})"


class Session:
    """A mutable execution session bound to a `Program`."""

    __slots__ = ("_program", "_calldata", "_output_slots", "_step_limit", "_memory_limit")

    def __init__(self, program: Program, output_slots: int, step_limit: int | None) -> None:
        self._program = program
        self._calldata: list = []
        self._output_slots = output_slots
        self._step_limit = step_limit
        self._memory_limit = _DEFAULT_MEMORY_LIMIT

    def set_calldata(self, data: list) -> None:
        for item in data:
            _validate_calldata_element(item)
        self._calldata = list(data)

    def set_output_slots(self, n: int) -> None:
        self._output_slots = n

    def set_step_limit(self, limit: int | None) -> None:
        """Cap the instructions a run may execute.

        The limit is exact: `0` permits none at all. `None` is unlimited.
        """
        self._step_limit = limit

    def set_memory_limit(self, nbytes: int) -> None:
        """Set the allocation budget in bytes for this session (default 1 GiB)."""
        self._memory_limit = nbytes

    def run(self) -> RunResult:
        vm = Vm()
        vm.set_calldata(self._calldata)
        vm.set_output_slots(self._output_slots)
        if self._step_limit is None:
            vm.set_unlimited_steps()
        else:
            vm.set_step_limit(self._step_limit)
        vm.set_memory_limit(self._memory_limit)
        vm.run(self._program.bytecode())

        raw_outputs = list(vm.outputs())
        outputs = {slot: val for slot, val in enumerate(raw_outputs)}
        return RunResult(outputs, list(vm.stack()), vm.steps())

    def __repr__(self) -> str:
        limit = "unlimited" if self._step_limit is None else str(self._step_limit)
        return f"Session(calldata_len={len(self._calldata)}, output_slots={self._output_slots}, step_limit={limit})"


class RunResult:
    """Result of a single `Session.run()`."""

    __slots__ = ("_outputs", "_stack", "_steps")

    def __init__(self, outputs: dict, stack: list[int], steps: int) -> None:
        self._outputs = outputs
        self._stack = stack
        self._steps = steps

    @property
    def outputs(self) -> dict:
        return self._outputs

    @property
    def stack(self) -> list[int]:
        return self._stack

    @property
    def steps(self) -> int:
        return self._steps

    def __repr__(self) -> str:
        return f"RunResult(outputs={len(self._outputs)} slots, stack_len={len(self._stack)}, steps={self._steps})"
