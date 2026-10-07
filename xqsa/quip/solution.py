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
:class:`Solution`, one answer to a Quip order decoded onto the model it was built from.

A receipt holds an order's answers as spin vectors over the placed problem's
chain nodes. Decoding them needs the model and the job that placed it, which
only a :class:`~xqsa.quip.JobOrder` (or a :meth:`SolverQuip.solve
<xqsa.quip.SolverQuip.solve>` call) holds. :func:`decode_answers` decodes every
stored vector; :func:`pick_best` picks the one the chain pays for: the best
vector of the solver the chain ranked first, or the best vector stored when
the leader's cannot be trusted.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from xqsa.quip.codec import decode_solution, ising_energy_milli
from xqsa.quip.errors import QuipJobFailedError
from xqvm_py.xqmx import XQMX, compute_energy

if TYPE_CHECKING:
    from xqsa.quip.codec import IsingJob
    from xqsa.quip.receipt import JobOrderReceipt

logger = logging.getLogger("xqsa.quip")


@dataclass(frozen=True)
class Solution:
    """One stored spin vector, decoded onto the model the order was built from.

    ``energy`` is recomputed on the original model, so it is authoritative;
    ``energy_milli`` is the vector's energy on the chain's milli scale, over
    the coefficients the order carried. ``solver`` is the account that
    answered with it, and ``leader`` is true when the chain ranks that
    account first: the front runner of a ``SingleBest`` order, or a top-N
    order's first-ranked solver.
    """

    sample: XQMX
    energy: int
    energy_milli: int
    solver: Any
    leader: bool


class Pick(NamedTuple):
    """What :func:`pick_best` chose, and how it squares with the chain.

    ``energy_milli`` is the energy the chain holds: the leader's ranked
    energy when the leader was read, else the chosen solver's stored best.
    ``energy_matches_chain`` is whether any stored vector of that solver
    recomputes to it; when none of the leader's does, ``solution`` is the
    best vector any solver stored. ``leader_read`` is false when the ranking could
    not be read or named no answer. ``num_solutions`` counts the chosen
    solver's stored vectors.
    """

    solution: Solution
    energy_milli: int
    energy_matches_chain: bool
    leader_read: bool
    num_submissions: int
    num_solutions: int


def decode_answers(receipt: JobOrderReceipt, job: IsingJob, model: XQMX, solver: Any = None) -> list[Solution]:
    """Decode every stored answer to ``receipt``'s order, best first.

    Ordered by energy recomputed on ``model``; on a tie the leader's vectors
    come first, then storage order. With ``solver``, decodes that solver's
    vectors alone.

    Raises:
        KeyError: if ``solver`` did not answer the order.
        QuipJobFailedError: if a stored answer lacks a field this client reads.
    """
    answers = _answers(receipt)
    if solver is not None:
        if solver not in answers:
            raise KeyError(f"{solver} did not answer order {receipt.order_id}")
        answers = {solver: answers[solver]}
    ranked = _ranked(receipt)
    return _decode_all(job, model, answers, None if ranked is None else ranked["solver"])


def pick_best(receipt: JobOrderReceipt, job: IsingJob, model: XQMX) -> Pick:
    """Pick the answer the chain pays for: the leader's best vector.

    The leader is the solver the chain ranks first, and its best vector is
    the one with the lowest energy recomputed on ``model``. The pick falls
    back to the best vector any solver stored, by recomputed energy with the
    leader first on ties, in two cases:

    - the ranking cannot be read, or names a solver with no stored answer
      (``leader_read`` is false);
    - none of the leader's stored vectors recomputes to the energy the
      chain ranked it at (``energy_matches_chain`` is false, and both
      energies are logged). The chain can rank a solver by a vector it no longer stores:
      a worse resubmission overwrites the stored answer (QUI-1408), and an
      order's ``min_solutions`` subset can leave the best vector out
      (QUI-1675).

    Raises:
        QuipJobFailedError: if the order has no answers, its answers carry no
            vectors, or a stored answer lacks a field this client reads.
    """
    order_id = receipt.order_id
    answers = _answers(receipt)
    if not answers:
        raise QuipJobFailedError(
            order_id, f"order {order_id} has no answers; once it is final, reclaim its reward with order.reclaim()"
        )
    ranked = _ranked(receipt)
    leader = ranked["solver"] if ranked is not None and ranked["solver"] in answers else None
    decoded = _decode_all(job, model, answers, leader)
    if not decoded:
        raise QuipJobFailedError(order_id, f"order {order_id}'s answers carried no solution vectors")
    if leader is None:
        best = decoded[0]
        chain_milli = answers[best.solver]["energy_milli"]
    else:
        best = next((solution for solution in decoded if solution.leader), decoded[0])
        chain_milli = int(ranked["energy_milli"])
    # The chain ranks by milli energy on the rounded coefficients, and the
    # model energy can order a rounded job's vectors differently, so the
    # check is whether any of the solver's vectors reaches the chain's energy.
    reached = min(solution.energy_milli for solution in decoded if solution.solver == best.solver)
    matches = reached == chain_milli
    if not matches:
        logger.warning(
            "order %d: %s's vectors reach %d milli, but the chain holds %d; returning %s's best vector",
            order_id,
            best.solver,
            reached,
            chain_milli,
            decoded[0].solver,
        )
        best = decoded[0]
    num_solutions = sum(solution.solver == best.solver for solution in decoded)
    return Pick(best, chain_milli, matches, leader is not None, len(answers), num_solutions)


def _answers(receipt: JobOrderReceipt) -> dict[Any, Any]:
    """Read every stored answer, turning an absent pallet field into a typed error."""
    try:
        return receipt.raw_solutions()
    except KeyError as exc:
        raise QuipJobFailedError(
            receipt.order_id,
            f"solution field {exc} is absent; the pre-release pallet field layout may have changed",
        ) from exc


def _ranked(receipt: JobOrderReceipt) -> Mapping[str, Any] | None:
    """Return the chain's first-ranked ``{"solver", "energy_milli"}``, or ``None`` if unranked or unreadable."""
    try:
        ranking = receipt._ranking()
    except Exception as exc:  # noqa: BLE001 -- an unreadable ranking falls back to the best stored vector.
        logger.warning("could not read the ranking of order %d: %s", receipt.order_id, exc)
        return None
    return ranking[0] if ranking else None


def _decode_all(job: IsingJob, model: XQMX, answers: Mapping[Any, Any], leader: Any) -> list[Solution]:
    """Decode every vector in ``answers``, lowest recomputed energy first, the leader's first on ties."""
    decoded = [
        solution
        for name, answer in answers.items()
        for solution in _decode(job, model, name, answer["spins"], leader=name == leader)
    ]
    return sorted(decoded, key=lambda solution: (solution.energy, not solution.leader))


def _decode(job: IsingJob, model: XQMX, solver: Any, vectors: list[list[int]], *, leader: bool) -> list[Solution]:
    """Decode one solver's vectors, lowest recomputed energy first (storage order on ties)."""
    solutions = []
    for vector in vectors:
        sample = decode_solution(job, vector, model)
        solutions.append(
            Solution(
                sample=sample,
                energy=int(compute_energy(model, sample)),
                energy_milli=ising_energy_milli(job, vector),
                solver=solver,
                leader=leader,
            )
        )
    return sorted(solutions, key=lambda solution: solution.energy)
