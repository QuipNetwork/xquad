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

"""Model and sample extents at the host boundary (QUI-1164).

`Vm::set_calldata` is the trusted-embedder path and stays infallible, so a
model or sample Python builds reaches the VM unchecked. The `xqffi`
constructors reject what no bytecode could have produced: a grid `RESIZE`
would refuse and a size past the allocators' ceiling, with the fault the VM
raises for each. Coefficient indices at or past `size` are covered in
`test_ffi_types.py`. No specification vector can reach this boundary, so
these files are its only coverage.
"""

from __future__ import annotations

import pytest

import xqffi.vm as ffi
from xqffi.asm import assemble_source
from xqffi.vm import MAX_ALLOCATION_SIZE, Domain, Vm, XqmxModel, XqmxSample

UNFIT_GRIDS = [
    # QUI-1164's reproducer: a 2^62 x 8 grid on a four-variable model.
    (2**62, 8),
    # More cells than variables.
    (2, 3),
    (1, 5),
    # One extent zero: RESIZE requires both positive.
    (0, 4),
    (4, 0),
]

FIT_GRIDS = [
    (0, 0),  # ungridded
    (2, 2),  # exact
    (1, 3),  # slack variables past the grid
]

CONSTRUCTORS = {
    "model": lambda rows, cols: XqmxModel(Domain.BINARY, 4, rows=rows, cols=cols),
    "model.binary": lambda rows, cols: XqmxModel.binary(4, rows=rows, cols=cols),
    "model.integer": lambda rows, cols: XqmxModel.integer(4, 3, rows=rows, cols=cols),
    "sample": lambda rows, cols: XqmxSample(Domain.BINARY, [0, 1, 0, 1], rows=rows, cols=cols),
    "sample.spin": lambda rows, cols: XqmxSample.spin([-1, 1, -1, 1], rows=rows, cols=cols),
    "sample.default": lambda rows, cols: XqmxSample.default(Domain.BINARY, 4, rows=rows, cols=cols),
}


@pytest.mark.parametrize("build", CONSTRUCTORS.values(), ids=CONSTRUCTORS.keys())
@pytest.mark.parametrize(("rows", "cols"), UNFIT_GRIDS)
def test_a_grid_resize_would_refuse_is_rejected(build, rows, cols):
    with pytest.raises(ffi.InvalidGridDimensions, match="grid does not fit 4 variables") as excinfo:
        build(rows, cols)
    assert excinfo.value.offset is None


@pytest.mark.parametrize("build", CONSTRUCTORS.values(), ids=CONSTRUCTORS.keys())
@pytest.mark.parametrize(("rows", "cols"), FIT_GRIDS)
def test_a_fitting_grid_is_accepted(build, rows, cols):
    built = build(rows, cols)
    assert (built.rows, built.cols) == (rows, cols)


def test_model_rejects_a_size_past_the_allocation_ceiling():
    size = MAX_ALLOCATION_SIZE + 1
    with pytest.raises(ffi.InvalidAllocation) as excinfo:
        XqmxModel.binary(size)
    assert str(excinfo.value) == f"invalid allocation size {size}"
    assert excinfo.value.offset is None


def test_model_accepts_the_allocation_ceiling():
    # Sparse, so the ceiling itself costs nothing to construct.
    assert XqmxModel.binary(MAX_ALLOCATION_SIZE).size == MAX_ALLOCATION_SIZE


def test_a_bytecode_built_model_round_trips_through_the_boundary():
    # Whatever the VM hands back must be installable again: the boundary
    # rejects only states bytecode cannot produce.
    build = Vm()
    build.set_output_slots(1)
    build.run(assemble_source("PUSH 4\nBQMX r0\nPUSH 2\nPUSH 2\nRESIZE r0\nPUSH 0\nOUTPUT r0\nHALT"))
    (model,) = build.outputs()
    rebuilt = XqmxModel(model.domain, model.size, rows=model.rows, cols=model.cols)
    for i, coeff in model.linear_items():
        rebuilt.set_linear(i, coeff)

    read = Vm()
    read.set_calldata([rebuilt])
    read.run(assemble_source("PUSH 0\nINPUT r0\nPUSH 1\nROWSUM r0\nHALT"))
    assert read.stack() == [0]
