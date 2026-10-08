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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from xqsa.quip.codec import _as_hex, _as_int_or_none, _canonical_hex, _event_ids, effective_expiry, is_final
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

    Each field is ``None`` when the runtime does not expose that constant; a
    missing bound is not checked.
    """

    min_reward: int | None
    max_deadline_blocks: int | None
    max_block_wait: int | None
    max_solutions: int | None


def read_limits(iface: Any) -> ChainLimits:
    """Read the mempool's order bounds into a :class:`ChainLimits`.

    Raises:
        QuipConnectionError: if reading a constant fails.
        QuipMetadataError: propagated unchanged from the constant reads.
    """

    def read(name: str) -> int | None:
        value = read_constant(iface, name)
        return None if value is None else int(value)

    return ChainLimits(
        min_reward=read("MinReward"),
        max_deadline_blocks=read("MaxDeadlineBlocks"),
        max_block_wait=read("MaxBlockWait"),
        max_solutions=read("MaxSolutions"),
    )


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
            f"could not read the free balance of {_as_hex(account_id)} from "
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
) -> tuple[bytes, str]:
    """Sign an extrinsic via :func:`xqsa.quip.signing.build_signed_extrinsic`: ``(wire, hash)``.

    ``nonce`` signs with that account nonce; ``None`` lets the signing layer
    read the current one.

    Raises:
        QuipSubmissionError: if assembly or signing fails.
    """
    try:
        return quip_signing.build_signed_extrinsic(iface, signer, call_module, call_function, call_params, nonce=nonce)
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


def unconfirmed_error(ext_hash: str, block_hash: str | None, why: str) -> QuipUnconfirmedError:
    """Build the :class:`QuipUnconfirmedError` for a sent ``propose_job`` whose outcome is unknown."""
    return _unconfirmed(MEMPOOL_PALLET, PROPOSE_JOB_CALL, ext_hash, block_hash, why)


def read_proposed_order_id(iface: Any, block_hash: str | None) -> int:
    """Extract the order id from the ``JobProposed`` event at ``block_hash``.

    Raises:
        QuipSubmissionError: if the block hash is missing or the block holds
            no ``JobProposed`` event with a readable ``order_id``.
    """
    if not block_hash:
        raise QuipSubmissionError("propose_job was included but returned no block hash; cannot read the order id")
    for record in iface.get_events(block_hash=block_hash) or []:
        value = record.value if hasattr(record, "value") else record
        inner = value.get("event", value) if isinstance(value, dict) else value
        if not isinstance(inner, dict):
            continue
        module_id, event_id = _event_ids(inner)
        if module_id != MEMPOOL_PALLET or event_id != JOB_PROPOSED_EVENT:
            continue
        attrs = inner.get("attributes")
        if attrs is None:
            attrs = inner.get("params")
        order_id = _order_id_from_attributes(attrs)
        if order_id is not None:
            return order_id
    raise QuipSubmissionError(
        f"propose_job was included in block {block_hash} but no {JOB_PROPOSED_EVENT} event with an order id was found"
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
    if isinstance(attrs, Mapping):
        return _as_int_or_none(attrs.get("order_id")) if "order_id" in attrs else None
    if isinstance(attrs, (list, tuple)):
        for item in attrs:
            if isinstance(item, Mapping) and item.get("name") == "order_id":
                return _as_int_or_none(item.get("value"))
    return None
