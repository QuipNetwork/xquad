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
constructors and coefficient accessors reject what no bytecode could have
produced: a grid `RESIZE` would refuse, a size past the allocators' ceiling,
and a coefficient index at or past `size`. No conformance vector can reach
this boundary, so this file is its only coverage.
"""

from __future__ import annotations

import pytest

from xqffi.asm import assemble_source
from xqffi.vm import MAX_ALLOCATION_SIZE, Vm, XqmxModel, XqmxSample
from xquad.vm import VM, VMBackend
from xqvm_py.xqmx import XQMX, XQMXDomain, XQMXMode

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


@pytest.mark.parametrize(("rows", "cols"), UNFIT_GRIDS)
def test_model_rejects_a_grid_resize_would_refuse(rows, cols):
    with pytest.raises(ValueError, match="grid does not fit 4 variables"):
        XqmxModel("binary", size=4, rows=rows, cols=cols)


@pytest.mark.parametrize(("rows", "cols"), UNFIT_GRIDS)
def test_sample_rejects_a_grid_resize_would_refuse(rows, cols):
    with pytest.raises(ValueError, match="grid does not fit 4 variables"):
        XqmxSample("binary", [0, 1, 0, 1], rows=rows, cols=cols)


@pytest.mark.parametrize(("rows", "cols"), FIT_GRIDS)
def test_model_and_sample_accept_a_fitting_grid(rows, cols):
    model = XqmxModel("binary", size=4, rows=rows, cols=cols)
    sample = XqmxSample("binary", [0, 1, 0, 1], rows=rows, cols=cols)
    assert (model.rows, model.cols) == (rows, cols)
    assert (sample.rows, sample.cols) == (rows, cols)


def test_model_rejects_a_size_past_the_allocation_ceiling():
    with pytest.raises(ValueError, match="exceeds the largest allocation"):
        XqmxModel("binary", size=MAX_ALLOCATION_SIZE + 1)


def test_model_accepts_the_allocation_ceiling():
    # Sparse, so the ceiling itself costs nothing to construct.
    assert XqmxModel("binary", size=MAX_ALLOCATION_SIZE).size == MAX_ALLOCATION_SIZE


@pytest.mark.parametrize(
    "access",
    [
        lambda m: m.set_linear(4, 1),
        lambda m: m.get_linear(4),
        lambda m: m.set_quad(0, 4, 1),
        lambda m: m.set_quad(4, 0, 1),
        lambda m: m.get_quad(0, 4),
        lambda m: m.set_linear(-1, 1),
        lambda m: m.get_quad(-1, 0),
    ],
    ids=[
        "set_linear",
        "get_linear",
        "set_quad_j",
        "set_quad_i",
        "get_quad",
        "set_linear_negative",
        "get_quad_negative",
    ],
)
def test_coefficient_index_outside_the_model_raises(access):
    model = XqmxModel("binary", size=4)
    with pytest.raises(IndexError, match="out of range for a model of size 4"):
        access(model)
    assert model.linear_items() == []
    assert model.quadratic_items() == []


def test_coefficient_index_below_size_is_accepted():
    model = XqmxModel("binary", size=4)
    model.set_linear(3, 5)
    model.set_quad(3, 0, 7)
    assert model.get_linear(3) == 5
    assert model.get_quad(0, 3) == 7


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


# `xquad.vm` converts an `XQMX` through the constructors and setters above on
# the RUST backend only, so the PYTHON backend applies the same rules itself.
# Both must refuse the same calldata, with the same error, at `set_calldata`.
BAD_CALLDATA = [
    (
        "grid",
        lambda: XQMX.binary_model(4, 2, 3),
        ValueError,
        "grid does not fit 4 variables",
    ),
    (
        "linear",
        lambda: XQMX(XQMXMode.MODEL, XQMXDomain.BINARY, 4, linear={7: 5}),
        IndexError,
        "variable index 7",
    ),
    (
        "quad",
        lambda: XQMX(XQMXMode.MODEL, XQMXDomain.BINARY, 4, quadratic={(0, 4): 1}),
        IndexError,
        "variable index 4",
    ),
    (
        "sample",
        lambda: XQMX(XQMXMode.SAMPLE, XQMXDomain.BINARY, 4, linear={4: 1}),
        IndexError,
        "variable index 4",
    ),
]


@pytest.mark.parametrize("backend", list(VMBackend))
@pytest.mark.parametrize(
    ("build", "exc", "match"),
    [case[1:] for case in BAD_CALLDATA],
    ids=[case[0] for case in BAD_CALLDATA],
)
def test_both_backends_refuse_calldata_bytecode_could_not_build(backend, build, exc, match):
    with pytest.raises(exc, match=match):
        VM(backend=backend).set_calldata([build()])


@pytest.mark.parametrize("backend", list(VMBackend))
def test_a_grid_widened_after_set_calldata_is_refused_at_run(backend):
    model = XQMX.binary_model(4, 2, 2)
    vm = VM(backend=backend)
    vm.set_calldata([model])
    model.cols = 3
    with pytest.raises(ValueError, match="grid does not fit"):
        vm.run("PUSH 0\nINPUT r0\nHALT")
