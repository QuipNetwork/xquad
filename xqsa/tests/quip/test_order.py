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

"""JobOrder option validation: the checks every order passes before signing."""

from __future__ import annotations

import warnings
from dataclasses import replace

import pytest

from xqsa.quip import QuipOrderOptionError, QuipSubmissionError
from xqsa.quip.chain import ChainLimits
from xqsa.quip.order import _OrderOptions, check_client_defaults, merge_options

from .test_client import UNIT, _make_solver

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
