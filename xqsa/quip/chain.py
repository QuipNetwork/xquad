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
Chain reads and extrinsic plumbing for the Quip backend.

Each function takes the ``substrate-interface`` client (``iface``), plus the
signing module and signer where it signs, so the reads work without a
:class:`~xqsa.quip.SolverQuip`. ``SolverQuip`` calls them through one-line
private methods.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from xqsa.quip.codec import (
    _as_hex,
    _as_int_or_none,
    _canonical_hex,
    _dispatch_error_text,
    _event_ids,
    _extrinsic_index,
    _phase_extrinsic_index,
    effective_expiry,
    is_final,
)
from xqsa.quip.errors import (
    QuipConnectionError,
    QuipError,
    QuipMetadataError,
    QuipSubmissionError,
    QuipUnconfirmedError,
)

# The same logger as the client, so the fee-fallback warning keeps its record name.
logger = logging.getLogger("xqsa.quip")

# The ``propose_job`` fee a quote assumes when ``payment_queryInfo`` does not
# answer; ``JobQuote.fee_exact`` is then false. Measured on aglais on
# 2026-09-28: typical fees run about 0.0019 AGLS and top out near 0.0072 AGLS
# over the benchmarked model range (n <= 5000 nodes, e <= 50000 edges), so this
# is about 38 percent over that ceiling. It depends on the runtime's weights and
# fee config, which an upgrade can move silently; it is a bound for today's
# runtime, not a law.
FEE_HEADROOM_PLANCK = 10_000_000_000  # 0.01 AGLS (12 decimals).

# The ``QuantumComputeMempool`` pallet name and the calls/storage/events this
# backend uses; gathered here so the chain surface is easy to audit.
MEMPOOL_PALLET = "QuantumComputeMempool"
PROPOSE_JOB_CALL = "propose_job"
RECLAIM_ORDER_CALL = "reclaim_order"
JOB_PROPOSED_EVENT = "JobProposed"
JOB_ORDERS_STORAGE = "JobOrders"
ORDER_SOLUTIONS_STORAGE = "OrderSolutions"
ORDER_FRONT_RUNNER_STORAGE = "OrderFrontRunner"
ORDER_TOP_SOLVERS_STORAGE = "OrderTopSolvers"
PROPOSER_ORDERS_STORAGE = "ProposerOrders"
SOLVERS_STORAGE = "Solvers"
REGISTER_SOLVER_CALL = "register_solver"

# The fee a signed extrinsic paid, emitted in the same phase as its own events.
TRANSACTION_PAYMENT_PALLET = "TransactionPayment"
TRANSACTION_FEE_PAID_EVENT = "TransactionFeePaid"

# ``QuantumPow`` topology storage this backend reads. ``RegisteredTopologies``
# (queried in ``SolverQuip._fetch_topology``) is the topology set a hash must belong to.
# ``MineableTopologies`` is the chain's active mining set, which gates
# ``submit_proof`` and not the compute mempool; nothing on the solve path
# consults it, and :func:`mineable_topologies` is kept only as a chain reader.
MINEABLE_TOPOLOGIES_STORAGE = "MineableTopologies"


def read_constant(iface: Any, name: str) -> Any | None:
    """Read a ``QuantumComputeMempool`` runtime constant's value, or ``None`` if unset.

    ``get_constant`` returns ``None`` for a constant absent from the runtime
    metadata; any other failure is a fault, not a genuine absence.

    Raises:
        QuipConnectionError: if reading the constant fails.
        QuipMetadataError: propagated unchanged; undecodable metadata is not
            an unreadable constant.
    """
    try:
        const = iface.get_constant(MEMPOOL_PALLET, name)
    except QuipMetadataError:
        raise  # undecodable metadata, not an unreadable constant.
    except Exception as exc:  # noqa: BLE001 -- a fault, not a genuine absence.
        raise QuipConnectionError(f"could not read the {MEMPOOL_PALLET}.{name} constant: {exc}") from exc
    return getattr(const, "value", None)


@dataclass(frozen=True)
class ChainLimits:
    """The ``QuantumComputeMempool`` order bounds, read once per client.

    Each bound is ``None`` when the runtime does not expose that constant; a
    missing bound is not checked. ``spec_version`` is the runtime's
    ``specVersion`` at client creation, ``None`` when unknown; a client that
    stays connected across a runtime upgrade keeps the old value.
    ``solver_types`` are the runtime's solver type names, ``None`` when the
    metadata does not say.
    """

    min_reward: int | None
    max_deadline_blocks: int | None
    max_block_wait: int | None
    max_solutions: int | None
    spec_version: int | None = None
    max_bid_miners: int | None = None
    solver_types: tuple[str, ...] | None = None


def read_limits(iface: Any) -> ChainLimits:
    """Read the mempool's order bounds and the runtime's ``specVersion`` into a :class:`ChainLimits`.

    Raises:
        QuipConnectionError: if reading a constant fails.
        QuipMetadataError: propagated unchanged from the constant reads.
    """

    def read(name: str) -> int | None:
        value = read_constant(iface, name)
        return None if value is None else int(value)

    # init_runtime() set this at connect, just before; a later query at a past block would move it.
    spec_version = getattr(iface, "runtime_version", None)
    return ChainLimits(
        min_reward=read("MinReward"),
        max_deadline_blocks=read("MaxDeadlineBlocks"),
        max_block_wait=read("MaxBlockWait"),
        max_solutions=read("MaxSolutions"),
        spec_version=spec_version,
        max_bid_miners=read("MaxBidMiners"),
        solver_types=solver_types(iface),
    )


def solver_types(iface: Any) -> tuple[str, ...] | None:
    """Return the runtime's solver type names (``MinerType``), or ``None`` if the metadata does not say.

    Read from ``register_solver``'s ``solver_type`` argument, which has the
    same type as a ``JobMode::Bid`` order's ``miner_types`` entries. Used only
    to name an unknown type before signing, so any failure reads as unknown.
    """
    try:
        call = iface.get_metadata_call_function(MEMPOOL_PALLET, REGISTER_SOLVER_CALL)
        names = call.get_param_info()["solver_type"]
    except Exception:  # noqa: BLE001 -- a pre-signing check; the chain still refuses a bad type.
        return None
    if not isinstance(names, (tuple, list)) or not all(isinstance(name, str) for name in names):
        return None
    return tuple(names)


def genesis_hash(iface: Any) -> str:
    """Return the chain's genesis block hash as ``0x`` hex.

    Raises:
        QuipConnectionError: if the hash cannot be read.
    """
    try:
        raw = iface.get_block_hash(0)
    except QuipMetadataError:
        raise  # undecodable metadata, not a missing genesis block.
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        raise QuipConnectionError(f"could not read the genesis block hash: {exc}") from exc
    if not raw:
        raise QuipConnectionError("the node returned no genesis block hash")
    return _as_hex(raw)


def default_topology(iface: Any) -> str | None:
    """Read the ``QuantumPow.DefaultTopology`` storage value, or ``None`` if unset.

    Distinguishes a genuinely-absent default (the storage item is not part of
    the runtime, or decodes to ``None``) from a transport/decode fault: only
    the former is reported as absent. A transient failure must not be
    mistaken for "no default" -- doing so would silently drop to whatever
    fallback an operator pinned and waste the reserved reward on a job that
    can never be solved, with no signal that the fallback was a fault.

    Raises:
        QuipConnectionError: if the storage read fails for any reason other
            than the item being absent from the runtime.
        QuipMetadataError: propagated unchanged; undecodable metadata is not
            an absent storage item.
    """
    try:
        entry = iface.query("QuantumPow", "DefaultTopology")
    except QuipMetadataError:
        raise  # undecodable metadata, not an absent storage item.
    except Exception as exc:  # noqa: BLE001 -- classify absence vs. fault below.
        if _is_storage_absent(exc):
            return None  # the runtime does not define this storage item.
        raise QuipConnectionError(
            f"could not read QuantumPow.DefaultTopology: {exc}; pass topology= explicitly to bypass the chain default."
        ) from exc
    value = getattr(entry, "value", None)
    return _as_hex(value) if value is not None else None


def mineable_topologies(iface: Any) -> frozenset[str] | None:
    """Return the canonical hashes in ``QuantumPow.MineableTopologies``, or ``None``.

    A chain reader with nothing behind it: this set is the chain's active
    *mining* set, gating ``submit_proof`` and so block production, and it
    does not decide whether the compute mempool admits or answers an order.
    Nothing on the solve path calls this, and :mod:`xqsa.quip` does not
    export it. It is kept solely because the live test tier
    asserts the chain default is mineable and that a ``QUIP_TOPOLOGY``
    override is not, which is the evidence that mineability does not gate
    the mempool. Delete it with those assertions, not before.

    ``MineableTopologies`` is a ``StorageMap<H256, ()>`` -- a set keyed by
    topology hash. It is enumerated (via ``query_map``) rather than probed
    with a keyed lookup so a defined-but-empty map is distinguishable from a
    hash simply being absent from a populated one: a keyed read returning
    ``None`` cannot tell those apart.

    Returns ``None`` only when the storage item is genuinely absent from the
    runtime -- the pallet does not compile the item into metadata at all. A
    runtime that *does* compile the pallet always exposes the item in
    metadata, so a deployment which simply does not use the feature surfaces
    it as an empty map; that returns an empty ``frozenset``, never ``None``.

    Distinguishes genuine runtime absence from a transport/decode fault,
    mirroring :func:`default_topology`: a read fault, or entries that
    are present but all fail to decode to a hash, surface as a connection
    error rather than silently reporting an absent set.

    Raises:
        QuipConnectionError: if reading the storage map fails for any reason
            other than the item being absent from the runtime, or if the map
            returns entries that none decode to a canonical hash.
        QuipMetadataError: propagated unchanged; undecodable metadata is not
            an absent storage item.
    """
    try:
        entries = iface.query_map("QuantumPow", MINEABLE_TOPOLOGIES_STORAGE)
    except QuipMetadataError:
        raise  # undecodable metadata, not an absent storage item.
    except Exception as exc:  # noqa: BLE001 -- classify absence vs. fault below.
        if _is_storage_absent(exc):
            return None  # the runtime does not define this storage item.
        raise QuipConnectionError(
            f"could not read QuantumPow.{MINEABLE_TOPOLOGIES_STORAGE}: {exc}; retry or verify chain connectivity."
        ) from exc
    # query_map yields decoded key objects; the H256 key may be wrapped
    # (``.value``, as in _fetch_solutions) or a bare hex/bytes hash. Unwrap
    # then canonicalise so membership is prefix/case robust on both sides.
    hashes: set[str] = set()
    saw_entry = False
    for key, _value in entries:
        saw_entry = True
        raw = key.value if hasattr(key, "value") else key
        canonical = _canonical_hex(raw)
        if canonical is not None:
            hashes.add(canonical)
    if saw_entry and not hashes:
        # Entries were present but none decoded to a canonical hash: a
        # populated set whose keys all fail to decode is a fault, not an
        # empty set. Surface it rather than masquerading as unset.
        raise QuipConnectionError(
            f"QuantumPow.{MINEABLE_TOPOLOGIES_STORAGE} returned entries but none decoded to a "
            "topology hash; retry or verify chain connectivity."
        )
    # A genuinely-empty set returns an empty frozenset (not None): only a
    # runtime lacking the storage item entirely reports absence.
    return frozenset(hashes)


def free_balance(iface: Any, account_id: Any) -> int:
    """Return the account's free balance in planck.

    ``propose_job`` reserves the full reward at proposal, so the free
    balance must cover the reward plus the fee; :class:`JobQuote` compares.

    Raises:
        QuipConnectionError: if the account entry or its ``data.free`` field
            cannot be read/decoded -- an anomalous read must not be reported
            as a zero balance.
    """
    entry = iface.query("System", "Account", [account_id])
    value = getattr(entry, "value", None)
    try:
        free = int(value["data"]["free"])
    except (TypeError, KeyError, ValueError) as exc:
        # A non-None entry with a missing/undecodable data.free is a read
        # or decode fault, not a funded-to-zero account -- surface it rather
        # than defaulting free=0 and blaming the user for "insufficient balance".
        raise QuipConnectionError(
            f"could not read the free balance of {ss58_address(iface, account_id)} from "
            f"System.Account (got {value!r}); cannot verify funding"
        ) from exc
    return free


def query_fee(iface: Any, quip_signing: Any, wire: bytes) -> tuple[int, bool]:
    """Ask the chain what ``wire`` would pay in fees: ``(fee_planck, exact)``.

    ``payment_queryInfo`` dry-runs the fee of a signed extrinsic, so the
    answer tracks the live runtime rather than a formula copied from it.
    ``wire`` itself never leaves this process here; the node prices a
    copy from :func:`xqsa.quip.signing.disarm_extrinsic`. ``partialFee``
    arrives as a decimal string, ``0x`` hex, or an int depending on the
    node. Any failure falls back to :data:`FEE_HEADROOM_PLANCK` with
    ``exact`` false rather than blocking the solve on a price estimate.
    """
    try:
        # Price a copy whose signature fails: the node sees a frame it can
        # never dispatch, so neither quote() nor a declined gate leaves it
        # holding a proposable job. Same length, so the same fee.
        priced = quip_signing.disarm_extrinsic(wire)
        raw = iface.rpc_request("payment_queryInfo", ["0x" + priced.hex()])["result"]["partialFee"]
        if isinstance(raw, str):
            return (int(raw, 16) if raw.startswith("0x") else int(raw)), True
        return int(raw), True
    except Exception as exc:  # noqa: BLE001 -- a price estimate must not block the solve.
        logger.warning(
            "payment_queryInfo did not answer (%s); quoting the %d planck fallback fee", exc, FEE_HEADROOM_PLANCK
        )
        return FEE_HEADROOM_PLANCK, False


def ss58_address(iface: Any, account_id: Any) -> str:
    """Encode ``account_id`` as an SS58 address in the chain's format, or ``0x`` hex if that fails.

    Display only, so an unknown format or a missing ``scalecodec`` never
    blocks a quote.
    """
    try:
        from scalecodec.utils.ss58 import ss58_encode

        return ss58_encode(bytes(account_id), iface.ss58_format)
    except Exception:  # noqa: BLE001 -- display only; never block a quote on it.
        return _as_hex(account_id)


def account_bytes(account_id: Any) -> bytes:
    """Return a decoded ``AccountId`` as raw bytes.

    ``substrate-interface`` decodes an account as SS58 text when the chain
    has an address format, and as ``0x`` hex or bytes otherwise; all three
    reduce to the same 32 bytes, so two accounts compare equal whichever way
    each was read.

    Raises:
        ValueError: if ``account_id`` is text that is neither hex nor SS58.
    """
    if isinstance(account_id, str):
        if account_id.startswith("0x"):
            return bytes.fromhex(account_id[2:])
        from scalecodec.utils.ss58 import ss58_decode

        return bytes.fromhex(ss58_decode(account_id))
    return bytes(account_id)


def block_time_ms(iface: Any) -> int | None:
    """Return the chain's expected block time in milliseconds, or ``None`` if unknown.

    Reads the ``Babe.ExpectedBlockTime`` constant. Display only, so any
    failure reads as unknown.
    """
    try:
        constant = iface.get_constant("Babe", "ExpectedBlockTime")
    except Exception:  # noqa: BLE001 -- display only; never block a caller on it.
        return None
    return _as_int_or_none(getattr(constant, "value", None))


def token(iface: Any) -> tuple[str, int]:
    """Return the chain's token symbol and decimals, or ``("planck", 0)`` if unknown."""
    try:
        symbol, decimals = iface.token_symbol, iface.token_decimals
    except Exception:  # noqa: BLE001 -- display only; never block a solve on it.
        symbol = decimals = None
    if symbol is None or decimals is None:
        return "planck", 0
    return str(symbol), int(decimals)


def wrap_bounded(value: Any) -> Any:
    """Wrap a ``BoundedVec`` call field for ``compose_call``.

    ``BoundedVec`` parameters on the ``v0.2`` runtime decode as 1-field
    composites in substrate metadata, so each value is passed as a 1-tuple
    (mirrors the miner's ``submit_solution`` wrapping in ``quip-protocol``
    ``shared/mempool_miner_controller.py``). If a future runtime exposes
    ``BoundedVec`` as a plain ``Vec``, drop the wrapping here -- this function
    isolates the quirk so the change is one line.
    """
    return (value,)


def account_nonce(iface: Any, account_id: Any) -> int:
    """Return the account's next nonce, counting its transactions pending in the node's pool.

    ``get_account_nonce`` is ``system_accountNextIndex``, which includes the
    pool, so a transaction this account already sent but that is not yet in a
    block moves it.

    Raises:
        QuipConnectionError: if the nonce cannot be read.
    """
    address = "0x" + bytes(account_id).hex()
    try:
        return int(iface.get_account_nonce(account_address=address))
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        raise QuipConnectionError(f"could not read the account nonce of {address}: {exc}") from exc


def finalized_head(iface: Any) -> tuple[int, str]:
    """Return the finalized head block as ``(number, hash)``.

    Raises:
        QuipConnectionError: if the head cannot be read.
    """
    try:
        raw = iface.get_chain_finalised_head()
        number = block_number(iface, raw) if raw else None
    except QuipMetadataError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        raise QuipConnectionError(f"could not read the finalized head: {exc}") from exc
    if number is None:
        raise QuipConnectionError("the node returned no finalized head")
    return number, _as_hex(raw)


def block_number(iface: Any, block_hash: str) -> int:
    """Return the height of the block ``block_hash``.

    Reads the header shallowly, as :func:`current_block` does.
    """
    header = iface.get_block_header(block_hash=block_hash, ignore_decoding_errors=True)
    return _coerce_block_number(header["header"]["number"])


def build_extrinsic(
    iface: Any,
    quip_signing: Any,
    signer: Any,
    call_module: str,
    call_function: str,
    call_params: dict,
    nonce: int | None = None,
    era: Any = None,
) -> tuple[bytes, str]:
    """Sign an extrinsic via :func:`xqsa.quip.signing.build_signed_extrinsic`: ``(wire, hash)``.

    ``nonce`` signs with that account nonce; ``None`` lets the signing layer
    read the current one. ``era``, a :class:`~xqsa.quip.signing.MortalEra`,
    signs it mortal; ``None`` signs it immortal.

    Raises:
        QuipSubmissionError: if assembly or signing fails.
        QuipMetadataError: propagated unchanged; a runtime this client cannot
            sign for is not a submission failure.
    """
    try:
        return quip_signing.build_signed_extrinsic(
            iface, signer, call_module, call_function, call_params, nonce=nonce, era=era
        )
    except quip_signing.QuipSigningError as exc:
        raise QuipSubmissionError(f"{call_module}.{call_function} could not be submitted: {exc}") from exc


def submit_built(
    iface: Any,
    quip_signing: Any,
    call_module: str,
    call_function: str,
    wire: bytes,
    ext_hash: str,
    wait_for: str = "inblock",
) -> Any:
    """Submit built extrinsic bytes via :func:`~xqsa.quip.signing.submit_and_watch`.

    Inclusion alone is not success on Substrate, so a receipt that did not
    reach a block or carries a dispatch error is raised as a submission
    failure.

    Raises:
        QuipSubmissionError: if submission fails or the dispatch was
            rejected by the chain.
    """
    try:
        receipt = quip_signing.submit_and_watch(iface, wire, ext_hash, wait_for=wait_for)
    except quip_signing.QuipSigningError as exc:
        raise QuipSubmissionError(f"{call_module}.{call_function} could not be submitted: {exc}") from exc
    if not receipt.is_success:
        raise QuipSubmissionError(
            f"{call_module}.{call_function} failed on-chain (block {receipt.block_hash}): "
            f"{receipt.error or 'unknown dispatch error'}"
        )
    return receipt


def send_extrinsic(
    iface: Any, quip_signing: Any, call_module: str, call_function: str, wire: bytes, ext_hash: str
) -> Any:
    """Send built extrinsic bytes and wait for a block, classifying what is known of the outcome.

    Unlike :func:`submit_built`, a receipt with a dispatch error is returned,
    not raised, so the caller can record the order as failed.

    Raises:
        QuipSubmissionError: if the extrinsic certainly did not land: the node
            rejected it outright, or the pool dropped, invalidated or usurped it.
        QuipUnconfirmedError: if it may have landed: the connection failed while
            watching, the pool reported ``retracted`` or ``finalityTimeout``, or
            no inclusion block was reported.
    """
    try:
        receipt = quip_signing.submit_and_watch(iface, wire, ext_hash)
    except quip_signing.SendOutcomeUnknown as exc:
        raise _unconfirmed(call_module, call_function, ext_hash, None, str(exc)) from exc
    except quip_signing.QuipSigningError as exc:
        raise QuipSubmissionError(f"{call_module}.{call_function} could not be submitted: {exc}") from exc
    except QuipError:
        raise
    except Exception as exc:  # noqa: BLE001 -- a transport fault mid-watch leaves the outcome unknown.
        raise _unconfirmed(call_module, call_function, ext_hash, None, f"the connection failed: {exc}") from exc
    if not receipt.block_hash:
        raise _unconfirmed(call_module, call_function, ext_hash, None, "no inclusion block was reported")
    return receipt


def _unconfirmed(
    call_module: str, call_function: str, ext_hash: str, block_hash: str | None, why: str
) -> QuipUnconfirmedError:
    """Build the error for a sent extrinsic whose outcome is unknown."""
    where = f" in block {block_hash}" if block_hash else ""
    return QuipUnconfirmedError(
        ext_hash,
        block_hash,
        f"{call_module}.{call_function} {ext_hash} was sent{where} but its outcome is unknown ({why}). "
        "Do not resubmit: it may already be on chain.",
    )


def _proposed_order_id(events: list[tuple[int | None, object, object, object]], phase: int) -> int | None:
    """Return the ``order_id`` of the ``JobProposed`` event in ``phase``, or ``None`` if there is none."""
    for event_phase, module_id, event_id, attrs in events:
        if (event_phase, module_id, event_id) == (phase, MEMPOOL_PALLET, JOB_PROPOSED_EVENT):
            order_id = _order_id_from_attributes(attrs)
            if order_id is not None:
                return order_id
    return None


class StatePruned(QuipConnectionError):
    """The node has discarded the state of a past block; a node that keeps full state history can read it."""


class GenesisMismatch(QuipConnectionError):
    """The node serves a different chain from the one expected; another node at a correct endpoint can serve it."""


def account_nonce_at(iface: Any, account_id: Any, block_hash: str) -> int:
    """Return the account's nonce in the state of block ``block_hash``.

    Unlike :func:`account_nonce`, this counts only transactions included up
    to that block, never the node's pool.

    Raises:
        StatePruned: if the node has discarded that block's state.
        QuipConnectionError: if the nonce cannot be read.
    """
    try:
        value = getattr(iface.query("System", "Account", [account_id], block_hash=block_hash), "value", None)
        return int(value["nonce"])
    except QuipMetadataError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        error = StatePruned if _is_state_pruned(exc) else QuipConnectionError
        raise error(f"could not read the account nonce at block {block_hash}: {exc}") from exc


def first_block_past_nonce(iface: Any, account_id: Any, nonce: int, low: int, high: int) -> int:
    """Return the first block from ``low`` to ``high`` whose state has the account's nonce past ``nonce``.

    The nonce never decreases, so a binary search over historical state
    finds it in about log2(high - low) reads. ``high`` must be such a block.

    Raises:
        QuipConnectionError: if a block hash or nonce cannot be read.
    """
    while low < high:
        middle = (low + high) // 2
        if account_nonce_at(iface, account_id, block_hash(iface, middle)) > nonce:
            high = middle
        else:
            low = middle + 1
    return low


@dataclass(frozen=True)
class Landed:
    """A ``propose_job`` in a finalized block: its order id, or the chain's error when its dispatch failed."""

    block_hash: str
    block_number: int
    order_id: int | None = None
    error: str | None = None


def settle_proposal(
    iface: Any, account_id: Any, ext_hash: str, nonce: int, checkpoint: int, window_end: int
) -> Landed | Literal["never", "pending"]:
    """Say from finalized state what became of a sent ``propose_job``, signed mortal with ``nonce``.

    Reads the account nonce at the finalized head. Past ``nonce``, the block
    that spent it is found; if it holds ``ext_hash`` the result is read from
    its events, and otherwise another transaction used the nonce, so the
    proposal can never land: ``"never"``. Not past ``nonce`` with the
    finalized head at ``window_end`` or later, the era has expired unspent:
    ``"never"`` too. Otherwise it may still land: ``"pending"``.

    Raises:
        StatePruned: if the node has discarded a past block's state this
            reads; retrying the same node cannot settle it.
        QuipConnectionError: if a chain read fails; retrying may settle it.
        QuipMetadataError: if the block that spent ``nonce`` has extrinsics
            that do not decode, so ``ext_hash`` may be among them; or if it
            holds ``ext_hash`` but its events cannot be read as a dispatch
            result, or a successful dispatch emitted no ``JobProposed`` in
            its phase: this client cannot read the runtime's blocks or events.
    """
    head_number, head_hash = finalized_head(iface)
    if account_nonce_at(iface, account_id, head_hash) <= nonce:
        return "never" if head_number >= window_end else "pending"
    number = first_block_past_nonce(iface, account_id, nonce, checkpoint, head_number)
    spent_in = block_hash(iface, number)
    try:
        block = iface.get_block(block_hash=spent_in, include_author=False, ignore_decoding_errors=True)
    except QuipMetadataError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        raise QuipConnectionError(f"could not read block {spent_in}: {exc}") from exc
    if block is None:
        raise QuipConnectionError(f"the node did not return block {spent_in}")
    index = _extrinsic_index(block, ext_hash)
    if index is None:
        # The block spent our nonce, so it holds a signed extrinsic; one that did not decode may be ours.
        extrinsics = block.get("extrinsics") or []
        if not extrinsics or any(ext is None for ext in extrinsics):
            raise QuipMetadataError(
                f"block {spent_in} spent nonce {nonce}, but not all of its extrinsics could be decoded, so "
                f"whether it holds propose_job {ext_hash} is unknown"
            )
        return "never"
    try:
        events = list(_events(iface, spent_in))
    except QuipMetadataError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        error = StatePruned if _is_state_pruned(exc) else QuipConnectionError
        raise error(f"could not read the events of block {spent_in}: {exc}") from exc
    for phase, module_id, event_id, attrs in events:
        if (phase, module_id) != (index, "System"):
            continue
        if event_id == "ExtrinsicFailed":
            return Landed(spent_in, number, error=f"System.ExtrinsicFailed: {_dispatch_error_text(iface, attrs)}")
        if event_id == "ExtrinsicSuccess":
            order_id = _proposed_order_id(events, index)
            if order_id is None:
                raise QuipMetadataError(
                    f"propose_job {ext_hash} succeeded in block {spent_in} but no {JOB_PROPOSED_EVENT} event with "
                    f"an order id was found in its phase {index}"
                )
            return Landed(spent_in, number, order_id=order_id)
    raise QuipMetadataError(
        f"block {spent_in} holds propose_job {ext_hash} at index {index}, but no System.ExtrinsicSuccess or "
        "ExtrinsicFailed event in its phase could be read"
    )


def current_block(iface: Any) -> int:
    """Return the current best-block height.

    Reads the head header shallowly (``ignore_decoding_errors``) so an
    opaque digest from a runtime upgrade cannot block learning the height.
    """
    header = iface.get_block_header(ignore_decoding_errors=True)
    return _coerce_block_number(header["header"]["number"])


def fetch_order(iface: Any, order_id: int) -> Mapping[str, Any]:
    """Return the decoded ``JobOrders[order_id]`` mapping.

    Raises:
        QuipConnectionError: if no such order exists on-chain.
    """
    entry = iface.query(MEMPOOL_PALLET, JOB_ORDERS_STORAGE, [order_id])
    value = getattr(entry, "value", None)
    if value is None:
        raise QuipConnectionError(f"order {order_id} not found on-chain ({MEMPOOL_PALLET}.{JOB_ORDERS_STORAGE})")
    return value


def order_lifecycle(order: Mapping[str, Any], current_block: int) -> dict[str, Any]:
    """Derive the lifecycle view of an order at ``current_block``.

    Computes the block-height ``effective_expiry`` and finality from the
    order's own timing (so :meth:`~xqsa.quip.SolverQuip.query` works on orders proposed with
    different parameters), not the solver's configured defaults.
    """
    status = _status_str(order["status"])
    created_at = _coerce_block_number(order["created_at"])
    raw_first = order.get("first_solution_at")
    first_solution_at = _coerce_block_number(raw_first) if raw_first is not None else None
    timing = order["timing"]
    expiry = effective_expiry(
        created_at,
        first_solution_at,
        int(timing["deadline_blocks"]),
        int(timing["block_wait"]),
    )
    return {
        "status": status,
        "created_at": created_at,
        "first_solution_at": first_solution_at,
        "effective_expiry": expiry,
        "is_final": is_final(status, current_block, expiry),
    }


def proposer_orders(iface: Any, account_id: Any) -> list[int]:
    """Return the ids of the orders ``account_id`` proposed, oldest first.

    ``ProposerOrders`` is a value-query ``BoundedVec<u64>`` the pallet pushes
    to on each proposal and removes an order from when a claim or a reclaim
    closes it, so it holds only orders not yet closed. An account with none
    reads as empty, and reversing the list gives newest first.
    """
    entry = iface.query(MEMPOOL_PALLET, PROPOSER_ORDERS_STORAGE, [account_id])
    return [int(order_id) for order_id in getattr(entry, "value", None) or []]


def unregistered_solvers(iface: Any, accounts: Sequence[str]) -> list[str]:
    """Return the ``accounts`` with no ``Solvers`` entry, in the order given.

    One read per account: an allowlist holds at most ``MaxBidMiners`` (16 on
    aglais), so batching would save little.
    """
    return [
        account
        for account in accounts
        if getattr(iface.query(MEMPOOL_PALLET, SOLVERS_STORAGE, [account]), "value", None) is None
    ]


def front_runner(iface: Any, order_id: int) -> Mapping[str, Any] | None:
    """Return a ``SingleBest`` order's leader as ``{"solver", "energy_milli"}``, or ``None``.

    ``OrderFrontRunner`` holds one entry, written by the first accepted answer
    and replaced only by a strictly lower energy; an unanswered order, or one
    ranked top-N, has none.
    """
    entry = iface.query(MEMPOOL_PALLET, ORDER_FRONT_RUNNER_STORAGE, [order_id])
    return getattr(entry, "value", None)


def top_solvers(iface: Any, order_id: int) -> list[Mapping[str, Any]]:
    """Return a top-N order's ranked ``{"solver", "energy_milli"}`` entries, best first.

    ``OrderTopSolvers`` is a value query, so an unanswered order, or one
    ranked ``SingleBest``, reads as empty.
    """
    entry = iface.query(MEMPOOL_PALLET, ORDER_TOP_SOLVERS_STORAGE, [order_id])
    return list(getattr(entry, "value", None) or [])


def block_hash(iface: Any, number: int) -> str:
    """Return the hash of the block at height ``number`` as ``0x`` hex.

    Raises:
        QuipConnectionError: if the hash cannot be read, or the node knows no
            block at that height.
    """
    try:
        raw = iface.get_block_hash(number)
    except QuipMetadataError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        raise QuipConnectionError(f"could not read the hash of block {number}: {exc}") from exc
    if not raw:
        raise QuipConnectionError(f"the node returned no hash for block {number}")
    return _as_hex(raw)


def block_timestamp(iface: Any, block_hash: str) -> int:
    """Return the ``Timestamp.Now`` of the block ``block_hash``, in Unix milliseconds.

    Raises:
        QuipConnectionError: if the timestamp is absent or cannot be read.
    """
    try:
        entry = iface.query("Timestamp", "Now", block_hash=block_hash)
    except QuipMetadataError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any read failure is a connection fault.
        raise QuipConnectionError(f"could not read Timestamp.Now at block {block_hash}: {exc}") from exc
    millis = _as_int_or_none(getattr(entry, "value", None))
    if millis is None:
        raise QuipConnectionError(f"Timestamp.Now is absent at block {block_hash}")
    return millis


def proposal_fee(iface: Any, block_hash: str, order_id: int) -> int:
    """Return the fee, in planck, that the ``propose_job`` of ``order_id`` paid.

    Finds the order's ``JobProposed`` event in its inclusion block, then the
    ``TransactionFeePaid`` event of the same extrinsic, matched by phase
    index. ``actual_fee`` is the whole charge, tip included.

    Raises:
        QuipConnectionError: if the block holds no ``JobProposed`` event for
            ``order_id``, or no fee event in that extrinsic's phase.
    """
    events = list(_events(iface, block_hash))
    phase = next(
        (
            phase
            for phase, module_id, event_id, attrs in events
            if module_id == MEMPOOL_PALLET
            and event_id == JOB_PROPOSED_EVENT
            and _order_id_from_attributes(attrs) == order_id
        ),
        None,
    )
    if phase is None:
        raise QuipConnectionError(f"block {block_hash} holds no {JOB_PROPOSED_EVENT} event for order {order_id}")
    fee = _phase_fee(events, phase)
    if fee is None:
        raise QuipConnectionError(
            f"block {block_hash} holds no {TRANSACTION_FEE_PAID_EVENT} event for the propose_job of order {order_id}"
        )
    return fee


def extrinsic_fee(iface: Any, block_hash: str, ext_hash: str) -> int:
    """Return the fee, in planck, that the extrinsic ``ext_hash`` paid in block ``block_hash``.

    For an extrinsic with no order to find it by, such as a ``propose_job``
    whose dispatch failed: the chain charges the fee either way. Finds the
    extrinsic's index in the block, then its ``TransactionFeePaid`` event.

    Raises:
        QuipConnectionError: if the block does not hold ``ext_hash``, or no fee
            event in its phase.
    """
    block = iface.get_block(block_hash=block_hash, include_author=False, ignore_decoding_errors=True) or {}
    index = _extrinsic_index(block, ext_hash)
    if index is None:
        raise QuipConnectionError(f"block {block_hash} does not hold extrinsic {ext_hash}")
    fee = _phase_fee(list(_events(iface, block_hash)), index)
    if fee is None:
        raise QuipConnectionError(f"block {block_hash} holds no {TRANSACTION_FEE_PAID_EVENT} event for {ext_hash}")
    return fee


def _phase_fee(events: list[tuple[int | None, object, object, object]], phase: int) -> int | None:
    """Return the ``actual_fee`` of the ``TransactionFeePaid`` event in ``phase``, or ``None`` if there is none."""
    for event_phase, module_id, event_id, attrs in events:
        if (event_phase, module_id, event_id) == (phase, TRANSACTION_PAYMENT_PALLET, TRANSACTION_FEE_PAID_EVENT):
            fee = _as_int_or_none(_attribute(attrs, "actual_fee"))
            if fee is not None:
                return fee
    return None


def fetch_solutions(iface: Any, order_id: int) -> list[Mapping[str, Any]]:
    """Return every solver's submission for ``order_id`` (decoded ``JobSolution``s).

    ``OrderSolutions`` is a double map keyed ``(order_id, solver)``; querying
    by the first key iterates the per-solver entries.
    """
    submissions: list[Mapping[str, Any]] = []
    for _solver_key, value in iface.query_map(MEMPOOL_PALLET, ORDER_SOLUTIONS_STORAGE, [order_id]):
        decoded = value.value if hasattr(value, "value") else value
        if decoded is not None:
            submissions.append(decoded)
    return submissions


def _events(iface: Any, block_hash: str) -> Iterator[tuple[int | None, object, object, object]]:
    """Yield ``(phase_index, module_id, event_id, attributes)`` for each event at ``block_hash``.

    Unwraps the record shapes ``substrate-interface`` decodes into.
    ``phase_index`` is the index of the extrinsic that emitted the event, read
    from the sibling ``extrinsic_idx`` field or from the ``phase`` dict, and
    ``None`` for events outside an extrinsic. ``attributes`` falls back to the
    older ``params`` key.
    """
    for record in iface.get_events(block_hash=block_hash) or []:
        value = record.value if hasattr(record, "value") else record
        if not isinstance(value, dict):
            continue
        inner = value.get("event", value)
        if not isinstance(inner, dict):
            continue
        phase = value.get("extrinsic_idx")
        if phase is None:
            phase = _phase_extrinsic_index(value.get("phase"))
        attrs = inner.get("attributes")
        if attrs is None:
            attrs = inner.get("params")
        yield _as_int_or_none(phase), *_event_ids(inner), attrs


def _is_storage_absent(exc: BaseException) -> bool:
    """Whether ``exc`` means a storage item is absent from the runtime metadata.

    Distinguishes an older runtime that simply lacks the storage item (a genuine
    absence, which the caller reports as "no chain default") from a
    transport/decode fault (surface as an error).
    The exception type is imported lazily so this module still imports without the
    optional ``[quip]`` extra installed.
    """
    try:
        from substrateinterface.exceptions import StorageFunctionNotFound
    except Exception:  # noqa: BLE001 -- extra not installed / stubbed in tests.
        return False
    return isinstance(exc, StorageFunctionNotFound)


def _is_state_pruned(exc: BaseException) -> bool:
    """Whether ``exc`` is the node refusing a read of state it has pruned (Substrate's "State already discarded")."""
    return "State already discarded" in str(exc)


def _status_str(value: object) -> str:
    """Normalize a SCALE-decoded ``OrderStatus`` enum to its variant name.

    Substrate-interface may hand back a bare variant string (``"Opened"``) or a
    single-key mapping (``{"Opened": None}``); both reduce to the name.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and len(value) == 1:
        return next(iter(value))
    # An unrecognized shape must not leak a garbled decoded value through the
    # public status() API; report a stable sentinel instead. Benign for
    # finality, which is governed by block height, not the status string.
    return "Unknown"


def _coerce_block_number(raw: object) -> int:
    """Coerce a header/storage block number (int or ``0x``-hex string) to ``int``."""
    if isinstance(raw, str):
        return int(raw, 16) if raw.startswith("0x") else int(raw)
    if raw is None:
        raise QuipConnectionError("expected a block number but the chain returned none")
    return int(raw)  # type: ignore[arg-type]


def _order_id_from_attributes(attrs: object) -> int | None:
    """Read the ``order_id`` from a decoded ``JobProposed`` event's attributes.

    Tolerates the shapes ``substrate-interface`` decodes event attributes into:
    a field-keyed mapping (``{"order_id": N, ...}``, metadata v14+) or a
    ``params`` list of ``{"name", "value"}`` dicts. Returns ``None`` if no order
    id can be read from a keyed shape, so the caller reports a missing event
    rather than guess.

    A bare-positional fallback (trusting ``attrs[0]`` as the order id) is
    deliberately NOT attempted: if a pre-release event layout reorders to an
    int-first field it would return a plausible-but-wrong id and ``solve()``
    would settle the wrong order silently. Better to raise "no order id found".
    """
    return _as_int_or_none(_attribute(attrs, "order_id"))


def _attribute(attrs: object, name: str) -> object:
    """Read the field ``name`` from decoded event attributes, or ``None`` if absent.

    Accepts a field-keyed mapping or a ``params`` list of ``{"name", "value"}``
    dicts. A bare positional list yields ``None``; see
    :func:`_order_id_from_attributes` for why.
    """
    if isinstance(attrs, Mapping):
        return attrs.get(name)
    if isinstance(attrs, (list, tuple)):
        for item in attrs:
            if isinstance(item, Mapping) and item.get("name") == name:
                return item.get("value")
    return None
