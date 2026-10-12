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

import logging
import math
import time
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Self

from xqsa.quip import chain
from xqsa.quip.access import VARIANTS as ACCESS_VARIANTS
from xqsa.quip.access import Access, AccessAllow, coerce_access
from xqsa.quip.access import from_chain as access_from_chain
from xqsa.quip.codec import MILLI_SCALE, _to_milli, energy_ceiling_milli
from xqsa.quip.display import Floors, _decimal_text, box, floors_text, terms_rows
from xqsa.quip.errors import EncodingError, QuipOrderOptionError, QuipSubmissionError, QuipUnconfirmedError
from xqsa.quip.quote import _format_planck
from xqsa.quip.receipt import JobOrderReceipt
from xqsa.quip.resolution import VARIANTS as RESOLUTION_VARIANTS
from xqsa.quip.resolution import (
    Resolution,
    ResolutionTopNEqual,
    ResolutionTopNWeighted,
    check_rank_weighting,
    coerce_resolution,
    from_chain,
)
from xqsa.quip.solution import Solution, decode_answers, pick_best

if TYPE_CHECKING:
    from xqsa.quip.chain import ChainLimits
    from xqsa.quip.client import SolverQuip
    from xqsa.quip.codec import IsingJob
    from xqsa.quip.quote import JobQuote
    from xqvm_py.xqmx import XQMX

logger = logging.getLogger("xqsa.quip")

OrderState = Literal["draft", "submitted", "unconfirmed", "failed", "finalized"]

# The options an order accepts; ``delivery`` is a client default only.
ORDER_OPTIONS = frozenset(
    {
        "reward",
        "access",
        "deadline_blocks",
        "block_wait",
        "topology",
        "mapping",
        "resolution",
        "max_energy",
        "min_solutions",
        "min_diversity",
    }
)

# The largest diversity floor: a vector and its full flip count as identical,
# so no set of vectors averages more than floor(n/2)/n, at most one half.
MAX_DIVERSITY = 0.5

# The shortest deadline worth proposing, and the one below which orders often
# go unanswered. On aglais a 1-block deadline was solved 0 of 11 times, 30
# blocks 28 of 38, and 100 blocks 108 of 108. The chain itself accepts 0,
# which expires on arrival.
MIN_DEADLINE_BLOCKS = 10
RELIABLE_DEADLINE_BLOCKS = 100

_SUPPORTED_DELIVERY = "OnChainOnly"


@dataclass(frozen=True)
class _OrderOptions:
    """One order's full configuration: its own options plus the inherited client defaults."""

    reward: int
    deadline_blocks: int
    block_wait: int
    topology: str | None
    mapping: Mapping[int, int] | None
    access: Any
    resolution: Any
    delivery: Any
    # Quality floors: per-order only, never client defaults.
    max_energy: float | None = None
    min_solutions: int | None = None
    min_diversity: float | None = None


def check_client_defaults(delivery: Any) -> None:
    """Refuse a ``delivery`` value no order can use yet: it accepts only ``"OnChainOnly"``.

    The client's ``access`` and ``resolution`` are checked by
    :func:`~xqsa.quip.access.coerce_access` and
    :func:`~xqsa.quip.resolution.coerce_resolution`.

    Raises:
        ValueError: naming the unsupported value.
    """
    if delivery != _SUPPORTED_DELIVERY:
        raise ValueError(
            f"delivery={delivery!r}: result delivery is not supported yet: ResultReady only fires on a claim, "
            "which network solvers do not make"
        )


def merge_options(
    current: _OrderOptions,
    updates: Mapping[str, Any],
    limits: ChainLimits,
    *,
    strict: bool,
    stacklevel: int = 2,
    iface: Any = None,
) -> _OrderOptions:
    """Apply ``updates`` to ``current`` and check the whole result.

    With ``iface``, an allowlist that differs from ``current``'s has its
    accounts looked up in the chain's ``Solvers``; see :func:`_check_access`.
    An unchanged one is not looked up again: ``current`` was checked when it
    was set, or at construction for the client default.

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
    for name, coerce in (("access", coerce_access), ("resolution", coerce_resolution)):
        if name in known:
            try:
                known[name] = coerce(known[name])
            except ValueError as exc:
                raise QuipOrderOptionError(name, str(exc)) from None
    merged = replace(current, **known)
    _check_raw_values(merged, strict=strict, stacklevel=stacklevel + 1)
    changed = merged.access != current.access
    _check_limits(merged, limits, stacklevel=stacklevel + 1, iface=iface if changed else None)
    return merged


def _check_raw_values(options: _OrderOptions, *, strict: bool, stacklevel: int) -> None:
    """Refuse (strict) or warn about (lenient) raw chain dicts anywhere but ``mapping``."""
    for option in fields(options):
        if option.name == "mapping" or not isinstance(getattr(options, option.name), Mapping):
            continue
        if strict:
            raise TypeError(f"{option.name}: raw chain values are not accepted")
        warnings.warn(
            f"{option.name}: passing a raw chain value is deprecated; pass a typed option instead",
            DeprecationWarning,
            stacklevel=stacklevel,
        )


def _check_limits(options: _OrderOptions, limits: ChainLimits, *, stacklevel: int, iface: Any = None) -> None:
    """Check every option against the others and the chain limits, short of the placed model.

    The floor checks that need the placed model are in :func:`_check_floors`.
    """
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
    _check_access(options.access, limits, iface, stacklevel=stacklevel + 1)
    _check_resolution(options.resolution, limits, block_wait, stacklevel=stacklevel + 1)
    if options.max_energy is not None:
        energy = _require_real(options.max_energy, "max_energy")
        try:
            finite = math.isfinite(energy)
        except OverflowError:  # an int beyond float range, maybe too long to str()
            raise QuipOrderOptionError("max_energy", "max_energy is too large to convert to a float") from None
        if not finite:
            raise QuipOrderOptionError("max_energy", f"max_energy={energy} must be finite")
    if options.min_solutions is not None:
        _check_min_solutions(_require_int(options.min_solutions, "min_solutions"), limits, stacklevel=stacklevel + 1)
    if options.min_diversity is not None:
        _check_min_diversity(_require_real(options.min_diversity, "min_diversity"), options.min_solutions)

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


def _check_access(access: Any, limits: ChainLimits, iface: Any, *, stacklevel: int) -> None:
    """Refuse an allowlist the chain cannot take: over ``MaxBidMiners`` accounts, or an unknown solver type.

    An over-cap list traps the runtime with no fee and no readable error, and
    an unknown type fails to encode, so both are refused here. A raw chain
    dict is read as the rule it names and checked the same way. One that names
    a rule but cannot be read as it is refused; one that names no rule passes
    unchecked. A bound the runtime does not expose is not checked.

    With ``iface``, each listed account that is not a registered solver warns
    once. It never raises: the chain does not check registration, and an
    operator may register after the order is posted.
    """
    if isinstance(access, Mapping):
        try:
            access = access_from_chain(access)
        except (ValueError, TypeError) as exc:
            if len(access) == 1 and next(iter(access)) in ACCESS_VARIANTS:
                raise QuipOrderOptionError("access", f"{exc}; it cannot be read as an access rule") from None
            return
    if not isinstance(access, AccessAllow):
        return
    cap = limits.max_bid_miners
    if cap is not None and len(access.accounts) > cap:
        raise QuipOrderOptionError(
            "access", f"AccessAllow lists {len(access.accounts)} accounts; the chain's MaxBidMiners is {cap}"
        )
    known = limits.solver_types
    unknown = [name for name in access.types if known is not None and name not in known]
    if unknown:
        raise QuipOrderOptionError(
            "access",
            f"AccessAllow: unknown solver type {', '.join(map(repr, unknown))}; the chain has {', '.join(known)}",
        )
    if iface is None or not access.accounts:
        return
    try:
        missing = chain.unregistered_solvers(iface, access.accounts)
    except Exception as exc:  # noqa: BLE001 -- an advisory check never blocks the order.
        logger.warning("could not check whether the allowlisted accounts are registered solvers: %s", exc)
        return
    for account in missing:
        warnings.warn(
            f"access: {account} is not a registered solver, so it cannot answer until it registers",
            UserWarning,
            stacklevel=stacklevel,
        )


def _check_resolution(resolution: Any, limits: ChainLimits, block_wait: int, *, stacklevel: int) -> None:
    """Refuse a payout rule the runtime cannot honour, and warn when a top-N order can have one winner only.

    A raw chain dict, which only the deprecated ``solve()`` path lets through,
    is read as the rule it names and checked the same way. One that names a
    rule but cannot be read as it is refused; one that names no rule passes
    unchecked.
    """
    if isinstance(resolution, Mapping):
        try:
            resolution = from_chain(resolution)
        except ValueError as exc:
            if len(resolution) == 1 and next(iter(resolution)) in RESOLUTION_VARIANTS:
                raise QuipOrderOptionError("resolution", f"{exc}; it cannot be read as a payout rule") from None
            return
    try:
        check_rank_weighting(resolution, limits.spec_version)
    except ValueError as exc:
        raise QuipOrderOptionError("resolution", str(exc)) from None
    if isinstance(resolution, (ResolutionTopNEqual, ResolutionTopNWeighted)) and resolution.n > 1 and block_wait == 0:
        warnings.warn(
            f"resolution={resolution}: with block_wait=0 only the first answer is accepted, so there can be only "
            "one winner",
            UserWarning,
            stacklevel=stacklevel,
        )


def _check_min_solutions(count: int, limits: ChainLimits, *, stacklevel: int) -> None:
    """Refuse a solution floor below 1 or above ``MaxSolutions``, and warn at 1."""
    upper = limits.max_solutions
    if count < 1 or (upper is not None and count > upper):
        bound = f" and at most MaxSolutions ({upper})" if upper is not None else ""
        raise QuipOrderOptionError("min_solutions", f"min_solutions={count} must be at least 1{bound}")
    if count == 1:
        warnings.warn(
            "min_solutions=1: the chain stores only the first vector of each answer, but ranks the answer by "
            "its best vector",
            UserWarning,
            stacklevel=stacklevel,
        )


def _check_min_diversity(diversity: float, min_solutions: int | None) -> None:
    """Refuse a diversity floor outside 0 to 0.5, off the milli grid, or paired with one solution."""
    if not 0 <= diversity <= MAX_DIVERSITY:
        raise QuipOrderOptionError("min_diversity", f"min_diversity={diversity} must be from 0 to {MAX_DIVERSITY}")
    if _to_milli(diversity, "min_diversity")[1]:
        raise QuipOrderOptionError(
            "min_diversity", f"min_diversity={diversity} is not a whole number of thousandths, which the chain needs"
        )
    # The chain refuses a positive diversity floor only with an explicit
    # min_solutions below 2; unset keeps every valid vector, so it is accepted.
    if diversity > 0 and min_solutions == 1:
        raise QuipOrderOptionError(
            "min_diversity",
            f"min_diversity={diversity} needs min_solutions of at least 2 or unset; one solution has no diversity "
            "(the chain's InvalidDiversityConfig)",
        )


def _require_int(value: Any, name: str) -> int:
    """Return ``value`` if it is an ``int`` (not a ``bool``), else raise ``TypeError``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    return value


def _require_real(value: Any, name: str) -> float:
    """Return ``value`` if it is an ``int`` or ``float`` (not a ``bool``), else raise ``TypeError``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, not {type(value).__name__}")
    return value


def _check_floors(options: _OrderOptions, job: IsingJob, *, stacklevel: int) -> None:
    """Check the quality floors against the placed model.

    Runs after every placement and every ``set()``: a re-placed model moves
    the bounds. The diversity floor is refused above floor(n/2)/n for n
    placed spins. The ceiling is refused below the lowest energy the placed
    model can reach, -(sum |h| + sum |J|) plus the offset, and warns at or
    above the highest, where it filters nothing. A ceiling on a model whose
    coefficients were rounded also warns, since the chain judges the rounded
    model. ``stacklevel`` is as for :func:`merge_options`.

    Raises:
        QuipOrderOptionError: if a floor cannot be met by the placed model.
    """
    # Checked as the chain takes them. Only the ceiling can fail to encode:
    # _check_limits has already held the diversity to 0..0.5.
    try:
        floors = _chain_floors(options, job)
    except EncodingError as exc:
        raise QuipOrderOptionError("max_energy", f"max_energy={options.max_energy}: {exc}") from exc
    spins = job.topology.num_nodes
    diversity = floors.min_diversity_milli
    # In whole milli, so a cap such as 12/25 compares exactly.
    if diversity is not None and diversity * spins > spins // 2 * MILLI_SCALE:
        raise QuipOrderOptionError(
            "min_diversity",
            f"min_diversity={options.min_diversity} is above {spins // 2}/{spins}, the most diversity "
            f"{spins} placed spins allow, since a vector and its full flip count as one",
        )
    ceiling = floors.max_energy_milli
    if ceiling is None:
        return
    reach = sum(map(abs, job.h_values)) + sum(map(abs, job.j_values))
    # Exact decimals: a rounded bound can land on the wrong side of the ceiling.
    offset = Decimal(repr(job.offset))
    lowest, highest = (_decimal_text(Decimal(bound) / MILLI_SCALE + offset) for bound in (-reach, reach))
    if ceiling < -reach:
        raise QuipOrderOptionError(
            "max_energy",
            f"max_energy={options.max_energy} is below {lowest}, the lowest "
            "energy the placed model can reach, so no answer could meet it",
        )
    if ceiling >= reach:
        warnings.warn(
            f"max_energy={options.max_energy} is at or above {highest}, the highest "
            "energy the placed model can reach, so it filters nothing",
            UserWarning,
            stacklevel=stacklevel,
        )
    if job.quantization_error > 0:
        warnings.warn(
            f"max_energy={options.max_energy}: the model's coefficients were rounded to the chain's milli grid, "
            "and the chain applies the ceiling to the rounded model, so answers near it can land on either side",
            UserWarning,
            stacklevel=stacklevel,
        )


def _chain_floors(options: _OrderOptions, job: IsingJob) -> Floors:
    """Return the floors as the chain takes them.

    The ceiling is in milli spin energy, offset included. A zero diversity
    floor is no floor, so it goes out as ``None``.

    Raises:
        EncodingError: if the ceiling does not fit the chain's ``i64``.
    """
    return Floors(
        max_energy_milli=None if options.max_energy is None else energy_ceiling_milli(options.max_energy, job.offset),
        min_solutions=options.min_solutions,
        min_diversity_milli=_to_milli(options.min_diversity, "min_diversity")[0] if options.min_diversity else None,
    )


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

    A submitted order has a :class:`~xqsa.quip.JobOrderReceipt`, returned by
    :meth:`receipt`. :meth:`wait`, :meth:`solvers`, :meth:`raw_solutions`,
    :meth:`settlement` and :meth:`reclaim` pass through to it.

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
            order.wait()
            order.settlement()
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
        self._receipt: JobOrderReceipt | None = None

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

    @property
    def access(self) -> Access | Mapping[str, Any]:
        """Who may answer; a raw chain dict only on the deprecated ``solve()`` path."""
        return self._options.access

    @property
    def resolution(self) -> Resolution | Mapping[str, Any]:
        """The payout rule; a raw chain dict only on the deprecated ``solve()`` path."""
        return self._options.resolution

    @property
    def max_energy(self) -> float | None:
        """The energy ceiling in the model's own units, or ``None`` for no ceiling."""
        return self._options.max_energy

    @property
    def min_solutions(self) -> int | None:
        """The number of vectors each answer must store, or ``None`` for no floor."""
        return self._options.min_solutions

    @property
    def min_diversity(self) -> float | None:
        """The diversity each answer's vectors must reach, from 0 to 0.5, or ``None`` for no floor."""
        return self._options.min_diversity

    def options(self) -> dict[str, Any]:
        """Return the order's options as a plain dict."""
        mapping = self._options.mapping
        return {
            "reward": self.reward,
            "deadline_blocks": self.deadline_blocks,
            "block_wait": self.block_wait,
            "topology": self.topology,
            "mapping": None if mapping is None else dict(mapping),
            "access": self.access,
            "resolution": self.resolution,
            "max_energy": self.max_energy,
            "min_solutions": self.min_solutions,
            "min_diversity": self.min_diversity,
        }

    def set(self, **options: Any) -> Self:
        """Change options on a draft and recheck the whole configuration.

        Re-places the model only when ``topology`` or ``mapping`` changed,
        rechecks the quality floors against the placed model, and drops any
        cached quote. A refused change leaves the order as it was.

        Raises:
            QuipSubmissionError: if the order is no longer a draft.
            TypeError: for an unknown option or a raw chain value.
            QuipOrderOptionError: if an option is outside its chain limit.
            ValueError: if ``mapping`` is combined with a native topology.
        """
        self._require_draft("set")
        merged = merge_options(
            self._options, options, self._client._limits, strict=True, stacklevel=3, iface=self._client._iface
        )
        merged = self._client._settle_topology(merged)
        job = self._job
        if (merged.topology, merged.mapping) != (self._options.topology, self._options.mapping):
            job = self._client._place(self._model, merged)
        _check_floors(merged, job, stacklevel=3)
        self._job, self._options = job, merged
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
        self._receipt = JobOrderReceipt(self._client, sent.order_id, self._genesis_hash)
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
        status = self.receipt().status()
        self._state = status["state"]
        return status

    def receipt(self) -> JobOrderReceipt:
        """Return the order's receipt, its handle on the chain.

        Raises:
            QuipSubmissionError: if the order has no order id: it is a draft,
                its dispatch failed, or its outcome is unconfirmed.
        """
        if self._receipt is None:
            why = {
                "draft": "is a draft; submit() it first",
                "failed": "failed on chain, so no order was placed",
                "unconfirmed": "is unconfirmed, so its order id is unknown",
            }[self._state]
            raise QuipSubmissionError(f"this order {why}")
        return self._receipt

    def wait(self) -> Self:
        """Block until the order is final; see :meth:`JobOrderReceipt.wait <xqsa.quip.JobOrderReceipt.wait>`.

        Raises:
            QuipSubmissionError: if the order has no receipt; see :meth:`receipt`.
            QuipTimeoutError: if it is not final within the client's ``timeout``.
        """
        self.receipt().wait()
        self._state = "finalized"
        return self

    def solvers(self) -> list[dict[str, Any]]:
        """See :meth:`JobOrderReceipt.solvers <xqsa.quip.JobOrderReceipt.solvers>`."""
        return self.receipt().solvers()

    def raw_solutions(self, solver: Any = None) -> dict[Any, Any]:
        """See :meth:`JobOrderReceipt.raw_solutions <xqsa.quip.JobOrderReceipt.raw_solutions>`."""
        return self.receipt().raw_solutions(solver)

    def best(self) -> Solution:
        """Return the answer the chain pays for, decoded onto this order's model.

        That is the best vector, by energy recomputed on the model, of the
        solver the chain ranks first. It is the best vector any solver stored
        instead, with ``leader`` false, when the ranking cannot be read or
        names no answer, or when the leader's stored vectors do not reach the
        energy it was ranked at; the latter is logged with both energies.
        Reads the chain now, so an order that is still open can return a
        different answer later. Never reclaims.

        Raises:
            QuipSubmissionError: if the order has no receipt; see :meth:`receipt`.
            QuipJobFailedError: if nobody answered the order (reclaim its
                reward with :meth:`reclaim` once it is final; the message
                names the order's quality floors and allowlist, if any), or the chosen
                answer carries no vectors.
        """
        return pick_best(self.receipt(), self._job, self._model, self._floors_text, self.access).solution

    def solutions(self, solver: Any = None) -> list[Solution]:
        """Return every stored answer, decoded onto this order's model, best first.

        Ordered by energy recomputed on the model, the leader's vectors first
        on ties. With ``solver``, returns that solver's vectors alone. An
        unanswered order returns an empty list.

        Raises:
            QuipSubmissionError: if the order has no receipt; see :meth:`receipt`.
            KeyError: if ``solver`` did not answer the order.
        """
        return decode_answers(self.receipt(), self._job, self._model, solver)

    def settlement(self) -> dict[str, Any]:
        """See :meth:`JobOrderReceipt.settlement <xqsa.quip.JobOrderReceipt.settlement>`."""
        return self.receipt().settlement()

    def reclaim(self) -> int:
        """See :meth:`JobOrderReceipt.reclaim <xqsa.quip.JobOrderReceipt.reclaim>`."""
        return self.receipt().reclaim()

    def _floors_text(self) -> str:
        """The order's quality floors in model units, with the chain value where the offset moves it."""
        floors, ceiling = _chain_floors(self._options, self._job), self._options.max_energy
        # Without the offset, the ceiling would convert to the same milli: nothing to add.
        moved = ceiling is not None and floors.max_energy_milli != energy_ceiling_milli(ceiling)
        return floors_text(floors, model_ceiling=ceiling, moved=moved)

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
        terms = terms_rows(
            resolution=options.resolution,
            access=options.access,
            floors=self._floors_text(),
            deadline_blocks=options.deadline_blocks,
            block_wait=options.block_wait,
            reward=f"{_format_planck(options.reward, decimals)} {symbol}",
        )
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
            try:
                outcome = self.receipt()._outcome()
            except Exception:  # noqa: BLE001 -- display only; a read fault keeps the plain row.
                pass
            else:
                leader = f", leader {outcome.leader}" if outcome.leader else ""
                receipt += f", {outcome.status}{leader}"
        network = [("Network", self._client._network or "custom endpoint")]
        summary = [("Quote", quoted), ("Receipt", receipt)]
        return box(f"Job order ({self._state})", [network, problem, terms, summary])
