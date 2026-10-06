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

"""Tests for order receipts and the chain readers they rest on."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xqsa.quip import QuipConnectionError, QuipMetadataError
from xqsa.quip.chain import (
    _attribute,
    block_hash,
    block_timestamp,
    front_runner,
    proposal_fee,
    proposer_orders,
    top_solvers,
)

from .test_client import FakeSubstrate, _job_proposed_event

MEMPOOL = "QuantumComputeMempool"
BLOCK = "0xblock"


def _record(
    idx: int | None,
    module: str,
    event: str,
    attrs: object,
    *,
    phase_form: str = "dict",
) -> dict:
    """Build a decoded event record; ``phase_form`` picks how the phase is spelled."""
    body = {"module_id": module, "event_id": event, "attributes": attrs}
    if phase_form == "dict":
        return {"phase": {"ApplyExtrinsic": idx}, "event": body}
    if phase_form == "string":
        return {"phase": "ApplyExtrinsic", "extrinsic_idx": idx, "event": body}
    return {"phase": "Finalization", "event": body}


def _proposed(idx: int, order_id: int, **kw) -> dict:
    return _record(idx, MEMPOOL, "JobProposed", {"order_id": order_id, "reward": 1}, **kw)


def _fee(idx: int, fee: object, **kw) -> dict:
    attrs = {"who": "0xabc", "actual_fee": fee, "tip": 0}
    return _record(idx, "TransactionPayment", "TransactionFeePaid", attrs, **kw)


class _HashStub:
    def __init__(self, result: object = None, raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises

    def get_block_hash(self, number: int):
        if self.raises is not None:
            raise self.raises
        return self.result


class _RecordingSubstrate(FakeSubstrate):
    def __init__(self, *args, query_raises: Exception | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.query_raises = query_raises
        self.queries: list[tuple[str, str, str | None]] = []

    def query(self, module, name, params=None, block_hash=None):
        self.queries.append((module, name, block_hash))
        if self.query_raises is not None:
            raise self.query_raises
        return super().query(module, name, params, block_hash)


class TestChainReaders:
    @pytest.mark.parametrize(("stored", "expected"), [([3, 1, 2], [3, 1, 2]), (["7", 8], [7, 8]), (None, []), ([], [])])
    def test_proposer_orders(self, stored, expected):
        iface = FakeSubstrate(storage={(MEMPOOL, "ProposerOrders"): stored})
        assert proposer_orders(iface, object()) == expected

    @pytest.mark.parametrize("stored", [{"solver": "0xs", "energy_milli": -5}, None])
    def test_front_runner(self, stored):
        iface = FakeSubstrate(storage={(MEMPOOL, "OrderFrontRunner"): stored})
        assert front_runner(iface, 1) == stored

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [([{"solver": "0xa", "energy_milli": 1}], [{"solver": "0xa", "energy_milli": 1}]), (None, [])],
    )
    def test_top_solvers(self, stored, expected):
        iface = FakeSubstrate(storage={(MEMPOOL, "OrderTopSolvers"): stored})
        assert top_solvers(iface, 1) == expected

    @pytest.mark.parametrize(("raw", "expected"), [("0xabcd", "0xabcd"), (b"\xab\xcd", "0xabcd"), ("abcd", "0xabcd")])
    def test_block_hash(self, raw, expected):
        assert block_hash(_HashStub(raw), 5) == expected

    @pytest.mark.parametrize("raw", [None, "", b""])
    def test_block_hash_missing_raises(self, raw):
        with pytest.raises(QuipConnectionError, match="no hash for block 5"):
            block_hash(_HashStub(raw), 5)

    def test_block_hash_wraps_generic_failure(self):
        with pytest.raises(QuipConnectionError, match="could not read the hash of block 5: boom"):
            block_hash(_HashStub(raises=RuntimeError("boom")), 5)

    def test_block_hash_reraises_metadata_error(self):
        err = QuipMetadataError("bad metadata")
        with pytest.raises(QuipMetadataError) as info:
            block_hash(_HashStub(raises=err), 5)
        assert info.value is err

    def test_block_timestamp(self):
        iface = _RecordingSubstrate(storage={("Timestamp", "Now"): 1_700_000_000_123})
        assert block_timestamp(iface, BLOCK) == 1_700_000_000_123

    def test_block_timestamp_passes_block_hash(self):
        iface = _RecordingSubstrate(storage={("Timestamp", "Now"): 1})
        block_timestamp(iface, BLOCK)
        assert iface.queries == [("Timestamp", "Now", BLOCK)]

    def test_block_timestamp_absent_raises(self):
        with pytest.raises(QuipConnectionError, match="Timestamp.Now is absent"):
            block_timestamp(_RecordingSubstrate(), BLOCK)

    def test_block_timestamp_query_failure_raises(self):
        iface = _RecordingSubstrate(query_raises=RuntimeError("boom"))
        with pytest.raises(QuipConnectionError, match="could not read Timestamp.Now"):
            block_timestamp(iface, BLOCK)

    def test_block_timestamp_reraises_metadata_error(self):
        iface = _RecordingSubstrate(query_raises=QuipMetadataError("bad metadata"))
        with pytest.raises(QuipMetadataError):
            block_timestamp(iface, BLOCK)

    @pytest.mark.parametrize("form", ["dict", "string"])
    def test_proposal_fee_matches_same_phase(self, form):
        events = [
            _proposed(1, 10, phase_form=form),
            _fee(1, 111, phase_form=form),
            _proposed(2, 20, phase_form=form),
            _fee(2, 222, phase_form=form),
        ]
        iface = FakeSubstrate(events=events)
        assert proposal_fee(iface, BLOCK, 10) == 111
        assert proposal_fee(iface, BLOCK, 20) == 222

    def test_proposal_fee_fee_event_before_proposal(self):
        iface = FakeSubstrate(events=[_fee(2, 222), _fee(1, 111), _proposed(1, 10)])
        assert proposal_fee(iface, BLOCK, 10) == 111

    def test_proposal_fee_params_list_attributes(self):
        fee = _record(
            1,
            "TransactionPayment",
            "TransactionFeePaid",
            [{"name": "who", "value": "0xabc"}, {"name": "actual_fee", "value": 333}],
        )
        proposed = _job_proposed_event(10, attrs_form="params") | {"phase": {"ApplyExtrinsic": 1}}
        assert proposal_fee(FakeSubstrate(events=[proposed, fee]), BLOCK, 10) == 333

    @pytest.mark.parametrize(("raw", "expected"), [("444", 444), (555, 555), (666.0, 666)])
    def test_proposal_fee_coerces_to_int(self, raw, expected):
        fee = proposal_fee(FakeSubstrate(events=[_proposed(1, 10), _fee(1, raw)]), BLOCK, 10)
        assert fee == expected
        assert isinstance(fee, int)

    def test_proposal_fee_unwraps_record_objects(self):
        events = [SimpleNamespace(value=_proposed(1, 10)), SimpleNamespace(value=_fee(1, 777))]
        assert proposal_fee(FakeSubstrate(events=events), BLOCK, 10) == 777

    def test_proposal_fee_skips_unreadable_records(self):
        events = ["junk", None, {"event": "junk"}, _proposed(1, 10), _fee(1, 5)]
        assert proposal_fee(FakeSubstrate(events=events), BLOCK, 10) == 5

    @pytest.mark.parametrize("events", [[], None])
    def test_proposal_fee_empty_block_raises(self, events):
        with pytest.raises(QuipConnectionError, match="no JobProposed"):
            proposal_fee(FakeSubstrate(events=events), BLOCK, 10)

    def test_proposal_fee_other_order_only_raises(self):
        iface = FakeSubstrate(events=[_proposed(1, 99), _fee(1, 5)])
        with pytest.raises(QuipConnectionError, match="no JobProposed"):
            proposal_fee(iface, BLOCK, 10)

    @pytest.mark.parametrize(
        "events",
        [
            [_proposed(1, 10)],
            [_proposed(1, 10), _fee(2, 5)],
            [_proposed(1, 10), _fee(None, 5, phase_form="none")],
            [_proposed(1, 10), _record(1, "Balances", "Withdraw", {"actual_fee": 5})],
            [_proposed(1, 10), _fee(1, None)],
        ],
    )
    def test_proposal_fee_missing_fee_raises(self, events):
        with pytest.raises(QuipConnectionError, match="TransactionFeePaid"):
            proposal_fee(FakeSubstrate(events=events), BLOCK, 10)

    @pytest.mark.parametrize(
        ("attrs", "expected"),
        [
            ({"order_id": 4, "other": 1}, 4),
            ({"other": 1}, None),
            ([{"name": "a", "value": 1}, {"name": "order_id", "value": 9}], 9),
            ([{"name": "a", "value": 1}], None),
            ([4, "0xspec"], None),
            (None, None),
            (7, None),
            ("order_id", None),
        ],
    )
    def test_attribute(self, attrs, expected):
        assert _attribute(attrs, "order_id") == expected
