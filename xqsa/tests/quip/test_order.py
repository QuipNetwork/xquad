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

"""JobOrder: option validation, the draft lifecycle, the nonce check and the displays."""

from __future__ import annotations

import warnings
from dataclasses import replace

import pytest

from xqsa.quip import QuipCancelledError, QuipOrderOptionError, QuipSubmissionError, QuipUnconfirmedError
from xqsa.quip.chain import ChainLimits
from xqsa.quip.order import JobOrder, _OrderOptions, check_client_defaults, merge_options
from xqsa.solver import SolverResult

from .test_client import (
    GENESIS_HASH,
    TOPO_HASH,
    UNIT,
    _make_solver,
    _model,
    _ok_receipt,
    _patch_signing,
    _solve_ready,
)

LIMITS = ChainLimits(min_reward=UNIT, max_deadline_blocks=1000, max_block_wait=100, max_solutions=20)
NO_LIMITS = ChainLimits(min_reward=None, max_deadline_blocks=None, max_block_wait=None, max_solutions=None)
BASE = _OrderOptions(
    reward=UNIT,
    deadline_blocks=100,
    block_wait=10,
    topology=None,
    mapping=None,
    mode="Open",
    resolution="SingleBest",
    delivery="OnChainOnly",
)


def replace_base(**changes) -> _OrderOptions:
    return replace(BASE, **changes)


def _merge(base: _OrderOptions = BASE, limits: ChainLimits = LIMITS, *, strict: bool = True, **updates):
    return merge_options(base, updates, limits, strict=strict)


def _quiet_merge(**updates):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return _merge(**updates)


class TestOrderOptionLimits:
    def test_defaults_pass_without_warnings(self) -> None:
        assert _quiet_merge() == BASE

    def test_update_applies(self) -> None:
        assert _quiet_merge(reward=2 * UNIT, deadline_blocks=500, block_wait=0).deadline_blocks == 500

    def test_reward_below_min_refused(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="MinReward") as info:
            _merge(reward=UNIT - 1)
        assert info.value.option == "reward"
        assert isinstance(info.value, QuipSubmissionError)

    def test_reward_has_no_upper_bound(self) -> None:
        assert _quiet_merge(reward=10**30).reward == 10**30

    @pytest.mark.parametrize("deadline", [0, 9, 1001])
    def test_deadline_out_of_range_refused(self, deadline) -> None:
        with pytest.raises(QuipOrderOptionError, match="deadline_blocks") as info:
            _merge(deadline_blocks=deadline, block_wait=0)
        assert info.value.option == "deadline_blocks"

    @pytest.mark.parametrize("deadline", [10, 99])
    def test_short_deadline_warns(self, deadline) -> None:
        with pytest.warns(UserWarning, match="often go unanswered"):
            _merge(deadline_blocks=deadline, block_wait=0)

    def test_deadline_at_max_passes(self) -> None:
        assert _quiet_merge(deadline_blocks=1000).deadline_blocks == 1000

    @pytest.mark.parametrize("wait", [-1, 101])
    def test_block_wait_out_of_range_refused(self, wait) -> None:
        with pytest.raises(QuipOrderOptionError, match="block_wait") as info:
            _merge(block_wait=wait)
        assert info.value.option == "block_wait"

    def test_block_wait_not_below_deadline_warns(self) -> None:
        with pytest.warns(UserWarning, match="not below deadline_blocks"):
            _merge(deadline_blocks=100, block_wait=100)

    def test_whole_configuration_rechecked(self) -> None:
        # Lowering the deadline alone conflicts with the existing block wait.
        current = _quiet_merge(deadline_blocks=300, block_wait=100)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _merge(current, deadline_blocks=100)
        assert [str(w.message) for w in caught] == [
            "block_wait=100 is not below deadline_blocks=100; the deadline ends the order "
            "before the wait for better answers can"
        ]

    def test_absent_limits_not_checked(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            merged = _merge(limits=NO_LIMITS, reward=1, deadline_blocks=5000, block_wait=5000)
        assert merged.deadline_blocks == 5000

    def test_absent_limits_keep_fixed_floors(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="at least 10"):
            _merge(limits=NO_LIMITS, deadline_blocks=5)

    @pytest.mark.parametrize("name", ["reward", "deadline_blocks", "block_wait"])
    @pytest.mark.parametrize("value", ["100", 1.5, True])
    def test_non_int_refused(self, name, value) -> None:
        with pytest.raises(TypeError, match=f"{name} must be an int"):
            _merge(**{name: value})


class TestOrderOptionStrictness:
    def test_unknown_option_strict_raises(self) -> None:
        with pytest.raises(TypeError, match="unknown order option 'colour'"):
            _merge(colour="red")

    def test_client_default_is_not_an_order_option(self) -> None:
        with pytest.raises(TypeError, match="unknown order option 'mode'"):
            _merge(mode="Open")

    def test_raw_dict_strict_raises(self) -> None:
        with pytest.raises(TypeError, match="raw chain values"):
            _merge(replace_base(mode={"Bid": 5}))

    def test_mapping_dict_is_not_a_raw_value(self) -> None:
        assert _quiet_merge(mapping={0: 1}).mapping == {0: 1}

    def test_unknown_option_lenient_warns_and_drops(self) -> None:
        with pytest.warns(DeprecationWarning, match="colour"):
            assert _merge(strict=False, colour="red") == BASE

    def test_raw_dict_lenient_warns_and_passes(self) -> None:
        with pytest.warns(DeprecationWarning, match="raw chain value"):
            merged = _merge(replace_base(resolution={"TopNEqual": 3}), strict=False)
        assert merged.resolution == {"TopNEqual": 3}


class TestClientDefaults:
    def test_supported_defaults_pass(self) -> None:
        check_client_defaults("Open", "SingleBest", "OnChainOnly")

    def test_mode_refused(self) -> None:
        with pytest.raises(ValueError, match="QUI-1607"):
            check_client_defaults("Whitelist", "SingleBest", "OnChainOnly")

    def test_resolution_refused(self) -> None:
        with pytest.raises(ValueError, match="QUI-1606"):
            check_client_defaults("Open", "TopNEqual", "OnChainOnly")

    @pytest.mark.parametrize("delivery", ["ResultReady", {"Callback": {}}])
    def test_delivery_refused(self, delivery) -> None:
        with pytest.raises(ValueError, match="result delivery is not supported yet"):
            check_client_defaults("Open", "SingleBest", delivery)

    def test_raw_dicts_pass_for_the_lenient_path(self) -> None:
        check_client_defaults({"Bid": 5}, {"TopNEqual": 3}, "OnChainOnly")

    def test_constructor_checks_before_connecting(self, monkeypatch) -> None:
        from xqsa.tests.quip.test_client import FakeSubstrate

        class _Unreachable(FakeSubstrate):
            def init_runtime(self, *args, **kwargs):
                raise AssertionError("must not connect")

        with pytest.raises(ValueError, match="QUI-1607"):
            _make_solver(monkeypatch, iface=_Unreachable(), mode="Whitelist")


def _ready(monkeypatch, **solver_kwargs):
    """A funded solver with signing patched: ``(solver, captured)``."""
    solver = _solve_ready(monkeypatch, **solver_kwargs)
    return solver, _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))


def _nonces(monkeypatch, solver, values: list[int]) -> list[int]:
    """Make the account nonce read ``values`` in turn; returns the list of reads made."""
    reads: list[int] = []
    pending = iter(values)

    def read(account_address: str) -> int:
        reads.append(value := next(pending))
        return value

    monkeypatch.setattr(solver._iface, "get_account_nonce", read)
    return reads


class TestJobOrderLifecycle:
    def test_create_order_returns_a_draft(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        assert isinstance(order, JobOrder)
        assert order.order_id() is None
        assert order.status() == {"state": "draft", "options": order.options()}

    def test_order_inherits_client_defaults(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch, reward=2 * UNIT, deadline_blocks=200, block_wait=20)
        assert solver.create_order(_model()).options() == {
            "reward": 2 * UNIT,
            "deadline_blocks": 200,
            "block_wait": 20,
            "topology": TOPO_HASH,
            "mapping": None,
        }

    def test_create_order_overrides_defaults(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), reward=3 * UNIT, mapping={0: 0, 1: 1})
        assert (order.reward, order.deadline_blocks, order.block_wait) == (3 * UNIT, 100, 10)
        assert order.mapping == {0: 0, 1: 1}

    def test_genesis_hash_stamped(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        assert solver.create_order(_model())._genesis_hash == GENESIS_HASH

    def test_submit_moves_to_submitted(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        assert order.submit() is order
        assert order.order_id() == 1
        assert order._included_block == solver._iface.head
        assert captured["submitted_wire"] == b"\x00\x01"

    @pytest.mark.parametrize("action", ["set", "quote", "submit"])
    def test_everything_but_status_refused_after_submit(self, monkeypatch, action) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        call = {"set": lambda: order.set(reward=2 * UNIT), "quote": order.quote, "submit": order.submit}[action]
        with pytest.raises(QuipSubmissionError, match=f"order 1 is submitted; {action}\\(\\) works only on a draft"):
            call()

    def test_status_after_submit_reaches_finalized(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        solver._iface.head = 10_000  # past the order's effective expiry.
        assert order.status() == {
            "state": "finalized",
            "order_id": 1,
            "chain_status": "Opened",
            "created_at": 0,
            "first_solution_at": None,
            "effective_expiry": 100,
            "current_block": 10_000,
            "solution_count": 1,
        }
        with pytest.raises(QuipSubmissionError, match="is finalized"):
            order.set(reward=2 * UNIT)

    def test_status_while_open_stays_submitted(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        solver._iface.head = 1  # well before expiry.
        assert order.status()["state"] == "submitted"

    def test_declined_gate_leaves_a_draft_and_signs_nothing(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch, autoconfirm=lambda quote: False)
        order = solver.create_order(_model())
        with pytest.raises(QuipCancelledError):
            order.submit()
        assert order.status()["state"] == "draft"
        assert order._signed is None  # dropped: a retry signs anew.
        assert "wait_for" not in captured


class TestJobOrderSet:
    def test_set_applies_and_returns_the_order(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        assert order.set(deadline_blocks=300, block_wait=0) is order
        assert (order.deadline_blocks, order.block_wait) == (300, 0)

    def test_set_refusal_keeps_the_old_options(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        with pytest.raises(QuipOrderOptionError, match="deadline_blocks"):
            order.set(deadline_blocks=5)
        assert order.deadline_blocks == 100

    def test_set_rechecks_against_existing_values(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), deadline_blocks=300, block_wait=100)
        with pytest.warns(UserWarning, match="block_wait=100 is not below deadline_blocks=100") as record:
            order.set(deadline_blocks=100)
        assert record[0].filename == __file__  # the warning points at the caller.

    def test_set_unknown_option_raises(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.raises(TypeError, match="unknown order option 'mode'"):
            solver.create_order(_model()).set(mode="Open")

    def test_set_topology_replaces_the_placement(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.set(topology="NATIVE")
        assert order.topology == "native"
        assert order._job.topology.num_nodes == 2

    def test_set_only_replaces_when_placement_changes(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        job = order._job
        order.set(reward=2 * UNIT, topology=TOPO_HASH.upper().replace("0X", "0x"))
        assert order._job is job

    def test_set_rejects_a_malformed_topology(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.raises(ValueError, match="topology"):
            solver.create_order(_model()).set(topology="0x1234")

    def test_native_with_mapping_refused(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.raises(ValueError, match="mapping"):
            solver.create_order(_model(), topology="native", mapping={0: 0, 1: 1})


class TestJobOrderQuoteAndSubmit:
    def test_each_quote_reprices_the_same_signed_bytes(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        first = order.quote()
        solver._iface.storage[("System", "Account")] = {"data": {"free": 3 * UNIT}}
        second = order.quote()
        assert second is not first
        assert (first.balance_planck, second.balance_planck) == (10 * UNIT, 3 * UNIT)
        assert captured["builds"] == 1  # signed once, priced twice.
        priced = [params for method, params in solver._iface.rpc_calls if method == "payment_queryInfo"]
        assert priced == [priced[0], priced[0]]

    def test_set_drops_the_signed_bytes(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        order.set(reward=2 * UNIT)
        assert order._signed is None
        assert order.quote().reward_planck == 2 * UNIT
        assert captured["builds"] == 2
        assert captured["params"][1]["reward"] == 2 * UNIT

    def test_submit_sends_the_quoted_bytes(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver._iface.nonce = 4
        order = solver.create_order(_model(), reward=2 * UNIT)
        order.quote()
        order.submit()
        assert captured["builds"] == 1
        assert captured["nonces"] == [4]
        assert captured["sent"] == captured["wires"]
        assert captured["params"][0]["reward"] == 2 * UNIT
        assert order._signed is None

    def test_submit_after_quote_rereads_the_balance(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        assert order.quote().affordable
        solver._iface.storage[("System", "Account")] = {"data": {"free": 0}}  # spent elsewhere since.
        with pytest.raises(QuipSubmissionError, match="insufficient balance"):
            order.submit()
        assert order.quote().balance_planck == 0
        assert "wait_for" not in captured

    def test_failed_submit_drops_the_signed_bytes(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        solver._iface.storage[("System", "Account")] = {"data": {"free": 0}}
        with pytest.raises(QuipSubmissionError, match="insufficient balance"):
            order.submit()
        assert order._signed is None
        solver._iface.storage[("System", "Account")] = {"data": {"free": 10 * UNIT}}
        order.submit()
        assert captured["builds"] == 2  # the retry signed anew.
        assert captured["sent"] == [captured["wires"][1]]

    def test_submit_without_quote_signs_once(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver.create_order(_model()).submit()
        assert captured["builds"] == 1  # signed, priced, then sent as is.
        assert captured["sent"] == captured["wires"]

    def test_two_drafts_each_sign_with_the_nonce_current_at_submit(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        iface = solver._iface
        send = solver._quip_signing.submit_and_watch

        def send_and_bump(*args, **kwargs):
            iface.nonce += 1  # the node now counts this transaction.
            return send(*args, **kwargs)

        monkeypatch.setattr(solver._quip_signing, "submit_and_watch", send_and_bump)
        first, second = solver.create_order(_model()), solver.create_order(_model(), reward=2 * UNIT)
        first.quote()
        second.quote()
        first.submit()
        second.submit()
        # Both quoted at nonce 0; the second re-signs at 1 once the first is sent.
        assert captured["nonces"] == [0, 0, 1]
        assert captured["sent"] == [captured["wires"][0], captured["wires"][2]]


class TestJobOrderNonceCheck:
    def test_unchanged_nonce_sends_after_one_signature(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver._iface.nonce = 5
        order = solver.create_order(_model())
        order.quote()
        reads = _nonces(monkeypatch, solver, [5])
        order.submit()
        assert reads == [5]
        assert captured["nonces"] == [5]
        assert captured["sent"] == captured["wires"]

    def test_nonce_moving_once_signs_again(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver._iface.nonce = 5
        order = solver.create_order(_model())
        order.quote()
        _nonces(monkeypatch, solver, [6, 6])
        order.submit()
        assert captured["nonces"] == [5, 6]
        assert captured["sent"] == [captured["wires"][1]]
        assert order.order_id() == 1

    def test_nonce_moving_twice_raises_without_sending(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver._iface.nonce = 5
        order = solver.create_order(_model())
        order.quote()
        _nonces(monkeypatch, solver, [6, 7])
        with pytest.raises(QuipSubmissionError, match="nonce moved twice"):
            order.submit()
        assert captured["nonces"] == [5, 6]
        assert "wait_for" not in captured  # submit_and_watch never called
        assert order.status()["state"] == "draft"
        assert order._signed is None

    def test_nonce_read_fault_raises_connection_error(self, monkeypatch) -> None:
        from xqsa.quip import QuipConnectionError

        solver, _ = _ready(monkeypatch)

        def boom(account_address):
            raise RuntimeError("socket closed")

        monkeypatch.setattr(solver._iface, "get_account_nonce", boom)
        with pytest.raises(QuipConnectionError, match="account nonce.*socket closed"):
            solver.create_order(_model()).submit()


def _send_raises(monkeypatch, solver, error: Exception, captured: dict) -> None:
    """Make the node's watch raise ``error`` after the extrinsic is handed over."""

    def send(iface, wire_bytes, ext_hash, wait_for="inblock"):
        captured.setdefault("sent", []).append(wire_bytes)
        raise error

    monkeypatch.setattr(solver._quip_signing, "submit_and_watch", send)


class TestJobOrderSendOutcomes:
    @pytest.mark.parametrize("status", ["invalid", "dropped", "usurped"])
    def test_certain_rejection_stays_a_draft(self, monkeypatch, status) -> None:
        solver, captured = _ready(monkeypatch)
        rejected = solver._quip_signing.QuipSigningError(f"transaction pool rejected the extrinsic: {status}")
        _send_raises(monkeypatch, solver, rejected, captured)
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError, match=status) as info:
            order.submit()
        assert not isinstance(info.value, QuipUnconfirmedError)
        assert order.status()["state"] == "draft"

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param("retracted", id="retracted"),
            pytest.param("finalityTimeout", id="finality-timeout"),
            pytest.param(ConnectionError("websocket closed"), id="transport"),
        ],
    )
    def test_uncertain_send_is_unconfirmed(self, monkeypatch, error) -> None:
        solver, captured = _ready(monkeypatch)
        if isinstance(error, str):
            error = solver._quip_signing.SendOutcomeUnknown(f"transaction pool rejected the extrinsic: {error}")
        _send_raises(monkeypatch, solver, error, captured)
        order = solver.create_order(_model())
        with pytest.raises(QuipUnconfirmedError, match="Do not resubmit") as info:
            order.submit()
        assert info.value.extrinsic_hash == "0xext"
        assert order.status() == {
            "state": "unconfirmed",
            "extrinsic_hash": "0xext",
            "block_hash": None,
            "included_block": None,
        }
        for call in (order.submit, order.quote, lambda: order.set(reward=2 * UNIT)):
            with pytest.raises(QuipSubmissionError, match="is unconfirmed"):
                call()
        assert len(captured["sent"]) == 1  # never sent again.

    def test_unverified_dispatch_is_unconfirmed(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        unverified = _ok_receipt(solver, error="unclassified: get_block failed for 0xblock: timeout")
        _patch_signing(monkeypatch, solver, receipt=unverified)
        order = solver.create_order(_model())
        with pytest.raises(QuipUnconfirmedError, match="unclassified"):
            order.submit()
        assert order.status()["block_hash"] == "0xblock"
        assert order.status()["included_block"] == solver._iface.head

    def test_unreadable_order_id_is_unconfirmed(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        solver._iface.events = []  # included, but no JobProposed event.
        order = solver.create_order(_model())
        with pytest.raises(QuipUnconfirmedError, match="order id could not be read"):
            order.submit()
        assert order.order_id() is None
        assert order.status()["state"] == "unconfirmed"
        assert order.status()["block_hash"] == "0xblock"

    def test_dispatch_failure_is_failed(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="QuantumComputeMempool.RewardTooLow"))
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError, match="fee was paid") as info:
            order.submit()
        assert not isinstance(info.value, QuipUnconfirmedError)
        assert order.status() == {
            "state": "failed",
            "extrinsic_hash": "0xext",
            "block_hash": "0xblock",
            "included_block": solver._iface.head,
            "error": "QuantumComputeMempool.RewardTooLow",
        }
        with pytest.raises(QuipSubmissionError, match="is failed"):
            order.submit()

    def test_solve_raises_unconfirmed(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        _send_raises(monkeypatch, solver, ConnectionError("websocket closed"), captured)
        with pytest.raises(QuipUnconfirmedError):
            solver.solve(_model())

    def test_receipt_rows(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        _send_raises(monkeypatch, solver, ConnectionError("websocket closed"), captured)
        unconfirmed = solver.create_order(_model())
        with pytest.raises(QuipUnconfirmedError):
            unconfirmed.submit()
        text = str(unconfirmed)
        assert text.startswith("╭─ Job order (unconfirmed) ")
        assert "│ Receipt     unconfirmed, extrinsic " in text
        assert {len(line) for line in text.splitlines()} == {72}

        solver, _ = _ready(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="QuantumComputeMempool.RewardTooLow"))
        failed = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError):
            failed.submit()
        text = str(failed)
        assert text.startswith("╭─ Job order (failed) ")
        assert f"│ Receipt     failed in block {solver._iface.head}: QuantumComputeMempool." in text


class TestJobOrderSignedBytesStayPrivate:
    def test_copy_and_pickle_state_drop_the_signed_bytes(self, monkeypatch) -> None:
        import copy

        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        assert order._signed is not None
        assert order.__getstate__()["_signed"] is None
        assert copy.copy(order)._signed is None
        assert order._signed is not None  # the original keeps its own.

    def test_signed_bytes_never_rendered(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        wire = captured["wires"][0]
        assert "wire" not in repr(order._signed)
        for text in (str(order), repr(order), repr(order._signed)):
            assert "0x" + wire.hex() not in text and repr(wire) not in text


class TestSolverQuipCompatibility:
    def test_limits_read_once_per_client(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        reads: list[str] = []
        original = solver._iface.get_constant

        def counting(module, name):
            reads.append(name)
            return original(module, name)

        monkeypatch.setattr(solver._iface, "get_constant", counting)
        solver.create_order(_model()).quote()
        solver.create_order(_model(), deadline_blocks=200).quote()
        assert reads == []

    def test_create_order_is_strict(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.raises(TypeError, match="unknown order option 'colour'"):
            solver.create_order(_model(), colour="red")

    def test_create_order_refuses_a_raw_client_default(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch, mode={"Bid": 5})
        with pytest.raises(TypeError, match="mode: raw chain values"):
            solver.create_order(_model())

    def test_quote_shortcut_warns_and_prices(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.warns(DeprecationWarning, match="create_order") as record:
            quote = solver.quote(_model(), reward=2 * UNIT)
        assert record[0].filename == __file__
        assert quote.reward_planck == 2 * UNIT
        assert "wait_for" not in captured

    def test_solve_applies_known_options_per_call(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        assert isinstance(solver.solve(_model(), reward=2 * UNIT, deadline_blocks=200), SolverResult)
        assert (captured["call_params"]["reward"], captured["call_params"]["deadline_blocks"]) == (2 * UNIT, 200)
        assert solver._reward == UNIT  # the client default is untouched.

    def test_solve_warns_on_an_unknown_option_and_submits(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.warns(DeprecationWarning, match="ignoring unknown SolverQuip option 'colour'") as record:
            solver.solve(_model(), colour="red")
        assert record[0].filename == __file__
        assert "wait_for" in captured

    def test_solve_warns_on_a_raw_client_default_and_submits(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch, resolution={"TopNEqual": 3})
        with pytest.warns(DeprecationWarning, match="resolution: passing a raw chain value"):
            solver.solve(_model())
        assert captured["call_params"]["resolution"] == {"TopNEqual": 3}

    def test_solve_refuses_an_out_of_limit_option_before_signing(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.raises(QuipOrderOptionError, match="MinReward"):
            solver.solve(_model(), reward=1)
        assert captured["builds"] == 0


class TestJobOrderDisplay:
    def test_draft_box(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        text = str(solver.create_order(_model(), topology="native"))
        assert text == "\n".join(
            [
                "╭─ Job order (draft) ──────────────────────────────────────────────────╮",
                "│ Network     custom endpoint                                          │",
                "├──────────────────────────────────────────────────────────────────────┤",
                "│ Model       2 variables, 1 couplings, spin                           │",
                "│ Placed      native, 2 spins                                          │",
                "├──────────────────────────────────────────────────────────────────────┤",
                "│ Payout      single best                                              │",
                "│ Access      open                                                     │",
                "│ Floors      none                                                     │",
                "│ Deadline    100 blocks                                               │",
                "│ Block wait  10 blocks                                                │",
                "│ Reward      1.000000000000 AGLS                                      │",
                "├──────────────────────────────────────────────────────────────────────┤",
                "│ Quote       not quoted                                               │",
                "│ Receipt     not submitted                                            │",
                "╰──────────────────────────────────────────────────────────────────────╯",
            ]
        )

    def test_quoted_and_submitted_rows(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        assert "│ Quote       1.002182560255 AGLS total, affordable, block 200 " in str(order)
        order.submit()
        text = str(order)
        assert text.startswith("╭─ Job order (submitted) ")
        assert "│ Receipt     order 1, included in block 200 " in text
        assert {len(line) for line in text.splitlines()} == {72}

    def test_topology_placement_wraps_the_hash(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        lines = str(solver.create_order(_model())).splitlines()
        assert {len(line) for line in lines} == {72}
        assert any(line.startswith("│ Placed      topology, ") for line in lines)

    def test_quote_box_carries_a_full_ss58_address(self, monkeypatch) -> None:
        ss58_encode = pytest.importorskip("scalecodec.utils.ss58").ss58_encode

        solver, _ = _ready(monkeypatch)
        text = str(solver.create_order(_model()).quote())
        assert f"│ Account     {ss58_encode(bytes(solver._signer.account_id), 42)} " in text
        assert {len(line) for line in text.splitlines()} == {72}
