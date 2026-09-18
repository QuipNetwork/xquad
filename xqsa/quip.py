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
SolverQuip: submit XQMX Ising models to the Quip Network mempool.

``SolverQuip`` proposes a job to the ``QuantumComputeMempool`` pallet via
``propose_job``, waits for a miner to solve it, and reads the solution back as
an XQMX sample. It is a synchronous :class:`~xqsa.solver.Solver`; the
``substrate-interface`` client is sync and the Quip chain is hybrid-signed, so
there is no async surface here.

Configuration is resolved from constructor arguments first, then environment:

    =====================  ====================================================
    Argument               Environment variable
    =====================  ====================================================
    ``url``                ``QUIP_RPC_URL``      websocket RPC endpoint
    ``seed``               ``QUIP_SIGNER_SEED``  32-byte hex master seed
    ``keystore``           ``QUIP_KEYSTORE``     keystore path (load or create)
    ``reward``             ``QUIP_REWARD``       reward in planck (else MinReward)
    =====================  ====================================================

Provide exactly one of ``seed`` or ``keystore`` (a keystore is generated on
first use if absent). The account must be funded; on localdev the faucet at
``QUIP_FAUCET_URL`` tops it up.

Signing is hybrid (sr25519 + FN-DSA-512): the chain's ``Signature`` is
``HybridTxSignature``, which ``substrate-interface`` cannot produce, so all
crypto is delegated to the ``quip_signer`` extension and extrinsic assembly to
:mod:`xqsa.quip_signing`. Install both with ``pip install xqsa[quip]`` (or
``uv sync --extra quip``); ``quip_signer`` resolves a wheel on Linux and builds
from its sdist with a local Rust toolchain elsewhere. It is lazy-imported, so
every other xqsa solver installs and runs without it; only constructing
``SolverQuip`` requires the extra.

The chain client is built by :func:`xqsa.quip_metadata.connect`, not by
``substrateinterface.SubstrateInterface`` directly: Quip runtimes serve Metadata
V16 and ``scalecodec`` decodes at most V14, so the stock client cannot read the
chain at all. The shim pulls V14 through the versioned runtime API and is
confined to the instances built here.

Beta-testnet notice: the Quip devnet/testnet is pre-release. Pallet metadata,
the signed-extension layout, and economic parameters can change between
releases; pin a node image and re-validate after upgrades.
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from xqsa import quip_metadata
from xqsa.quip_codec import (
    _TERMINAL_STATUSES,
    DEFAULT_ISING_SPEC_ID,
    QUIP_COEFFICIENTS_DOC_URL,
    QuipError,
    QuipMetadataError,
    Topology,
    _as_hex,
    _as_int_or_none,
    _canonical_hex,
    _event_ids,
    check_allowed_values,
    decode_solution,
    effective_expiry,
    is_final,
    ising_energy_milli,
    model_to_ising,
)
from xqsa.solver import Solver, SolverResult

if TYPE_CHECKING:
    from xqsa.quip_codec import IsingJob
    from xqvm_py.xqmx import XQMX

logger = logging.getLogger(__name__)

# Default signed-extension config. ``deadline_blocks``/``block_wait`` stay well
# under the pallet bounds (<= 1000 / <= 100); the lifecycle math lives in
# ``quip_codec`` (``effective_expiry``/``is_final``).
DEFAULT_DEADLINE_BLOCKS = 100
DEFAULT_BLOCK_WAIT = 10
DEFAULT_POLL_INTERVAL = 6.0
DEFAULT_TIMEOUT = 600.0

# Heuristic free-balance buffer added over the reserved reward in the pre-submit
# balance check, to cover the ``propose_job`` transaction fee. The chain does the
# authoritative reserve + fee deduction; this only yields a friendly early error.
# ``propose_job`` carries the placed subgraph's arrays, whose length scales with
# the model rather than with the hardware graph -- keep the buffer generous
# anyway, since a large model still pays a non-trivial length fee. Tuned during
# live validation.
FEE_HEADROOM_PLANCK = 100_000_000_000  # 0.1 tQUIP (12 decimals).

# The ``QuantumComputeMempool`` pallet name and the calls/storage/events this
# backend uses; gathered here so the chain surface is easy to audit.
MEMPOOL_PALLET = "QuantumComputeMempool"
PROPOSE_JOB_CALL = "propose_job"
RECLAIM_ORDER_CALL = "reclaim_order"
JOB_PROPOSED_EVENT = "JobProposed"
JOB_ORDERS_STORAGE = "JobOrders"
ORDER_SOLUTIONS_STORAGE = "OrderSolutions"

# ``QuantumPow`` topology storage this backend reads. ``RegisteredTopologies``
# (queried in ``_fetch_topology``) is the topology set; ``MineableTopologies``
# is the subset miners actually match on -- a hash can be registered without
# being mineable, so both are checked before the reward is reserved.
MINEABLE_TOPOLOGIES_STORAGE = "MineableTopologies"


class QuipConnectionError(QuipError):
    """Raised when the Quip node is unreachable or missing expected chain state."""


class QuipSubmissionError(QuipError):
    """Raised when an extrinsic cannot be submitted or is rejected by the chain."""


class QuipTopologyError(QuipError):
    """Raised when the resolved topology is registered but not in the mineable set.

    A distinct pre-submit configuration error: the hash exists in
    ``QuantumPow.RegisteredTopologies`` (so ``_fetch_topology`` succeeds) but is
    absent from ``QuantumPow.MineableTopologies``, so no miner would match a job
    proposed against it. Raised before the reward is reserved, unlike the
    connection/submission/finality errors around it.
    """


class QuipTimeoutError(QuipError):
    """Raised when an order does not reach finality before the configured timeout.

    Carries ``order_id`` so the caller can later recover the result via
    :meth:`SolverQuip.query` once the order finalizes.
    """

    def __init__(self, order_id: int, message: str | None = None) -> None:
        self.order_id = order_id
        super().__init__(message or f"order {order_id} did not reach finality before the timeout")


class QuipJobFailedError(QuipError):
    """Raised when a final order yielded no usable solution.

    Carries ``order_id``. Auto-reclaim of the reserved reward is best-effort and
    applies only on the no-submissions path (there the message notes the refund
    outcome); the other paths -- a winning submission with no solution vectors,
    or a missing result field -- raise without reclaiming.
    """

    def __init__(self, order_id: int, message: str | None = None) -> None:
        self.order_id = order_id
        super().__init__(message or f"order {order_id} closed with no solutions")


class SolverQuip(Solver):
    """Network-mempool backend: solve XQMX Ising models on the Quip Network.

    Constructing the solver connects to the node, resolves the Ising job spec,
    default reward, and target topology from chain state, and builds the hybrid
    signer from a seed or keystore. The topology defaults to the chain's
    ``QuantumPow.DefaultTopology``; pass ``topology=`` to override. An
    unresolvable topology raises rather than falling through to a hash no chain
    would accept. See the module docstring for configuration and installation.

    Raises:
        ImportError: if the ``[quip]`` extra is not installed
            (``pip install xqsa[quip]`` -- provides ``substrate-interface`` and
            the ``quip_signer`` signing extension).
        ValueError: if no RPC URL or signer (seed/keystore) is configured.
        QuipConnectionError: if the node is unreachable or the configured Ising
            spec is not registered on-chain.
        QuipMetadataError: if the node's runtime metadata cannot be decoded by
            the installed substrate-interface/scalecodec, even through the V14
            shim. Raised in its own right rather than folded into
            QuipConnectionError, because the remedy is a runtime that still
            serves V14, not a retry.
    """

    def __init__(
        self,
        url: str | None = None,
        seed: str | None = None,
        keystore: str | None = None,
        reward: int | None = None,
        *,
        spec_id: str | None = None,
        topology: str | None = None,
        mode: str = "Open",
        resolution: str = "SingleBest",
        delivery: str = "OnChainOnly",
        deadline_blocks: int = DEFAULT_DEADLINE_BLOCKS,
        block_wait: int = DEFAULT_BLOCK_WAIT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        # Two distinct guards, one per piece of the [quip] extra: client + signer.
        try:
            import substrateinterface  # noqa: F401 -- presence check; the client is built by quip_metadata.connect.
        except ImportError as exc:
            raise ImportError(
                "substrate-interface is not installed. Install xqsa with: pip install xqsa[quip]"
            ) from exc
        try:
            import quip_signer  # noqa: F401 -- presence check; used via xqsa.quip_signing.
        except ImportError as exc:
            raise ImportError(
                "the quip_signer signing extension is not installed. Install it with: pip install xqsa[quip]"
            ) from exc
        from xqsa import quip_signing

        resolved_url = url or os.environ.get("QUIP_RPC_URL")
        if not resolved_url:
            raise ValueError("A Quip RPC URL is required. Pass url= or set QUIP_RPC_URL.")

        self._signer = self._build_signer(quip_signing, seed=seed, keystore=keystore)

        self._mode = mode
        self._resolution = resolution
        self._delivery = delivery
        self._deadline_blocks = deadline_blocks
        self._block_wait = block_wait
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._topology_cache: dict[str, Topology] = {}
        self._warned_allowed_values = False
        self._quip_signing = quip_signing

        try:
            # Not substrateinterface.SubstrateInterface directly: Quip runtimes
            # serve metadata V16, which scalecodec cannot decode. The subclass
            # pulls V14 through the versioned runtime API instead, and confines
            # that to instances we build. See xqsa.quip_metadata.
            self._iface = quip_metadata.connect(resolved_url)
            # Resolve metadata here rather than letting the first storage read
            # trigger it: a node whose metadata this client cannot decode must
            # fail as QuipMetadataError, and every read below wraps its faults
            # as "could not read <pallet constant>", which names the wrong
            # cause. Doing it once, in the open, also makes the failure
            # independent of which resolve step happens to run first.
            self._iface.init_runtime()
        except QuipMetadataError:
            raise  # a client/runtime metadata mismatch is not a connection fault.
        except Exception as exc:  # noqa: BLE001 -- any connect failure is a connection error.
            raise QuipConnectionError(f"could not connect to the Quip node at {resolved_url}: {exc}") from exc
        self._url = resolved_url

        self._spec_id = self._resolve_spec_id(spec_id)
        self._reward = self._resolve_reward(reward)
        self._topology_hash = self._resolve_topology_hash(topology)

    # ------------------------------------------------------------------
    # Identity / configuration resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _build_signer(quip_signing: Any, *, seed: str | None, keystore: str | None) -> Any:
        """Build the hybrid signer from a seed or keystore (env fallbacks applied).

        Raises:
            ValueError: if neither a seed nor a keystore is configured.
        """
        resolved_seed = seed or os.environ.get("QUIP_SIGNER_SEED")
        resolved_keystore = keystore or os.environ.get("QUIP_KEYSTORE")
        if resolved_seed:
            return quip_signing.signer_from_seed(resolved_seed)
        if resolved_keystore:
            return quip_signing.load_or_generate_keystore(resolved_keystore).signer
        raise ValueError("A signer is required. Pass seed= (or QUIP_SIGNER_SEED) or keystore= (or QUIP_KEYSTORE).")

    def _resolve_spec_id(self, spec_id: str | None) -> str:
        """Resolve and verify the Ising job spec id.

        Prefers the explicit ``spec_id``, then the chain's ``DefaultIsingSpecId``
        constant, then the pinned :data:`DEFAULT_ISING_SPEC_ID`. The chosen id
        must exist in ``QuantumComputeMempool.JobSpecs``.

        Raises:
            QuipConnectionError: if the spec is not registered on-chain.
        """
        resolved = spec_id or self._chain_default_spec_id() or DEFAULT_ISING_SPEC_ID
        resolved = _as_hex(resolved)
        entry = self._iface.query("QuantumComputeMempool", "JobSpecs", [resolved])
        if entry is None or getattr(entry, "value", None) is None:
            raise QuipConnectionError(
                f"Ising job spec {resolved} is not registered on-chain (QuantumComputeMempool.JobSpecs); "
                "check the node is seeded and the spec id is correct."
            )
        return resolved

    def _chain_default_spec_id(self) -> str | None:
        """Read the ``DefaultIsingSpecId`` runtime constant, or ``None`` if unset.

        ``get_constant`` returns ``None`` when the constant is absent from the
        runtime metadata (a genuine "no chain default"); a transport/decode fault
        is surfaced as a connection error rather than silently masked as absence,
        which would let a transient blip pass for a chain that declares no
        default.

        Raises:
            QuipConnectionError: if reading the constant fails (as opposed to the
                constant simply not being defined on this runtime).
            QuipMetadataError: propagated unchanged; undecodable metadata is not
                an unreadable constant.
        """
        try:
            const = self._iface.get_constant("QuantumComputeMempool", "DefaultIsingSpecId")
        except QuipMetadataError:
            raise  # undecodable metadata, not an unreadable constant.
        except Exception as exc:  # noqa: BLE001 -- a fault, not a genuine absence.
            raise QuipConnectionError(
                f"could not read the QuantumComputeMempool.DefaultIsingSpecId constant: {exc}"
            ) from exc
        value = getattr(const, "value", None)
        return _as_hex(value) if value is not None else None

    def _resolve_topology_hash(self, topology: str | None) -> str:
        """Resolve the topology hash to target.

        Prefers the explicit ``topology`` argument, then the chain's
        ``QuantumPow.DefaultTopology``. Resolved once at construction so
        :meth:`solve` and :meth:`query` on the same instance agree on the
        topology even if the chain default later changes.

        Those two are the only sources, deliberately. The same advantage2
        graph hashes differently across deployments (the hash folds in each
        deployment's allowed-value specs), so no constant could be correct
        everywhere, and a stale one would resolve to a topology no chain
        accepts. An unresolvable topology raises instead.

        Raises:
            ValueError: if neither source yields a hash.
        """
        resolved = topology or self._chain_default_topology()
        if not resolved:
            raise ValueError("no topology hash available: pass topology=, or seed QuantumPow.DefaultTopology on-chain.")
        return _as_hex(resolved)

    def _chain_default_topology(self) -> str | None:
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
            entry = self._iface.query("QuantumPow", "DefaultTopology")
        except QuipMetadataError:
            raise  # undecodable metadata, not an absent storage item.
        except Exception as exc:  # noqa: BLE001 -- classify absence vs. fault below.
            if _is_storage_absent(exc):
                return None  # the runtime does not define this storage item.
            raise QuipConnectionError(
                f"could not read QuantumPow.DefaultTopology: {exc}; "
                "pass topology= explicitly to bypass the chain default."
            ) from exc
        value = getattr(entry, "value", None)
        return _as_hex(value) if value is not None else None

    def _resolve_reward(self, reward: int | None) -> int:
        """Resolve the proposal reward (planck): arg, then ``QUIP_REWARD``, then MinReward."""
        if reward is not None:
            return int(reward)
        env_reward = os.environ.get("QUIP_REWARD")
        if env_reward:
            return int(env_reward)
        const = self._iface.get_constant("QuantumComputeMempool", "MinReward")
        return int(const.value)

    # ------------------------------------------------------------------
    # Chain reads
    # ------------------------------------------------------------------

    def _fetch_topology(self, topology_hash: str | None = None) -> Topology:
        """Fetch and cache the hardware topology from ``QuantumPow.RegisteredTopologies``.

        ``topology_hash`` defaults to the hash resolved at construction (an
        explicit ``topology=``, else the chain's ``QuantumPow.DefaultTopology``).
        The decoded ``TopologyMeta`` carries the graph and the allowed-value
        sets (captured for the educational warning).

        Raises:
            ValueError: if no topology hash is configured.
            QuipConnectionError: if the topology is not registered on-chain.
        """
        key = topology_hash or self._topology_hash
        if not key:
            raise ValueError(
                "no topology hash configured. Pass topology= (the chain QuantumPow.DefaultTopology resolved empty)."
            )
        key = _as_hex(key)
        cached = self._topology_cache.get(key)
        if cached is not None:
            return cached
        entry = self._iface.query("QuantumPow", "RegisteredTopologies", [key])
        if entry is None or getattr(entry, "value", None) is None:
            raise QuipConnectionError(f"topology {key} is not registered on-chain (QuantumPow.RegisteredTopologies)")
        topology = Topology.from_chain(entry.value)
        self._topology_cache[key] = topology
        return topology

    def _mineable_topologies(self) -> frozenset[str] | None:
        """Return the canonical hashes in ``QuantumPow.MineableTopologies``, or ``None``.

        ``MineableTopologies`` is a ``StorageMap<H256, ()>`` -- a set keyed by
        topology hash. It is enumerated (via ``query_map``) rather than probed
        with a keyed lookup so a defined-but-empty map is distinguishable from a
        hash simply being absent from a populated one: a keyed read returning
        ``None`` cannot tell those apart, and the two demand opposite outcomes.

        Returns ``None`` only when the storage item is genuinely absent from the
        runtime -- the pallet does not compile the item into metadata at all.
        That is the sole "skip the mineability check" signal: a runtime that
        *does* compile the pallet always exposes the item in metadata, so a
        deployment which simply does not use the feature can only surface it as
        an empty map, never as runtime absence. A defined-but-empty set therefore
        returns an empty ``frozenset`` (no topology is mineable, so the caller
        rejects), never ``None``.

        Distinguishes genuine runtime absence from a transport/decode fault,
        mirroring :meth:`_chain_default_topology`: a read fault, or entries that
        are present but all fail to decode to a hash, surface as a connection
        error rather than silently skipping the check.

        Raises:
            QuipConnectionError: if reading the storage map fails for any reason
                other than the item being absent from the runtime, or if the map
                returns entries that none decode to a canonical hash.
            QuipMetadataError: propagated unchanged; undecodable metadata is not
                an absent storage item.
        """
        try:
            entries = self._iface.query_map("QuantumPow", MINEABLE_TOPOLOGIES_STORAGE)
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
        # runtime lacking the storage item entirely skips the check.
        return frozenset(hashes)

    def _ensure_mineable(self, topology_hash: str | None = None) -> None:
        """Raise if the resolved topology is registered but not in the mineable set.

        Called from :meth:`solve` after :meth:`_fetch_topology` (which already
        confirmed the hash is registered) and before the reward is reserved.
        ``topology_hash`` defaults to the hash resolved at construction, mirroring
        :meth:`_fetch_topology`'s defaulting so both validate the same hash from a
        single source of truth.

        A ``MineableTopologies`` absent from the runtime skips the check (see
        :meth:`_mineable_topologies`); a defined-but-empty set, or a populated set
        that omits the hash, rejects -- no miner would match the proposed job.

        Raises:
            QuipTopologyError: if the set is defined and omits the hash.
            QuipConnectionError: if the set cannot be read (transport/decode fault).
        """
        key = topology_hash or self._topology_hash
        mineable = self._mineable_topologies()
        if mineable is None:
            logger.debug("QuantumPow.%s is unset; skipping the mineability check", MINEABLE_TOPOLOGIES_STORAGE)
            return
        if not mineable:
            raise QuipTopologyError(
                f"the chain's mineable set (QuantumPow.{MINEABLE_TOPOLOGIES_STORAGE}) is empty; no topology "
                f"is currently mineable, so a job proposed against {_as_hex(key)} would sit unsolved until "
                "expiry. Seed a topology into MineableTopologies on-chain."
            )
        if _canonical_hex(key) not in mineable:
            raise QuipTopologyError(
                f"topology {_as_hex(key)} is registered but not in the chain's mineable set "
                f"(QuantumPow.{MINEABLE_TOPOLOGIES_STORAGE}); no miner would pick up a job proposed against "
                "it. Pass a mineable topology= or seed the topology into MineableTopologies on-chain."
            )

    def _check_balance(self, required: int) -> int:
        """Return the account's free balance, raising if it cannot cover ``required``.

        ``propose_job`` reserves the full reward at proposal, so the free
        balance must cover the reward plus fees.

        Raises:
            QuipConnectionError: if the account entry or its ``data.free`` field
                cannot be read/decoded -- an anomalous read must not be reported
                as a zero balance.
            QuipSubmissionError: if the readable free balance is below ``required``.
        """
        entry = self._iface.query("System", "Account", [self._signer.account_id])
        value = getattr(entry, "value", None)
        try:
            free = int(value["data"]["free"])
        except (TypeError, KeyError, ValueError) as exc:
            # A non-None entry with a missing/undecodable data.free is a read
            # or decode fault, not a funded-to-zero account -- surface it rather
            # than defaulting free=0 and blaming the user for "insufficient balance".
            raise QuipConnectionError(
                f"could not read the free balance of {_as_hex(self._signer.account_id)} from "
                f"System.Account (got {value!r}); cannot verify funding"
            ) from exc
        if free < required:
            raise QuipSubmissionError(
                f"insufficient balance: account holds {free} planck but the job needs {required} "
                f"(reward {self._reward} + fees). Fund the account via the faucet."
            )
        return free

    # ------------------------------------------------------------------
    # Coefficient allowed-value warning
    # ------------------------------------------------------------------

    def _maybe_warn_allowed_values(self, job: IsingJob) -> None:
        """Emit a one-time educational warning if any encoded coefficient is out-of-spec.

        The chain accepts out-of-spec coefficients (the pallet does not enforce
        the allowed-value sets), the SA miner solves them as-is, and real
        hardware rescales monotonically -- so this never blocks submission. The
        warning fires at most once per solver instance.
        """
        if self._warned_allowed_values:
            return
        offenders = check_allowed_values(job, allowed_h=job.topology.allowed_h, allowed_j=job.topology.allowed_j)
        if not offenders:
            return
        self._warned_allowed_values = True
        kind, position, milli = offenders[0]
        warnings.warn(
            f"{len(offenders)} encoded coefficient(s) fall outside this topology's allowed-value set "
            f"(e.g. {kind}[{position}] = {milli} milli). Quip accepts and solves these as-is and real "
            f"hardware rescales coefficients monotonically (the optimum is preserved), so submission "
            f"proceeds. See {QUIP_COEFFICIENTS_DOC_URL}.",
            stacklevel=2,
        )

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_bounded(value: Any) -> Any:
        """Wrap a ``BoundedVec`` call field for ``compose_call``.

        ``BoundedVec`` parameters on the ``v0.2`` runtime decode as 1-field
        composites in substrate metadata, so each value is passed as a 1-tuple
        (mirrors the miner's ``submit_solution`` wrapping in ``quip-protocol``
        ``shared/mempool_miner_controller.py``). If a future runtime exposes
        ``BoundedVec`` as a plain ``Vec``, drop the wrapping here -- this method
        isolates the quirk so the change is one line.
        """
        return (value,)

    def _propose_job(self, job: IsingJob) -> int:
        """Submit ``job`` via ``propose_job`` and return the assigned order id.

        Builds the ``QuantumComputeMempool.propose_job`` call params from the
        placed :class:`~xqsa.quip_codec.IsingJob`, signs and submits the
        extrinsic, then reads the ``JobProposed`` event from the inclusion block
        for the ``order_id`` (the call has no return value -- the id is only
        emitted as an event).

        Raises:
            QuipSubmissionError: if the extrinsic fails on-chain or no
                ``JobProposed`` event is found in the inclusion block.
        """
        ising_params = {
            "nodes": self._wrap_bounded(list(job.nodes)),
            "edges": self._wrap_bounded([list(edge) for edge in job.edges]),
            "h_values": self._wrap_bounded(list(job.h_values)),
            "j_values": self._wrap_bounded(list(job.j_values)),
            # Quality floors are advisory and unset in v1 (Option::None).
            "min_energy_milli": None,
            "min_diversity_milli": None,
            "min_solutions": None,
        }
        call_params = {
            "spec_id": self._spec_id,
            "ising_params": ising_params,
            "reward": self._reward,
            # Unit enum variants encode from their bare variant name (the same
            # form they decode to -- see the miner's _decode_result_delivery).
            # Data-carrying variants (Bid / TopN* / Callback*) are out of v1.
            "mode": self._mode,
            "resolution": self._resolution,
            "deadline_blocks": self._deadline_blocks,
            "block_wait": self._block_wait,
            "delivery": self._delivery,
        }
        receipt = self._submit_extrinsic(MEMPOOL_PALLET, PROPOSE_JOB_CALL, call_params)
        order_id = self._read_proposed_order_id(receipt.block_hash)
        logger.info(
            "proposed Quip job: order_id=%d spec_id=%s reward=%d planck (block %s)",
            order_id,
            self._spec_id,
            self._reward,
            receipt.block_hash,
        )
        return order_id

    def _submit_extrinsic(
        self,
        call_module: str,
        call_function: str,
        call_params: dict,
        wait_for: str = "inblock",
    ) -> Any:
        """Sign, submit, and confirm an extrinsic, returning its receipt.

        A thin wrapper over :func:`xqsa.quip_signing.build_signed_extrinsic`
        plus :func:`~xqsa.quip_signing.submit_and_watch`. Inclusion alone is not
        success on Substrate, so a receipt that did not reach a block or carries
        a dispatch error is raised as a submission failure.

        Raises:
            QuipSubmissionError: if assembly/submission fails or the dispatch
                was rejected by the chain.
        """
        try:
            wire_bytes, ext_hash = self._quip_signing.build_signed_extrinsic(
                self._iface, self._signer, call_module, call_function, call_params
            )
            receipt = self._quip_signing.submit_and_watch(self._iface, wire_bytes, ext_hash, wait_for=wait_for)
        except self._quip_signing.QuipSigningError as exc:
            raise QuipSubmissionError(f"{call_module}.{call_function} could not be submitted: {exc}") from exc
        if not receipt.is_success:
            raise QuipSubmissionError(
                f"{call_module}.{call_function} failed on-chain (block {receipt.block_hash}): "
                f"{receipt.error or 'unknown dispatch error'}"
            )
        return receipt

    def _read_proposed_order_id(self, block_hash: str | None) -> int:
        """Extract the order id from the ``JobProposed`` event at ``block_hash``.

        Raises:
            QuipSubmissionError: if the block hash is missing or the block holds
                no ``JobProposed`` event with a readable ``order_id``.
        """
        if not block_hash:
            raise QuipSubmissionError("propose_job was included but returned no block hash; cannot read the order id")
        for record in self._iface.get_events(block_hash=block_hash) or []:
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
            f"propose_job was included in block {block_hash} but no {JOB_PROPOSED_EVENT} event "
            "with an order id was found"
        )

    # ------------------------------------------------------------------
    # Monitor / retrieve / decode
    # ------------------------------------------------------------------

    def _current_block(self) -> int:
        """Return the current best-block height.

        Reads the head header shallowly (``ignore_decoding_errors``) so an
        opaque digest from a runtime upgrade cannot block learning the height.
        """
        header = self._iface.get_block_header(ignore_decoding_errors=True)
        return _coerce_block_number(header["header"]["number"])

    def _fetch_order(self, order_id: int) -> Mapping[str, Any]:
        """Return the decoded ``JobOrders[order_id]`` mapping.

        Raises:
            QuipConnectionError: if no such order exists on-chain.
        """
        entry = self._iface.query(MEMPOOL_PALLET, JOB_ORDERS_STORAGE, [order_id])
        value = getattr(entry, "value", None)
        if value is None:
            raise QuipConnectionError(f"order {order_id} not found on-chain ({MEMPOOL_PALLET}.{JOB_ORDERS_STORAGE})")
        return value

    def _order_lifecycle(self, order: Mapping[str, Any], current_block: int) -> dict[str, Any]:
        """Derive the lifecycle view of an order at ``current_block``.

        Computes the block-height ``effective_expiry`` and finality from the
        order's own timing (so :meth:`query` works on orders proposed with
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

    def _await_finality(self, order_id: int) -> Mapping[str, Any]:
        """Poll the order until it is final by block height, returning it.

        The lazy lifecycle means an order can be past its expiry while still
        reported ``Opened``, so finality is decided by height
        (:func:`~xqsa.quip_codec.is_final`), never by waiting for ``OrderClosed``.

        Raises:
            QuipTimeoutError: if the order does not finalize within ``timeout``
                (carries ``order_id`` so the result is recoverable via
                :meth:`query`).
        """
        deadline = time.monotonic() + self._timeout
        while True:
            order = self._fetch_order(order_id)
            # A terminal chain status is final regardless of height, so skip the
            # extra head-height read on the terminal check (and on every poll of
            # an already-closed order via query()).
            if _status_str(order["status"]) in _TERMINAL_STATUSES:
                return order
            if self._order_lifecycle(order, self._current_block())["is_final"]:
                return order
            if time.monotonic() >= deadline:
                raise QuipTimeoutError(order_id)
            time.sleep(self._poll_interval)

    def _fetch_solutions(self, order_id: int) -> list[Mapping[str, Any]]:
        """Return every solver's submission for ``order_id`` (decoded ``JobSolution``s).

        ``OrderSolutions`` is a double map keyed ``(order_id, solver)``; querying
        by the first key iterates the per-solver entries.
        """
        submissions: list[Mapping[str, Any]] = []
        for _solver_key, value in self._iface.query_map(MEMPOOL_PALLET, ORDER_SOLUTIONS_STORAGE, [order_id]):
            decoded = value.value if hasattr(value, "value") else value
            if decoded is not None:
                submissions.append(decoded)
        return submissions

    def _collect_result(self, order_id: int, job: IsingJob, model: Any, *, elapsed: float) -> SolverResult:
        """Decode the best on-chain solution for ``order_id`` into a result.

        Selects the submission with the lowest chain ``best_energy_milli``,
        decodes every spin vector in it, and keeps the one with the best
        locally-recomputed (authoritative) energy on the original model. With no
        submissions, auto-reclaims the reserved reward and raises.

        Raises:
            QuipJobFailedError: if the order finalized with no usable solution
                (the reward is auto-reclaimed first; the message notes the
                refund outcome).
        """
        submissions = self._fetch_solutions(order_id)
        if not submissions:
            reclaimed = self._reclaim(order_id)
            refund = (
                "the reserved reward was reclaimed"
                if reclaimed
                else "the reward reclaim failed (see warnings); funds remain reserved -- "
                "retry SolverQuip.query(order_id, model) or reclaim manually"
            )
            raise QuipJobFailedError(order_id, f"order {order_id} finalized with no solutions; {refund}")

        chosen = min(submissions, key=lambda submission: int(_require(submission, "best_energy_milli", order_id)))
        chosen_solutions = _require(chosen, "solutions", order_id)
        best: tuple[int, XQMX, list[int]] | None = None
        for vector in chosen_solutions:
            spin_vector = [int(spin) for spin in vector]
            sample = decode_solution(job, spin_vector, model)
            energy = self._recompute_energy(model, sample)
            if best is None or energy < best[0]:
                best = (energy, sample, spin_vector)
        if best is None:
            raise QuipJobFailedError(order_id, f"order {order_id}'s winning submission carried no solution vectors")

        energy, best_sample, best_vector = best
        chain_best_milli = int(_require(chosen, "best_energy_milli", order_id))
        return SolverResult(
            sample=best_sample,
            energy=energy,
            timing=elapsed,
            metadata={
                "order_id": order_id,
                "solver": chosen.get("solver"),
                "best_energy_milli": chain_best_milli,
                # Canary: our milli recompute from the returned spins must equal
                # the chain's reported best, confirming index alignment + encoding.
                "energy_matches_chain": ising_energy_milli(job, best_vector) == chain_best_milli,
                "num_submissions": len(submissions),
                "num_solutions": len(chosen_solutions),
            },
        )

    def _reclaim(self, order_id: int) -> bool:
        """Best-effort ``reclaim_order`` to unreserve the reward; never raises.

        Valid only for the proposer once the order is Expired with zero accepted
        solutions (the pallet flips Opened->Expired by height as a side effect).
        Any failure (not proposer, not yet expired, RPC error) is logged and
        swallowed -- reclaim is a courtesy on the failure path, not a guarantee.
        """
        try:
            self._submit_extrinsic(MEMPOOL_PALLET, RECLAIM_ORDER_CALL, {"order_id": order_id})
        except Exception as exc:  # noqa: BLE001 -- reclaim must never mask the original failure.
            logger.warning("could not reclaim the reward for order %d: %s", order_id, exc)
            return False
        logger.info("reclaimed the reserved reward for order %d", order_id)
        return True

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Propose ``model`` as a job, await a solution, and decode the best one.

        Encodes the model onto the hardware topology, warns once about any
        out-of-spec coefficients, checks the account can cover the reward plus
        fees, proposes the job (reserving the reward on-chain), polls for
        finality by block height, then decodes the winning solution. If the
        order finalizes with no solutions, the reward is auto-reclaimed and
        :class:`QuipJobFailedError` is raised.

        Keyword args:
            topology: override topology hash for this solve.
            mapping: explicit variable -> node placement (else searched).

        Raises:
            QuipTopologyError: if the resolved topology is registered but not in
                the chain's mineable set (raised before the reward is reserved).
            QuipConnectionError: if the mineable-set read faults (transport/decode).
            QuipSubmissionError: if proposing the job fails.
            QuipTimeoutError: if the order does not finalize within ``timeout``
                (the order id is recoverable via :meth:`query`).
            QuipJobFailedError: if the order finalizes with no usable solution.
        """
        self._validate_model(model)
        topology = self._fetch_topology(kwargs.get("topology"))
        # Gate on mineability before committing funds: a registered-but-unmineable
        # hash would otherwise propose a job no miner matches, wasting a full
        # propose -> expiry -> reclaim round-trip. Kept here (not in
        # _fetch_topology) so query()'s order recovery stays independent of the
        # current mineable set.
        self._ensure_mineable(kwargs.get("topology"))
        job = model_to_ising(model, topology, mapping=kwargs.get("mapping"))
        self._maybe_warn_allowed_values(job)
        self._check_balance(self._reward + FEE_HEADROOM_PLANCK)

        start = time.perf_counter()
        order_id = self._propose_job(job)
        self._await_finality(order_id)
        elapsed = time.perf_counter() - start
        return self._collect_result(order_id, job, model, elapsed=elapsed)

    def query(
        self,
        order_id: int,
        model: XQMX,
        *,
        mapping: Mapping[int, int] | None = None,
        topology: str | None = None,
    ) -> SolverResult | None:
        """Recover the result of an already-proposed order.

        Returns ``None`` if the order is not yet final (poll again later). Once
        final, re-derives the deterministic placement from ``model`` and decodes
        the winning solution -- so an order proposed in a different process (or
        recovered after a :class:`QuipTimeoutError`) can still be read, provided
        the same ``model`` (and ``mapping``/``topology`` if non-default) is
        supplied. A final order with no solutions auto-reclaims and raises
        :class:`QuipJobFailedError`.
        """
        self._validate_model(model)
        order = self._fetch_order(order_id)
        if not self._order_lifecycle(order, self._current_block())["is_final"]:
            return None
        resolved_topology = self._fetch_topology(topology)
        job = model_to_ising(model, resolved_topology, mapping=mapping)
        return self._collect_result(order_id, job, model, elapsed=0.0)

    def status(self, order_id: int) -> dict[str, Any]:
        """Return a lightweight lifecycle snapshot of an order (no solution decode).

        One ``JobOrders`` read plus the chain head: status, key block heights,
        the computed ``effective_expiry``, and whether the order ``is_final``.
        """
        order = self._fetch_order(order_id)
        current_block = self._current_block()
        lifecycle = self._order_lifecycle(order, current_block)
        return {
            "order_id": order_id,
            "current_block": current_block,
            "solution_count": int(order.get("solution_count", 0) or 0),
            **lifecycle,
        }


def _require(mapping: Mapping[str, Any], key: str, order_id: int) -> Any:
    """Read ``key`` from a decoded pallet mapping, or raise a typed error.

    The pre-release ``QuantumComputeMempool`` field layout can change between
    releases; a missing field surfaces as a ``QuipJobFailedError`` (inside the
    ``QuipError`` hierarchy) with context, rather than a bare ``KeyError``.
    """
    if key not in mapping:
        raise QuipJobFailedError(
            order_id,
            f"solution field {key!r} is absent (present: {sorted(mapping)}); "
            "the pre-release pallet field layout may have changed",
        )
    return mapping[key]


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
