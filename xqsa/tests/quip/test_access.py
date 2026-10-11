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

"""Access rules: account parsing, bounds, deduplication, chain encoding and text."""

from __future__ import annotations

import pytest

from xqsa.quip.access import MAX_BID_TYPES, AccessAllow, AccessOpen, coerce_access, from_chain

# The well-known dev accounts Alice and Bob, as SS58 (format 42) and hex.
ALICE = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
ALICE_HEX = "0xd43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d"
BOB = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
BOB_HEX = "0x8eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a48"


def needs_scalecodec():
    pytest.importorskip("scalecodec", reason="SS58 needs the [quip] extra")


def test_accounts_kept_as_hex():
    assert AccessAllow(accounts=[ALICE_HEX, bytes.fromhex(BOB_HEX[2:])]).accounts == (ALICE_HEX, BOB_HEX)


def test_ss58_account():
    needs_scalecodec()
    assert AccessAllow(accounts=[ALICE]).accounts == (ALICE_HEX,)


def test_deduplicates_keeping_order():
    rule = AccessAllow(accounts=[BOB_HEX, ALICE_HEX, BOB_HEX.upper().replace("0X", "0x")], types=["Gpu", "Cpu", "Gpu"])
    assert rule.accounts == (BOB_HEX, ALICE_HEX)
    assert rule.types == ("Gpu", "Cpu")


def test_deduplicates_across_forms():
    needs_scalecodec()
    assert AccessAllow(accounts=[ALICE, ALICE_HEX]).accounts == (ALICE_HEX,)


@pytest.mark.parametrize("account", ["0x1234", "0xzz", "not-an-address", None, 7])
def test_bad_account(account):
    if account == "not-an-address":
        needs_scalecodec()
    with pytest.raises(ValueError, match="AccessAllow: .* (is not an SS58|bytes, an account is 32)"):
        AccessAllow(accounts=[account])


def test_needs_an_entry():
    with pytest.raises(ValueError, match="at least one account or solver type"):
        AccessAllow()
    with pytest.raises(ValueError, match="at least one"):
        AccessAllow(accounts=[], types=[])


def test_type_cap():
    names = [f"T{i}" for i in range(MAX_BID_TYPES + 1)]
    assert len(AccessAllow(types=names[:MAX_BID_TYPES]).types) == MAX_BID_TYPES
    with pytest.raises(ValueError, match=f"at most {MAX_BID_TYPES} solver types, got 9"):
        AccessAllow(types=names)
    # Duplicates do not count against the cap.
    assert AccessAllow(types=names[:MAX_BID_TYPES] + ["T0"]).types == tuple(names[:MAX_BID_TYPES])


def test_type_not_a_string():
    with pytest.raises(TypeError, match="solver type is a name"):
        AccessAllow(types=[2])


def test_single_value_instead_of_a_list():
    with pytest.raises(TypeError, match="take lists"):
        AccessAllow(accounts=ALICE_HEX)
    with pytest.raises(TypeError, match="take lists"):
        AccessAllow(types="QpuDwave")


@pytest.mark.parametrize(
    ("rule", "chain"),
    [
        (AccessOpen(), "Open"),
        (
            AccessAllow(accounts=[ALICE_HEX], types=["QpuDwave", "Cpu"]),
            {"Bid": {"miners": ([ALICE_HEX],), "miner_types": (["QpuDwave", "Cpu"],)}},
        ),
        (AccessAllow(types=["QpuDwave"]), {"Bid": {"miners": None, "miner_types": (["QpuDwave"],)}}),
        (AccessAllow(accounts=[ALICE_HEX]), {"Bid": {"miners": ([ALICE_HEX],), "miner_types": None}}),
    ],
)
def test_to_chain(rule, chain):
    # Both Bid fields are Option<BoundedVec>: an empty list is None, a list a 1-tuple.
    assert rule.to_chain() == chain


@pytest.mark.parametrize(
    ("chain", "rule"),
    [
        ("Open", AccessOpen()),
        ({"Open": None}, AccessOpen()),
        ({"Bid": {"miners": [ALICE_HEX], "miner_types": None}}, AccessAllow(accounts=[ALICE_HEX])),
        ({"Bid": {"miners": None, "miner_types": ["QpuDwave"]}}, AccessAllow(types=["QpuDwave"])),
    ],
)
def test_from_chain(chain, rule):
    assert from_chain(chain) == rule


def test_from_chain_ss58_as_read_back():
    needs_scalecodec()
    decoded = {"Bid": {"miners": [ALICE, BOB], "miner_types": ["Cpu"]}}
    assert from_chain(decoded) == AccessAllow(accounts=[ALICE_HEX, BOB_HEX], types=["Cpu"])


@pytest.mark.parametrize("value", ["Bid", {"Bid": 5}, {"Callback": {}}, None])
def test_from_chain_refuses_unknown(value):
    with pytest.raises(ValueError, match="unknown JobMode"):
        from_chain(value)


def test_text():
    needs_scalecodec()
    assert str(AccessOpen()) == "open"
    assert str(AccessAllow(accounts=[ALICE, BOB], types=["QpuDwave"])) == (
        "allowlist (accounts 5Grw..utQY, 5FHn..94ty; types QpuDwave)"
    )
    assert str(AccessAllow(types=["Cpu", "Gpu", "QpuDwave", "QpuIbm", "Asic"])) == (
        "allowlist (types Cpu, Gpu, QpuDwave and 2 more)"
    )


def test_coerce():
    assert coerce_access("Open") == AccessOpen()
    rule = AccessAllow(types=["Cpu"])
    assert coerce_access(rule) is rule
    raw = {"Bid": {"miners": None, "miner_types": (["Cpu"],)}}
    assert coerce_access(raw) is raw
    with pytest.raises(ValueError, match="AccessAllow"):
        coerce_access("Whitelist")
