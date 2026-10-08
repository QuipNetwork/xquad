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

import logging
from types import SimpleNamespace

import pytest

from xqsa.quip import QuipConnectionError, QuipMetadataError, QuipSubmissionError, QuipTimeoutError, chain
from xqsa.quip.chain import (
    _attribute,
    account_bytes,
    block_hash,
    block_time_ms,
    block_timestamp,
    front_runner,
    proposal_fee,
    proposer_orders,
    top_solvers,
)
from xqsa.quip.display import terms_rows, variant_text
from xqsa.quip.receipt import JobOrderReceipt

from .test_client import (
    GENESIS_HASH,
    TOPO_HASH,
    UNIT,
    FakeSubstrate,
    _chain_iface,
    _force_timeout,
    _job_proposed_event,
    _make_solver,
    _model,
    _ok_receipt,
    _order,
    _patch_signing,
    _submission,
)
from .test_order import _ready

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

    def test_account_bytes_forms(self):
        raw = bytes(range(32))
        assert account_bytes(raw) == raw
        assert account_bytes("0x" + raw.hex()) == raw

    def test_account_bytes_ss58_round_trip(self):
        ss58 = pytest.importorskip("scalecodec.utils.ss58")
        raw = bytes(range(32))
        assert account_bytes(ss58.ss58_encode(raw, 42)) == raw

    def test_account_bytes_junk_text_raises(self):
        pytest.importorskip("scalecodec.utils.ss58")
        with pytest.raises(ValueError):
            account_bytes("not an account")

    def test_block_time_ms(self):
        assert block_time_ms(FakeSubstrate(constants={("Babe", "ExpectedBlockTime"): 6000})) == 6000

    @pytest.mark.parametrize(
        "iface",
        [FakeSubstrate(), FakeSubstrate(get_constant_raises=RuntimeError("boom"))],
        ids=["absent", "raises"],
    )
    def test_block_time_ms_unknown(self, iface):
        assert block_time_ms(iface) is None

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


REWARD = 5 * UNIT
TOP_N = {"TopNEqual": {"n": 3}}


def _receipt_order(**kw) -> dict:
    """``_order`` plus what a receipt reads: ``reward``, ``resolution`` and the fields its display shows."""
    resolution = kw.pop("resolution", "SingleBest")
    return {
        **_order(**kw),
        "reward": REWARD,
        "resolution": resolution,
        "mode": "Open",
        "spec_id": "0x" + "ab" * 32,
        "ising_params": {"nodes": [0, 1, 2], "edges": [[0, 1], [1, 2]]},
    }


def _answer(solver_id: str, energy: int, submitted_at: int, vectors=((1, -1),)) -> tuple:
    """One ``OrderSolutions`` map entry."""
    return (
        solver_id.encode(),
        {**_submission(solver_id, [list(v) for v in vectors], energy), "submitted_at": submitted_at},
    )


def _receipt_for(monkeypatch, *, order=None, head: int = 50, answers=None, front=None, top=None):
    """A receipt for order 1 on a mocked chain; ``front``/``top`` seed the ranking storage."""
    iface = _chain_iface(order=order or _receipt_order(), head=head, submissions=answers)
    if front is not None:
        iface.storage[(MEMPOOL, "OrderFrontRunner")] = front
    if top is not None:
        iface.storage[(MEMPOOL, "OrderTopSolvers")] = top
    return _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH).get_receipt(1)


class TestJobOrderReceipt:
    def test_get_receipt(self, monkeypatch) -> None:
        receipt = _receipt_for(monkeypatch)
        assert isinstance(receipt, JobOrderReceipt)
        assert receipt.order_id == 1
        assert receipt._genesis_hash == GENESIS_HASH

    def test_get_receipt_missing_order_raises(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)  # default iface has no JobOrders entry.
        with pytest.raises(QuipConnectionError, match="not found"):
            solver.get_receipt(7)

    def test_construction_reads_nothing(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)

        def _boom(order_id: int):
            raise AssertionError("construction must not read the chain")

        monkeypatch.setattr(solver, "_fetch_order", _boom)
        assert JobOrderReceipt(solver, 1, GENESIS_HASH).order_id == 1

    def test_repr_hides_the_genesis_hash(self, monkeypatch) -> None:
        text = repr(_receipt_for(monkeypatch))
        assert text == "JobOrderReceipt(order_id=1)"
        assert GENESIS_HASH not in text

    def test_status_open_is_submitted(self, monkeypatch) -> None:
        assert _receipt_for(monkeypatch, head=50).status() == {
            "state": "submitted",
            "order_id": 1,
            "chain_status": "Opened",
            "created_at": 0,
            "first_solution_at": None,
            "effective_expiry": 100,
            "current_block": 50,
            "solution_count": 1,
        }

    def test_status_past_expiry_is_finalized(self, monkeypatch) -> None:
        assert _receipt_for(monkeypatch, head=200).status()["state"] == "finalized"

    def test_status_stays_finalized_on_a_lagging_read(self, monkeypatch) -> None:
        receipt = _receipt_for(monkeypatch, head=200)
        assert receipt.status()["state"] == "finalized"
        receipt._client._iface.head = 50
        snap = receipt.status()
        assert (snap["state"], snap["current_block"]) == ("finalized", 50)  # only state is sticky.


class TestJobOrderReceiptWait:
    def test_timeout_error_pickles_without_its_receipt(self, monkeypatch) -> None:
        import pickle

        error = QuipTimeoutError(1, receipt=_receipt_for(monkeypatch))
        error.add_note("retry with get_receipt(1)")
        restored = pickle.loads(pickle.dumps(error))
        assert (type(restored), restored.order_id, str(restored), restored.receipt) == (
            QuipTimeoutError,
            1,
            str(error),
            None,
        )
        assert restored.__notes__ == ["retry with get_receipt(1)"]

    def test_terminal_status_returns_immediately(self, monkeypatch) -> None:
        # Closed short-circuits regardless of height (head 5 < expiry 100).
        receipt = _receipt_for(monkeypatch, order=_receipt_order(status="Closed"), head=5)
        assert receipt.wait() is receipt

    def test_terminal_skips_head_read(self, monkeypatch) -> None:
        # A terminal status is final regardless of height, so the head-height
        # RPC must be skipped entirely.
        receipt = _receipt_for(monkeypatch, order=_receipt_order(status="Closed"), head=5)

        def _boom() -> int:
            raise AssertionError("head-height read must be skipped for a terminal order")

        monkeypatch.setattr(receipt._client, "_current_block", _boom)
        assert receipt.wait() is receipt

    def test_final_by_height(self, monkeypatch) -> None:
        # Opened but past the hard deadline (head 200 >= expiry 100).
        receipt = _receipt_for(monkeypatch, order=_receipt_order(status="Opened"), head=200)
        assert receipt.wait() is receipt

    def test_times_out_carrying_the_receipt(self, monkeypatch) -> None:
        iface = _chain_iface(order=_receipt_order(status="Opened"), head=50)  # 50 < expiry 100 -> never final
        receipt = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH, timeout=0.0).get_receipt(1)
        _force_timeout(monkeypatch)
        with pytest.raises(QuipTimeoutError) as excinfo:
            receipt.wait()
        assert excinfo.value.order_id == 1
        assert excinfo.value.receipt is receipt


class TestJobOrderReceiptSolvers:
    def test_front_runner_first_then_energy_then_submission_order(self, monkeypatch) -> None:
        answers = [
            _answer("0xA", 100, 5),
            _answer("0xB", 300, 9),  # the front runner, though not the lowest energy.
            _answer("0xC", 100, 3),  # ties 0xA on energy; submitted earlier.
            _answer("0xD", 200, 1),
        ]
        front = {"solver": "0xB", "energy_milli": 300}
        receipt = _receipt_for(monkeypatch, answers=answers, front=front)
        assert [entry["solver"] for entry in receipt.solvers()] == ["0xB", "0xC", "0xA", "0xD"]

    def test_entry_shape(self, monkeypatch) -> None:
        answers = [_answer("0xA", 100, 5, vectors=[(1, -1), (-1, 1)])]
        receipt = _receipt_for(monkeypatch, answers=answers)
        assert receipt.solvers() == [{"solver": "0xA", "energy_milli": 100, "submitted_at": 5, "num_solutions": 2}]

    def test_top_n_ranking_leads(self, monkeypatch) -> None:
        answers = [_answer("0xA", 100, 5), _answer("0xB", 50, 1), _answer("0xC", 300, 2)]
        # 0xGHOST is ranked but never answered, so it is skipped.
        top = [
            {"solver": "0xC", "energy_milli": 300},
            {"solver": "0xGHOST", "energy_milli": 1},
            {"solver": "0xA", "energy_milli": 100},
        ]
        receipt = _receipt_for(monkeypatch, order=_receipt_order(resolution=TOP_N), answers=answers, top=top)
        assert [entry["solver"] for entry in receipt.solvers()] == ["0xC", "0xA", "0xB"]

    def test_no_answers(self, monkeypatch) -> None:
        assert _receipt_for(monkeypatch, answers=[]).solvers() == []


class TestJobOrderReceiptRawSolutions:
    ANSWERS = [_answer("0xA", 100, 5, vectors=[(1, -1), (-1, 1)]), _answer("0xB", 200, 6)]

    def test_all_solvers(self, monkeypatch) -> None:
        receipt = _receipt_for(monkeypatch, answers=self.ANSWERS)
        assert receipt.raw_solutions() == {
            "0xA": {"energy_milli": 100, "spins": [[1, -1], [-1, 1]]},
            "0xB": {"energy_milli": 200, "spins": [[1, -1]]},
        }

    def test_one_solver(self, monkeypatch) -> None:
        receipt = _receipt_for(monkeypatch, answers=self.ANSWERS)
        assert receipt.raw_solutions("0xB") == {"energy_milli": 200, "spins": [[1, -1]]}

    def test_unknown_solver_names_the_order(self, monkeypatch) -> None:
        receipt = _receipt_for(monkeypatch, answers=self.ANSWERS)
        with pytest.raises(KeyError, match="0xZ did not answer order 1"):
            receipt.raw_solutions("0xZ")


class TestJobOrderReceiptSettlement:
    LEADER = {"solver": "0xB", "energy_milli": 300}
    WINNER = [{"solver": "0xB", "energy_milli": 300, "allocation_planck": REWARD}]

    @pytest.mark.parametrize(
        ("order", "front", "expected"),
        [
            pytest.param(
                _receipt_order(status="Opened"), LEADER, {"claimed": False, "winners": WINNER}, id="open-leader"
            ),
            pytest.param(
                _receipt_order(status="Closed"), LEADER, {"claimed": True, "winners": WINNER}, id="closed-answered"
            ),
            pytest.param(
                _receipt_order(status="Opened", solution_count=0),
                None,
                {"claimed": False, "winners": []},
                id="unanswered",
            ),
            pytest.param(
                _receipt_order(status="Closed", solution_count=0),
                None,
                {"claimed": False, "winners": []},
                id="reclaimed",
            ),
        ],
    )
    def test_single_best(self, monkeypatch, order, front, expected) -> None:
        assert _receipt_for(monkeypatch, order=order, front=front).settlement() == expected

    def test_top_n_not_supported(self, monkeypatch) -> None:
        receipt = _receipt_for(monkeypatch, order=_receipt_order(resolution=TOP_N))
        with pytest.raises(NotImplementedError, match="QUI-1606"):
            receipt.settlement()


OTHER_ACCOUNT = b"\x11" * 32
BLOCK_TIME = ("Babe", "ExpectedBlockTime")


def _reclaimable(monkeypatch, *, block_time: int | None = None, head: int = 200, spell=None, **order_kw):
    """A receipt for an order the signer could reclaim, plus the list of submitted calls.

    The defaults are final (head 200 past expiry 100), unanswered, proposed by the signer;
    ``spell`` re-spells the signer's account (bytes in, any decoded form out) as the proposer.
    """
    receipt = _receipt_for(monkeypatch, order=_receipt_order(**{"solution_count": 0, **order_kw}), head=head)
    client = receipt._client
    if spell is not None:
        client._iface.storage[(MEMPOOL, "JobOrders")]["proposer"] = spell(bytes(client._signer.account_id))
    if block_time is not None:
        client._iface.constants[BLOCK_TIME] = block_time
    calls: list[tuple] = []
    monkeypatch.setattr(client, "_submit_extrinsic", lambda *args: calls.append(args))
    return receipt, calls


class TestJobOrderReceiptReclaim:
    def test_reads_the_head_before_the_order(self, monkeypatch) -> None:
        receipt, _ = _reclaimable(monkeypatch)
        client, reads = receipt._client, []
        current_block, fetch_order = client._current_block, client._fetch_order
        monkeypatch.setattr(client, "_current_block", lambda: reads.append("head") or current_block())
        monkeypatch.setattr(client, "_fetch_order", lambda order_id: reads.append("order") or fetch_order(order_id))
        receipt.reclaim()
        assert reads == ["head", "order"]

    def test_a_final_already_seen_survives_a_lagging_head(self, monkeypatch) -> None:
        receipt, calls = _reclaimable(monkeypatch)
        receipt.wait()
        receipt._client._iface.head = 50
        assert receipt.reclaim() == REWARD
        assert len(calls) == 1

    def test_one_block_short_of_expiry_is_reclaimable(self, monkeypatch) -> None:
        # The reclaim lands at head + 1 at the earliest, where the chain flips the order to Expired.
        receipt, calls = _reclaimable(monkeypatch, head=99)
        assert receipt.reclaim() == REWARD
        assert calls == [(MEMPOOL, "reclaim_order", {"order_id": 1})]

    @pytest.mark.parametrize("form", ["hex", "bytes", "ss58"])
    def test_refunds_an_unanswered_final_order(self, monkeypatch, form) -> None:
        spells = {"hex": lambda raw: "0x" + raw.hex(), "bytes": bytes}
        if form == "ss58":
            ss58_encode = pytest.importorskip("scalecodec.utils.ss58").ss58_encode
            spells[form] = lambda raw: ss58_encode(raw, 42)
        receipt, calls = _reclaimable(monkeypatch, spell=spells[form])
        assert receipt.reclaim() == REWARD
        assert calls == [(MEMPOOL, "reclaim_order", {"order_id": 1})]

    @pytest.mark.parametrize(
        ("order_kw", "head", "block_time", "match"),
        [
            pytest.param(
                {"proposer": OTHER_ACCOUNT},
                200,
                None,
                r"^order 1 was proposed by .+, not by this account$",
                id="not-proposer",
            ),
            pytest.param(
                {"status": "Closed"}, 200, None, r"^order 1 is already closed \(reclaimed or settled\)$", id="closed"
            ),
            pytest.param(
                {}, 50, 6000, r"^order 1 is open until block 100 \(about 5 min\); reclaim after it closes$", id="open"
            ),
            pytest.param(
                {}, 50, None, r"^order 1 is open until block 100; reclaim after it closes$", id="open-no-time"
            ),
            pytest.param(
                {"solution_count": 2},
                200,
                None,
                r"^order 1 was answered by 2 solvers; its reward awaits the winner's claim and cannot be reclaimed$",
                id="answered-2",
            ),
            pytest.param({"solution_count": 1}, 200, None, r"^order 1 was answered by 1 solver; its", id="answered-1"),
            # Check order follows the chain: proposer, then closed, then open, then answered.
            pytest.param(
                {"proposer": OTHER_ACCOUNT, "solution_count": 2, "status": "Opened"},
                50,
                None,
                r"was proposed by .+, not by this account$",
                id="not-proposer-beats-open-and-answered",
            ),
            pytest.param({"status": "Closed"}, 50, 6000, r"^order 1 is already closed", id="closed-beats-open"),
        ],
    )
    def test_local_refusals_never_submit(self, monkeypatch, order_kw, head, block_time, match) -> None:
        receipt, calls = _reclaimable(monkeypatch, block_time=block_time, head=head, **order_kw)
        with pytest.raises(QuipSubmissionError, match=match):
            receipt.reclaim()
        assert calls == []

    def test_open_rounds_up_to_whole_minutes(self, monkeypatch) -> None:
        # 50 blocks at 7000 ms is 5.83 min.
        receipt, _ = _reclaimable(monkeypatch, block_time=7000, head=50)
        with pytest.raises(QuipSubmissionError, match=r"\(about 6 min\)"):
            receipt.reclaim()


def _fail_dispatch(monkeypatch, solver) -> None:
    _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="QuantumComputeMempool.RewardTooLow"))


def _lose_order_id(monkeypatch, solver) -> None:
    solver._iface.events = []  # included, but no JobProposed event.


class TestJobOrderPassThroughs:
    def test_finalized_stays_finalized_on_a_lagging_read(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)  # head 200 is past the order's expiry.
        order = solver.create_order(_model()).submit().wait()
        solver._iface.head = 50  # a node behind the one that saw it final.
        assert order.status()["state"] == "finalized"
        assert order.receipt().status()["state"] == "finalized"
        assert order._state == "finalized"

    def test_submit_sets_the_receipt(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        receipt = order.receipt()
        assert receipt.order_id == order.order_id()
        assert receipt._genesis_hash == order._genesis_hash

    @pytest.mark.parametrize(
        ("setup", "match"),
        [
            pytest.param(None, "submit\\(\\) it first", id="draft"),
            pytest.param(_fail_dispatch, "failed on chain", id="failed"),
            pytest.param(_lose_order_id, "is unconfirmed", id="unconfirmed"),
        ],
    )
    def test_receipt_refused_without_an_order_id(self, monkeypatch, setup, match) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        if setup is not None:
            setup(monkeypatch, solver)
            with pytest.raises(QuipSubmissionError):
                order.submit()
        with pytest.raises(QuipSubmissionError, match=match):
            order.receipt()

    def test_wait_returns_the_order_and_finalizes_it(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)  # head 200 is past the order's expiry.
        order = solver.create_order(_model()).submit()
        assert order.wait() is order
        assert order._state == "finalized"

    def test_readers_delegate_to_the_receipt(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        seen: list[object] = []
        monkeypatch.setattr(order._receipt, "solvers", lambda: ["solvers"])
        monkeypatch.setattr(order._receipt, "settlement", lambda: {"claimed": False, "winners": []})
        monkeypatch.setattr(order._receipt, "raw_solutions", lambda solver=None: seen.append(solver) or {"raw": solver})
        monkeypatch.setattr(order._receipt, "reclaim", lambda: REWARD)
        assert order.solvers() == ["solvers"]
        assert order.settlement() == {"claimed": False, "winners": []}
        assert order.raw_solutions() == {"raw": None}
        assert order.raw_solutions("0xSOLVER") == {"raw": "0xSOLVER"}
        assert seen == [None, "0xSOLVER"]
        assert order.reclaim() == REWARD

    def test_status_goes_through_the_receipt(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        snapshot = {"state": "finalized", "order_id": 1}
        monkeypatch.setattr(order._receipt, "status", lambda: snapshot)
        assert order.status() is snapshot
        assert order._state == "finalized"


class TestListOrders:
    def _solver(self, monkeypatch, ids, *, final=frozenset(), head: int = 50):
        """A solver whose account proposed ``ids``; the orders in ``final`` are past expiry."""
        iface = _chain_iface(order=_receipt_order(), head=head)
        iface.storage[(MEMPOOL, "ProposerOrders")] = ids
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        monkeypatch.setattr(
            solver, "_fetch_order", lambda order_id: _receipt_order(status="Closed" if order_id in final else "Opened")
        )
        return solver

    def test_newest_first(self, monkeypatch) -> None:
        assert self._solver(monkeypatch, [3, 7, 9]).list_orders() == [9, 7, 3]

    def test_no_orders(self, monkeypatch) -> None:
        assert self._solver(monkeypatch, None).list_orders() == []

    @pytest.mark.parametrize(("state", "expected"), [("submitted", [9, 3]), ("finalized", [7])])
    def test_state_filter(self, monkeypatch, state, expected) -> None:
        assert self._solver(monkeypatch, [3, 7, 9], final={7}).list_orders(state=state) == expected

    def test_unknown_state_refused(self, monkeypatch) -> None:
        with pytest.raises(ValueError, match="state='open'"):
            self._solver(monkeypatch, [1]).list_orders(state="open")

    def test_other_account(self, monkeypatch) -> None:
        solver = self._solver(monkeypatch, [1])
        keys: list = []
        query = solver._iface.query
        monkeypatch.setattr(
            solver._iface, "query", lambda module, name, params=None, **kw: keys.append(params) or query(module, name)
        )
        solver.list_orders(account="0x" + "11" * 32)
        assert keys == [[bytes.fromhex("11" * 32)]]

    def test_caps_at_32_and_warns(self, monkeypatch) -> None:
        solver = self._solver(monkeypatch, list(range(40)))
        with pytest.warns(UserWarning, match="showing 32 of 40 orders on this account") as record:
            ids = solver.list_orders()
        assert ids == list(range(39, 7, -1))
        assert "flag it to the xquad team" in str(record[0].message)

    def test_exactly_32_does_not_warn(self, monkeypatch, recwarn) -> None:
        assert len(self._solver(monkeypatch, list(range(32))).list_orders()) == 32
        assert not [w for w in recwarn if "orders on this account" in str(w.message)]


PROPOSER = "0x" + "11" * 32
SOLVER = "0x" + "cd" * 32
FEE = 2_182_560_255
SUBMITTED_MS = 1_700_000_000_000  # 2023-11-14 22:13:20 UTC.
SUBMITTED_TEXT = "2023-11-14 22:13:20 UTC"
LEADING = {"solver": SOLVER, "energy_milli": -1500}


def _display_for(monkeypatch, *, head: int = 200, fee: int | None = FEE, ms: int | None = SUBMITTED_MS, **kw):
    """A receipt for order 1 with the fee event and block time its display reads.

    ``created_at`` stays 0 because ``FakeSubstrate.get_block_hash`` serves only block 0.
    """
    order = kw.pop("order", None) or _receipt_order(proposer=PROPOSER)
    receipt = _receipt_for(monkeypatch, order=order, head=head, **kw)
    iface = receipt._client._iface
    iface.events = [_proposed(1, 1), *([_fee(1, fee)] if fee is not None else [])]
    if ms is not None:
        iface.storage[("Timestamp", "Now")] = ms
    return receipt


def _rows(text: str) -> list[str]:
    return [line[2:-2].rstrip() for line in text.splitlines() if line.startswith("│")]


class TestJobOrderReceiptDisplay:
    def test_awaiting_claim_box(self, monkeypatch) -> None:
        pytest.importorskip("scalecodec.utils.ss58")  # the addresses below are SS58 of 0x11.. and 0xcd...
        receipt = _display_for(monkeypatch, answers=[_answer(SOLVER, -1500, 9)], front=LEADING)
        text = str(receipt)
        assert text == "\n".join(
            [
                "╭─ Job order 1 receipt ────────────────────────────────────────────────╮",
                "│ Network     custom endpoint                                          │",
                "│ Proposer    5CT5jwBEAhveEjgiSCQbkaKcKcUyF3VJ8qNXM9rXsuQyn3Kd         │",
                "│ Submitted   block 0, 2023-11-14 22:13:20 UTC                         │",
                "├──────────────────────────────────────────────────────────────────────┤",
                "│ Problem     3 spins, 2 couplings                                     │",
                "│ Spec        0xababababababababababababababababababababababababababab │",
                "│             ababababab                                               │",
                "├──────────────────────────────────────────────────────────────────────┤",
                "│ Payout      single best                                              │",
                "│ Access      open                                                     │",
                "│ Floors      none                                                     │",
                "│ Deadline    100 blocks                                               │",
                "│ Block wait  10 blocks                                                │",
                "│ Reward      5.000000000000 AGLS                                      │",
                "├──────────────────────────────────────────────────────────────────────┤",
                "│ Status      final                                                    │",
                "│ Answers     1                                                        │",
                "│ Leader      5GiYowJ1A5h6K97yVfx5Nxw6Gmdb1vYLc1LjnYwWsv3KiM4a         │",
                "│ Energy      -1.5                                                     │",
                "╞══════════════════════════════════════════════════════════════════════╡",
                "│ Fee         0.002182560255 AGLS                                      │",
                "│ Winner      5.000000000000 AGLS, awaiting claim                      │",
                "╰──────────────────────────────────────────────────────────────────────╯",
            ]
        )
        assert {len(line) for line in text.splitlines()} == {72}

    @pytest.mark.parametrize(
        ("order_kw", "head", "front", "status", "money"),
        [
            pytest.param({"solution_count": 0}, 50, None, "open", "Reward      reserved, no answers yet", id="open"),
            pytest.param({}, 50, LEADING, "open", "Reward      reserved, leader so far", id="open-answered"),
            pytest.param(
                {"solution_count": 0},
                200,
                None,
                "final",
                "Reward      unanswered: reclaim with get_receipt(1).reclaim()",
                id="unanswered",
            ),
            pytest.param(
                {},
                200,
                LEADING,
                "final",
                "Winner      5.000000000000 AGLS, awaiting claim",
                id="awaiting-claim",
            ),
            pytest.param(
                {"status": "Closed"}, 200, LEADING, "closed", "Winner      5.000000000000 AGLS, claimed", id="settled"
            ),
            pytest.param(
                {"status": "Closed", "solution_count": 0}, 200, None, "closed", "Reward      reclaimed", id="reclaimed"
            ),
        ],
    )
    def test_status_and_money_rows(self, monkeypatch, order_kw, head, front, status, money) -> None:
        order = _receipt_order(proposer=PROPOSER, **order_kw)
        receipt = _display_for(monkeypatch, order=order, head=head, front=front)
        rows = _rows(str(receipt))
        assert f"Status      {status}" in rows
        assert money in rows

    def test_top_n_with_answers_shows_the_unsupported_settlement(self, monkeypatch) -> None:
        order = _receipt_order(proposer=PROPOSER, resolution=TOP_N)
        text = str(_display_for(monkeypatch, order=order))
        assert "QUI-1606" in text
        assert "│ Reward      " in text.split("╞")[1]
        assert {len(line) for line in text.splitlines()} == {72}

    def test_missing_fee_reads_unknown_and_warns(self, monkeypatch, caplog) -> None:
        receipt = _display_for(monkeypatch, fee=None)
        with caplog.at_level(logging.WARNING, logger="xqsa.quip"):
            text = str(receipt)
        assert "│ Fee         unknown " in text
        assert any("the fee of order 1" in record.getMessage() for record in caplog.records)

    def test_faulted_fee_is_read_again(self, monkeypatch) -> None:
        receipt = _display_for(monkeypatch, fee=None)
        assert "│ Fee         unknown " in str(receipt)
        receipt._client._iface.events = [_proposed(1, 1), _fee(1, FEE)]  # the node recovers.
        assert f"│ Fee         {FEE / 10**12:.12f} AGLS " in str(receipt)

    def test_missing_timestamp_reads_unknown(self, monkeypatch, caplog) -> None:
        receipt = _display_for(monkeypatch, ms=None)
        with caplog.at_level(logging.WARNING, logger="xqsa.quip"):
            text = str(receipt)
        assert "│ Submitted   block 0, time unknown " in text
        assert any("the submission time of order 1" in record.getMessage() for record in caplog.records)
        assert f"{FEE / 10**12:.12f} AGLS" in text  # the fee still reads.

    def test_time_renders_in_utc(self, monkeypatch) -> None:
        assert f"│ Submitted   block 0, {SUBMITTED_TEXT} " in str(_display_for(monkeypatch))

    def test_facts_are_read_once(self, monkeypatch) -> None:
        receipt = _display_for(monkeypatch)
        reads: list[int] = []
        real = chain.proposal_fee

        def counting(*args, **kwargs):
            reads.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(chain, "proposal_fee", counting)
        assert str(receipt) == str(receipt)
        assert len(reads) == 1

    @pytest.mark.parametrize("warm", [False, True], ids=["cold", "cached-facts"])
    def test_chain_fault_renders_a_one_row_box(self, monkeypatch, warm) -> None:
        receipt = _display_for(monkeypatch)
        if warm:
            str(receipt)

        def _boom(order_id: int):
            raise QuipConnectionError("node gone")

        monkeypatch.setattr(receipt._client, "_fetch_order", _boom)
        text = str(receipt)
        assert text == "\n".join(
            [
                "╭─ Job order 1 receipt ────────────────────────────────────────────────╮",
                "│ Chain       could not be read: node gone                             │",
                "╰──────────────────────────────────────────────────────────────────────╯",
            ]
        )

    def test_genesis_hash_never_shown(self, monkeypatch) -> None:
        assert GENESIS_HASH not in str(_display_for(monkeypatch))
        solver, _ = _ready(monkeypatch)
        assert GENESIS_HASH not in str(solver.create_order(_model()).submit())

    def test_energy_is_milli_scaled(self, monkeypatch) -> None:
        assert "│ Energy      -1.5 " in str(_display_for(monkeypatch, front=LEADING))

    def test_large_energy_keeps_every_milli(self, monkeypatch) -> None:
        leader = {**LEADING, "energy_milli": -1234567890}
        assert "│ Energy      -1234567.89 " in str(_display_for(monkeypatch, front=leader))

    def test_no_leader_reads_none(self, monkeypatch) -> None:
        text = str(_display_for(monkeypatch, order=_receipt_order(proposer=PROPOSER, solution_count=0)))
        assert "│ Leader      none " in text
        assert "│ Energy      none " in text


class TestDisplayHelpers:
    @pytest.mark.parametrize(
        ("value", "text"),
        [("SingleBest", "single best"), ("Open", "open"), ({"TopNEqual": {"n": 3}}, "{'TopNEqual': {'n': 3}}")],
    )
    def test_variant_text(self, value, text) -> None:
        assert variant_text(value) == text

    def test_terms_rows(self) -> None:
        rows = terms_rows(resolution="SingleBest", mode="Open", deadline_blocks=100, block_wait=10, reward="1 AGLS")
        assert rows == [
            ("Payout", "single best"),
            ("Access", "open"),
            ("Floors", "none"),
            ("Deadline", "100 blocks"),
            ("Block wait", "10 blocks"),
            ("Reward", "1 AGLS"),
        ]


class TestDeprecations:
    def test_order_status_does_not_warn(self, monkeypatch, recwarn) -> None:
        solver, _ = _ready(monkeypatch)
        solver.create_order(_model()).submit().status()
        assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]

    def test_receipt_is_exported(self) -> None:
        import xqsa
        import xqsa.quip

        assert xqsa.JobOrderReceipt is xqsa.quip.JobOrderReceipt is JobOrderReceipt
