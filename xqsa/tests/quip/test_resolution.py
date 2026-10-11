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

"""Payout rules: bounds, chain encoding, the pallet's split arithmetic and the runtime gate."""

from __future__ import annotations

import warnings

import pytest

from xqsa.quip.resolution import (
    MAX_WINNERS,
    ResolutionSingleBest,
    ResolutionTopNEqual,
    ResolutionTopNWeighted,
    check_rank_weighting,
    coerce_resolution,
    from_chain,
)

TOP_N = [ResolutionTopNEqual, ResolutionTopNWeighted]
AGLS = 10**12


@pytest.mark.parametrize("cls", TOP_N)
@pytest.mark.parametrize("n", [0, MAX_WINNERS + 1, -1])
def test_n_out_of_bounds(cls, n):
    with pytest.raises(ValueError, match=f"from 1 to {MAX_WINNERS}"):
        cls(n)


@pytest.mark.parametrize("cls", TOP_N)
@pytest.mark.parametrize("n", [True, 2.0, "2", None])
def test_n_not_an_int(cls, n):
    with pytest.raises(TypeError, match="must be an int"):
        cls(n)


@pytest.mark.parametrize("cls", TOP_N)
def test_bounds_accepted(cls):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert cls(2).n == 2
        assert cls(MAX_WINNERS).n == MAX_WINNERS


@pytest.mark.parametrize("cls", TOP_N)
def test_n_of_one_warns(cls):
    with pytest.warns(UserWarning, match="pays exactly like ResolutionSingleBest") as record:
        cls(1)
    assert record[0].filename == __file__


@pytest.mark.parametrize(
    ("rule", "chain"),
    [
        (ResolutionSingleBest(), "SingleBest"),
        (ResolutionTopNEqual(2), {"TopNEqual": {"n": 2}}),
        (ResolutionTopNWeighted(32), {"TopNWeighted": {"n": 32}}),
    ],
)
def test_chain_round_trip(rule, chain):
    assert rule.to_chain() == chain
    assert from_chain(chain) == rule


def test_from_chain_unit_variant_as_dict():
    assert from_chain({"SingleBest": None}) == ResolutionSingleBest()


def test_from_chain_n_of_one_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert from_chain({"TopNEqual": {"n": 1}}).n == 1


@pytest.mark.parametrize("value", ["TopNEqual", {"TopNOther": {"n": 2}}, {"TopNEqual": 2}, None])
def test_from_chain_refuses_unknown(value):
    with pytest.raises(ValueError, match="unknown RewardResolution"):
        from_chain(value)


def test_rule_text():
    assert str(ResolutionSingleBest()) == "single best"
    assert str(ResolutionTopNEqual(2)) == "top 2, equal split"
    assert str(ResolutionTopNWeighted(2)) == "top 2, weighted by rank"


def test_coerce():
    assert coerce_resolution("SingleBest") == ResolutionSingleBest()
    rule = ResolutionTopNEqual(3)
    assert coerce_resolution(rule) is rule
    raw = {"TopNEqual": {"n": 2}}
    assert coerce_resolution(raw) is raw
    with pytest.raises(ValueError, match="ResolutionTopNEqual"):
        coerce_resolution("TopNEqual")


def test_single_best_payouts():
    assert ResolutionSingleBest().payouts(["a"], 7) == [7]
    assert ResolutionSingleBest().payouts([], 7) == []


@pytest.mark.parametrize(
    ("k", "reward", "expected"),
    [
        (2, AGLS + 1, [500000000001, 500000000000]),  # aglais order 239
        (3, 10, [4, 3, 3]),
        (1, 10, [10]),
        (0, 10, []),
    ],
)
def test_equal_split(k, reward, expected):
    assert ResolutionTopNEqual(4).payouts(["s"] * k, reward) == expected


@pytest.mark.parametrize(
    ("k", "reward", "expected"),
    [
        (1, 10, [10]),
        (2, 3 * AGLS, [2 * AGLS, AGLS]),
        (2, 10, [7, 3]),  # 10 // 3 * 2 + 1 * 2 // 3 = 6, then 3; the 1 left goes to rank 0
        (3, 6 * AGLS + 5, [3 * AGLS + 4, 2 * AGLS + 1, AGLS]),
        (3, 1, [1, 0, 0]),
        (0, 10, []),
    ],
)
def test_weighted_split(k, reward, expected):
    payouts = ResolutionTopNWeighted(4).payouts(["s"] * k, reward)
    assert payouts == expected
    assert sum(payouts) == (reward if k else 0)


def test_fewer_ranked_than_n():
    # k = 2 under n = 5: weights 2, 1, not 5, 4.
    assert ResolutionTopNWeighted(5).payouts(["a", "b"], 30) == [20, 10]
    assert ResolutionTopNEqual(5).payouts(["a", "b"], 31) == [16, 15]


def test_gate():
    check_rank_weighting(ResolutionTopNWeighted(2), 119)
    check_rank_weighting(ResolutionTopNWeighted(2), 120)
    with pytest.raises(ValueError, match="runtime 118.*absolute energy"):
        check_rank_weighting(ResolutionTopNWeighted(2), 118)
    with pytest.raises(ValueError, match="unknown runtime version"):
        check_rank_weighting(ResolutionTopNWeighted(2), None)
    for rule in (ResolutionSingleBest(), ResolutionTopNEqual(2), "SingleBest", {"TopNWeighted": {"n": 2}}):
        check_rank_weighting(rule, 118)
        check_rank_weighting(rule, None)
