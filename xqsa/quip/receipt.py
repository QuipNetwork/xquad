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

"""
:class:`JobOrderReceipt`, the chain-side handle of a submitted Quip order.

A receipt is built from an order id alone, so any session can rebuild it with
:meth:`SolverQuip.get_receipt <xqsa.quip.SolverQuip.get_receipt>` and follow
the order without the model it was built from. :meth:`JobOrder.submit
<xqsa.quip.JobOrder.submit>` creates one for the order it proposes. Every
method reads the chain when called; constructing a receipt reads nothing.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self

from xqsa.quip import chain
from xqsa.quip.chain import MEMPOOL_PALLET, RECLAIM_ORDER_CALL, _status_str
from xqsa.quip.codec import _TERMINAL_STATUSES, ORDER_STATUS_CLOSED
from xqsa.quip.errors import QuipSubmissionError, QuipTimeoutError

if TYPE_CHECKING:
    from xqsa.quip.client import SolverQuip

_SINGLE_BEST = "SingleBest"


class JobOrderReceipt:
    """The chain-side handle of one submitted order: status, answers and settlement.

    Built by :meth:`JobOrder.submit <xqsa.quip.JobOrder.submit>` or
    :meth:`SolverQuip.get_receipt <xqsa.quip.SolverQuip.get_receipt>`, never
    directly. It holds the order id and the genesis hash of the chain the
    order lives on, and reads everything else from that chain when asked, so
    a receipt rebuilt in another session reports the same as the original.

    Examples:
        Connects to the network, so it is not run as a doctest::

            receipt = solver.get_receipt(42)
            receipt.wait()
            receipt.solvers()
            receipt.settlement()
            receipt.reclaim()  # only for a final order nobody answered.
    """

    def __init__(self, client: SolverQuip, order_id: int, genesis_hash: str) -> None:
        self._client = client
        self._order_id = order_id
        # The chain this order belongs to; checked by the first client method
        # that accepts a receipt (QUI-1609). Never displayed.
        self._genesis_hash = genesis_hash
        # Set once a read shows the order final; a later read from a lagging node must not reopen it.
        self._final_seen = False

    @property
    def order_id(self) -> int:
        """The chain's order id."""
        return self._order_id

    def status(self) -> dict[str, Any]:
        """Return the order's lifecycle as the chain reports it now.

        Keys: ``state`` (``submitted``, or ``finalized`` once the chain closes
        the order; once seen final it stays final, while the other keys are
        as read), ``order_id``, ``chain_status`` (the chain's
        ``OrderStatus``), the order's block heights under the pallet's names
        (``created_at``, ``first_solution_at``, ``effective_expiry``),
        ``current_block`` and ``solution_count``.

        Raises:
            QuipConnectionError: if the order or the head block cannot be read.
        """
        order = self._fetch_order()
        current_block = self._client._current_block()
        lifecycle = self._client._order_lifecycle(order, current_block)
        self._final_seen = self._final_seen or lifecycle["is_final"]
        return {
            "state": "finalized" if self._final_seen else "submitted",
            "order_id": self._order_id,
            "chain_status": lifecycle["status"],
            "created_at": lifecycle["created_at"],
            "first_solution_at": lifecycle["first_solution_at"],
            "effective_expiry": lifecycle["effective_expiry"],
            "current_block": current_block,
            "solution_count": _solution_count(order),
        }

    def wait(self) -> Self:
        """Block until the order is final, polling at the client's ``poll_interval``.

        An order is final ``block_wait`` blocks after its first accepted
        answer, or at its deadline if nobody answers. The chain flips an
        expired order's status lazily, so finality is decided by block height
        and never by waiting for the status to change. Waiting never reclaims.

        Raises:
            QuipTimeoutError: if the order is not final within the client's
                ``timeout``; it carries this receipt.
            QuipConnectionError: if the order or the head block cannot be read.
        """
        deadline = time.monotonic() + self._client._timeout
        while True:
            order = self._fetch_order()
            # A terminal status is final whatever the height, so skip the head read.
            if self._final_seen or _status_str(order["status"]) in _TERMINAL_STATUSES:
                self._final_seen = True
                return self
            if self._client._order_lifecycle(order, self._client._current_block())["is_final"]:
                self._final_seen = True
                return self
            if time.monotonic() >= deadline:
                raise QuipTimeoutError(self._order_id, receipt=self)
            time.sleep(self._client._poll_interval)

    def solvers(self) -> list[dict[str, Any]]:
        """Return who answered, in the chain's ranking order.

        Each entry is ``{"solver", "energy_milli", "submitted_at",
        "num_solutions"}``, with the solver's best energy in milli Ising units
        as the chain computed it. The ranked answers come first: the front
        runner of a ``SingleBest`` order, or a top-N order's ranked solvers.
        The unranked answers follow, lowest energy first, then earliest.
        """
        answers = {answer["solver"]: answer for answer in self._client._fetch_solutions(self._order_id)}
        ranked = [entry["solver"] for entry in self._ranking() if entry["solver"] in answers]
        rest = sorted(
            (answer for solver, answer in answers.items() if solver not in ranked),
            key=lambda answer: (int(answer["best_energy_milli"]), answer.get("submitted_at") or 0),
        )
        return [_solver_entry(answer) for answer in [answers[solver] for solver in ranked] + rest]

    def raw_solutions(self, solver: Any = None) -> dict[Any, Any]:
        """Return the spin vectors each solver answered with, as the chain stores them.

        Maps each solver to ``{"energy_milli", "spins"}``, where ``spins`` is a
        list of spin vectors indexed by the placed problem's chain nodes. They
        are not decoded onto the model's variables. With ``solver``, returns
        that solver's entry alone.

        Raises:
            KeyError: if ``solver`` did not answer this order.
        """
        solutions = {
            answer["solver"]: {
                "energy_milli": int(answer["best_energy_milli"]),
                "spins": [[int(spin) for spin in vector] for vector in answer["solutions"]],
            }
            for answer in self._client._fetch_solutions(self._order_id)
        }
        if solver is None:
            return solutions
        if solver not in solutions:
            raise KeyError(f"{solver} did not answer order {self._order_id}")
        return solutions[solver]

    def settlement(self) -> dict[str, Any]:
        """Return who the reward goes to and whether it was claimed.

        ``{"claimed": bool, "winners": [{"solver", "energy_milli",
        "allocation_planck"}]}``. A ``SingleBest`` order's front runner takes
        the whole reward. The chain stores no allocations; it computes them
        when the winner claims, which closes the order, so ``claimed`` means
        the order is closed with answers. An unanswered order has no winners.

        Raises:
            NotImplementedError: for a top-N order, whose split lands with
                QUI-1606.
        """
        order = self._fetch_order()
        resolution = _status_str(order["resolution"])
        if resolution != _SINGLE_BEST:
            raise NotImplementedError(f"settlement of a {resolution} order is not supported yet (QUI-1606)")
        leader = chain.front_runner(self._client._iface, self._order_id)
        winners = (
            []
            if leader is None
            else [
                {
                    "solver": leader["solver"],
                    "energy_milli": int(leader["energy_milli"]),
                    "allocation_planck": int(order["reward"]),
                }
            ]
        )
        claimed = _status_str(order["status"]) == ORDER_STATUS_CLOSED and _solution_count(order) > 0
        return {"claimed": claimed, "winners": winners}

    def reclaim(self) -> int:
        """Refund the reward of a final order nobody answered, and return it in planck.

        Only the proposer can reclaim, and only once the order is final with
        no answers. This checks those conditions against the latest block,
        in the order the chain does, and raises before anything is signed,
        so a refused reclaim costs no fee. An answer that lands between the
        check and inclusion still fails on chain, which stays the authority.
        Nothing reclaims on its own.

        Raises:
            QuipSubmissionError: if this account did not propose the order,
                the order is already closed, it is still open, or it was
                answered; or if the chain refuses the reclaim.
            QuipConnectionError: if the order or the head block cannot be read.
            QuipUnconfirmedError: if the reclaim was sent but its outcome is
                unknown.
        """
        client = self._client
        order_id = self._order_id
        # The head first: the order read after it then reflects every answer
        # up to that block, so a reclaim the chain would refuse is caught here.
        current_block = client._current_block()
        order = self._fetch_order()
        lifecycle = client._order_lifecycle(order, current_block)

        proposer = chain.account_bytes(order["proposer"])
        if proposer != bytes(client._signer.account_id):
            proposed_by = chain.ss58_address(client._iface, proposer)
            raise QuipSubmissionError(f"order {order_id} was proposed by {proposed_by}, not by this account")
        if lifecycle["status"] == ORDER_STATUS_CLOSED:
            raise QuipSubmissionError(f"order {order_id} is already closed (reclaimed or settled)")
        expiry = lifecycle["effective_expiry"]
        # The reclaim executes in a later block than the head read here, so an
        # order one block short of expiry is already reclaimable on chain.
        if not (lifecycle["is_final"] or self._final_seen) and current_block + 1 < expiry:
            about = _minutes_until(expiry - current_block - 1, chain.block_time_ms(client._iface))
            raise QuipSubmissionError(f"order {order_id} is open until block {expiry}{about}; reclaim after it closes")
        answers = _solution_count(order)
        if answers:
            solvers = "solver" if answers == 1 else "solvers"
            raise QuipSubmissionError(
                f"order {order_id} was answered by {answers} {solvers}; its reward awaits the winner's claim "
                "and cannot be reclaimed"
            )

        client._submit_extrinsic(MEMPOOL_PALLET, RECLAIM_ORDER_CALL, {"order_id": order_id})
        return int(order["reward"])

    def _fetch_order(self) -> Mapping[str, Any]:
        return self._client._fetch_order(self._order_id)

    def _ranking(self) -> list[Mapping[str, Any]]:
        """Return the chain's ranked ``{"solver", "energy_milli"}`` entries, best first."""
        iface = self._client._iface
        leader = chain.front_runner(iface, self._order_id)
        return [leader] if leader is not None else chain.top_solvers(iface, self._order_id)

    def __repr__(self) -> str:
        return f"JobOrderReceipt(order_id={self._order_id})"


def _minutes_until(blocks: int, block_time_ms: int | None) -> str:
    """Render ``blocks`` as " (about M min)", or nothing when the block time is unknown."""
    if not block_time_ms:
        return ""
    return f" (about {max(1, math.ceil(blocks * block_time_ms / 60_000))} min)"


def _solution_count(order: Mapping[str, Any]) -> int:
    return int(order.get("solution_count", 0) or 0)


def _solver_entry(answer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "solver": answer["solver"],
        "energy_milli": int(answer["best_energy_milli"]),
        "submitted_at": answer.get("submitted_at"),
        "num_solutions": len(answer["solutions"]),
    }
