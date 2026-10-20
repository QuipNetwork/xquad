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

import logging
import math
import time
import warnings
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from xqsa.quip import (
    QuipCancelledError,
    QuipConnectionError,
    QuipJobFailedError,
    QuipMetadataError,
    QuipOrderOptionError,
    QuipSubmissionError,
    QuipSubmitIncompleteError,
    chain,
)
from xqsa.quip.access import AccessAllow, AccessOpen
from xqsa.quip.chain import ChainLimits, unregistered_solvers
from xqsa.quip.errors import QuipUnconfirmedError
from xqsa.quip.order import JobOrder, _OrderOptions, check_client_defaults, merge_options
from xqsa.quip.resolution import ResolutionSingleBest, ResolutionTopNEqual, ResolutionTopNWeighted
from xqsa.solver import SolverResult
from xqvm_py.xqmx import XQMX

from .test_client import (
    GENESIS_HASH,
    SOLVER_TYPES,
    TOPO_HASH,
    UNIT,
    _make_solver,
    _model,
    _ok_receipt,
    _patch_signing,
    _solve_ready,
    _spin_vector,
    _submission,
)

LIMITS = ChainLimits(
    min_reward=UNIT,
    max_deadline_blocks=1000,
    max_block_wait=100,
    max_solutions=20,
    max_bid_miners=16,
    solver_types=SOLVER_TYPES,
)
NO_LIMITS = ChainLimits(min_reward=None, max_deadline_blocks=None, max_block_wait=None, max_solutions=None)
BASE = _OrderOptions(
    reward=UNIT,
    deadline_blocks=100,
    block_wait=10,
    topology=None,
    mapping=None,
    access="Open",
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


class TestCeilingOption:
    """``max_energy`` checks that need no placed model."""

    @pytest.mark.parametrize("value", [-4, -2.5, 0])
    def test_real_numbers_pass(self, value) -> None:
        assert _quiet_merge(max_energy=value).max_energy == value

    @pytest.mark.parametrize("value", [True, "-4"])
    def test_non_real_refused(self, value) -> None:
        with pytest.raises(TypeError, match="max_energy must be a real number"):
            _merge(max_energy=value)

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_non_finite_refused(self, value) -> None:
        with pytest.raises(QuipOrderOptionError, match="must be finite") as info:
            _merge(max_energy=value)
        assert info.value.option == "max_energy"

    @pytest.mark.parametrize("value", [10**400, -(10**400), 10**5000], ids=["huge", "huge-negative", "past-str-limit"])
    def test_int_beyond_float_range_refused(self, value) -> None:
        with pytest.raises(QuipOrderOptionError, match="too large to convert to a float") as info:
            _merge(max_energy=value)
        assert info.value.option == "max_energy"


def _rounded_model() -> XQMX:
    """A SPIN model whose 0.0012 field rounds to milli 1."""
    model = XQMX.spin_model(2)
    model.set_linear(0, 0.0012)
    model.set_quadratic(0, 1, 1)
    return model


def _binary_model() -> XQMX:
    """E = -2*x0 + x0*x1: spin h = (-0.75, 0.25), J = 0.25, offset -0.75; energies -2 to 0."""
    model = XQMX.binary_model(2)
    model.set_linear(0, -2)
    model.set_quadratic(0, 1, 1)
    return model


def _wide_model() -> XQMX:
    """A SPIN model reaching -1234.567 to 1234.567, bounds that 6 significant digits would round."""
    model = XQMX.spin_model(1)
    model.set_linear(0, 1234.567)
    return model


class TestCeilingAgainstThePlacedModel:
    """``_check_floors``: _model() reaches spin energies -4 to +4."""

    def test_reachable_ceiling_passes_quietly(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            order = solver.create_order(_model(), max_energy=-4)
        assert order.max_energy == -4
        assert order.options()["max_energy"] == -4

    def test_below_the_lowest_energy_refused(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.raises(QuipOrderOptionError, match="below -4, the lowest energy") as info:
            solver.create_order(_model(), max_energy=-4.001)
        assert info.value.option == "max_energy"

    def test_at_the_highest_energy_warns(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.warns(UserWarning, match="at or above 4, the highest energy") as record:
            solver.create_order(_model(), max_energy=4)
        assert record[0].filename == __file__

    def test_binary_bounds_include_the_offset(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        # Spin reach is 1.25, so the model reaches -1.25 - 0.75 = -2 at the lowest.
        solver.create_order(_binary_model(), topology="native", max_energy=-2)
        with pytest.raises(QuipOrderOptionError, match="below -2"):
            solver.create_order(_binary_model(), topology="native", max_energy=-2.001)

    def test_bounds_are_stated_exactly(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.raises(QuipOrderOptionError, match=r"below -1234\.567, the lowest"):
            solver.create_order(_wide_model(), topology="native", max_energy=-1234.568)
        with pytest.warns(UserWarning, match=r"at or above 1234\.567, the highest"):
            solver.create_order(_wide_model(), topology="native", max_energy=1234.567)

    def test_numpy_ceiling_shows_on_the_order(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), max_energy=np.float64(-2.0))
        assert "Floors      energy <= -2" in _box_rows(str(order))

    def test_i64_overflow_refused(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.raises(QuipOrderOptionError, match="outside i64"):
            solver.create_order(_model(), max_energy=-1e19)

    def test_rounded_model_warns(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            solver.create_order(_rounded_model(), topology="native", max_energy=-1)
        assert any("chain applies the ceiling to the rounded model" in str(w.message) for w in caught)

    def test_rounded_model_without_ceiling_does_not_warn_about_it(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            solver.create_order(_rounded_model(), topology="native")
        assert not any("ceiling" in str(w.message) for w in caught)

    def test_set_rechecks_and_a_refusal_keeps_the_order(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), max_energy=-3)
        job = order._job
        with pytest.raises(QuipOrderOptionError, match="max_energy"):
            order.set(max_energy=-5)
        assert (order.max_energy, order._job) == (-3, job)

    def test_set_rechecks_after_replacing(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_binary_model(), topology="native", max_energy=-2)
        with pytest.raises(QuipOrderOptionError, match="below"):
            order.set(topology=TOPO_HASH, mapping={0: 0, 1: 1}, max_energy=-2.5)
        assert order.topology == "native"

    def test_set_warning_points_at_the_caller(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        with pytest.warns(UserWarning, match="filters nothing") as record:
            order.set(max_energy=10)
        assert record[0].filename == __file__

    def test_ceiling_goes_under_both_keys(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        ising = solver.create_order(_binary_model(), topology="native", max_energy=-2)._call_params()["ising_params"]
        assert ising["min_energy_milli"] == ising["max_energy_milli"] == -1250

    def test_solve_passes_the_ceiling(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver.solve(_model(), max_energy=-2)
        assert captured["call_params"]["ising_params"]["min_energy_milli"] == -2000


class TestSolutionFloorOptions:
    """``min_solutions`` and ``min_diversity`` checks that need no placed model."""

    @pytest.mark.parametrize("count", [2, 20])
    def test_solutions_in_range_pass(self, count) -> None:
        assert _quiet_merge(min_solutions=count).min_solutions == count

    @pytest.mark.parametrize("count", [-1, 0, 21])
    def test_solutions_out_of_range_refused(self, count) -> None:
        with pytest.raises(QuipOrderOptionError, match=r"at least 1 and at most MaxSolutions \(20\)") as info:
            _merge(min_solutions=count)
        assert info.value.option == "min_solutions"

    def test_one_solution_warns(self) -> None:
        with pytest.warns(UserWarning, match="stores only the first vector"):
            _merge(min_solutions=1)

    def test_absent_max_solutions_not_checked(self) -> None:
        assert _merge(limits=NO_LIMITS, min_solutions=500).min_solutions == 500

    @pytest.mark.parametrize("name", ["min_solutions", "min_diversity"])
    def test_wrong_type_refused(self, name) -> None:
        with pytest.raises(TypeError, match=name):
            _merge(**{name: "2"})
        with pytest.raises(TypeError, match=name):
            _merge(**{name: True})

    @pytest.mark.parametrize("diversity", [0, 0.001, 0.3, 0.4, 0.5])
    def test_diversity_in_range_passes(self, diversity) -> None:
        assert _quiet_merge(min_diversity=diversity).min_diversity == diversity

    @pytest.mark.parametrize("diversity", [-0.001, 0.501, 1, math.nan])
    def test_diversity_out_of_range_refused(self, diversity) -> None:
        with pytest.raises(QuipOrderOptionError, match="from 0 to 0.5") as info:
            _merge(min_diversity=diversity)
        assert info.value.option == "min_diversity"

    def test_diversity_off_the_milli_grid_refused(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="whole number of thousandths"):
            _merge(min_diversity=0.4005)

    def test_diversity_with_one_solution_refused(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(QuipOrderOptionError, match="InvalidDiversityConfig"):
                _merge(min_solutions=1, min_diversity=0.4)
            assert _merge(min_solutions=1, min_diversity=0).min_diversity == 0

    def test_whole_configuration_rechecked(self) -> None:
        current = _quiet_merge(min_diversity=0.4)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(QuipOrderOptionError, match="InvalidDiversityConfig"):
                _merge(current, min_solutions=1)


def _path3_model() -> XQMX:
    """A 3-spin SPIN chain, so floor(3/2)/3 caps diversity at 1/3."""
    model = XQMX.spin_model(3)
    model.set_quadratic(0, 1, 1)
    model.set_quadratic(1, 2, 1)
    return model


class TestSolutionFloorsAgainstThePlacedModel:
    def test_diversity_cap_follows_the_placed_spins(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        assert solver.create_order(_path3_model(), topology="native", min_diversity=0.333).min_diversity == 0.333
        with pytest.raises(QuipOrderOptionError, match="above 1/3, the most diversity 3 placed spins") as info:
            solver.create_order(_path3_model(), topology="native", min_diversity=0.334)
        assert info.value.option == "min_diversity"

    def test_two_spins_allow_one_half(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        assert solver.create_order(_model(), min_diversity=0.5).min_diversity == 0.5

    def test_set_rechecks_the_cap(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_path3_model(), topology="native")
        with pytest.raises(QuipOrderOptionError, match="min_diversity"):
            order.set(min_diversity=0.4)
        assert order.min_diversity is None

    def test_floors_reach_the_call_params(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), min_solutions=5, min_diversity=0.3)
        ising = order._call_params()["ising_params"]
        assert (ising["min_solutions"], ising["min_diversity_milli"]) == (5, 300)
        assert order.options()["min_solutions"] == 5

    @pytest.mark.parametrize("diversity", [None, 0])
    def test_no_diversity_floor_is_none(self, monkeypatch, diversity) -> None:
        solver, _ = _ready(monkeypatch)
        ising = solver.create_order(_model(), min_diversity=diversity)._call_params()["ising_params"]
        assert (ising["min_solutions"], ising["min_diversity_milli"]) == (None, None)

    def test_solve_passes_the_floors(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver.solve(_model(), min_solutions=2, min_diversity=0.5)
        ising = captured["call_params"]["ising_params"]
        assert (ising["min_solutions"], ising["min_diversity_milli"]) == (2, 500)


class TestResolutionOption:
    def test_string_coerced(self) -> None:
        assert _quiet_merge(resolution="SingleBest").resolution == ResolutionSingleBest()

    def test_unknown_string_refused(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="not a payout rule") as info:
            _merge(resolution="TopNEqual")
        assert info.value.option == "resolution"

    def test_raw_dict_strict_raises(self) -> None:
        with pytest.raises(TypeError, match="resolution: raw chain values"):
            _merge(resolution={"TopNEqual": {"n": 2}})

    def test_raw_weighted_dict_gated_on_the_lenient_path(self) -> None:
        raw = {"TopNWeighted": {"n": 2}}
        with pytest.warns(DeprecationWarning), pytest.raises(QuipOrderOptionError, match="runtime 118"):
            _merge(limits=replace(LIMITS, spec_version=118), strict=False, resolution=raw)
        with pytest.warns(DeprecationWarning):
            assert _merge(limits=replace(LIMITS, spec_version=119), strict=False, resolution=raw).resolution is raw

    @pytest.mark.parametrize("spec_version", [118, 119])
    @pytest.mark.parametrize(
        "raw", [{"TopNWeighted": 2}, {"TopNWeighted": {"n": 40}}, {"TopNWeighted": {}}, {"TopNEqual": 3}]
    )
    def test_undecodable_raw_rule_refused_on_the_lenient_path(self, raw, spec_version) -> None:
        with pytest.warns(DeprecationWarning), pytest.raises(QuipOrderOptionError, match="cannot be read") as info:
            _merge(limits=replace(LIMITS, spec_version=spec_version), strict=False, resolution=raw)
        assert info.value.option == "resolution"

    def test_raw_dict_naming_no_rule_passes_the_lenient_path(self) -> None:
        with pytest.warns(DeprecationWarning):
            assert _merge(strict=False, resolution={"FutureRule": 3}).resolution == {"FutureRule": 3}

    @pytest.mark.parametrize("value", [None, 2])
    def test_non_rule_refused(self, value) -> None:
        with pytest.raises(QuipOrderOptionError, match="not a payout rule") as info:
            _merge(resolution=value)
        assert info.value.option == "resolution"

    def test_equal_passes(self) -> None:
        assert _quiet_merge(resolution=ResolutionTopNEqual(3)).resolution == ResolutionTopNEqual(3)

    def test_weighted_passes_from_runtime_119(self) -> None:
        rule = ResolutionTopNWeighted(2)
        assert _quiet_merge(limits=replace(LIMITS, spec_version=119), resolution=rule).resolution == rule

    def test_weighted_refused_below_119(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="runtime 118.*absolute energy") as info:
            _merge(limits=replace(LIMITS, spec_version=118), resolution=ResolutionTopNWeighted(2))
        assert info.value.option == "resolution"

    def test_weighted_refused_on_unknown_runtime(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="unknown runtime version"):
            _merge(resolution=ResolutionTopNWeighted(2))

    @pytest.mark.parametrize("rule", [ResolutionTopNEqual(2), ResolutionTopNWeighted(2)])
    def test_top_n_without_block_wait_warns(self, rule) -> None:
        with pytest.warns(UserWarning, match="only one winner"):
            _merge(limits=replace(LIMITS, spec_version=119), resolution=rule, block_wait=0)

    def test_create_order_refuses_weighted_before_signing(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)  # the fake runtime is 118
        with pytest.raises(QuipOrderOptionError, match="runtime 118"):
            solver.create_order(_model(), topology="native", resolution=ResolutionTopNWeighted(2))
        assert "submitted_wire" not in captured

    def test_create_order_takes_equal(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), topology="native", resolution=ResolutionTopNEqual(2))
        assert order.resolution == order.options()["resolution"] == ResolutionTopNEqual(2)
        assert "top 2, equal split" in str(order)
        assert order.set(resolution="SingleBest").resolution == ResolutionSingleBest()


class TestOrderOptionStrictness:
    def test_unknown_option_strict_raises(self) -> None:
        with pytest.raises(TypeError, match="unknown order option 'colour'"):
            _merge(colour="red")

    def test_client_default_is_not_an_order_option(self) -> None:
        with pytest.raises(TypeError, match="unknown order option 'delivery'"):
            _merge(delivery="OnChainOnly")

    def test_mode_is_not_an_order_option(self) -> None:
        with pytest.raises(TypeError, match="unknown order option 'mode'"):
            _merge(mode="Open")

    def test_raw_dict_strict_raises(self) -> None:
        with pytest.raises(TypeError, match="access: raw chain values"):
            _merge(replace_base(access={"Bid": 5}))

    def test_mapping_dict_is_not_a_raw_value(self) -> None:
        assert _quiet_merge(mapping={0: 1}).mapping == {0: 1}

    def test_unknown_option_lenient_warns_and_drops(self) -> None:
        with pytest.warns(DeprecationWarning, match="colour"):
            assert _merge(strict=False, colour="red") == BASE

    def test_raw_dict_lenient_warns_and_passes(self) -> None:
        with pytest.warns(DeprecationWarning, match="raw chain value"):
            merged = _merge(replace_base(resolution={"FutureRule": 3}), strict=False)
        assert merged.resolution == {"FutureRule": 3}


class TestClientDefaults:
    def test_supported_defaults_pass(self) -> None:
        check_client_defaults("OnChainOnly")

    @pytest.mark.parametrize("delivery", ["ResultReady", {"Callback": {}}])
    def test_delivery_refused(self, delivery) -> None:
        with pytest.raises(ValueError, match="result delivery is not supported yet"):
            check_client_defaults(delivery)

    def test_constructor_checks_before_connecting(self, monkeypatch) -> None:
        from xqsa.tests.quip.test_client import FakeSubstrate

        class _Unreachable(FakeSubstrate):
            def init_runtime(self, *args, **kwargs):
                raise AssertionError("must not connect")

        with pytest.raises(ValueError, match="not an access rule"):
            _make_solver(monkeypatch, iface=_Unreachable(), access="Whitelist")

    def test_access_defaults_to_open(self, monkeypatch) -> None:
        assert _make_solver(monkeypatch)._access == AccessOpen()

    def test_constructor_mode_is_deprecated(self, monkeypatch) -> None:
        with pytest.warns(DeprecationWarning, match="mode= is deprecated, use access= .*QUI-1608"):
            solver = _make_solver(monkeypatch, mode="Open")
        assert solver._access == AccessOpen()

    def test_constructor_mode_and_access_refused(self, monkeypatch) -> None:
        with pytest.raises(TypeError, match="not both"):
            _make_solver(monkeypatch, mode="Open", access="Open")


ALICE_HEX = "0x" + "d4" * 32
BOB_HEX = "0x" + "8e" * 32
CAROL_HEX = "0x" + "90" * 32


def _ss58(account_hex: str) -> str:
    """``account_hex`` as warnings name it: SS58 in the fake chain's format, or hex without ``scalecodec``."""
    return chain.ss58_address(SimpleNamespace(ss58_format=42), bytes.fromhex(account_hex[2:]))


class _SolversIface:
    """Answers ``Solvers`` reads from ``registered``, recording each account read."""

    def __init__(self, registered=(), *, raises: Exception | None = None) -> None:
        self.registered = set(registered)
        self.raises = raises
        self.reads: list[str] = []

    def query(self, module, name, params=None):
        assert (module, name) == ("QuantumComputeMempool", "Solvers")
        if self.raises is not None:
            raise self.raises
        self.reads.append(params[0])
        return SimpleNamespace(value={"solver_type": "Cpu"} if params[0] in self.registered else None)


class TestUnregisteredSolvers:
    RULE = AccessAllow(accounts=[ALICE_HEX, BOB_HEX, CAROL_HEX], types=["Cpu"])

    def test_lists_the_unregistered_in_order(self) -> None:
        iface = _SolversIface([BOB_HEX])
        assert unregistered_solvers(iface, [ALICE_HEX, BOB_HEX, CAROL_HEX]) == [ALICE_HEX, CAROL_HEX]
        assert iface.reads == [ALICE_HEX, BOB_HEX, CAROL_HEX]

    def test_warns_once_per_unregistered_account(self) -> None:
        with pytest.warns(UserWarning, match="is not a registered solver") as record:
            merged = merge_options(BASE, {"access": self.RULE}, LIMITS, strict=True, iface=_SolversIface([BOB_HEX]))
        assert merged.access == self.RULE
        assert [str(w.message).split()[1] for w in record] == [ALICE_HEX, CAROL_HEX]

    def test_all_registered_is_silent(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            iface = _SolversIface([ALICE_HEX, BOB_HEX, CAROL_HEX])
            merge_options(BASE, {"access": self.RULE}, LIMITS, strict=True, iface=iface)

    def test_types_only_reads_nothing(self) -> None:
        iface = _SolversIface()
        merge_options(BASE, {"access": AccessAllow(types=["Cpu"])}, LIMITS, strict=True, iface=iface)
        assert iface.reads == []

    def test_read_fault_does_not_raise(self, caplog) -> None:
        iface = _SolversIface(raises=RuntimeError("socket closed"))
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            merged = merge_options(BASE, {"access": self.RULE}, LIMITS, strict=True, iface=iface)
        assert merged.access == self.RULE
        assert "could not check whether the allowlisted accounts are registered" in caplog.text

    def test_create_order_warns_and_set_rechecks_only_a_new_list(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.warns(UserWarning, match=f"access: {_ss58(ALICE_HEX)} is not a registered solver") as record:
            order = solver.create_order(_model(), access=AccessAllow(accounts=[ALICE_HEX]))
        assert record[0].filename == __file__
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            order.set(reward=2 * UNIT)
        with pytest.warns(UserWarning, match=f"access: {_ss58(BOB_HEX)} is not a registered solver"):
            order.set(access=AccessAllow(accounts=[BOB_HEX]))

    def test_warning_names_the_account_through_ss58_address(self, monkeypatch) -> None:
        # A marker stands in for the encoder, so this runs without scalecodec; bytes() refuses a hex string.
        monkeypatch.setattr(chain, "ss58_address", lambda iface, account_id: f"<ss58:{bytes(account_id).hex()}>")
        solver, _ = _ready(monkeypatch)
        with pytest.warns(UserWarning, match="is not a registered solver") as record:
            solver.create_order(_model(), access=AccessAllow(accounts=[ALICE_HEX]))
        assert str(record[0].message).split()[1] == f"<ss58:{ALICE_HEX[2:]}>"

    def test_set_with_the_same_allowlist_reads_nothing(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        with pytest.warns(UserWarning, match="is not a registered solver"):
            order = solver.create_order(_model(), access=AccessAllow(accounts=[ALICE_HEX, BOB_HEX]))
        reads = _count_solvers_reads(solver)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            order.set(access=order.access, reward=2 * UNIT)
            order.set(**{name: value for name, value in order.options().items() if name != "mapping"})
        assert reads == []

    def test_client_default_allowlist_is_looked_up_once_at_construction(self, monkeypatch) -> None:
        with pytest.warns(UserWarning, match="is not a registered solver") as record:
            solver, _ = _ready(monkeypatch, access=AccessAllow(accounts=[ALICE_HEX, BOB_HEX]))
        assert [str(w.message).split()[1] for w in record] == [_ss58(ALICE_HEX), _ss58(BOB_HEX)]
        reads = _count_solvers_reads(solver)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for _ in range(3):
                solver.create_order(_model())
        assert reads == []
        with pytest.warns(UserWarning, match=f"access: {_ss58(CAROL_HEX)} is not a registered solver"):
            solver.create_order(_model(), access=AccessAllow(accounts=[CAROL_HEX]))
        assert reads == [CAROL_HEX]


def _count_solvers_reads(solver) -> list[str]:
    """Record the account of every ``Solvers`` read the solver makes from now on."""
    reads: list[str] = []
    query = solver._iface.query

    def counted(module, name, params=None, block_hash=None):
        if name == "Solvers":
            reads.append(params[0])
        return query(module, name, params, block_hash)

    solver._iface.query = counted
    return reads


class TestAccess:
    def test_open_string_coerced(self) -> None:
        assert _merge(access="Open").access == AccessOpen()

    def test_bad_string_is_an_option_error(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="not an access rule") as info:
            _merge(access="Whitelist")
        assert info.value.option == "access"

    def test_allowlist_within_limits(self) -> None:
        rule = AccessAllow(accounts=[f"0x{i:064x}" for i in range(16)], types=["QpuDwave"])
        assert _merge(access=rule).access == rule

    def test_too_many_accounts_refused(self) -> None:
        rule = AccessAllow(accounts=[f"0x{i:064x}" for i in range(17)])
        with pytest.raises(QuipOrderOptionError, match="17 accounts; the chain's MaxBidMiners is 16"):
            _merge(access=rule)

    def test_absent_cap_not_checked(self) -> None:
        rule = AccessAllow(accounts=[f"0x{i:064x}" for i in range(17)])
        assert _merge(limits=replace(LIMITS, max_bid_miners=None), access=rule).access == rule

    def test_unknown_type_refused(self) -> None:
        with pytest.raises(QuipOrderOptionError, match="unknown solver type 'QpuRigetti'; the chain has Cpu, Gpu"):
            _merge(access=AccessAllow(types=["QpuDwave", "QpuRigetti"]))

    def test_unknown_types_unchecked_without_metadata(self) -> None:
        rule = AccessAllow(types=["QpuRigetti"])
        assert _merge(limits=replace(LIMITS, solver_types=None), access=rule).access == rule

    @pytest.mark.parametrize("wrap", [lambda v: v, lambda v: (v,)], ids=["readback", "to_chain"])
    def test_raw_dict_checked_on_the_lenient_path(self, wrap) -> None:
        raw = {"Bid": {"miners": None, "miner_types": wrap(["QpuRigetti"])}}
        with pytest.warns(DeprecationWarning), pytest.raises(QuipOrderOptionError, match="unknown solver type"):
            _merge(strict=False, access=raw)

    def test_raw_to_chain_dict_over_cap_refused(self) -> None:
        raw = AccessAllow(accounts=[f"0x{i:064x}" for i in range(17)]).to_chain()
        with pytest.warns(DeprecationWarning), pytest.raises(QuipOrderOptionError, match="MaxBidMiners is 16"):
            _merge(strict=False, access=raw)

    def test_raw_to_chain_dict_passes_on_the_lenient_path(self) -> None:
        raw = AccessAllow(types=["Cpu"]).to_chain()
        with pytest.warns(DeprecationWarning, match="raw chain value"):
            assert _merge(strict=False, access=raw).access == raw

    @pytest.mark.parametrize(
        "raw",
        [
            {"Bid": {"miners": None, "miner_types": [f"Type{i}" for i in range(9)]}},
            {"Bid": {"miners": ["0x" + "11" * 20], "miner_types": None}},
            {"Bid": {"miners": [7], "miner_types": None}},
            {"Bid": {"miners": None, "miner_types": None}},
            {"Bid": {"miners": None, "miner_types": [1]}},
            {"Bid": 5},
            {"Open": 1},
            {"FutureRule": 1},
        ],
        ids=[
            "nine-types",
            "20-byte-account",
            "int-account",
            "empty",
            "non-str-type",
            "not-a-struct",
            "open-value",
            "no-rule",
        ],
    )
    def test_raw_dict_that_does_not_decode_refused(self, raw) -> None:
        with (
            pytest.warns(DeprecationWarning),
            pytest.raises(QuipOrderOptionError, match="it cannot be read as an access rule") as info,
        ):
            _merge(strict=False, access=raw)
        assert info.value.option == "access"

    def test_raw_bid_with_an_unknown_field_refused(self) -> None:
        raw = {"Bid": {"miners": [ALICE_HEX], "miner_type": ["Cpu"]}}
        with (
            pytest.warns(DeprecationWarning),
            pytest.raises(QuipOrderOptionError, match="Bid has no field 'miner_type'") as info,
        ):
            _merge(strict=False, access=raw)
        assert info.value.option == "access"

    def test_client_default_raw_dict_naming_no_rule_refused_at_construction(self, monkeypatch) -> None:
        with pytest.raises(QuipOrderOptionError, match="cannot be read as an access rule"):
            _ready(monkeypatch, access={"FutureRule": 1})

    def test_create_order_encodes_the_allowlist(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        rule = AccessAllow(accounts=[ALICE_HEX, BOB_HEX], types=["QpuDwave"])
        with pytest.warns(UserWarning, match="not a registered solver"):  # the fake chain has no solvers.
            order = solver.create_order(_model(), access=rule)
        assert order.access == order.options()["access"] == rule
        assert "allowlist (accounts " in str(order)
        assert order._call_params()["mode"] == {
            "Bid": {"miners": ([ALICE_HEX, BOB_HEX],), "miner_types": (["QpuDwave"],)}
        }
        assert order.set(access="Open")._call_params()["mode"] == "Open"

    def test_client_default_allowlist(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch, access=AccessAllow(types=["Gpu"]))
        params = solver.create_order(_model())._call_params()
        assert params["mode"] == {"Bid": {"miners": None, "miner_types": (["Gpu"],)}}

    def test_create_order_refuses_unknown_type_before_signing(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.raises(QuipOrderOptionError, match="unknown solver type"):
            solver.create_order(_model(), access=AccessAllow(types=["Tpu"]))
        assert captured["builds"] == 0

    def test_solve_mode_is_deprecated(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.warns(DeprecationWarning, match="mode= is deprecated") as record:
            solver.solve(_model(), mode=AccessAllow(types=["Cpu"]))
        assert record[0].filename == __file__
        assert captured["call_params"]["mode"] == {"Bid": {"miners": None, "miner_types": (["Cpu"],)}}

    def test_solve_takes_access(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            solver.solve(_model(), access=AccessAllow(types=["Cpu"]))
        assert captured["call_params"]["mode"] == {"Bid": {"miners": None, "miner_types": (["Cpu"],)}}

    @pytest.mark.parametrize("default", [False, True], ids=["per-call", "client-default"])
    def test_solve_encodes_a_raw_dict(self, monkeypatch, default) -> None:
        raw = {"Bid": {"miners": None, "miner_types": ["Cpu"]}}
        solver, captured = _ready(monkeypatch, **({"access": raw} if default else {}))
        with pytest.warns(DeprecationWarning, match="raw chain value"):
            solver.solve(_model(), **({} if default else {"access": raw}))
        assert captured["call_params"]["mode"] == {"Bid": {"miners": None, "miner_types": (["Cpu"],)}}

    @pytest.mark.parametrize("shortcut", ["solve", "quote"])
    def test_shortcut_refuses_a_raw_dict_naming_no_rule_before_signing(self, monkeypatch, shortcut) -> None:
        solver, captured = _ready(monkeypatch)
        with (
            pytest.warns(DeprecationWarning),
            pytest.raises(QuipOrderOptionError, match="cannot be read as an access rule") as info,
        ):
            getattr(solver, shortcut)(_model(), access={"FutureRule": 1})
        assert info.value.option == "access"
        assert captured["builds"] == 0

    @pytest.mark.parametrize("value", [None, 5, ["Open"]])
    def test_non_rule_refused(self, monkeypatch, value) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.raises(QuipOrderOptionError, match="not an access rule"):
            solver.create_order(_model(), access=value)
        assert captured["builds"] == 0

    @pytest.mark.parametrize("kwargs", [{"mode": None}, {"access": None}, {"mode": None, "access": None}])
    def test_solve_none_is_the_client_default(self, monkeypatch, kwargs) -> None:
        solver, captured = _ready(monkeypatch)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            solver.solve(_model(), **kwargs)
        assert captured["call_params"]["mode"] == "Open"

    def test_quote_shortcut_maps_mode(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.warns(DeprecationWarning) as record:
            solver.quote(_model(), mode=AccessAllow(types=["Cpu"]))
        messages = [str(w.message) for w in record]
        assert any("mode= is deprecated" in m for m in messages)
        assert not any("ignoring unknown" in m for m in messages)
        assert all(w.filename == __file__ for w in record)
        assert captured["params"][-1]["mode"] == {"Bid": {"miners": None, "miner_types": (["Cpu"],)}}

    def test_solve_mode_and_access_refused(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.raises(TypeError, match="not both"):
            solver.solve(_model(), mode="Open", access="Open")
        assert captured["builds"] == 0


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
        assert order.status() == {"state": "draft", "options": order.options(), "inclusion_window_blocks": 64}

    def test_order_inherits_client_defaults(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch, reward=2 * UNIT, deadline_blocks=200, block_wait=20)
        assert solver.create_order(_model()).options() == {
            "reward": 2 * UNIT,
            "deadline_blocks": 200,
            "block_wait": 20,
            "topology": TOPO_HASH,
            "mapping": None,
            "access": AccessOpen(),
            "resolution": ResolutionSingleBest(),
            "max_energy": None,
            "min_solutions": None,
            "min_diversity": None,
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


class TestJobOrderInclusionWindow:
    def test_quote_shows_the_effective_window(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch, inclusion_window_blocks=100)
        quote = solver.create_order(_model()).quote()
        assert quote.inclusion_window_blocks == 128
        assert "│ Window      included within 128 blocks " in str(quote)

    def test_a_fresh_quote_is_sent_as_signed(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        solver._iface.head += 31  # 32 of 64 blocks left: not yet lapsing.
        order.submit()
        assert captured["builds"] == 1

    def test_a_lapsing_quote_is_signed_again(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        quoted_at = solver._iface.head
        solver._iface.head += 32  # 31 of 64 blocks left after the best block.
        order.submit()
        assert captured["builds"] == 2
        assert [era.checkpoint for era in captured["eras"]] == [quoted_at, quoted_at + 32]
        assert captured["sent"] == [captured["wires"][1]]
        assert captured["nonces"] == [0, 0]


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


def _send_raises(monkeypatch, solver, error: Exception | None, captured: dict, *, lands_on: int | None = 1) -> None:
    """Make the first send raise ``error`` after the bytes reach the node; each send is a block.

    The proposal lands, finalized at once, on send number ``lands_on``
    (``None``: never). Each send also moves the head one block on, the way
    a resend once per block would see it.
    """

    def send(iface, wire_bytes, ext_hash, wait_for="inblock"):
        sends = captured.setdefault("sent", [])
        sends.append(wire_bytes)
        if len(sends) == lands_on:
            iface.land(captured["nonces"][-1])
        iface.head += 1
        if len(sends) == 1 and error is not None:
            raise error

    monkeypatch.setattr(solver._quip_signing, "submit_and_watch", send)


def _stall_finality(monkeypatch, solver, captured: dict) -> None:
    """The best block moves on with every send but finality stays where it was, and nothing lands."""
    solver._iface.finalized = solver._iface.head
    _send_raises(monkeypatch, solver, None, captured, lands_on=None)


def _uncertain(solver, message: str) -> Exception:
    return solver._quip_signing.SendOutcomeUnknown(message)


class TestJobOrderSendOutcomes:
    """Every send ends submitted, failed or draft, read from finalized state.

    The seven ways a send used to leave the outcome unknown: a transport
    fault while sending, no inclusion block reported, a ``retracted`` or
    ``finalityTimeout`` pool status, an RPC 1012 or 1013 refusal, an
    unreadable dispatch result, and an unreadable order id.
    """

    @pytest.mark.parametrize("status", ["invalid", "dropped", "usurped"])
    def test_certain_rejection_stays_a_draft(self, monkeypatch, status) -> None:
        solver, captured = _ready(monkeypatch)
        rejected = solver._quip_signing.QuipSigningError(f"transaction pool rejected the extrinsic: {status}")
        _send_raises(monkeypatch, solver, rejected, captured, lands_on=None)
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError, match=status) as info:
            order.submit()
        assert not isinstance(info.value, QuipUnconfirmedError)
        assert order.status()["state"] == "draft"
        assert len(captured["sent"]) == 1  # refused outright: never settled or sent again.

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param("transaction pool rejected the extrinsic: retracted (0xblock)", id="retracted"),
            pytest.param("transaction pool rejected the extrinsic: finalityTimeout (0xblock)", id="finality-timeout"),
            pytest.param("author_submitExtrinsic rejected the extrinsic: {'code': 1012}", id="rpc-1012"),
            pytest.param("author_submitExtrinsic rejected the extrinsic: {'code': 1013}", id="rpc-1013"),
            pytest.param(ConnectionError("websocket closed"), id="transport"),
        ],
    )
    def test_uncertain_send_that_landed_is_submitted(self, monkeypatch, error) -> None:
        solver, captured = _ready(monkeypatch)
        _send_raises(monkeypatch, solver, _uncertain(solver, error) if isinstance(error, str) else error, captured)
        order = solver.create_order(_model())
        assert order.submit().order_id() == 1
        assert order._state == "submitted"
        assert len(captured["sent"]) == 1

    def test_send_seen_nowhere_is_resent_until_it_lands(self, monkeypatch) -> None:
        """No inclusion block reported: the same bytes go out once a block until finalized state shows them."""
        solver, captured = _ready(monkeypatch)
        _send_raises(monkeypatch, solver, None, captured, lands_on=3)
        order = solver.create_order(_model()).submit()
        assert order.order_id() == 1
        assert captured["sent"] == [captured["wires"][0]] * 3
        assert captured["builds"] == 1  # never signed again while it could land.

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(None, id="window-expired"),
            pytest.param("transaction pool rejected the extrinsic: retracted (0xblock)", id="retracted"),
        ],
    )
    def test_window_ending_unspent_is_a_draft(self, monkeypatch, error) -> None:
        """Seen nowhere, or retracted and never included again: once the window is final, it never was."""
        solver, captured = _ready(monkeypatch)
        _send_raises(monkeypatch, solver, error and _uncertain(solver, error), captured, lands_on=None)
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError, match="never reached the chain.*window ended at block 263") as info:
            order.submit()
        assert not isinstance(info.value, QuipUnconfirmedError)
        assert order.status()["state"] == "draft"
        assert len(set(captured["sent"])) == 1
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        assert order.submit().order_id() == 1  # a draft submits again, signed anew.

    def test_nonce_used_by_another_transaction_is_a_draft(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        solver._iface.extrinsics = ["0xother"]  # the block that spent the nonce holds someone else's.
        _send_raises(monkeypatch, solver, None, captured)
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError, match="nonce 0 was used by another transaction"):
            order.submit()
        assert order.status()["state"] == "draft"
        assert len(captured["sent"]) == 1

    def test_unreadable_block_is_read_again(self, monkeypatch) -> None:
        """A dispatch result that cannot be read for a moment reconnects and reads it again."""
        solver, _ = _ready(monkeypatch)
        iface, faults = solver._iface, iter([TimeoutError("get_block timed out")])
        read = iface.get_block

        def flaky(**kwargs):
            if (fault := next(faults, None)) is not None:
                raise fault
            return read(**kwargs)

        monkeypatch.setattr(iface, "get_block", flaky)
        assert solver.create_order(_model()).submit().order_id() == 1

    def test_undecodable_dispatch_result_is_sending(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="unclassified: no phase matched"))
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmitIncompleteError, match="no System.ExtrinsicSuccess or ExtrinsicFailed") as info:
            order.submit()
        assert isinstance(info.value.__cause__, QuipMetadataError)
        assert info.value.order is order
        assert order.status()["state"] == "sending"

    def test_unreadable_order_id_is_sending(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        solver._iface.events = []  # included and dispatched, but no JobProposed event.
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmitIncompleteError, match="no JobProposed") as info:
            order.submit()
        assert isinstance(info.value.__cause__, QuipMetadataError)
        assert order.order_id() is None
        assert order.status()["state"] == "sending"

    def test_finality_stall_is_sending(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        _stall_finality(monkeypatch, solver, captured)
        order = solver.create_order(_model())
        with pytest.raises(
            QuipSubmitIncompleteError, match="finality did not reach the window's end block 263"
        ) as info:
            order.submit()
        assert isinstance(info.value, QuipSubmissionError)
        assert (info.value.extrinsic_hash, info.value.nonce, info.value.window_end) == ("0xext", 0, 263)
        assert order.status() == {"state": "sending", "extrinsic_hash": "0xext", "nonce": 0, "window_end": 263}
        assert len(set(captured["sent"])) == 1

    def test_a_zero_poll_interval_does_not_spin(self, monkeypatch, virtual_clock) -> None:
        solver, captured = _ready(monkeypatch)
        solver._poll_interval = 0
        _stall_finality(monkeypatch, solver, captured)
        with pytest.raises(QuipSubmitIncompleteError, match="finality did not reach"):
            solver.create_order(_model()).submit()
        assert virtual_clock and all(seconds >= 1.0 for seconds in virtual_clock[:-1])

    def test_unreachable_node_is_sending_after_reconnecting(self, monkeypatch, virtual_clock) -> None:
        solver, captured = _ready(monkeypatch)
        _send_raises(monkeypatch, solver, ConnectionError("websocket closed"), captured, lands_on=None)
        connects: list[str] = []
        order = solver.create_order(_model())
        order.quote()  # signed while the node still answers.
        monkeypatch.setattr(solver._iface, "get_chain_finalised_head", lambda: _raise(ConnectionError("down")))
        monkeypatch.setattr(chain_module_metadata(), "connect", lambda url: connects.append(url) or solver._iface)
        with pytest.raises(QuipSubmitIncompleteError, match="could not read the finalized head: down") as info:
            order.submit()
        assert isinstance(info.value.__cause__, QuipConnectionError)
        assert connects and set(connects) == {"ws://fake:9944"}
        # Backoff doubles from the poll interval, capped at a minute.
        assert virtual_clock[:5] == [6.0, 12.0, 24.0, 48.0, 60.0]
        assert order.status()["state"] == "sending"

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
            "error": "System.ExtrinsicFailed: QuantumComputeMempool.RewardTooLow",
        }
        with pytest.raises(QuipSubmissionError, match="is failed"):
            order.submit()

    def test_solve_raises_incomplete_with_the_order(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        _stall_finality(monkeypatch, solver, captured)
        with pytest.raises(QuipSubmitIncompleteError) as info:
            solver.solve(_model())
        order = info.value.order
        assert isinstance(order, JobOrder) and order._state == "sending"
        solver._iface.finalized = None
        solver._iface.land(0)
        assert order.submit().order_id() == 1  # a solve() caller settles it through the order.

    def test_receipt_rows(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        _stall_finality(monkeypatch, solver, captured)
        sending = solver.create_order(_model())
        with pytest.raises(QuipSubmitIncompleteError):
            sending.submit()
        text = str(sending)
        rows = _box_rows(text)
        assert text.startswith("╭─ Job order (sending) ")
        assert "│ Quoted      1.002182560255 AGLS total, block 200 " in text
        start = rows.index("Receipt     sending, extrinsic")
        assert rows[start + 1 : start + 3] == ["            0xext", "            nonce 0, window ends at block 263"]
        assert {len(line) for line in text.splitlines()} == {72}

        solver, _ = _ready(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="QuantumComputeMempool.RewardTooLow"))
        failed = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError):
            failed.submit()
        reads: list[tuple[str, str]] = []
        monkeypatch.setattr(chain, "extrinsic_fee", lambda _iface, block, ext: reads.append((block, ext)) or 7)
        text = str(failed)
        rows = _box_rows(text)
        assert text.startswith("╭─ Job order (failed) ")
        start = rows.index("Paid        0.000000000007 AGLS fee")
        # A failed order reserved no reward, so the row has none.
        assert rows[start + 1] == "            quoted 1.002182560255 AGLS total, block 200"
        assert not any("reward" in row for row in rows[start:])
        assert "affordable" not in text
        assert f"│ Receipt     failed in block {solver._iface.head}: System.ExtrinsicFailed: " in text
        assert reads == [("0xblock", "0xext")]
        assert {len(line) for line in text.splitlines()} == {72}


def _raise(error: Exception):
    raise error


def chain_module_metadata():
    from xqsa.quip import metadata

    return metadata


class TestJobOrderInterrupted:
    """Ctrl-C during submit(): a draft before the bytes can reach the node, ``sending`` after."""

    def test_interrupt_before_the_send_stays_a_draft(self, monkeypatch, capsys) -> None:
        solver, captured = _ready(monkeypatch, autoconfirm=lambda quote: _raise(KeyboardInterrupt()))
        order = solver.create_order(_model())
        with pytest.raises(KeyboardInterrupt):
            order.submit()
        assert order._state == "draft"
        assert order._signed is None
        assert "sent" not in captured
        assert capsys.readouterr().err.count("[quip] submit() stopped") == 0

    @pytest.mark.parametrize("where", ["send", "settle"])
    def test_interrupt_after_the_send_is_sending(self, monkeypatch, capsys, where) -> None:
        solver, captured = _ready(monkeypatch)
        interrupt = KeyboardInterrupt()
        if where == "send":
            _send_raises(monkeypatch, solver, interrupt, captured, lands_on=None)
        else:
            _stall_finality(monkeypatch, solver, captured)
            monkeypatch.setattr(time, "sleep", lambda seconds: _raise(interrupt))
        order = solver.create_order(_model())
        with pytest.raises(KeyboardInterrupt) as info:
            order.submit()
        assert info.value is interrupt  # re-raised unchanged.
        assert order._state == "sending"
        assert order._signed.wire == captured["wires"][0]
        err = capsys.readouterr().err
        assert "propose_job 0xext (nonce 0)" in err
        assert "window ends at block 263" in err
        assert "calling submit() again resumes it; it never places a second order" in err

    def test_resume_after_an_interrupt_signs_nothing(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        _send_raises(monkeypatch, solver, KeyboardInterrupt(), captured)  # it landed despite the interrupt.
        order = solver.create_order(_model())
        with pytest.raises(KeyboardInterrupt):
            order.submit()
        assert order.submit().order_id() == 1
        assert captured["builds"] == 1
        assert len(captured["sent"]) == 1


def _sending(monkeypatch):
    """A funded solver and an order left ``sending`` by a finality stall: ``(solver, order, captured)``."""
    solver, captured = _ready(monkeypatch)
    _stall_finality(monkeypatch, solver, captured)
    order = solver.create_order(_model())
    with pytest.raises(QuipSubmitIncompleteError):
        order.submit()
    return solver, order, captured


def _finality_resumes(solver, *, lands: bool = True, error: str | None = None) -> None:
    """Finality catches up: with ``lands`` the proposal is in its block, else the window is final and unspent."""
    iface = solver._iface
    if lands:
        iface.finalized = None
        iface.land(0, error=error)
    else:
        iface.finalized = 263


class TestJobOrderSending:
    """A ``sending`` order: sent, not settled yet, and never signed again while it may land."""

    @pytest.mark.parametrize("action", ["set", "quote", "receipt", "wait"])
    def test_actions_settle_first_and_raise_while_pending(self, monkeypatch, action) -> None:
        solver, order, captured = _sending(monkeypatch)
        call = {
            "set": lambda: order.set(reward=2 * UNIT),
            "quote": order.quote,
            "receipt": order.receipt,
            "wait": order.wait,
        }[action]
        with pytest.raises(QuipSubmitIncompleteError, match="may still land up to block 263") as info:
            call()
        assert info.value.order is order
        assert order._state == "sending"
        assert captured["builds"] == 1

    def test_actions_go_ahead_once_it_landed(self, monkeypatch) -> None:
        solver, order, _ = _sending(monkeypatch)
        _finality_resumes(solver)
        assert order.receipt().order_id == 1
        assert order._state == "submitted"
        with pytest.raises(QuipSubmissionError, match="order 1 is submitted; set"):
            order.set(reward=2 * UNIT)

    def test_status_settles_without_raising(self, monkeypatch) -> None:
        solver, order, _ = _sending(monkeypatch)
        assert order.status()["state"] == "sending"
        _finality_resumes(solver, lands=False)
        assert order.status()["state"] == "draft"
        assert order._signed is None  # dead bytes: the next quote signs anew.

    def test_status_reports_a_failed_landing(self, monkeypatch) -> None:
        solver, order, _ = _sending(monkeypatch)
        _finality_resumes(solver, error="QuantumComputeMempool.RewardTooLow")
        assert order.status()["state"] == "failed"

    def test_quote_on_a_proven_draft_signs_afresh(self, monkeypatch) -> None:
        solver, order, captured = _sending(monkeypatch)
        _finality_resumes(solver, lands=False)
        assert order.quote().affordable
        assert captured["builds"] == 2

    def test_resume_returns_a_landed_order_without_signing(self, monkeypatch) -> None:
        solver, order, captured = _sending(monkeypatch)
        sends = len(captured["sent"])
        _finality_resumes(solver)
        assert order.submit() is order
        assert (order._state, order.order_id()) == ("submitted", 1)
        assert captured["builds"] == 1
        assert len(captured["sent"]) == sends  # settled on the first read: nothing sent again.

    def test_resume_of_a_failed_landing_raises_failed(self, monkeypatch) -> None:
        solver, order, captured = _sending(monkeypatch)
        _finality_resumes(solver, error="QuantumComputeMempool.RewardTooLow")
        with pytest.raises(QuipSubmissionError, match="fee was paid"):
            order.submit()
        assert order._state == "failed"
        assert captured["builds"] == 1

    def test_resume_while_still_pending_only_resends_the_same_bytes(self, monkeypatch) -> None:
        solver, order, captured = _sending(monkeypatch)
        with pytest.raises(QuipSubmitIncompleteError):
            order.submit()
        assert captured["builds"] == 1
        assert set(captured["sent"]) == {captured["wires"][0]}
        assert order._state == "sending"

    def test_resume_sends_afresh_only_once_proven_never_on_chain(self, monkeypatch) -> None:
        solver, order, captured = _sending(monkeypatch)
        _finality_resumes(solver, lands=False)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        assert order.submit().order_id() == 1
        assert order._state == "submitted"

    def test_copy_and_pickle_state_keep_the_sending_bytes(self, monkeypatch) -> None:
        import copy

        _, order, captured = _sending(monkeypatch)
        state = order.__getstate__()
        assert state["_state"] == "sending"
        assert state["_signed"].wire == captured["wires"][0]
        assert state["_signed"].nonce == 0
        assert copy.copy(order)._signed == order._signed

    def test_error_pickles_without_its_order(self, monkeypatch) -> None:
        import pickle

        solver, captured = _ready(monkeypatch)
        _stall_finality(monkeypatch, solver, captured)
        with pytest.raises(QuipSubmitIncompleteError) as info:
            solver.create_order(_model()).submit()
        restored = pickle.loads(pickle.dumps(info.value))
        assert restored.order is None
        assert (restored.extrinsic_hash, restored.nonce, restored.window_end) == ("0xext", 0, 263)
        assert str(restored) == str(info.value)


class TestUnconfirmedDeprecation:
    """``unconfirmed`` and ``QuipUnconfirmedError``: importable with a warning, never produced."""

    def test_import_warns_at_the_caller(self) -> None:
        import xqsa.quip

        with pytest.warns(DeprecationWarning, match="no longer produced since v0.4.5") as record:
            cls = xqsa.quip.QuipUnconfirmedError
        assert cls is QuipUnconfirmedError
        assert record[0].filename == __file__
        assert "QuipUnconfirmedError" not in xqsa.quip.__all__

    def test_umbrella_and_flat_names_warn_once(self) -> None:
        import xqsa
        from xquad.sa import quip

        with pytest.warns(DeprecationWarning, match="no longer produced") as record:
            assert quip.QuipUnconfirmedError is QuipUnconfirmedError
        assert record[0].filename == __file__
        with pytest.warns(DeprecationWarning) as record:
            assert xqsa.QuipUnconfirmedError is QuipUnconfirmedError
        assert len(record) == 1
        assert "import it from xqsa.quip" in str(record[0].message)
        assert "no longer produced since v0.4.5" in str(record[0].message)

    def test_internal_import_does_not_warn(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            from xqsa.quip.errors import QuipUnconfirmedError as internal
        assert internal is QuipUnconfirmedError

    def test_unknown_name_still_raises(self) -> None:
        import xqsa.quip

        with pytest.raises(AttributeError, match="has no attribute 'QuipNoSuchError'"):
            xqsa.quip.QuipNoSuchError  # noqa: B018

    def test_a_legacy_unconfirmed_order_loads_and_keeps_refusing(self, monkeypatch) -> None:
        import copy

        solver, captured = _ready(monkeypatch)
        legacy = solver.create_order(_model())
        # As 0.4.3 and 0.4.4 left it: no _signed bytes, its outcome unknown.
        legacy._state, legacy._ext_hash = "unconfirmed", "0xext"
        with pytest.warns(DeprecationWarning, match="look up extrinsic 0xext"):
            order = copy.copy(legacy)  # through __getstate__ and __setstate__, as unpickling does.
        assert order.status() == {
            "state": "unconfirmed",
            "extrinsic_hash": "0xext",
            "block_hash": None,
            "included_block": None,
        }
        for call in (order.submit, order.quote, lambda: order.set(reward=2 * UNIT)):
            with pytest.raises(QuipSubmissionError, match="is unconfirmed"):
                call()
        with pytest.raises(QuipSubmissionError, match="is unconfirmed, so its order id is unknown"):
            order.receipt()
        assert captured["builds"] == 0 and "sent" not in captured


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
        solver, _ = _ready(monkeypatch, access=AccessAllow(types=["Cpu"]).to_chain())
        with pytest.raises(TypeError, match="access: raw chain values"):
            solver.create_order(_model())

    def test_constructor_refuses_a_raw_client_default_that_does_not_decode(self, monkeypatch) -> None:
        with pytest.raises(QuipOrderOptionError, match="it cannot be read as an access rule"):
            _ready(monkeypatch, access={"Bid": 5})

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
        solver, captured = _ready(monkeypatch, resolution={"TopNEqual": {"n": 3}})
        with pytest.warns(DeprecationWarning, match="resolution: passing a raw chain value"):
            solver.solve(_model())
        assert captured["call_params"]["resolution"] == {"TopNEqual": {"n": 3}}

    def test_solve_refuses_an_out_of_limit_option_before_signing(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        with pytest.raises(QuipOrderOptionError, match="MinReward"):
            solver.solve(_model(), reward=1)
        assert captured["builds"] == 0


def _box_rows(text: str) -> list[str]:
    return [line[2:-2].rstrip() for line in text.splitlines() if line.startswith("│")]


class TestJobOrderDisplay:
    def test_draft_box(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        text = str(solver.create_order(_model(), topology="native"))
        assert text == "\n".join(
            [
                "╭─ Job order (draft) ──────────────────────────────────────────────────╮",
                "│ Network     custom endpoint                                          │",
                "├──────────────────────────────────────────────────────────────────────┤",
                "│ Model       2 variables, 1 coupling, spin                            │",
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

    def test_floors_row_in_model_units(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(
            _binary_model(), topology="native", max_energy=-2, min_solutions=5, min_diversity=0.4
        )
        rows = _box_rows(str(order))
        start = rows.index("Floors      energy <= -2 (chain -1.25), 5 solutions, diversity >=")
        assert rows[start + 1] == "            0.4"  # wrapped onto a blank-label line.

    def test_block_wait_of_one_is_singular(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        assert "Block wait  1 block" in _box_rows(str(solver.create_order(_model(), block_wait=1)))

    def test_spin_floors_row_has_no_chain_value(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        assert "Floors      energy <= -3" in _box_rows(str(solver.create_order(_model(), max_energy=-3)))

    def test_quoted_and_submitted_rows(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        order.quote()
        assert "│ Quote       1.002182560255 AGLS total, affordable, block 200 " in str(order)
        order.submit()
        monkeypatch.setattr(chain, "proposal_fee", lambda _iface, _block, _order_id: 1_907_881_410)
        rows = _box_rows(str(order))
        assert str(order).startswith("╭─ Job order (submitted) ")
        start = rows.index("Paid        0.001907881410 AGLS fee")
        assert rows[start + 1 : start + 4] == [
            "            1.000000000000 AGLS reward",
            "            quoted 1.002182560255 AGLS total, block 200",
            "Receipt     order 1, included in block 200, final",
        ]
        assert not any("affordable" in row for row in rows)
        assert {len(line) for line in str(order).splitlines()} == {72}

    def test_paid_row_keeps_the_fee_unknown_when_its_read_faults(self, monkeypatch, caplog) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()

        def _boom(*_args):
            raise QuipConnectionError("node gone")

        monkeypatch.setattr(chain, "proposal_fee", _boom)
        with caplog.at_level(logging.WARNING, logger="xqsa.quip"):
            rows = _box_rows(str(order))
            str(order)
        start = rows.index("Paid        fee unknown")
        assert rows[start + 1 : start + 3] == [
            "            1.000000000000 AGLS reward",
            "            quoted 1.002182560255 AGLS total, block 200",
        ]
        # The row already says the fee is unknown; a print in a loop must not log on every pass.
        assert "fee" not in caplog.text

    def test_paid_row_keeps_a_read_fee_and_retries_a_failed_read(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        fees = iter([None, 1_907_881_410])
        reads: list[int | None] = []

        def proposal_fee(_iface, _block, _order_id) -> int:
            reads.append(fee := next(fees))
            if fee is None:
                raise QuipConnectionError("node gone")
            return fee

        monkeypatch.setattr(chain, "proposal_fee", proposal_fee)
        assert "Paid        fee unknown" in _box_rows(str(order))
        assert "Paid        0.001907881410 AGLS fee" in _box_rows(str(order))
        str(order)
        assert reads == [None, 1_907_881_410]  # the third print reads nothing.

    def test_receipt_row_keeps_the_plain_text_when_the_live_read_faults(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()

        def _boom():
            raise QuipConnectionError("node gone")

        monkeypatch.setattr(order.receipt(), "_outcome", _boom)
        text = str(order)
        assert "│ Receipt     order 1, included in block 200 " in text
        assert ", final" not in text
        assert {len(line) for line in text.splitlines()} == {72}

    def test_receipt_row_names_the_leader(self, monkeypatch) -> None:
        ss58_encode = pytest.importorskip("scalecodec.utils.ss58").ss58_encode

        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        solver._iface.storage[("QuantumComputeMempool", "OrderFrontRunner")] = {
            "solver": "0x" + "cd" * 32,
            "energy_milli": -5,
        }
        leader = ss58_encode(b"\xcd" * 32, 42)
        text = str(order)
        assert "│ Receipt     order 1, included in block 200, final, leader " in text
        assert f"│             {leader} " in text  # the address wraps whole onto the next line.

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


MEMPOOL = "QuantumComputeMempool"
# Energies of _model() by (s0, s1): (-1, 1) -> -4, (1, 1) -> 0, (1, -1) and (-1, -1) -> +2.
BEST, MID, HIGH, HIGH2 = (-1, 1), (1, 1), (1, -1), (-1, -1)


def _answer(job, solver_id: str, spins: list[tuple[int, int]], energy_milli: int, submitted_at: int = 0) -> tuple:
    """One ``OrderSolutions`` entry holding ``spins`` as vectors."""
    vectors = [_spin_vector(job, {0: s0, 1: s1}) for s0, s1 in spins]
    return solver_id.encode(), {**_submission(solver_id, vectors, energy_milli), "submitted_at": submitted_at}


class TestJobOrderSolutions:
    def test_best_follows_the_front_runner_on_a_tie(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        solver._iface.maps[(MEMPOOL, "OrderSolutions")] = [
            _answer(order._job, "0xA", [BEST], -4000, 0),
            _answer(order._job, "0xB", [BEST], -4000, 1),
        ]
        solver._iface.storage[(MEMPOOL, "OrderFrontRunner")] = {"solver": "0xB", "energy_milli": -4000}
        best = order.best()
        assert (best.solver, best.leader, best.energy) == ("0xB", True, -4)

    def test_solutions_lists_every_vector_best_first(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        solver._iface.maps[(MEMPOOL, "OrderSolutions")] = [_answer(order._job, "0xA", [HIGH, MID, BEST, HIGH2], -4000)]
        solutions = order.solutions()
        assert [s.energy for s in solutions] == [-4, 0, 2, 2]
        assert {s.solver for s in solutions} == {"0xA"}

    def test_solutions_for_a_solver_that_did_not_answer(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        with pytest.raises(KeyError, match="0xZ did not answer order"):
            order.solutions("0xZ")

    @pytest.mark.parametrize("method", ["best", "solutions"])
    def test_draft_has_no_receipt(self, monkeypatch, method) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model())
        with pytest.raises(QuipSubmissionError, match=r"submit\(\) it first"):
            getattr(order, method)()

    def test_best_on_an_unanswered_order_names_its_floors(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), max_energy=-3, min_solutions=2).submit()
        solver._iface.maps[(MEMPOOL, "OrderSolutions")] = []
        with pytest.raises(
            QuipJobFailedError, match=r"no answers under its quality floors \(energy <= -3, 2 solutions\)"
        ):
            order.best()

    def test_best_on_an_answered_order_skips_the_floors_note(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), max_energy=-3).submit()
        solver._iface.maps[(MEMPOOL, "OrderSolutions")] = [_answer(order._job, "0xA", [BEST], -4000)]
        monkeypatch.setattr(JobOrder, "_floors_text", lambda self: pytest.fail("floors note built for an answer"))
        assert order.best().energy == -4

    def test_best_on_an_unanswered_order_names_its_allowlist(self, monkeypatch) -> None:
        solver, _ = _ready(monkeypatch)
        order = solver.create_order(_model(), access=AccessAllow(types=["Gpu"])).submit()
        solver._iface.maps[(MEMPOOL, "OrderSolutions")] = []
        with pytest.raises(
            QuipJobFailedError, match=r"only allowlisted solvers could answer \(types Gpu\) and none did"
        ):
            order.best()

    def test_best_on_an_unanswered_order_does_not_reclaim(self, monkeypatch) -> None:
        solver, captured = _ready(monkeypatch)
        order = solver.create_order(_model()).submit()
        solver._iface.maps[(MEMPOOL, "OrderSolutions")] = []
        builds = captured["builds"]
        with pytest.raises(QuipJobFailedError):
            order.best()
        assert captured["builds"] == builds
        assert captured["call_function"] == "propose_job"  # the submit; nothing was signed since.
