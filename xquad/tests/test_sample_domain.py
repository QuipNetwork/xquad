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

"""Sample value domain at the host boundary (QUI-1168).

The VM checks `SETLINE` and `ADDLINE`, which a host-supplied sample never
passes through: `Vm::set_calldata` is infallible by design and stays the
trusted-embedder path. `XqmxSample` closes that gap instead: its
constructor and its one setter both raise `ValueError` for an
out-of-domain value, so no out-of-domain sample can exist to reach the VM.
Neither is reachable by a conformance vector, so this file is their only
coverage.
"""

from __future__ import annotations

import pytest

from xqffi.vm import Domain, SampleOutOfDomain, XqmxModel, XqmxSample
from xquad.vm import VM

I64_MAX = 2**63 - 1
I64_MIN = -(2**63)

_READ_SLOT_0 = "PUSH 0\nINPUT r0\nPUSH 0\nGETLINE r0\nSTOW r1\nPUSH 0\nOUTPUT r1\nHALT"


@pytest.mark.parametrize(
    ("domain", "values"),
    [
        (Domain.BINARY, [0, 2]),
        (Domain.BINARY, [-1]),
        (Domain.SPIN, [0]),
        (Domain.SPIN, [2]),
        (Domain.integer(3), [-1]),
        (Domain.integer(3), [3]),
    ],
)
def test_constructor_rejects_out_of_domain_values(domain, values):
    with pytest.raises(ValueError, match="outside the"):
        XqmxSample(domain, values)


@pytest.mark.parametrize(
    ("domain", "values"),
    [
        (Domain.BINARY, [0, 1, 1, 0]),
        (Domain.SPIN, [-1, 1, -1]),
        (Domain.integer(3), [0, 1, 2]),
    ],
)
def test_constructor_accepts_in_domain_values(domain, values):
    sample = XqmxSample(domain, values)
    assert sample.values == values


def test_model_coefficients_stay_unbounded():
    # A bias is not an assignment. Only the sample checks its values.
    model = XqmxModel.binary(2)
    model.set_linear(0, I64_MIN)
    model.set_linear(1, I64_MAX)
    assert model.get_linear(0) == I64_MIN
    assert model.get_linear(1) == I64_MAX


def test_the_setter_is_the_only_write_and_it_checks_the_domain():
    # This is what makes xquad/program.py and xquad.vm safe without a
    # second scan: every way to put a value in a sample checks it.
    sample = XqmxSample.binary([0, 1])
    for attr in ("set_value", "__setitem__"):
        assert not hasattr(sample, attr), attr
    with pytest.raises((AttributeError, TypeError)):
        sample.values = [5, 5]
    with pytest.raises(ValueError, match="outside the"):
        sample.set_linear(0, 5)
    assert sample.values == [0, 1]


def test_an_in_domain_sample_runs():
    sample = XqmxSample.default(Domain.BINARY, 2)
    sample.set_linear(0, 1)

    machine = VM()
    machine.set_calldata([sample])
    machine.set_output_slots(1)
    machine.run(_READ_SLOT_0)
    assert machine.outputs()[0] == 1


def test_a_sample_written_after_set_calldata_runs_as_written():
    # `set_calldata` copies the list, not the samples in it, so a later
    # write reaches the run. It can only be an in-domain write.
    sample = XqmxSample.default(Domain.BINARY, 1)

    machine = VM()
    machine.set_calldata([sample])
    machine.set_output_slots(1)
    sample.set_linear(0, 1)
    machine.run(_READ_SLOT_0)
    assert machine.outputs()[0] == 1


def test_setline_out_of_domain_faults_in_the_vm():
    # The ticket's original reproducer, through the public API.
    machine = VM()
    with pytest.raises(SampleOutOfDomain):
        machine.run("PUSH 4\nPUSH 3\nXSMX r0\nPUSH 0\nPUSH 7\nSETLINE r0\nHALT")


def test_default_spin_sample_reads_back_as_minus_one():
    machine = VM()
    machine.set_calldata([XqmxSample.default(Domain.SPIN, 4)])
    machine.set_output_slots(1)
    machine.run(_READ_SLOT_0)
    assert machine.outputs()[0] == -1


def test_default_spin_sample_energy_uses_the_domain_default():
    # Biases 3 and 5 over two variables both at -1 give -8.
    model = XqmxModel.spin(2)
    model.set_linear(0, 3)
    model.set_linear(1, 5)
    sample = XqmxSample.default(Domain.SPIN, 2)

    machine = VM()
    machine.set_calldata([model, sample])
    machine.set_output_slots(1)
    machine.run("PUSH 0\nINPUT r0\nPUSH 1\nINPUT r1\nENERGY r0 r1\nSTOW r2\nPUSH 0\nOUTPUT r2\nHALT")
    assert machine.outputs()[0] == model.energy(sample) == -8


def test_default_spin_sample_addline_accumulates_from_minus_one():
    # From -1, a delta of +2 lands on +1; reading it as 0 would land on +2
    # and fault.
    machine = VM()
    machine.set_calldata([XqmxSample.default(Domain.SPIN, 1)])
    machine.set_output_slots(1)
    machine.run("PUSH 0\nINPUT r0\nPUSH 0\nPUSH 2\nADDLINE r0\nPUSH 0\nGETLINE r0\nSTOW r1\nPUSH 0\nOUTPUT r1\nHALT")
    assert machine.outputs()[0] == 1
