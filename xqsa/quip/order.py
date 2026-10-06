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
:class:`JobOrder`, one Quip order, and the checks its options pass before anything is signed.

:meth:`SolverQuip.create_order <xqsa.quip.SolverQuip.create_order>` returns a
draft. A draft is edited with :meth:`JobOrder.set`, priced with
:meth:`JobOrder.quote` and sent with :meth:`JobOrder.submit`; after that it
is read-only. An order inherits the client's defaults, and ``create_order`` or
``set`` override them. Every change is checked against the whole configuration
and the chain limits read at client creation, so a reward below ``MinReward``
or a deadline over ``MaxDeadlineBlocks`` is refused here instead of paying a
fee to fail on chain.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Any, Literal, Self

from xqsa.quip.display import box
from xqsa.quip.errors import QuipOrderOptionError, QuipSubmissionError
from xqsa.quip.quote import _format_planck

if TYPE_CHECKING:
    from xqsa.quip.chain import ChainLimits
    from xqsa.quip.client import SolverQuip
    from xqsa.quip.codec import IsingJob
    from xqsa.quip.quote import JobQuote
    from xqvm_py.xqmx import XQMX

OrderState = Literal["draft", "submitted", "final"]

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
    current: _OrderOptions, updates: Mapping[str, Any], limits: ChainLimits, *, strict: bool, stacklevel: int = 2
) -> _OrderOptions:
    """Apply ``updates`` to ``current`` and check the whole result.

    The strict path (``create_order``, ``set``) refuses unknown keywords and
    raw chain dicts with ``TypeError``. The lenient path, used only by the
    deprecated ``SolverQuip.solve``/``quote`` surface, warns and drops an
    unknown keyword and warns and passes a dict through. ``stacklevel`` places
    the warnings as if ``warnings.warn`` were called here.

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
            warnings.warn(f"ignoring unknown SolverQuip option {name!r}", DeprecationWarning, stacklevel=stacklevel)
            continue
        known[name] = value
    merged = replace(current, **known)
    _check_raw_values(merged, strict=strict, stacklevel=stacklevel + 1)
    _check_limits(merged, limits, stacklevel=stacklevel + 1)
    return merged


def _check_raw_values(options: _OrderOptions, *, strict: bool, stacklevel: int) -> None:
    """Refuse (strict) or warn about (lenient) raw chain dicts anywhere but ``mapping``."""
    for field in fields(options):
        if field.name == "mapping" or not isinstance(getattr(options, field.name), Mapping):
            continue
        if strict:
            raise TypeError(f"{field.name}: raw chain values are not accepted")
        warnings.warn(
            f"{field.name}: passing a raw chain value is deprecated and unchecked",
            DeprecationWarning,
            stacklevel=stacklevel,
        )


def _check_limits(options: _OrderOptions, limits: ChainLimits, *, stacklevel: int) -> None:
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
            stacklevel=stacklevel,
        )
    if block_wait >= deadline:
        warnings.warn(
            f"block_wait={block_wait} is not below deadline_blocks={deadline}; the deadline ends the order "
            "before the wait for better answers can",
            UserWarning,
            stacklevel=stacklevel,
        )


def _require_int(value: Any, name: str) -> int:
    """Return ``value`` if it is an ``int`` (not a ``bool``), else raise ``TypeError``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    return value


class JobOrder:
    """One Quip order: its model, placement and options, from draft to final.

    Built by :meth:`~xqsa.quip.SolverQuip.create_order`, never directly. A
    draft is edited with :meth:`set`, priced with :meth:`quote` and proposed
    with :meth:`submit`. After submission :meth:`set`, :meth:`quote` and
    :meth:`submit` raise; :meth:`status` works in every state. Options are read
    through read-only properties and :meth:`options`.

    :meth:`quote` and :meth:`submit` sign the same call params, built once per
    configuration. The quote's signed copy only prices the call; :meth:`submit`
    signs afresh with the account's nonce at that moment, so a quote taken
    earlier never leaves a stale nonce behind.

    Examples:
        Connects to the network, so it is not run as a doctest::

            order = solver.create_order(model, deadline_blocks=200)
            order.set(reward=2 * order.reward)
            print(order.quote())
            order.submit()
            order.status()
    """

    def __init__(
        self, client: SolverQuip, model: XQMX, options: _OrderOptions, job: IsingJob, genesis_hash: str
    ) -> None:
        self._client = client
        self._model = model
        self._options = options
        self._job = job
        self._genesis_hash = genesis_hash
        self._state: OrderState = "draft"
        # The call params and their quote; the content only, never a signed transaction.
        self._prepared: tuple[dict, JobQuote] | None = None
        self._order_id: int | None = None
        self._included_block: int | None = None
        self._submitted_at: float | None = None

    # -- options ---------------------------------------------------------

    @property
    def reward(self) -> int:
        """The reward in planck, reserved at proposal and refunded if the order goes unanswered."""
        return self._options.reward

    @property
    def deadline_blocks(self) -> int:
        """Blocks the order stays open for answers."""
        return self._options.deadline_blocks

    @property
    def block_wait(self) -> int:
        """Blocks to wait for better answers after the first; 0 takes the first."""
        return self._options.block_wait

    @property
    def topology(self) -> str:
        """The topology hash the model is placed onto, or ``"native"``."""
        return self._options.topology

    @property
    def mapping(self) -> Mapping[int, int] | None:
        """The explicit variable to node placement, or ``None`` when it was searched."""
        return self._options.mapping

    def options(self) -> dict[str, Any]:
        """Return the order's options as a plain dict."""
        mapping = self._options.mapping
        return {
            "reward": self.reward,
            "deadline_blocks": self.deadline_blocks,
            "block_wait": self.block_wait,
            "topology": self.topology,
            "mapping": None if mapping is None else dict(mapping),
        }

    def set(self, **options: Any) -> Self:
        """Change options on a draft and recheck the whole configuration.

        Re-places the model only when ``topology`` or ``mapping`` changed, and
        drops any cached quote.

        Raises:
            QuipSubmissionError: if the order is no longer a draft.
            TypeError: for an unknown option or a raw chain value.
            QuipOrderOptionError: if an option is outside its chain limit.
            ValueError: if ``mapping`` is combined with a native topology.
        """
        self._require_draft("set")
        merged = merge_options(self._options, options, self._client._limits, strict=True, stacklevel=3)
        merged = self._client._settle_topology(merged)
        if (merged.topology, merged.mapping) != (self._options.topology, self._options.mapping):
            self._job = self._client._place(self._model, merged)
        self._options = merged
        self._prepared = None
        return self

    # -- lifecycle -------------------------------------------------------

    def quote(self) -> JobQuote:
        """Price the draft without proposing it.

        Repeat calls reuse the quote until the next :meth:`set`.

        Raises:
            QuipSubmissionError: if the order is no longer a draft, or the
                extrinsic cannot be built.
            QuipConnectionError: if a chain read faults.
        """
        self._require_draft("quote")
        return self._prepare()[1]

    def submit(self) -> Self:
        """Propose the draft and return once it is included in a block.

        Shows the quote, refuses a shortfall no faucet drip can cover, asks the
        ``autoconfirm`` and, if short, ``autofund`` gates, then signs with the
        account's current nonce and proposes. Signing comes after the gates, so
        time spent at a prompt or waiting on a drip never ages the nonce.

        Raises:
            QuipSubmissionError: if the order is no longer a draft, the account
                cannot cover the quote, the nonce moves twice while submitting,
                or proposing fails.
            QuipCancelledError: if a consent gate declines.
            QuipFaucetError: if the faucet refuses or cannot be reached.
        """
        self._require_draft("submit")
        quoted_earlier = self._prepared is not None
        call_params, quote = self._prepare()
        if quoted_earlier:
            # The balance may have moved since quote(), e.g. another order's
            # reward was reserved; the gates must judge the account as it is now.
            quote = replace(quote, balance_planck=self._client._free_balance())
            self._prepared = (call_params, quote)
        self._client._clear_gates(quote)
        self._submitted_at = time.perf_counter()
        self._order_id, self._included_block = self._client._propose(call_params, self.reward)
        self._state = "submitted"
        return self

    def order_id(self) -> int | None:
        """Return the chain's order id, or ``None`` while the order is a draft."""
        return self._order_id

    def status(self) -> dict[str, Any]:
        """Return the order's state and, once submitted, its on-chain lifecycle.

        A draft reports ``{"state": "draft", "options": ...}``. A submitted
        order adds ``"state"`` to :meth:`SolverQuip.status
        <xqsa.quip.SolverQuip.status>`, and moves to ``"final"`` once the
        chain says it is.
        """
        if self._order_id is None:
            return {"state": self._state, "options": self.options()}
        snapshot = self._client.status(self._order_id)
        if snapshot["is_final"]:
            self._state = "final"
        return {"state": self._state, **snapshot}

    def _prepare(self) -> tuple[dict, JobQuote]:
        """Build the call params and their quote once per configuration."""
        if self._prepared is None:
            call_params = self._client._propose_call_params(self._job, self._options)
            self._prepared = (call_params, self._client._quote_call(call_params, self._job, self._options))
        return self._prepared

    def _require_draft(self, action: str) -> None:
        if self._state != "draft":
            raise QuipSubmissionError(
                f"order {self._order_id} is {self._state}; {action}() works only on a draft order"
            )

    # -- display ---------------------------------------------------------

    def __str__(self) -> str:
        """Render the order as a box: network, problem, terms, then its quote and receipt."""
        symbol, decimals = self._client._token()
        options = self._options
        topology = self._job.topology
        placed = (
            f"native, {topology.num_nodes} spins"
            if options.topology == "native"
            else f"topology, {topology.num_nodes} spins\n{options.topology}"
        )
        model = self._model
        problem = [
            (
                "Model",
                f"{model.size} variables, {len(model.quadratic)} couplings, {model.domain.name.lower()}",
            ),
            ("Placed", placed),
        ]
        terms = [
            ("Payout", _variant_text(options.resolution)),
            ("Access", _variant_text(options.mode)),
            ("Floors", "none"),
            ("Deadline", f"{options.deadline_blocks} blocks"),
            ("Block wait", f"{options.block_wait} blocks"),
            ("Reward", f"{_format_planck(options.reward, decimals)} {symbol}"),
        ]
        if self._prepared is None:
            quoted = "not quoted"
        else:
            quote = self._prepared[1]
            total = f"{_format_planck(quote.total_planck, decimals)} {symbol}"
            afford = "affordable" if quote.affordable else "not affordable"
            quoted = ", ".join(
                [f"{total} total", afford] + ([f"block {quote.quoted_at_block}"] if quote.quoted_at_block else [])
            )
        if self._order_id is None:
            receipt = "not submitted"
        elif self._included_block is None:
            receipt = f"order {self._order_id}"
        else:
            receipt = f"order {self._order_id}, included in block {self._included_block}"
        network = [("Network", self._client._network or "custom endpoint")]
        summary = [("Quote", quoted), ("Receipt", receipt)]
        return box(f"Job order ({self._state})", [network, problem, terms, summary])


def _variant_text(value: Any) -> str:
    """Render a unit enum variant as words (``"SingleBest"`` -> ``"single best"``); anything else as ``repr``."""
    if not isinstance(value, str):
        return repr(value)
    words = "".join(f" {char}" if char.isupper() else char for char in value).split()
    return " ".join(word.lower() for word in words)
