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
Every Quip error class, in one dependency-free module.

Nothing here imports the optional ``[quip]`` extra, so ``xqsa`` re-exports
these without ``substrate-interface`` or ``quip_signer`` installed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xqsa.quip.quote import JobQuote
    from xqsa.quip.receipt import JobOrderReceipt


class QuipError(Exception):
    """Base class for all Quip Network codec/backend errors."""


class PlacementError(QuipError):
    """Raised when a model cannot be placed onto the hardware topology.

    Carries the couplings (model variable pairs) that could not be satisfied,
    so the caller can report exactly which interactions have no hardware edge.
    """

    def __init__(self, message: str, couplings: Iterable[tuple[int, int]] = ()) -> None:
        super().__init__(message)
        self.couplings: tuple[tuple[int, int], ...] = tuple(couplings)


class EncodingError(QuipError):
    """Raised when a model cannot be encoded into the on-chain representation."""


class QuipSigningError(QuipError):
    """Raised when extrinsic assembly, keystore handling, or submission fails.

    Defined here in the dependency-free errors module (rather than in
    :mod:`xqsa.quip.signing`, which does ``import quip_signer`` at module top) so it can
    be re-exported from ``xqsa`` without pulling in the optional ``[quip]``
    extra -- ``import xqsa`` stays healthy without the extension installed.
    """


class QuipMetadataError(QuipError):
    """Raised when a node's runtime metadata cannot be decoded by this client.

    ``substrate-interface``/``scalecodec`` decode metadata up to V14 and Quip
    runtimes serve V16, so :mod:`xqsa.quip.metadata` fetches V14 through the
    versioned runtime API instead. This is raised only when that path and the
    stock ``state_getMetadata`` both fail, and it names the version the node
    serves in place of the bare ``Index '16' not present in Enum type mapping``
    that ``scalecodec`` would otherwise surface.

    Defined here in the dependency-free errors module for the same reason as
    :class:`QuipSigningError`: it can be re-exported from ``xqsa`` without
    pulling in the optional ``[quip]`` extra.
    """


class QuipCancelledError(QuipError):
    """Raised when a consent gate (``autoconfirm`` or ``autofund``) declines a job.

    Carries the :class:`~xqsa.quip.JobQuote` that was declined as ``quote``, so a caller
    can tell "you said no" from "it failed" and still see the price.
    """

    def __init__(self, quote: JobQuote, message: str) -> None:
        self.quote = quote
        super().__init__(message)


class QuipConnectionError(QuipError):
    """Raised when the Quip node is unreachable or missing expected chain state."""


class QuipSubmissionError(QuipError):
    """Raised when an extrinsic cannot be submitted or is rejected by the chain."""


class QuipOrderOptionError(QuipSubmissionError):
    """Raised when an order option is refused before anything is signed.

    Carries the offending option's name as ``option``. A subclass of
    :class:`QuipSubmissionError`, which the chain raised for the same values
    after charging the fee.
    """

    def __init__(self, option: str, message: str) -> None:
        self.option = option
        super().__init__(message)


class QuipReclaimRefusedError(QuipSubmissionError):
    """Raised when the chain refuses a reclaim, so sending it again cannot succeed.

    The order was proposed by another account, is already closed, is still
    open, or was answered, whether found before anything is signed or by the
    chain at dispatch. A subclass of :class:`QuipSubmissionError`; every
    other reclaim failure is a fault after which the reward may still be
    reserved and a retry may refund it.
    """


class QuipUnconfirmedError(QuipSubmissionError):
    """Raised when a ``propose_job`` was sent but its outcome or order id is unknown.

    The order may be on chain: resubmitting it could place a second order,
    reserving a second reward and paying a second fee. The :class:`~xqsa.quip.JobOrder`
    moves to ``unconfirmed`` and refuses further submits. Carries
    ``extrinsic_hash`` and, when the extrinsic was seen in a block,
    ``block_hash`` (else ``None``).
    """

    def __init__(self, extrinsic_hash: str, block_hash: str | None, message: str) -> None:
        self.extrinsic_hash = extrinsic_hash
        self.block_hash = block_hash
        super().__init__(message)


class QuipTopologyError(QuipError):
    """Retained for compatibility; nothing in :mod:`xqsa.quip` raises it any more.

    It used to report a resolved topology that was registered but absent from
    ``QuantumPow.MineableTopologies``. That set is the chain's active *mining*
    set: it gates ``submit_proof``, i.e. block production, and has no bearing on
    whether the compute mempool admits or answers an order. An order carries its
    nodes, edges and coefficients inline and no topology hash at all, so the
    chain cannot perceive which topology an order was built against. The check
    was therefore answering a mempool question with a consensus predicate and is
    gone; the class stays exported so downstream ``except`` clauses still import.
    """


class QuipTimeoutError(QuipError):
    """Raised when an order does not reach finality before the configured timeout.

    Carries ``order_id`` and, when raised by a wait, the order's ``receipt``,
    so the caller can wait again or read the order once it is final. In
    another session, :meth:`~xqsa.quip.SolverQuip.get_receipt` rebuilds the
    receipt from the id.
    """

    def __init__(self, order_id: int, message: str | None = None, *, receipt: JobOrderReceipt | None = None) -> None:
        self.order_id = order_id
        self.receipt = receipt
        super().__init__(message or f"order {order_id} did not reach finality before the timeout")

    def __reduce__(self) -> tuple:
        """Pickle without the receipt, whose client holds a live connection; ``receipt`` comes back ``None``."""
        state = {name: value for name, value in self.__dict__.items() if name != "receipt"}
        return (type(self), (self.order_id, str(self)), state)


class QuipJobFailedError(QuipError):
    """Raised when a final order yielded no usable solution.

    Carries ``order_id``. :meth:`SolverQuip.solve <xqsa.quip.SolverQuip.solve>`
    auto-reclaims the reserved reward of an unanswered order, best-effort, and
    the message says whether the reward was reclaimed or why the reclaim was
    refused. :meth:`JobOrder.best <xqsa.quip.JobOrder.best>` never reclaims.
    The other paths -- a winning submission with no solution vectors, or a
    missing result field -- raise without reclaiming.
    """

    def __init__(self, order_id: int, message: str | None = None) -> None:
        self.order_id = order_id
        super().__init__(message or f"order {order_id} closed with no solutions")


class QuipFaucetError(QuipError):
    """Raised when a faucet funding request fails.

    Carries the HTTP status (``None`` for a transport-level failure) and the
    parsed JSON error body, so a caller can distinguish a rate limit, a
    balance-ceiling denial, and a malformed request from each other.
    """

    def __init__(self, status: int | None, body: dict, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
