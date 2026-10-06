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
Order options for :class:`~xqsa.quip.SolverQuip`, and the checks they pass before anything is signed.

An order inherits the client's defaults, and ``create_order`` or ``set``
override them. Every change is checked against the whole configuration and
the chain limits read at client creation, so a reward below ``MinReward`` or
a deadline over ``MaxDeadlineBlocks`` is refused here instead of paying a fee
to fail on chain.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Any

from xqsa.quip.errors import QuipOrderOptionError

if TYPE_CHECKING:
    from xqsa.quip.chain import ChainLimits

# The options an order accepts; ``mode``, ``resolution`` and ``delivery`` are
# client defaults only.
ORDER_OPTIONS = frozenset({"reward", "deadline_blocks", "block_wait", "topology", "mapping"})

# The shortest deadline worth proposing, and the one below which orders often
# go unanswered. On aglais a 1-block deadline was solved 0 of 11 times, 30
# blocks 28 of 38, and 100 blocks 108 of 108. The chain itself accepts 0,
# which expires on arrival.
MIN_DEADLINE_BLOCKS = 10
RELIABLE_DEADLINE_BLOCKS = 100

_SUPPORTED_MODE = "Open"
_SUPPORTED_RESOLUTION = "SingleBest"
_SUPPORTED_DELIVERY = "OnChainOnly"


@dataclass(frozen=True)
class _OrderOptions:
    """One order's full configuration: its own options plus the inherited client defaults."""

    reward: int
    deadline_blocks: int
    block_wait: int
    topology: str | None
    mapping: Mapping[int, int] | None
    mode: Any
    resolution: Any
    delivery: Any


def check_client_defaults(mode: Any, resolution: Any, delivery: Any) -> None:
    """Refuse the ``mode``, ``resolution`` and ``delivery`` values no order can use yet.

    Bare strings are checked here. A raw chain dict in ``mode`` or
    ``resolution`` passes, so the deprecated ``solve()`` path keeps working;
    ``create_order`` refuses it. ``delivery`` accepts only ``"OnChainOnly"``.

    Raises:
        ValueError: naming the unsupported value.
    """
    if isinstance(mode, str) and mode != _SUPPORTED_MODE:
        raise ValueError(f"mode={mode!r} is not supported yet; only {_SUPPORTED_MODE!r} orders are (QUI-1607)")
    if isinstance(resolution, str) and resolution != _SUPPORTED_RESOLUTION:
        raise ValueError(
            f"resolution={resolution!r} is not supported yet; only {_SUPPORTED_RESOLUTION!r} is (QUI-1606)"
        )
    if delivery != _SUPPORTED_DELIVERY:
        raise ValueError(
            f"delivery={delivery!r}: result delivery is not supported yet: ResultReady only fires on a claim, "
            "which network solvers do not make"
        )


def merge_options(
    current: _OrderOptions, updates: Mapping[str, Any], limits: ChainLimits, *, strict: bool
) -> _OrderOptions:
    """Apply ``updates`` to ``current`` and check the whole result.

    The strict path (``create_order``, ``set``) refuses unknown keywords and
    raw chain dicts with ``TypeError``. The lenient path, used only by the
    deprecated ``SolverQuip.solve``/``quote`` surface, warns and drops an
    unknown keyword and warns and passes a dict through.

    Raises:
        TypeError: on the strict path, for an unknown keyword or a dict value;
            on either path, for a non-integer reward, deadline or block wait.
        QuipOrderOptionError: if an option is outside its chain limit.
    """
    known: dict[str, Any] = {}
    for name, value in updates.items():
        if name not in ORDER_OPTIONS:
            if strict:
                raise TypeError(f"unknown order option {name!r}; expected one of {', '.join(sorted(ORDER_OPTIONS))}")
            warnings.warn(f"ignoring unknown SolverQuip option {name!r}", DeprecationWarning, stacklevel=3)
            continue
        known[name] = value
    merged = replace(current, **known)
    _check_raw_values(merged, strict=strict)
    _check_limits(merged, limits)
    return merged


def _check_raw_values(options: _OrderOptions, *, strict: bool) -> None:
    """Refuse (strict) or warn about (lenient) raw chain dicts anywhere but ``mapping``."""
    for field in fields(options):
        if field.name == "mapping" or not isinstance(getattr(options, field.name), Mapping):
            continue
        if strict:
            raise TypeError(f"{field.name}: raw chain values are not accepted")
        warnings.warn(
            f"{field.name}: passing a raw chain value is deprecated and unchecked", DeprecationWarning, stacklevel=4
        )


def _check_limits(options: _OrderOptions, limits: ChainLimits) -> None:
    """Check reward, deadline and block wait against each other and the chain limits."""
    reward = _require_int(options.reward, "reward")
    deadline = _require_int(options.deadline_blocks, "deadline_blocks")
    block_wait = _require_int(options.block_wait, "block_wait")

    if limits.min_reward is not None and reward < limits.min_reward:
        raise QuipOrderOptionError(
            "reward", f"reward={reward} planck is below the chain's MinReward of {limits.min_reward}"
        )
    max_deadline = limits.max_deadline_blocks
    if deadline < MIN_DEADLINE_BLOCKS or (max_deadline is not None and deadline > max_deadline):
        upper = f" and at most MaxDeadlineBlocks ({max_deadline})" if max_deadline is not None else ""
        raise QuipOrderOptionError(
            "deadline_blocks", f"deadline_blocks={deadline} must be at least {MIN_DEADLINE_BLOCKS}{upper}"
        )
    max_wait = limits.max_block_wait
    if block_wait < 0 or (max_wait is not None and block_wait > max_wait):
        upper = f" and at most MaxBlockWait ({max_wait})" if max_wait is not None else ""
        raise QuipOrderOptionError("block_wait", f"block_wait={block_wait} must be at least 0{upper}")

    if deadline < RELIABLE_DEADLINE_BLOCKS:
        warnings.warn(
            f"deadline_blocks={deadline}: orders shorter than {RELIABLE_DEADLINE_BLOCKS} blocks often go unanswered",
            UserWarning,
            stacklevel=4,
        )
    if block_wait >= deadline:
        warnings.warn(
            f"block_wait={block_wait} is not below deadline_blocks={deadline}; the deadline ends the order "
            "before the wait for better answers can",
            UserWarning,
            stacklevel=4,
        )


def _require_int(value: Any, name: str) -> int:
    """Return ``value`` if it is an ``int`` (not a ``bool``), else raise ``TypeError``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    return value
