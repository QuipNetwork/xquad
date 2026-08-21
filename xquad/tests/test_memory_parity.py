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

"""Allocation-budget parity across the Rust and Python VM backends.

The budget is only useful if both interpreters agree on it: a program that
the chain's Rust VM rejects must also be rejected by the reference VM, at the
same instruction, having charged the same number of bytes. The charge
schedule is therefore defined over program-visible quantities and the rate
constants are duplicated verbatim between `xqvm/src/vm.rs` and
`xqvm_py/executor.py`; these tests pin the two together.
"""

from __future__ import annotations

import pytest

from xquad.vm import VM, VMBackend

BACKENDS = [VMBackend.RUST, VMBackend.PYTHON]

# -- programs whose allocation the budget has to see -------------------------

OVERSIZED_SAMPLE = """
PUSH 134217728
BSMX r0
HALT
"""

OVERSIZED_MODEL = """
PUSH 1073741824
BQMX r0
HALT
"""


def _equality_expansion(n: int) -> str:
    """EQUALITY over `n` indices: O(n^2) coefficients from O(n) steps."""
    lines = [f"PUSH {n}", "BQMX r0", "VECI r1", "VECI r2"]
    for i in range(n):
        lines += [f"PUSH {i}", "VECPUSH r1", "PUSH 1", "VECPUSH r2"]
    lines += ["PUSH 1", "PUSH 1", "EQUALITY r0 r1 r2", "HALT"]
    return "\n".join(lines)


HUGE_GRID_ONE_HOT = """
PUSH 4
BQMX r0
PUSH 1
PUSH 1048576
RESIZE r0
PUSH 0
PUSH 1
ONEHOTR r0
HALT
"""

REJECTED = [
    pytest.param(OVERSIZED_SAMPLE, 1 << 20, id="bsmx-1GiB-sample"),
    pytest.param(OVERSIZED_MODEL, 1 << 20, id="bqmx-declared-size"),
    pytest.param(_equality_expansion(200), 1 << 14, id="equality-expansion"),
    pytest.param(HUGE_GRID_ONE_HOT, 1 << 20, id="onehotr-huge-grid"),
]


def _run(backend: VMBackend, source: str, memory_limit: int) -> VM:
    vm = VM(backend=backend)
    vm.set_output_slots(16)
    vm.set_memory_limit(memory_limit)
    vm.run(source)
    return vm


@pytest.mark.parametrize("source,memory_limit", REJECTED)
def test_both_backends_reject_the_same_programs(source: str, memory_limit: int) -> None:
    for backend in BACKENDS:
        with pytest.raises(Exception) as excinfo:  # noqa: B017 - backends raise different types
            _run(backend, source, memory_limit)
        # The Python backend raises `xqvm_py.errors.MemoryLimitExceeded`; the
        # Rust backend surfaces its own variant through a `RuntimeError` whose
        # message carries the variant name.
        raised = f"{type(excinfo.value).__name__} {excinfo.value}".lower()
        assert "memory limit" in raised or "memorylimit" in raised, (
            f"{backend} raised {excinfo.value!r}, expected a memory-limit error"
        )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("PUSH 1000\nBSMX r0\nHALT", id="sample"),
        pytest.param("PUSH 1000\nBQMX r0\nHALT", id="model"),
        pytest.param(_equality_expansion(20), id="equality-expansion"),
    ],
)
def test_both_backends_charge_the_same_bytes(source: str) -> None:
    charged = {}
    for backend in BACKENDS:
        vm = _run(backend, source, 1 << 30)
        charged[backend] = vm.memory_used()
    assert charged[VMBackend.RUST] > 0, "the program should have been charged something"
    assert charged[VMBackend.RUST] == charged[VMBackend.PYTHON], (
        f"charge mismatch -- Rust={charged[VMBackend.RUST]}, Python={charged[VMBackend.PYTHON]}"
    )


@pytest.mark.parametrize("source,_memory_limit", REJECTED)
def test_a_generous_budget_admits_the_same_programs(source: str, _memory_limit: int) -> None:
    """The rejections above are the budget's doing, not a program defect.

    The one-hot grid case is excluded: its expansion is genuinely enormous, so
    "it would pass with a bigger budget" cannot be demonstrated in test time.
    """
    if "ONEHOTR" in source or "1073741824" in source or "134217728" in source:
        pytest.skip("allocation is too large to run even with an unlimited budget")
    for backend in BACKENDS:
        _run(backend, source, 1 << 30)
