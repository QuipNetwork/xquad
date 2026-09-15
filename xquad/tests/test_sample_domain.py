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
trusted-embedder path. These tests cover the two places that close that
gap instead -- the `XqmxSample` constructor, and `VM.set_calldata` -- and
neither is reachable by a conformance vector, so this file is their only
coverage.
"""

from __future__ import annotations

import pytest

from xqffi.vm import XqmxModel, XqmxSample
from xquad.vm import VM, VMBackend
from xqvm_py.errors import SampleOutOfDomain
from xqvm_py.limits import I64_MAX, I64_MIN
from xqvm_py.xqmx import XQMX, XQMXDomain, XQMXMode

BACKENDS = [VMBackend.RUST, VMBackend.PYTHON]


@pytest.mark.parametrize(
    ("domain", "values", "k"),
    [
        ("binary", [0, 2], None),
        ("binary", [-1], None),
        ("spin", [0], None),
        ("spin", [2], None),
        ("integer", [-1], 3),
        ("integer", [3], 3),
    ],
)
def test_constructor_rejects_out_of_domain_values(domain, values, k):
    kwargs = {"k": k} if k is not None else {}
    with pytest.raises(ValueError, match="outside the"):
        XqmxSample(domain, values, **kwargs)


@pytest.mark.parametrize(
    ("domain", "values", "k"),
    [
        ("binary", [0, 1, 1, 0], None),
        ("spin", [-1, 1, -1], None),
        ("integer", [0, 1, 2], 3),
    ],
)
def test_constructor_accepts_in_domain_values(domain, values, k):
    kwargs = {"k": k} if k is not None else {}
    sample = XqmxSample(domain, values, **kwargs)
    assert sample.values == values


def test_model_coefficients_stay_unbounded():
    # A bias is not an assignment. Only the sample constructor gained a
    # value check.
    model = XqmxModel("binary", 2)
    model.set_linear(0, I64_MIN)
    model.set_linear(1, I64_MAX)
    assert model.get_linear(0) == I64_MIN
    assert model.get_linear(1) == I64_MAX


def test_sample_exposes_no_value_setter():
    # This is what makes xquad/program.py safe without a second scan: once
    # the constructor validates, no out-of-domain instance can exist.
    sample = XqmxSample("binary", [0, 1])
    for attr in ("set_linear", "set_value", "__setitem__"):
        assert not hasattr(sample, attr), attr
    with pytest.raises((AttributeError, TypeError)):
        sample.values = [5, 5]


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.name for b in BACKENDS])
def test_set_calldata_rejects_a_mutated_sample(backend):
    # The one hole the constructor and __post_init__ leave: reaching into
    # an already-built XQMX. xqcp's own tests use that idiom, so the guard
    # is at the boundary rather than inferred from where a value came from.
    sample = XQMX.binary_sample(2)
    sample.linear[0] = 5

    machine = VM(backend=backend)
    with pytest.raises(SampleOutOfDomain) as excinfo:
        machine.set_calldata([sample])
    assert excinfo.value.value == 5


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.name for b in BACKENDS])
def test_set_calldata_accepts_an_in_domain_sample(backend):
    sample = XQMX.binary_sample(2)
    sample.set_linear(0, 1)

    machine = VM(backend=backend)
    machine.set_calldata([sample])
    machine.set_output_slots(1)
    machine.run("PUSH 0\nINPUT r0\nPUSH 0\nGETLINE r0\nSTOW r1\nPUSH 0\nOUTPUT r1\nHALT")
    assert machine.outputs()[0] == 1


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.name for b in BACKENDS])
def test_setline_out_of_domain_faults_on_both_backends(backend):
    # The ticket's original reproducer, through the public API. The two
    # backends word the fault differently -- the Rust one crosses the FFI
    # as `PyRuntimeError` carrying the error's Debug form, the Python one
    # raises `SampleOutOfDomain` itself -- so the assertion is on the
    # offending value and the domain, which both carry.
    machine = VM(backend=backend)
    with pytest.raises(Exception) as excinfo:
        machine.run("PUSH 4\nPUSH 3\nXSMX r0\nPUSH 0\nPUSH 7\nSETLINE r0\nHALT")
    message = str(excinfo.value)
    assert "7" in message
    assert "nteger" in message


# A directly-constructed `XQMX` is the third write path: `XQMX(mode=SAMPLE,
# domain=SPIN, size=n)` is a legal public construction that leaves `linear`
# empty, and an empty dict has to read back as the domain's default rather
# than as a bare `0` -- which for spin is a value this ticket's own
# invariant declares unwritable. No conformance vector reaches these: the
# construction is a host-side call, not a bytecode path.

_READ_SLOT_0 = "PUSH 0\nINPUT r0\nPUSH 0\nGETLINE r0\nSTOW r1\nPUSH 0\nOUTPUT r1\nHALT"


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.name for b in BACKENDS])
def test_bare_spin_sample_reads_back_as_minus_one(backend):
    sample = XQMX(mode=XQMXMode.SAMPLE, domain=XQMXDomain.SPIN, size=4)
    assert sample.linear == {}

    machine = VM(backend=backend)
    machine.set_calldata([sample])
    machine.set_output_slots(1)
    machine.run(_READ_SLOT_0)
    assert machine.outputs()[0] == -1


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.name for b in BACKENDS])
def test_bare_spin_sample_energy_uses_the_domain_default(backend):
    # `compute_energy` reads every variable through `get_linear`, so the
    # sparse default decides the answer as much as `GETLINE` does. Biases
    # 3 and 5 over two variables both at -1 give -8.
    model = XQMX.spin_model(2)
    model.set_linear(0, 3)
    model.set_linear(1, 5)
    sample = XQMX(mode=XQMXMode.SAMPLE, domain=XQMXDomain.SPIN, size=2)

    machine = VM(backend=backend)
    machine.set_calldata([model, sample])
    machine.set_output_slots(1)
    machine.run("PUSH 0\nINPUT r0\nPUSH 1\nINPUT r1\nENERGY r0 r1\nSTOW r2\nPUSH 0\nOUTPUT r2\nHALT")
    assert machine.outputs()[0] == -8


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.name for b in BACKENDS])
def test_bare_spin_sample_addline_accumulates_from_minus_one(backend):
    # `add_linear`'s `current` reads the same default. From -1, a delta of
    # +2 lands on +1; reading it as 0 would land on +2 and fault.
    sample = XQMX(mode=XQMXMode.SAMPLE, domain=XQMXDomain.SPIN, size=1)

    machine = VM(backend=backend)
    machine.set_calldata([sample])
    machine.set_output_slots(1)
    machine.run("PUSH 0\nINPUT r0\nPUSH 0\nPUSH 2\nADDLINE r0\nPUSH 0\nGETLINE r0\nSTOW r1\nPUSH 0\nOUTPUT r1\nHALT")
    assert machine.outputs()[0] == 1


@pytest.mark.parametrize("backend", BACKENDS, ids=[b.name for b in BACKENDS])
def test_sample_mutated_after_set_calldata_is_refused(backend):
    # `set_calldata` copies the list, not the samples in it, so the caller
    # keeps a live reference. The RUST path already faulted here by
    # rebuilding the `XqmxSample`; without a matching re-scan the PYTHON
    # path executed the mutated sample and returned 5.
    sample = XQMX.binary_sample(1)

    machine = VM(backend=backend)
    machine.set_calldata([sample])
    machine.set_output_slots(1)
    sample.linear[0] = 5

    with pytest.raises(Exception) as excinfo:
        machine.run(_READ_SLOT_0)
    message = str(excinfo.value)
    assert "5" in message
    assert "inary" in message


def test_both_host_guards_are_catchable_as_value_error():
    # The two guards close the same hole at the same boundary, so a caller
    # should not need to know which backend refused. `xqffi` raises
    # `PyValueError`; `SampleOutOfDomain` is one too.
    with pytest.raises(ValueError):
        XqmxSample(domain="binary", values=[2])

    sample = XQMX.binary_sample(1)
    sample.linear[0] = 5
    with pytest.raises(ValueError):
        VM(backend=VMBackend.PYTHON).set_calldata([sample])
