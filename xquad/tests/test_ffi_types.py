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

"""`xqffi.vm` model types, energy, `triu` and limits (QUI-1479).

Where the VM computes the same quantity through an opcode (`ENERGY`,
`IDXTRIU`), the host-side result is compared against a run of that opcode,
so the two cannot drift.
"""

from __future__ import annotations

import pytest

import xqffi.vm as ffi
from xqffi.asm import assemble_source
from xqffi.vm import Domain, XqmxModel, XqmxSample

I64_MAX = 2**63 - 1

# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------


def test_domain_equality_and_hash_are_by_value() -> None:
    assert Domain.integer(3) == Domain.integer(3)
    assert Domain.integer(3) != Domain.integer(4)
    assert Domain.BINARY != Domain.SPIN
    assert {Domain.BINARY: "b", Domain.integer(3): "i"}[Domain.integer(3)] == "i"


@pytest.mark.parametrize("k", [1, 0, -5])
def test_integer_domain_rejects_k_below_two(k: int) -> None:
    with pytest.raises(ValueError, match="k >= 2"):
        Domain.integer(k)


@pytest.mark.parametrize(
    ("domain", "name", "k", "description", "text"),
    [
        (Domain.BINARY, "binary", None, "binary {0, 1}", "Domain.BINARY"),
        (Domain.SPIN, "spin", None, "spin {-1, +1}", "Domain.SPIN"),
        (Domain.integer(3), "integer", 3, "integer {0, ..., 2}", "Domain.integer(3)"),
    ],
)
def test_domain_accessors(domain: Domain, name: str, k: int | None, description: str, text: str) -> None:
    assert domain.name == name
    assert domain.k == k
    assert domain.description == description
    assert repr(domain) == text


@pytest.mark.parametrize(
    ("domain", "inside", "outside"),
    [
        (Domain.BINARY, [0, 1], [-1, 2]),
        (Domain.SPIN, [-1, 1], [0, 2, -2]),
        (Domain.integer(3), [0, 1, 2], [-1, 3]),
    ],
)
def test_domain_contains(domain: Domain, inside: list[int], outside: list[int]) -> None:
    assert all(domain.contains(v) for v in inside)
    assert not any(domain.contains(v) for v in outside)


def test_domain_is_immutable() -> None:
    with pytest.raises(AttributeError):
        Domain.BINARY.k = 3


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "domain"),
    [
        (XqmxModel.binary(4, rows=2, cols=2), Domain.BINARY),
        (XqmxModel.spin(4, rows=2, cols=2), Domain.SPIN),
        (XqmxModel.integer(4, 5, rows=2, cols=2), Domain.integer(5)),
        (XqmxModel(Domain.integer(5), 4, rows=2, cols=2), Domain.integer(5)),
    ],
)
def test_model_constructors(model: XqmxModel, domain: Domain) -> None:
    assert model.domain == domain
    assert model.k == domain.k
    assert (model.size, model.rows, model.cols) == (4, 2, 2)
    assert model.linear_items() == []
    assert model.quadratic_items() == []


@pytest.mark.parametrize(
    ("sample", "domain", "values"),
    [
        (XqmxSample.binary([0, 1]), Domain.BINARY, [0, 1]),
        (XqmxSample.spin([-1, 1]), Domain.SPIN, [-1, 1]),
        (XqmxSample.integer([0, 2], 3), Domain.integer(3), [0, 2]),
        (XqmxSample(Domain.integer(3), [2, 0]), Domain.integer(3), [2, 0]),
    ],
)
def test_sample_constructors(sample: XqmxSample, domain: Domain, values: list[int]) -> None:
    assert sample.domain == domain
    assert sample.k == domain.k
    assert sample.values == values
    assert sample.size == len(sample) == len(values)


def test_integer_constructors_reject_k_below_two() -> None:
    with pytest.raises(ValueError, match="k >= 2"):
        XqmxModel.integer(4, 1)
    with pytest.raises(ValueError, match="k >= 2"):
        XqmxSample.integer([0], 1)


@pytest.mark.parametrize(
    "build",
    [
        lambda: XqmxSample.binary([2]),
        lambda: XqmxSample.spin([0]),
        lambda: XqmxSample.integer([3], 3),
    ],
    ids=["binary", "spin", "integer"],
)
def test_sample_class_constructors_check_the_domain(build) -> None:
    with pytest.raises(ValueError, match="outside the"):
        build()


def test_a_domain_string_is_no_longer_accepted() -> None:
    with pytest.raises(TypeError):
        XqmxModel("binary", 4)
    with pytest.raises(TypeError):
        XqmxSample("binary", [0, 1])


# ---------------------------------------------------------------------------
# Coefficient accumulation
# ---------------------------------------------------------------------------


def test_add_linear_and_add_quad_accumulate() -> None:
    model = XqmxModel.binary(3)
    model.add_linear(0, 2)
    model.add_linear(0, 3)
    model.add_quad(1, 0, 4)
    model.add_quad(0, 1, -1)
    assert model.get_linear(0) == 5
    assert model.get_quad(0, 1) == 3
    assert model.quadratic_items() == [((0, 1), 3)]


def test_adding_back_to_zero_removes_the_term() -> None:
    model = XqmxModel.binary(2)
    model.add_linear(1, 7)
    model.add_linear(1, -7)
    model.add_quad(0, 1, 7)
    model.add_quad(0, 1, -7)
    assert model.linear_items() == []
    assert model.quadratic_items() == []


def test_overflowing_add_raises_and_leaves_the_model_unchanged() -> None:
    model = XqmxModel.binary(2)
    model.set_linear(0, I64_MAX)
    model.set_quad(0, 1, I64_MAX)
    with pytest.raises(ffi.ArithmeticOverflow):
        model.add_linear(0, 1)
    with pytest.raises(ffi.ArithmeticOverflow):
        model.add_quad(1, 0, 1)
    assert model.get_linear(0) == I64_MAX
    assert model.get_quad(0, 1) == I64_MAX


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------


def _vm_energy(model: XqmxModel, sample: XqmxSample) -> int:
    vm = ffi.Vm()
    vm.set_calldata([model, sample])
    vm.set_output_slots(1)
    vm.run(assemble_source("PUSH 0\nINPUT r0\nPUSH 1\nINPUT r1\nENERGY r0 r1\nSTOW r2\nPUSH 0\nOUTPUT r2\nHALT"))
    return vm.outputs()[0]


@pytest.mark.parametrize(
    ("model", "sample"),
    [
        (XqmxModel.binary(3), XqmxSample.binary([1, 1, 0])),
        (XqmxModel.spin(3), XqmxSample.spin([-1, 1, -1])),
        (XqmxModel.integer(3, 4), XqmxSample.integer([3, 0, 2], 4)),
    ],
    ids=["binary", "spin", "integer"],
)
def test_energy_matches_the_energy_opcode(model: XqmxModel, sample: XqmxSample) -> None:
    for i, coeff in enumerate([-3, 5, 7]):
        model.set_linear(i, coeff)
    model.set_quad(0, 1, 2)
    model.set_quad(1, 2, -4)
    model.set_quad(0, 2, 6)
    assert model.energy(sample) == _vm_energy(model, sample)


def test_energy_of_an_empty_model_is_zero() -> None:
    assert XqmxModel.binary(2).energy(XqmxSample.binary([1, 1])) == 0


def test_energy_rejects_a_sample_of_another_size() -> None:
    with pytest.raises(ffi.SizeMismatch):
        XqmxModel.binary(2).energy(XqmxSample.binary([1]))


def test_energy_raises_on_overflow() -> None:
    model = XqmxModel.binary(2)
    model.set_linear(0, I64_MAX)
    model.set_linear(1, I64_MAX)
    with pytest.raises(ffi.ArithmeticOverflow):
        model.energy(XqmxSample.binary([1, 1]))


def test_energy_rejects_a_model_argument() -> None:
    with pytest.raises(TypeError):
        XqmxModel.binary(1).energy(XqmxModel.binary(1))


# ---------------------------------------------------------------------------
# triu
# ---------------------------------------------------------------------------


def _vm_triu(i: int, j: int) -> int:
    vm = ffi.Vm()
    vm.run(assemble_source(f"PUSH {i}\nPUSH {j}\nIDXTRIU\nHALT"))
    return vm.stack()[0]


@pytest.mark.parametrize(("i", "j"), [(0, 1), (1, 3), (3, 1), (2, 2), (0, 0), (-3, 4), (5, -2)])
def test_triu_matches_the_idxtriu_opcode(i: int, j: int) -> None:
    assert ffi.triu(i, j) == _vm_triu(i, j)
    assert ffi.triu(i, j) == ffi.triu(j, i)


def test_triu_raises_on_overflow() -> None:
    with pytest.raises(ffi.ArithmeticOverflow):
        ffi.triu(0, I64_MAX)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_limits_come_from_the_rust_crate() -> None:
    assert ffi.DEFAULT_STEP_LIMIT == 10_000_000
    assert ffi.DEFAULT_MEMORY_LIMIT == 1 << 30
    assert ffi.MAX_ALLOCATION_SIZE == 2**32 - 1


def test_one_past_max_allocation_size_is_not_an_allocation() -> None:
    # The charge precedes the range check, so the budget has to cover it for
    # the size itself to be what is refused.
    vm = ffi.Vm()
    vm.set_memory_limit(1 << 40)
    with pytest.raises(ffi.InvalidAllocation):
        vm.run(assemble_source(f"PUSH {ffi.MAX_ALLOCATION_SIZE + 1}\nBQMX r0\nHALT"))
