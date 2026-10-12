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
Payout rules for a Quip order: who among the answering solvers shares the reward, and how.

:class:`ResolutionSingleBest` pays the whole reward to the best answer.
:class:`ResolutionTopNEqual` splits it evenly across the best ``n`` solvers,
:class:`ResolutionTopNWeighted` by rank. The chain ranks solvers by energy,
lowest first, with the earlier submitter first on a tie, and keeps at most
``n``; fewer answers than ``n`` means fewer winners. Allocations are computed
when the reward is claimed, so :meth:`Resolution.payouts` mirrors the pallet's
arithmetic to the planck.
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# The most winners a top-N order can name (the pallet's ``MAX_REWARD_WINNERS``).
MAX_WINNERS = 32

# The first runtime whose ``TopNWeighted`` weights by rank. Older runtimes
# weight by absolute energy, which can give a worse answer the larger share.
MIN_RANK_WEIGHTED_SPEC = 119


class Resolution(ABC):
    """Base class of the payout rules; use one of its subclasses."""

    @abstractmethod
    def to_chain(self) -> Any:
        """Return the rule as the ``RewardResolution`` value ``propose_job`` takes."""

    @abstractmethod
    def payouts(self, ranked: Sequence[Any], reward: int) -> list[int]:
        """Return the planck each ranked solver receives, best first, one per entry of ``ranked``."""

    @abstractmethod
    def __str__(self) -> str:
        """Name the rule for the order and receipt boxes."""


@dataclass(frozen=True)
class ResolutionSingleBest(Resolution):
    """The best answer takes the whole reward."""

    def to_chain(self) -> Any:
        """Return ``"SingleBest"``."""
        return "SingleBest"

    def payouts(self, ranked: Sequence[Any], reward: int) -> list[int]:
        """Return ``[reward]`` for the front runner, or ``[]`` with no answer."""
        return [reward] if ranked else []

    def __str__(self) -> str:
        return "single best"


@dataclass(frozen=True)
class _TopN(Resolution):
    """A rule paying the best ``n`` solvers, ``n`` from 1 to :data:`MAX_WINNERS`."""

    n: int

    def __post_init__(self) -> None:
        name = type(self).__name__
        if isinstance(self.n, bool) or not isinstance(self.n, int):
            raise TypeError(f"{name}: n must be an int, got {self.n!r}")
        if not 1 <= self.n <= MAX_WINNERS:
            raise ValueError(f"{name}: n must be from 1 to {MAX_WINNERS}, got {self.n}")
        if self.n == 1:
            warnings.warn(f"{name}(1) pays exactly like ResolutionSingleBest()", UserWarning, stacklevel=3)

    def to_chain(self) -> Any:
        """Return ``{"<variant>": {"n": n}}``."""
        return {type(self).__name__.removeprefix("Resolution"): {"n": self.n}}


@dataclass(frozen=True)
class ResolutionTopNEqual(_TopN):
    """The best ``n`` solvers split the reward evenly; the best takes the remainder."""

    def payouts(self, ranked: Sequence[Any], reward: int) -> list[int]:
        """Return ``reward // k`` each for the ``k`` ranked solvers, with ``reward % k`` added to the first."""
        k = len(ranked)
        if k == 0:
            return []
        base = reward // k
        return [base + reward % k] + [base] * (k - 1)

    def __str__(self) -> str:
        return f"top {self.n}, equal split"


@dataclass(frozen=True)
class ResolutionTopNWeighted(_TopN):
    """The best ``n`` solvers split the reward by rank: of ``k`` winners, rank ``r`` has weight ``k - r``.

    Energies decide the order only. Rank weighting needs runtime
    :data:`MIN_RANK_WEIGHTED_SPEC` or later; see :func:`check_rank_weighting`.
    """

    def payouts(self, ranked: Sequence[Any], reward: int) -> list[int]:
        """Return each rank's weighted share; whatever rounding leaves goes to the first."""
        k = len(ranked)
        if k == 0:
            return []
        total = k * (k + 1) // 2
        shares = [(reward // total) * w + (reward % total) * w // total for w in range(k, 0, -1)]
        shares[0] += reward - sum(shares)
        return shares

    def __str__(self) -> str:
        return f"top {self.n}, weighted by rank"


_TOP_N_VARIANTS: dict[str, type[_TopN]] = {"TopNEqual": ResolutionTopNEqual, "TopNWeighted": ResolutionTopNWeighted}

# Every ``RewardResolution`` variant this client can encode.
VARIANTS = frozenset({"SingleBest", *_TOP_N_VARIANTS})


def from_chain(value: Any) -> Resolution:
    """Decode a ``RewardResolution`` as an order reads it back.

    Accepts the unit variant as ``"SingleBest"`` (or ``{"SingleBest": None}``)
    and a top-N variant as ``{"TopNEqual": {"n": 2}}``.

    Raises:
        ValueError: for any other value.
    """
    if value == "SingleBest" or value == {"SingleBest": None}:
        return ResolutionSingleBest()
    if isinstance(value, Mapping) and len(value) == 1:
        [(variant, fields)] = value.items()
        cls = _TOP_N_VARIANTS.get(variant)
        if cls is not None and isinstance(fields, Mapping) and "n" in fields:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # an n = 1 order on chain is a fact, not a choice to warn about.
                return cls(int(fields["n"]))
    raise ValueError(f"unknown RewardResolution {value!r}")


def coerce_resolution(value: Any) -> Resolution | Mapping[str, Any]:
    """Return ``value`` as a :class:`Resolution` where it names one.

    ``"SingleBest"`` becomes :class:`ResolutionSingleBest`, and a
    :class:`Resolution` passes. A raw chain dict, which only the deprecated
    ``solve()`` path accepts, passes unchanged for the option checks to judge.

    Raises:
        ValueError: for anything else, such as another string or ``None``.
    """
    if value == "SingleBest":
        return ResolutionSingleBest()
    if isinstance(value, (Resolution, Mapping)):
        return value
    raise ValueError(
        f"resolution={value!r} is not a payout rule; pass 'SingleBest' or one of ResolutionSingleBest(), "
        "ResolutionTopNEqual(n), ResolutionTopNWeighted(n)"
    )


def check_rank_weighting(resolution: Any, spec_version: int | None) -> None:
    """Refuse :class:`ResolutionTopNWeighted` on a runtime that does not weight by rank.

    Below :data:`MIN_RANK_WEIGHTED_SPEC` the chain weights a top-N split by
    absolute energy, so a worse answer can take the larger share, and the
    local arithmetic would not match what it pays. An unknown ``spec_version``
    is refused too. Every other rule passes.

    Raises:
        ValueError: explaining the refusal.
    """
    if not isinstance(resolution, ResolutionTopNWeighted):
        return
    if spec_version is None or spec_version < MIN_RANK_WEIGHTED_SPEC:
        runtime = "an unknown runtime version" if spec_version is None else f"runtime {spec_version}"
        raise ValueError(
            f"ResolutionTopNWeighted needs runtime {MIN_RANK_WEIGHTED_SPEC} or later, and the chain runs {runtime}; "
            "older runtimes weight by absolute energy, so a worse answer can take the larger share"
        )
