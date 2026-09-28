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

"""`xquad.vm` -- the XQVM interpreter behind a source-level interface.

`VM` wraps the Rust VM (`xqffi.vm.Vm`). It keeps calldata, output slots and
limits across runs, assembles `.xqasm` source on `run`, and hands back the
`xqffi` types unchanged: calldata and outputs are `int`, `list[int]`,
`XqmxModel`, `XqmxSample` or `None`. A fault raises the `XqvmError` subclass
named for it, carrying the failing `.offset`.
"""

from __future__ import annotations

from xqffi.asm import assemble_source as _assemble_source
from xqffi.vm import DEFAULT_MEMORY_LIMIT as _DEFAULT_MEMORY_LIMIT
from xqffi.vm import DEFAULT_STEP_LIMIT as _DEFAULT_STEP_LIMIT
from xqffi.vm import Vm as _RustVm

__all__ = ["DEFAULT_STEP_LIMIT", "VM"]

#: Default step budget, read from the Rust VM rather than restated here so
#: the two cannot drift. Same number as the `xquad run --step-limit`
#: default, which now reads it from the same place.
#:
#: Unbounded execution has to be asked for rather than stumbled into, so
#: `None` is reserved for a caller who writes it. Defaulting to `None` made
#: `Program.from_source("TARGET .0\nNOP\nJUMP .0").session().run()` -- which
#: raised in about a tenth of a second on 0.3.x -- never return.
DEFAULT_STEP_LIMIT: int = _DEFAULT_STEP_LIMIT


class VM:
    """The XQVM interpreter.

    Configure with `set_calldata`, `set_output_slots` and the limit setters,
    then `run` source or `run_bytecode`. Settings persist across runs until
    `reset`; every run starts from a fresh machine state.
    """

    def __init__(self) -> None:
        self._vm = _RustVm()
        self._calldata: list = []
        self._output_slots: int = 0
        self._step_limit: int | None = DEFAULT_STEP_LIMIT
        self._memory_limit: int = _DEFAULT_MEMORY_LIMIT

    def set_calldata(self, data: list) -> None:
        """Install the calldata slots for the next run.

        An `XqmxSample` needs no check here: its constructor and its setter
        both reject an out-of-domain value, so none can exist.
        """
        self._calldata = list(data)

    def set_output_slots(self, n: int) -> None:
        self._output_slots = n

    def set_step_limit(self, limit: int | None) -> None:
        """Cap the metered steps a run may take.

        The limit is exact: `0` permits no instructions at all. `None` is
        unlimited and has to be written by the caller; the default is
        `DEFAULT_STEP_LIMIT`.
        """
        self._step_limit = limit

    def set_memory_limit(self, nbytes: int) -> None:
        """Set the allocation budget in bytes (default 1 GiB)."""
        self._memory_limit = nbytes

    # -- execution -----------------------------------------------------------

    def run(self, source: str) -> None:
        """Assemble and execute `.xqasm` source."""
        self.run_bytecode(_assemble_source(source))

    def run_bytecode(self, bytecode: bytes) -> None:
        """Execute pre-assembled bytecode."""
        self._vm.reset()
        self._vm.set_calldata(self._calldata)
        self._vm.set_output_slots(self._output_slots)
        if self._step_limit is None:
            self._vm.set_unlimited_steps()
        else:
            self._vm.set_step_limit(self._step_limit)
        self._vm.set_memory_limit(self._memory_limit)
        self._vm.run(bytecode)

    # -- results -------------------------------------------------------------

    def outputs(self) -> list:
        return list(self._vm.outputs())

    def stack(self) -> list[int]:
        return list(self._vm.stack())

    def steps(self) -> int:
        return self._vm.steps()

    def memory_used(self) -> int:
        """Bytes charged against the allocation budget by the last run."""
        return self._vm.memory_used()

    def reset(self) -> None:
        self._calldata = []
        self._output_slots = 0
        self._step_limit = DEFAULT_STEP_LIMIT
        self._memory_limit = _DEFAULT_MEMORY_LIMIT
        self._vm.reset()
