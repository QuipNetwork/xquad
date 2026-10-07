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

"""Tests for decoding an order's stored answers and picking the one the chain pays."""

from __future__ import annotations

import logging

import pytest

from xqsa.quip import QuipJobFailedError
from xqsa.quip.solution import decode_answers, pick_best

from .test_client import _job, _model, _spin_vector
from .test_receipt import TOP_N, _answer, _receipt_for, _receipt_order

# Energies of _model() by (s0, s1): (-1, 1) -> -4, (1, 1) -> 0, (1, -1) and (-1, -1) -> +2.
BEST, MID, HIGH, HIGH2 = (-1, 1), (1, 1), (1, -1), (-1, -1)


def _setup(monkeypatch, answers, **ranking):
    """A receipt for order 1 with ``answers`` as ``(solver, [(s0, s1), ...], energy_milli)``, plus its job."""
    receipt = _receipt_for(monkeypatch, answers=[], **ranking)
    job = _job(receipt._client)
    vectors = {name: [_spin_vector(job, {0: s0, 1: s1}) for s0, s1 in spins] for name, spins, _ in answers}
    receipt._client._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
        _answer(name, milli, index, vectors=vectors[name]) for index, (name, _, milli) in enumerate(answers)
    ]
    return receipt, job


class TestPickBest:
    def test_tie_under_single_best_follows_the_front_runner(self, monkeypatch) -> None:
        answers = [("0xA", [BEST], -4000), ("0xB", [BEST], -4000)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xB", "energy_milli": -4000})
        pick = pick_best(receipt, job, _model())
        assert pick.solution.solver == "0xB"
        assert pick.solution.leader is True
        assert pick.leader_read is True
        assert pick.energy_matches_chain is True
        assert pick.num_submissions == 2

    def test_tie_under_top_n_follows_the_first_ranked(self, monkeypatch) -> None:
        answers = [("0xA", [BEST], -4000), ("0xB", [BEST], -4000)]
        top = [{"solver": "0xB", "energy_milli": -4000}, {"solver": "0xA", "energy_milli": -4000}]
        receipt, job = _setup(monkeypatch, answers, top=top, order=_receipt_order(resolution=TOP_N))
        assert pick_best(receipt, job, _model()).solution.solver == "0xB"

    def test_best_vector_by_recomputed_energy(self, monkeypatch) -> None:
        answers = [("0xA", [HIGH, MID, BEST, HIGH2], -4000)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xA", "energy_milli": -4000})
        pick = pick_best(receipt, job, _model())
        assert (pick.solution.energy, pick.solution.energy_milli) == (-4, -4000)
        assert pick.num_solutions == 4

    def test_mismatch_is_flagged_and_logged(self, monkeypatch, caplog) -> None:
        # The chain ranked the leader at -4000, but its stored vectors only reach 0.
        answers = [("0xA", [MID], 0)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xA", "energy_milli": -4000})
        with caplog.at_level(logging.WARNING, logger="xqsa.quip"):
            pick = pick_best(receipt, job, _model())
        assert pick.energy_matches_chain is False
        assert pick.energy_milli == -4000
        assert (pick.solution.solver, pick.solution.leader) == ("0xA", True)  # nothing better is stored.
        assert "vectors reach 0 milli, but the chain holds -4000" in caplog.text

    def test_mismatched_leader_yields_to_the_best_stored_vector(self, monkeypatch, caplog) -> None:
        # The leader was ranked at -4000 but its stored vectors only reach 0
        # (QUI-1408 or QUI-1675); 0xB stored a -4000 vector.
        answers = [("0xA", [MID], 0), ("0xB", [HIGH, BEST], -4000)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xA", "energy_milli": -4000})
        with caplog.at_level(logging.WARNING, logger="xqsa.quip"):
            pick = pick_best(receipt, job, _model())
        assert (pick.solution.solver, pick.solution.leader, pick.solution.energy) == ("0xB", False, -4)
        assert (pick.leader_read, pick.energy_matches_chain, pick.energy_milli) == (True, False, -4000)
        assert pick.num_solutions == 2
        assert "returning 0xB's best vector" in caplog.text

    def test_rounded_job_checks_every_leader_vector(self, monkeypatch) -> None:
        # On a rounded job the model energy can prefer a vector the chain's
        # milli energy does not: here MID beats BEST on the model, while the
        # chain ranked the leader at BEST's -4000. The leader still holds it.
        energies = {(1, 1): -10, (-1, 1): -4}
        monkeypatch.setattr(
            "xqsa.quip.solution.compute_energy",
            lambda model, sample: energies.get((sample.get_linear(0), sample.get_linear(1)), 2),
        )
        answers = [("0xA", [BEST, MID], -4000), ("0xB", [MID], 0)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xA", "energy_milli": -4000})
        pick = pick_best(receipt, job, _model())
        assert (pick.solution.solver, pick.solution.energy) == ("0xA", -10)
        assert pick.energy_matches_chain is True

    def test_mismatched_leader_keeps_a_tie(self, monkeypatch) -> None:
        # The leader's vector ties the best stored one, so it stays the pick.
        answers = [("0xA", [BEST], -4000), ("0xB", [BEST], -4000)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xB", "energy_milli": -8000})
        pick = pick_best(receipt, job, _model())
        assert (pick.solution.solver, pick.solution.leader, pick.energy_matches_chain) == ("0xB", True, False)

    def test_ranked_solver_without_an_answer_falls_back(self, monkeypatch) -> None:
        answers = [("0xA", [MID], 0), ("0xB", [BEST], -4000)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xGHOST", "energy_milli": -9000})
        pick = pick_best(receipt, job, _model())
        assert pick.solution.solver == "0xB"
        assert pick.solution.leader is False
        assert pick.leader_read is False
        assert pick.energy_milli == -4000
        assert pick.energy_matches_chain is True

    def test_fallback_ranks_by_recomputed_energy_not_stored(self, monkeypatch) -> None:
        # 0xA's stored best claims -9000, but its only vector recomputes to 0.
        answers = [("0xA", [MID], -9000), ("0xB", [BEST], -4000)]
        receipt, job = _setup(monkeypatch, answers)
        pick = pick_best(receipt, job, _model())
        assert (pick.solution.solver, pick.energy_milli, pick.energy_matches_chain) == ("0xB", -4000, True)

    def test_unreadable_ranking_falls_back(self, monkeypatch, caplog) -> None:
        answers = [("0xA", [MID], 0), ("0xB", [BEST], -4000)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xA", "energy_milli": 0})

        def boom():
            raise ConnectionError("node gone")

        monkeypatch.setattr(receipt, "_ranking", boom)
        with caplog.at_level(logging.WARNING, logger="xqsa.quip"):
            pick = pick_best(receipt, job, _model())
        assert (pick.solution.solver, pick.leader_read) == ("0xB", False)
        assert "could not read the ranking of order 1: node gone" in caplog.text

    def test_unanswered_raises(self, monkeypatch) -> None:
        receipt, job = _setup(monkeypatch, [])
        with pytest.raises(QuipJobFailedError, match=r"has no answers; once it is final, .* order\.reclaim\(\)"):
            pick_best(receipt, job, _model())

    def test_answers_without_vectors_raise(self, monkeypatch) -> None:
        receipt, job = _setup(monkeypatch, [("0xA", [], 0)], front={"solver": "0xA", "energy_milli": 0})
        with pytest.raises(QuipJobFailedError, match="answers carried no solution vectors"):
            pick_best(receipt, job, _model())

    def test_absent_field_raises_typed_error(self, monkeypatch) -> None:
        receipt, job = _setup(monkeypatch, [("0xA", [BEST], -4000)])
        for _key, answer in receipt._client._iface.maps[("QuantumComputeMempool", "OrderSolutions")]:
            del answer["best_energy_milli"]
        with pytest.raises(QuipJobFailedError, match="field layout"):
            pick_best(receipt, job, _model())


class TestDecodeAnswers:
    def test_every_vector_best_first_leader_first_on_ties(self, monkeypatch) -> None:
        answers = [("0xA", [HIGH, BEST], -4000), ("0xB", [MID, BEST, HIGH2, HIGH], -4000)]
        receipt, job = _setup(monkeypatch, answers, front={"solver": "0xB", "energy_milli": -4000})
        decoded = decode_answers(receipt, job, _model())
        assert [(s.solver, s.energy, s.leader) for s in decoded] == [
            ("0xB", -4, True),
            ("0xA", -4, False),
            ("0xB", 0, True),
            ("0xB", 2, True),
            ("0xB", 2, True),
            ("0xA", 2, False),
        ]
        assert [s.energy_milli for s in decoded] == [-4000, -4000, 0, 2000, 2000, 2000]

    def test_one_solver(self, monkeypatch) -> None:
        answers = [("0xA", [HIGH, BEST], -4000), ("0xB", [MID], 0)]
        receipt, job = _setup(monkeypatch, answers)
        decoded = decode_answers(receipt, job, _model(), solver="0xA")
        assert [(s.solver, s.energy) for s in decoded] == [("0xA", -4), ("0xA", 2)]
        assert decoded[0].sample.get_linear(0) == -1

    def test_unknown_solver_names_the_order(self, monkeypatch) -> None:
        receipt, job = _setup(monkeypatch, [("0xA", [BEST], -4000)])
        with pytest.raises(KeyError, match="0xZ did not answer order 1"):
            decode_answers(receipt, job, _model(), solver="0xZ")

    def test_no_answers(self, monkeypatch) -> None:
        receipt, job = _setup(monkeypatch, [])
        assert decode_answers(receipt, job, _model()) == []
