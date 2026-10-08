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
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, Literal, Self

from xqsa.quip.display import box
from xqsa.quip.errors import QuipOrderOptionError, QuipSubmissionError, QuipUnconfirmedError
from xqsa.quip.quote import _format_planck

if TYPE_CHECKING:
    from xqsa.quip.chain import ChainLimits
    from xqsa.quip.client import SolverQuip
    from xqsa.quip.codec import IsingJob
    from xqsa.quip.quote import JobQuote
    from xqvm_py.xqmx import XQMX

OrderState = Literal["draft", "submitted", "unconfirmed", "failed", "finalized"]

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
    for option in fields(options):
        if option.name == "mapping" or not isinstance(getattr(options, option.name), Mapping):
            continue
        if strict:
            raise TypeError(f"{option.name}: raw chain values are not accepted")
        warnings.warn(
            f"{option.name}: passing a raw chain value is deprecated and unchecked",
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


@dataclass(frozen=True)
class _SignedCall:
    """A signed ``propose_job`` extrinsic and the nonce it was signed with.

    ``wire`` is a sendable transaction, so it is kept out of ``repr``.
    """

    wire: bytes = field(repr=False)
    ext_hash: str
    nonce: int


@dataclass(frozen=True)
class _Sent:
    """What a ``propose_job`` that reached a block placed.

    ``order_id`` is set when the dispatch succeeded; ``error`` holds the
    chain's dispatch error when it failed, in which case the fee was paid and
    no order exists.
    """

    ext_hash: str
    block_hash: str | None
    included_block: int | None
    order_id: int | None = None
    error: str | None = None


class JobOrder:
    """One Quip order: its model, placement and options, from draft to finalized.

    Built by :meth:`~xqsa.quip.SolverQuip.create_order`, never directly. A
    draft is edited with :meth:`set`, priced with :meth:`quote` and proposed
    with :meth:`submit`. Every other state refuses :meth:`set`, :meth:`quote`
    and :meth:`submit`; :meth:`status` works in every state. Options are read
    through read-only properties and :meth:`options`.

    States: ``draft``; ``submitted`` once placed with a known order id, then
    ``finalized`` once the chain closes it; ``failed`` when it was included but
    its dispatch failed, so the fee was paid and no order exists; and
    ``unconfirmed`` when it was sent but its outcome or order id is unknown.
    An ``unconfirmed`` order may be on chain, so it is never sent again.

    The order caches its signed extrinsic, never a quote. :meth:`quote` signs
    once and re-prices the same bytes on every call, reading the fee, balance
    and head block afresh. :meth:`submit` quotes afresh too, then sends the
    cached bytes if the account nonce has not moved, and re-signs otherwise.
    The cache is dropped on :meth:`set` and on every :meth:`submit`, whatever
    its outcome, and is never shown, logged, pickled or copied.

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
        # The signed extrinsic, reused until set() or submit(); never a quote.
        self._signed: _SignedCall | None = None
        # The most recent quote, from quote() or submit(); shown, never gated on.
        self._last_quote: JobQuote | None = None
        self._order_id: int | None = None
        self._included_block: int | None = None
        # The sent extrinsic's hash and inclusion block, and the chain's error when its dispatch failed.
        self._ext_hash: str | None = None
        self._block_hash: str | None = None
        self._error: str | None = None
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
        self._signed = None
        return self

    # -- lifecycle -------------------------------------------------------

    def quote(self) -> JobQuote:
        """Price the draft without proposing it.

        Every call reads the fee, balance and head block afresh. The signed
        extrinsic it prices is reused until the next :meth:`set` or
        :meth:`submit`.

        Raises:
            QuipSubmissionError: if the order is no longer a draft, or the
                extrinsic cannot be built.
            QuipConnectionError: if a chain read faults.
        """
        self._require_draft("quote")
        return self._fresh_quote(self._signed_call())

    def submit(self) -> Self:
        """Propose the draft and return once it is included in a block.

        Quotes afresh, shows the quote, refuses a shortfall no faucet drip can
        cover, and asks the ``autoconfirm`` and, if short, ``autofund`` gates.
        It then sends the bytes :meth:`quote` signed if the account nonce has
        not moved since, and re-signs otherwise. The cached extrinsic is
        dropped whatever the outcome, so a retry signs anew.

        A failure before the extrinsic certainly reaches the chain leaves the
        order a draft. Once it reaches a block the order is ``submitted``, or
        ``failed`` if its dispatch failed. If it was sent but its outcome or
        order id is unknown, the order is ``unconfirmed``.

        Raises:
            QuipSubmissionError: if the order is no longer a draft, the account
                cannot cover the quote, the nonce moves twice while submitting,
                or the extrinsic certainly did not land (the order stays a
                draft); or if its dispatch failed on chain (the order is
                ``failed`` and the fee was paid).
            QuipUnconfirmedError: if it was sent but its outcome or order id is
                unknown; the order is ``unconfirmed`` and must not be resubmitted.
            QuipCancelledError: if a consent gate declines.
            QuipFaucetError: if the faucet refuses or cannot be reached.
        """
        self._require_draft("submit")
        call_params = self._call_params()
        signed, self._signed = self._signed_call(call_params), None
        # The balance and fee may have moved since quote(), e.g. another order's
        # reward was reserved; the gates judge the account as it is now.
        self._client._clear_gates(self._fresh_quote(signed))
        self._submitted_at = time.perf_counter()
        try:
            sent = self._client._propose(signed, call_params, self.reward)
        except QuipUnconfirmedError as exc:
            self._state = "unconfirmed"
            self._ext_hash, self._block_hash = exc.extrinsic_hash, exc.block_hash
            self._included_block = self._client._included_block(exc.block_hash)
            raise
        self._ext_hash, self._block_hash, self._included_block = sent.ext_hash, sent.block_hash, sent.included_block
        if sent.error is not None:
            self._state = "failed"
            self._error = sent.error
            raise QuipSubmissionError(
                f"propose_job {sent.ext_hash} failed on chain in block {sent.block_hash}: {sent.error}. "
                "The fee was paid and no order was placed."
            )
        self._order_id = sent.order_id
        self._state = "submitted"
        return self

    def order_id(self) -> int | None:
        """Return the chain's order id, or ``None`` while the order is a draft."""
        return self._order_id

    def status(self) -> dict[str, Any]:
        """Return the order's state and what is known of it.

        - ``draft``: ``state`` and ``options``.
        - ``unconfirmed``: ``state``, ``extrinsic_hash``, ``block_hash`` and
          ``included_block`` (``None`` when unknown).
        - ``failed``: the same, plus the chain's dispatch ``error``.
        - ``submitted`` and ``finalized``: ``state``, ``order_id``,
          ``chain_status`` (the chain's ``OrderStatus``), the order's block
          heights under the pallet's names (``created_at``,
          ``first_solution_at``, ``effective_expiry``), ``current_block`` and
          ``solution_count``. Reading it moves a submitted order to
          ``finalized`` once the chain says it is final.
        """
        if self._state == "draft":
            return {"state": self._state, "options": self.options()}
        if self._order_id is None:
            sent = {
                "state": self._state,
                "extrinsic_hash": self._ext_hash,
                "block_hash": self._block_hash,
                "included_block": self._included_block,
            }
            return sent if self._state == "unconfirmed" else {**sent, "error": self._error}
        order = self._client._fetch_order(self._order_id)
        current_block = self._client._current_block()
        lifecycle = self._client._order_lifecycle(order, current_block)
        if lifecycle["is_final"]:
            self._state = "finalized"
        return {
            "state": self._state,
            "order_id": self._order_id,
            "chain_status": lifecycle["status"],
            "created_at": lifecycle["created_at"],
            "first_solution_at": lifecycle["first_solution_at"],
            "effective_expiry": lifecycle["effective_expiry"],
            "current_block": current_block,
            "solution_count": int(order.get("solution_count", 0) or 0),
        }

    def _call_params(self) -> dict:
        """Build the ``propose_job`` call params for the current configuration."""
        return self._client._propose_call_params(self._job, self._options)

    def _signed_call(self, call_params: dict | None = None) -> _SignedCall:
        """Return the cached signed extrinsic, signing ``call_params`` first if there is none."""
        if self._signed is None:
            self._signed = self._client._sign(call_params if call_params is not None else self._call_params())
        return self._signed

    def _fresh_quote(self, signed: _SignedCall) -> JobQuote:
        """Price ``signed`` now and remember it for the display."""
        self._last_quote = self._client._quote_wire(signed.wire, self._job, self._options)
        return self._last_quote

    def __getstate__(self) -> dict[str, Any]:
        """Pickle and copy without the signed extrinsic: a copy signs its own."""
        state = self.__dict__.copy()
        state["_signed"] = None
        return state

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
        if self._last_quote is None:
            quoted = "not quoted"
        else:
            quote = self._last_quote
            total = f"{_format_planck(quote.total_planck, decimals)} {symbol}"
            afford = "affordable" if quote.affordable else "not affordable"
            quoted = ", ".join(
                [f"{total} total", afford] + ([f"block {quote.quoted_at_block}"] if quote.quoted_at_block else [])
            )
        block = f"block {self._included_block}" if self._included_block is not None else None
        if self._state == "draft":
            receipt = "not submitted"
        elif self._state == "unconfirmed":
            receipt = f"unconfirmed, extrinsic\n{self._ext_hash}" + (f"\nseen in {block}" if block else "")
        elif self._state == "failed":
            receipt = f"failed in {block or 'an unknown block'}: {self._error}"
        else:
            receipt = f"order {self._order_id}" + (f", included in {block}" if block else "")
        network = [("Network", self._client._network or "custom endpoint")]
        summary = [("Quote", quoted), ("Receipt", receipt)]
        return box(f"Job order ({self._state})", [network, problem, terms, summary])


def _variant_text(value: Any) -> str:
    """Render a unit enum variant as words (``"SingleBest"`` -> ``"single best"``); anything else as ``repr``."""
    if not isinstance(value, str):
        return repr(value)
    words = "".join(f" {char}" if char.isupper() else char for char in value).split()
    return " ".join(word.lower() for word in words)
