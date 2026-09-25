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

"""Step-count parity test across all conformance vectors.

Runs every conformance vector on both the Rust and Python VM backends
and asserts they produce identical step counts.  This ensures the
Python VM's step-counting logic matches the Rust implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xquad.vm import VM, VMBackend

VECTORS_DIR = Path(__file__).resolve().parents[2] / "xqvm" / "tests" / "vectors"


def _discover_vectors() -> list[tuple[str, Path]]:
    vectors = []
    for category_dir in sorted(VECTORS_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        for vector_dir in sorted(category_dir.iterdir()):
            if not vector_dir.is_dir():
                continue
            if (vector_dir / "program.xqasm").exists():
                label = f"{category_dir.name}/{vector_dir.name}"
                vectors.append((label, vector_dir))
    return vectors


VECTORS = _discover_vectors()


@pytest.mark.parametrize("label,vector_dir", VECTORS, ids=[v[0] for v in VECTORS])
def test_step_count_parity(label: str, vector_dir: Path) -> None:
    with (vector_dir / "expected.json").open(encoding="utf-8") as f:
        expected = json.load(f)

    if "error" in expected:
        # A vector that asserts a fault has no completed run to compare step
        # counts for, and an unbounded one (step_limit_exceeded) would never
        # return here, since this test deliberately runs without a budget.
        # Nothing compares the Python VM's fault identity against the vectors
        # any more: `cargo test -p xqvm --test vectors` checks the Rust VM
        # alone, and this module goes with xqvm_py in QUI-1481.
        pytest.skip(f"{label} asserts a fault ({expected['error']}), not a step count")

    source = (vector_dir / "program.xqasm").read_text(encoding="utf-8")

    with (vector_dir / "inputs.json").open(encoding="utf-8") as f:
        inputs = json.load(f)

    calldata = inputs.get("calldata", [])
    output_slots = inputs.get("output_slots", 16)
    # The allocation budget is a property of the vector, the way calldata
    # and the slot count are, so it is resolved here rather than left to
    # whichever default each backend carries. Leaving it out ran a vector
    # whose `inputs.json` asks for a larger budget at the 1 GiB default
    # instead, so the run faulted with MemoryLimitExceeded and the step
    # comparison never happened -- the vector read as broken when it was
    # this walk that was not reading its inputs. The step budget stays
    # unset on purpose: see the fault-vector skip above.
    memory_limit = inputs.get("memory_limit")

    def _vm(backend: VMBackend) -> VM:
        vm = VM(backend=backend)
        vm.set_calldata(calldata)
        vm.set_output_slots(output_slots)
        if memory_limit is not None:
            vm.set_memory_limit(memory_limit)
        vm.run(source)
        return vm

    rust_steps = _vm(VMBackend.RUST).steps()
    py_steps = _vm(VMBackend.PYTHON).steps()

    assert rust_steps > 0, f"{label}: Rust VM reported 0 steps"
    assert py_steps > 0, f"{label}: Python VM reported 0 steps"
    assert rust_steps == py_steps, f"{label}: step mismatch — Rust={rust_steps}, Python={py_steps}"
