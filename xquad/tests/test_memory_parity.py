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

"""The allocation budget, seen through `xquad.vm`.

Every allocating opcode is charged before it allocates, and a program that
cannot pay raises `MemoryLimitExceeded`. These tests pin that the umbrella
VM forwards the budget and reports the charge; the exact byte counts are
pinned by the specification vectors.
"""

from __future__ import annotations

import pytest

from xqffi.vm import MemoryLimitExceeded
from xquad.vm import VM

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


# The grid is legitimately oversized rather than degenerate: the model
# declares 4096 variables and the grid describes exactly those 4096 cells, so
# RESIZE accepts it and the budget is what stops the O(cols^2) expansion.
# A size-4 model resized to `1 x 2^20` is now rejected at RESIZE instead.
HUGE_GRID_ONE_HOT = """
PUSH 4096
BQMX r0
PUSH 1
PUSH 4096
RESIZE r0
PUSH 0
PUSH 1
ONEHOTR r0
HALT
"""

# OUTPUT copies a whole register into a slot the host keeps after the run, so
# the copy is live memory that a slot bound alone does not bound. Uncharged,
# one allocation that spends the budget exactly plus one OUTPUT per slot hands
# the host an unbounded multiple of the budget.
OUTPUT_AMPLIFICATION = """
PUSH 32768
BSMX r0
PUSH 0
OUTPUT r0
PUSH 1
OUTPUT r0
PUSH 2
OUTPUT r0
HALT
"""

REJECTED = [
    pytest.param(OVERSIZED_SAMPLE, 1 << 20, id="bsmx-1GiB-sample"),
    pytest.param(OVERSIZED_MODEL, 1 << 20, id="bqmx-declared-size"),
    pytest.param(_equality_expansion(200), 1 << 14, id="equality-expansion"),
    pytest.param(HUGE_GRID_ONE_HOT, 1 << 20, id="onehotr-huge-grid"),
    pytest.param(OUTPUT_AMPLIFICATION, 1 << 19, id="output-clone-amplification"),
]


def _run(source: str, memory_limit: int) -> VM:
    vm = VM()
    vm.set_output_slots(16)
    vm.set_memory_limit(memory_limit)
    vm.run(source)
    return vm


@pytest.mark.parametrize("source,memory_limit", REJECTED)
def test_the_budget_rejects_oversized_programs(source: str, memory_limit: int) -> None:
    with pytest.raises(MemoryLimitExceeded):
        _run(source, memory_limit)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("PUSH 1000\nBSMX r0\nHALT", id="sample"),
        pytest.param("PUSH 1000\nBQMX r0\nHALT", id="model"),
        pytest.param(_equality_expansion(20), id="equality-expansion"),
        pytest.param(
            "PUSH 1000\nBSMX r0\nPUSH 0\nOUTPUT r0\nPUSH 1\nOUTPUT r0\nHALT",
            id="output-sample-clone",
        ),
        pytest.param(
            "PUSH 1000\nBQMX r0\nPUSH 10\nPUSH 1\nSETLINE r0\nPUSH 0\nOUTPUT r0\nHALT",
            id="output-model-clone",
        ),
        pytest.param("PUSH 7\nSTOW r0\nPUSH 0\nOUTPUT r0\nHALT", id="output-int-clone"),
        pytest.param(
            "VECI r0\nPUSH 1\nVECPUSH r0\nPUSH 2\nVECPUSH r0\nPUSH 0\nOUTPUT r0\nHALT",
            id="output-vec-clone",
        ),
        # ITER copies its slice into the loop frame, and the copy is priced
        # over the element type. The vec<xqmx> half of that rule is not
        # reachable from bytecode -- VECX makes only an empty vec and
        # VECPUSH pops from the integer stack -- and xqffi's calldata
        # accepts a single model but not a list of them, so the vec<int>
        # half is what a host-level test can pin. The model half is
        # pinned crate-locally by xqvm's
        # iterating_a_model_vec_costs_what_outputting_the_same_model_costs.
        pytest.param(
            "VECI r0\nPUSH 1\nVECPUSH r0\nPUSH 2\nVECPUSH r0\nPUSH 0\nPUSH 2\nITER r0\nNEXT\nHALT",
            id="iter-vec-int-slice-copy",
        ),
    ],
)
def test_an_allocating_program_is_charged(source: str) -> None:
    assert _run(source, 1 << 30).memory_used() > 0, "the program should have been charged something"


# RANGE is the one loop header that allocates nothing: the VM keeps the
# iteration bounds and nothing else, so a count no host could materialise is
# admitted under a budget of a single kilobyte. The loop is left immediately
# rather than run, because what is under test is the header's allocation and
# not the iteration. A VM that built the iteration space eagerly would
# either blow the budget or -- worse -- exhaust host memory while reporting
# zero bytes charged.
LARGE_RANGE = """
PUSH 0
PUSH 100000000
RANGE
HALT
"""


def test_a_range_header_is_charged_nothing() -> None:
    assert _run(LARGE_RANGE, 1 << 10).memory_used() == 0, "RANGE must not allocate the iteration space"


@pytest.mark.parametrize("source,_memory_limit", REJECTED)
def test_a_generous_budget_admits_the_same_programs(source: str, _memory_limit: int) -> None:
    """The rejections above are the budget's doing, not a program defect.

    The one-hot grid case is excluded: its expansion is genuinely enormous, so
    "it would pass with a bigger budget" cannot be demonstrated in test time.
    """
    if "ONEHOTR" in source or "1073741824" in source or "134217728" in source:
        pytest.skip("allocation is too large to run even with an unlimited budget")
    _run(source, 1 << 30)
