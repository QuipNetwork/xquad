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
    ``topology``           ``QUIP_TOPOLOGY``     topology hash (else DefaultTopology)
    ``faucet``             ``QUIP_FAUCET_URL``   faucet base URL (for_network: preset)
    ``autoconfirm``        ``QUIP_AUTOCONFIRM``  submit without asking (default true)
    ``autofund``           ``QUIP_AUTOFUND``     fund from the faucet without asking
    =====================  ====================================================

Provide exactly one of ``seed`` or ``keystore`` (a keystore is generated on
first use if absent). Every :meth:`SolverQuip.solve` first quotes the job
(:class:`JobQuote`: reward plus the chain-reported fee, against the balance)
and passes two consent gates. ``autoconfirm`` decides whether to submit at that
price; ``autofund`` decides whether an account short of it is topped up with
one faucet drip first. A shortfall larger than one drip is never requested; it
raises :class:`QuipSubmissionError`, since it is more often a mistyped reward.
Each gate is ``True`` (proceed, the default), ``False`` (ask on the terminal;
raises :class:`QuipCancelledError` when stdin is not one, which includes a
Jupyter kernel), or a callable taking the quote and returning a verdict. The
environment variables take ``1/true/yes/on`` or ``0/false/no/off``. A short
account with no faucet configured raises :class:`QuipSubmissionError`.

Named networks skip the URL: ``SolverQuip.for_network("aglais", keystore=...)``
builds a solver against a preset, ``aglais`` (the public testnet) or ``devnet``
(the localdev stack). The coordinates, and how often they move, are in
:mod:`xqsa.quip_networks`.

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
import sys
import time
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Self

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
    _require_h256,
    check_allowed_values,
    decode_solution,
    effective_expiry,
    is_final,
    ising_energy_milli,
    model_to_ising,
)
from xqsa.quip_faucet import DEFAULT_DRIP_PLANCK, fund_from_faucet
from xqsa.quip_networks import NETWORKS
from xqsa.solver import Solver, SolverResult

if TYPE_CHECKING:
    from collections.abc import Callable

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
# How long solve() waits for a faucet drip to show up in the free balance. The
# faucet answers only once the drip is on chain, so this covers a lagging RPC
# node, not block time.
FUND_WAIT_SECONDS = 30.0

# The ``propose_job`` fee a quote assumes when ``payment_queryInfo`` does not
# answer; ``JobQuote.fee_exact`` is then false. Measured fees on aglais at spec
# 117 run about 0.0022 AGLS and top out near 0.0075 AGLS over the benchmarked
# model range, so this is about 33 percent over that ceiling. It depends on the
# runtime's weights and fee config, which an upgrade can move silently; it is a
# bound for today's runtime, not a law.
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
# (queried in ``_fetch_topology``) is the topology set a hash must belong to.
# ``MineableTopologies`` is the chain's active mining set, which gates
# ``submit_proof`` and not the compute mempool; nothing on the solve path
# consults it, and ``_mineable_topologies`` is kept only as a chain reader.
MINEABLE_TOPOLOGIES_STORAGE = "MineableTopologies"


@dataclass(frozen=True)
class JobQuote:
    """What proposing one job costs, and whether the account can pay for it.

    Built by :meth:`SolverQuip.quote` (and by :meth:`SolverQuip.solve` before it
    proposes) from the signed ``propose_job`` extrinsic the solve submits. The
    fee is the chain's own ``payment_queryInfo`` answer when ``fee_exact`` is
    true, else the :data:`FEE_HEADROOM_PLANCK` fallback. The reward is reserved
    at proposal and returned if the job closes unanswered; the fee is burned
    either way. Amounts are in planck; ``str()`` renders them in token units.
    """

    network: str | None
    reward_planck: int
    fee_planck: int
    fee_exact: bool
    balance_planck: int
    token_symbol: str
    token_decimals: int

    @property
    def total_planck(self) -> int:
        """Reward plus fee: what the account must hold to propose the job."""
        return self.reward_planck + self.fee_planck

    @property
    def shortfall_planck(self) -> int:
        """How far the balance falls short of the total, or 0."""
        return max(0, self.total_planck - self.balance_planck)

    def __str__(self) -> str:
        """Render the quote as ``[quip]``-prefixed ASCII lines, decimal points aligned."""
        where = f"network {self.network}" if self.network else "custom endpoint"
        fee = "fee exact" if self.fee_exact else "fee estimated"
        rows = {
            "reward": self.reward_planck,
            "fee": self.fee_planck,
            "total": self.total_planck,
            "balance": self.balance_planck,
            "shortfall": self.shortfall_planck,
        }
        amounts = {label: _format_planck(value, self.token_decimals) for label, value in rows.items()}
        width = max(map(len, amounts.values()))
        lines = [f"[quip] job quote ({where}, {fee})"]
        lines += [f"[quip]   {label:<10} {amount:>{width}} {self.token_symbol}" for label, amount in amounts.items()]
        return "\n".join(lines)


class QuipCancelledError(QuipError):
    """Raised when a consent gate (``autoconfirm`` or ``autofund``) declines a job.

    Carries the :class:`JobQuote` that was declined as ``quote``, so a caller
    can tell "you said no" from "it failed" and still see the price.
    """

    def __init__(self, quote: JobQuote, message: str) -> None:
        self.quote = quote
        super().__init__(message)


class QuipConnectionError(QuipError):
    """Raised when the Quip node is unreachable or missing expected chain state."""


class QuipSubmissionError(QuipError):
    """Raised when an extrinsic cannot be submitted or is rejected by the chain."""


class QuipTopologyError(QuipError):
    """Retained for compatibility; nothing in this module raises it any more.

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
    signer from a seed or keystore. The topology is resolved from ``topology=``,
    then ``QUIP_TOPOLOGY``, then the chain's ``QuantumPow.DefaultTopology``. An
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
        faucet: str | None = None,
        autoconfirm: bool | Callable[[JobQuote], bool] | None = None,
        autofund: bool | Callable[[JobQuote], bool] | None = None,
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
        self._faucet = faucet or os.environ.get("QUIP_FAUCET_URL")
        # Set by for_network; a solver built from a raw url= names no network.
        self._network: str | None = None
        self._autoconfirm = _resolve_gate(autoconfirm, "autoconfirm", "QUIP_AUTOCONFIRM")
        self._autofund = _resolve_gate(autofund, "autofund", "QUIP_AUTOFUND")

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

    @classmethod
    def for_network(cls, name: str, /, **kwargs: Any) -> Self:
        """Build a solver against a named Quip network preset.

        Looks ``name`` up in :data:`xqsa.quip_networks.NETWORKS` and passes the
        preset's RPC endpoint to the constructor as ``url`` and its faucet as
        ``faucet``. A caller's own ``faucet=`` overrides the preset's; every
        other keyword goes to the constructor unchanged. A classmethod rather than a
        constructor argument so the preset enters as ``url=``, which beats
        ``QUIP_RPC_URL``: an exported localdev URL cannot silently redirect a
        solver the caller asked to point at a named network.

        Examples:
            Connects to the network, so it is not run as a doctest::

                solver = SolverQuip.for_network("aglais", keystore="~/.quip/keystore.json")

        Raises:
            ValueError: if ``name`` is not a known network, before any
                connection is attempted.
            TypeError: if ``url`` is also passed.
            Everything the constructor raises.
        """
        preset = NETWORKS.get(name)
        if preset is None:
            raise ValueError(f"unknown Quip network {name!r}; known networks: {', '.join(sorted(NETWORKS))}")
        logger.info("Quip network %s resolved to %s", name, preset.rpc)
        if not kwargs.get("faucet"):  # an unset faucet must not let QUIP_FAUCET_URL beat the preset.
            kwargs["faucet"] = preset.faucet
        solver = cls(url=preset.rpc, **kwargs)
        solver._network = name
        return solver

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
            ValueError: if the resolved id is not a 32-byte hex hash. The
                registration lookup below encodes the id as a SCALE storage key,
                which fails on a malformed value as a raw codec exception; the
                shape is checked first so a typo is named as a typo.
            QuipConnectionError: if the spec is not registered on-chain.
        """
        resolved = spec_id or self._chain_default_spec_id() or DEFAULT_ISING_SPEC_ID
        resolved = _require_h256(resolved, "spec_id")
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

        Prefers the explicit ``topology`` argument, then ``QUIP_TOPOLOGY``,
        then the chain's ``QuantumPow.DefaultTopology``. Resolved once at
        construction so :meth:`solve` and :meth:`query` on the same instance
        agree on the topology even if the chain default later changes.

        All three sources are deployment-local, deliberately, and none of them
        is a constant in this codebase. The same advantage2 graph hashes
        differently across deployments (the hash folds in each deployment's
        allowed-value specs), so no pinned constant could be correct
        everywhere, and a stale one would resolve to a topology no chain
        accepts. ``QUIP_TOPOLOGY`` is an operator's override for the chain
        they are pointed at -- it targets a registered non-default topology
        without threading a constructor argument through, which is what makes
        that path testable against a live chain. An unresolvable topology
        raises instead.

        Raises:
            ValueError: if no source yields a hash, or if the one that wins is
                not a 32-byte hex hash. The shape is checked here, at
                construction, rather than left to the first :meth:`solve`: the
                registration lookup encodes the hash as a SCALE storage key, so
                a typo would otherwise surface much later as a raw codec
                exception that no ``except Quip*Error`` clause catches, and
                nothing at default log level would name the source it came
                from. ``QUIP_TOPOLOGY`` being ambient makes that likelier here
                than for ``topology=``.
        """
        env_topology = os.environ.get("QUIP_TOPOLOGY")
        resolved = topology or env_topology or self._chain_default_topology()
        if not resolved:
            raise ValueError(
                "no topology hash available: pass topology=, set QUIP_TOPOLOGY, "
                "or seed QuantumPow.DefaultTopology on-chain."
            )
        # Name the winning source. QUIP_TOPOLOGY is ambient: an operator who
        # exports it for one run and then solves an unrelated model in the same
        # shell gets a PlacementError against the wrong graph, with nothing else
        # pointing at the variable.
        source = "topology=" if topology else ("QUIP_TOPOLOGY" if env_topology else "QuantumPow.DefaultTopology")
        hexed = _require_h256(resolved, source)
        logger.debug("topology %s resolved from %s", hexed, source)
        return hexed

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

        ``topology_hash`` defaults to the hash resolved at construction (see
        :meth:`_resolve_topology_hash`: ``topology=``, then ``QUIP_TOPOLOGY``,
        then the chain's ``QuantumPow.DefaultTopology``). The decoded
        ``TopologyMeta`` carries the graph and the allowed-value sets (captured
        for the educational warning).

        Raises:
            ValueError: if no topology hash is configured.
            QuipConnectionError: if the topology is not registered on-chain.
        """
        key = topology_hash or self._topology_hash
        if not key:
            raise ValueError(
                "no topology hash configured. Pass topology= or set QUIP_TOPOLOGY "
                "(the chain QuantumPow.DefaultTopology resolved empty)."
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

        A chain reader with nothing behind it: this set is the chain's active
        *mining* set, gating ``submit_proof`` and so block production, and it
        does not decide whether the compute mempool admits or answers an order.
        Nothing on the solve path calls this, and it is private, so there is no
        supported route to it. It is kept solely because the live test tier
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
        mirroring :meth:`_chain_default_topology`: a read fault, or entries that
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
        # runtime lacking the storage item entirely reports absence.
        return frozenset(hashes)

    def _free_balance(self) -> int:
        """Return the account's free balance in planck.

        ``propose_job`` reserves the full reward at proposal, so the free
        balance must cover the reward plus the fee; :class:`JobQuote` compares.

        Raises:
            QuipConnectionError: if the account entry or its ``data.free`` field
                cannot be read/decoded -- an anomalous read must not be reported
                as a zero balance.
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
        return free

    def _query_fee(self, wire: bytes) -> tuple[int, bool]:
        """Ask the chain what ``wire`` would pay in fees: ``(fee_planck, exact)``.

        ``payment_queryInfo`` dry-runs the fee of a signed extrinsic, so the
        answer tracks the live runtime rather than a formula copied from it.
        ``wire`` itself never leaves this process here; the node prices a
        copy from :func:`xqsa.quip_signing.disarm_extrinsic`. ``partialFee``
        arrives as a decimal string, ``0x`` hex, or an int depending on the
        node. Any failure falls back to :data:`FEE_HEADROOM_PLANCK` with
        ``exact`` false rather than blocking the solve on a price estimate.
        """
        try:
            # Price a copy whose signature fails: the node sees a frame it can
            # never dispatch, so neither quote() nor a declined gate leaves it
            # holding a proposable job. Same length, so the same fee.
            priced = self._quip_signing.disarm_extrinsic(wire)
            raw = self._iface.rpc_request("payment_queryInfo", ["0x" + priced.hex()])["result"]["partialFee"]
            if isinstance(raw, str):
                return (int(raw, 16) if raw.startswith("0x") else int(raw)), True
            return int(raw), True
        except Exception as exc:  # noqa: BLE001 -- a price estimate must not block the solve.
            logger.warning(
                "payment_queryInfo did not answer (%s); quoting the %d planck fallback fee", exc, FEE_HEADROOM_PLANCK
            )
            return FEE_HEADROOM_PLANCK, False

    def _token(self) -> tuple[str, int]:
        """Return the chain's token symbol and decimals, or ``("planck", 0)`` if unknown."""
        try:
            symbol, decimals = self._iface.token_symbol, self._iface.token_decimals
        except Exception:  # noqa: BLE001 -- display only; never block a solve on it.
            symbol = decimals = None
        if symbol is None or decimals is None:
            return "planck", 0
        return str(symbol), int(decimals)

    def _insufficient_error(
        self, quote: JobQuote, *, beyond_drip: bool = False, above_ceiling: bool = False
    ) -> QuipSubmissionError:
        """Build the error for an account that cannot cover ``quote``, naming where to fund it.

        ``beyond_drip`` marks a shortfall larger than one faucet drip, which
        ``autofund`` never requests: that is more often a mistyped reward than
        a real need. ``above_ceiling`` marks an account already holding more
        than one drip, which the faucet refuses to top up.
        """

        def amount(planck: int) -> str:
            return f"{_format_planck(planck, quote.token_decimals)} {quote.token_symbol}"

        if beyond_drip:
            remedy = (
                f"That is more than one faucet drip ({amount(DEFAULT_DRIP_PLANCK)}), which is all autofund "
                "requests; check reward=, or fund the account another way."
            )
        elif above_ceiling:
            remedy = (
                f"The faucet only tops up an account holding at most one drip ({amount(DEFAULT_DRIP_PLANCK)}); "
                "fund the account another way."
            )
        elif self._faucet:
            remedy = f"Fund it from the faucet at {self._faucet}."
        else:
            remedy = "Pass faucet=, set QUIP_FAUCET_URL, or build with SolverQuip.for_network(...) to name a faucet."
        return QuipSubmissionError(
            f"insufficient balance: account {_as_hex(self._signer.account_id)} holds "
            f"{amount(quote.balance_planck)} but the job needs {amount(quote.total_planck)} "
            f"(reward plus fee), {amount(quote.shortfall_planck)} short. {remedy}"
        )

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

    def _propose_call_params(self, job: IsingJob) -> dict:
        """Build the ``QuantumComputeMempool.propose_job`` call params for a placed job."""
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
        return call_params

    def _propose_job(self, wire: bytes, ext_hash: str) -> int:
        """Submit a built ``propose_job`` extrinsic and return the assigned order id.

        Takes the bytes :meth:`_prepare` built and quoted, so the job that was
        priced is the job that is submitted. The immortal era means those bytes
        stay valid however long a confirmation prompt takes. Reads the
        ``JobProposed`` event from the inclusion block for the ``order_id``
        (the call has no return value -- the id is only emitted as an event).

        Raises:
            QuipSubmissionError: if the extrinsic fails on-chain or no
                ``JobProposed`` event is found in the inclusion block.
        """
        receipt = self._submit_built(MEMPOOL_PALLET, PROPOSE_JOB_CALL, wire, ext_hash)
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

        :meth:`_build_extrinsic` then :meth:`_submit_built`.

        Raises:
            QuipSubmissionError: if assembly/submission fails or the dispatch
                was rejected by the chain.
        """
        wire, ext_hash = self._build_extrinsic(call_module, call_function, call_params)
        return self._submit_built(call_module, call_function, wire, ext_hash, wait_for=wait_for)

    def _build_extrinsic(self, call_module: str, call_function: str, call_params: dict) -> tuple[bytes, str]:
        """Sign an extrinsic via :func:`xqsa.quip_signing.build_signed_extrinsic`: ``(wire, hash)``.

        Raises:
            QuipSubmissionError: if assembly or signing fails.
        """
        try:
            return self._quip_signing.build_signed_extrinsic(
                self._iface, self._signer, call_module, call_function, call_params
            )
        except self._quip_signing.QuipSigningError as exc:
            raise QuipSubmissionError(f"{call_module}.{call_function} could not be submitted: {exc}") from exc

    def _submit_built(
        self,
        call_module: str,
        call_function: str,
        wire: bytes,
        ext_hash: str,
        wait_for: str = "inblock",
    ) -> Any:
        """Submit built extrinsic bytes via :func:`~xqsa.quip_signing.submit_and_watch`.

        Inclusion alone is not success on Substrate, so a receipt that did not
        reach a block or carries a dispatch error is raised as a submission
        failure.

        Raises:
            QuipSubmissionError: if submission fails or the dispatch was
                rejected by the chain.
        """
        try:
            receipt = self._quip_signing.submit_and_watch(self._iface, wire, ext_hash, wait_for=wait_for)
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

    def _prepare(self, model: XQMX, kwargs: Mapping[str, Any]) -> tuple[IsingJob, bytes, str, JobQuote]:
        """Place ``model``, build its ``propose_job`` extrinsic once, and quote it.

        Returns the placed job, the signed wire bytes and their hash, and the
        quote priced off exactly those bytes, so :meth:`solve` submits what it
        quoted without paying for a second build.
        """
        self._validate_model(model)
        topology = self._fetch_topology(kwargs.get("topology"))
        job = model_to_ising(model, topology, mapping=kwargs.get("mapping"))
        self._maybe_warn_allowed_values(job)
        wire, ext_hash = self._build_extrinsic(MEMPOOL_PALLET, PROPOSE_JOB_CALL, self._propose_call_params(job))
        fee, fee_exact = self._query_fee(wire)
        symbol, decimals = self._token()
        quote = JobQuote(
            network=self._network,
            reward_planck=self._reward,
            fee_planck=fee,
            fee_exact=fee_exact,
            balance_planck=self._free_balance(),
            token_symbol=symbol,
            token_decimals=decimals,
        )
        return job, wire, ext_hash, quote

    @staticmethod
    def _display(quote: JobQuote) -> None:
        """Log the quote at INFO, and print it to stderr when stderr is a terminal.

        Not ``warnings.warn``: that dedupes per call site, so the second solve
        in a loop would show nothing.
        """
        text = str(quote)
        for line in text.splitlines():
            logger.info("%s", line)
        if _isatty(sys.stderr):
            print(text, file=sys.stderr)

    @staticmethod
    def _pass_gate(gate: bool | Callable[[JobQuote], bool], quote: JobQuote, *, name: str, question: str) -> None:
        """Return if ``gate`` consents to ``quote``, else raise :class:`QuipCancelledError`.

        ``True`` consents; a callable decides from the quote; ``False`` asks on
        the terminal and never hangs: with no TTY on stdin it raises at once.
        """
        if gate is True:
            return
        if callable(gate):
            if gate(quote):
                return
            raise QuipCancelledError(quote, f"{name} declined: the {name} callable rejected the quote")
        if not _isatty(sys.stdin):
            raise QuipCancelledError(
                quote,
                f"{name}=False asks for confirmation but stdin is not a terminal; "
                f"pass {name}=True or {name}=lambda q: ... to decide in code",
            )
        # Ask on whichever output stream is the terminal, so a redirect of the
        # other cannot hide the question while the process waits. When stderr
        # is the terminal, _display already showed the quote there.
        out = sys.stderr if _isatty(sys.stderr) else sys.stdout
        if out is sys.stdout:
            print(str(quote), file=out)
        print(f"[quip] {question} [y/N] ", end="", file=out, flush=True)
        try:
            answer = input()
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            raise QuipCancelledError(quote, f"{name} declined: answered {answer.strip()!r} at the prompt")

    def _fund(self, quote: JobQuote) -> None:
        """Top the account up with one faucet drip and wait for it to cover ``quote``.

        The faucet answers once the drip is on chain, but the node this solver
        reads may lag, so the balance is re-read until it covers the quote.

        Raises:
            QuipFaucetError: if the faucet refuses or cannot be reached.
            QuipSubmissionError: if the balance still falls short after
                :data:`FUND_WAIT_SECONDS`.
        """
        # No amount: the faucet's default drip. solve() only gets here when the
        # shortfall fits in one, so a mistyped reward is never funded.
        fund_from_faucet("0x" + bytes(self._signer.account_id).hex(), url=self._faucet)
        deadline = time.monotonic() + FUND_WAIT_SECONDS
        while (balance := self._free_balance()) < quote.total_planck:
            if time.monotonic() >= deadline:
                raise self._insufficient_error(replace(quote, balance_planck=balance))
            time.sleep(self._poll_interval)

    def quote(self, model: XQMX, **kwargs: Any) -> JobQuote:
        """Price ``model`` as a job without proposing it.

        Places the model, builds and signs the ``propose_job`` extrinsic that
        :meth:`solve` would submit, asks the chain for its fee, and reads the
        account balance. Nothing is submitted and no nonce is consumed.

        Keyword args:
            topology: override topology hash for this quote.
            mapping: explicit variable -> node placement (else searched).

        Raises:
            QuipConnectionError: if a chain read faults while resolving the
                topology or the account balance.
            QuipSubmissionError: if the extrinsic cannot be built.
        """
        return self._prepare(model, kwargs)[3]

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Propose ``model`` as a job, await a solution, and decode the best one.

        Encodes the model onto the hardware topology, warns once about any
        out-of-spec coefficients, builds the ``propose_job`` extrinsic, quotes
        it (see :meth:`quote`) and shows the quote, passes the consent gates,
        proposes the job (reserving the reward on-chain), polls for finality
        by block height, then decodes the winning solution. If the order
        finalizes with no solutions, the reward is auto-reclaimed and
        :class:`QuipJobFailedError` is raised.

        The ``autoconfirm`` gate comes first, so the price is accepted before
        funding is considered. Only if the account is short does the
        ``autofund`` gate follow, then a faucet drip and a balance re-read.
        A drip is not returned if the job fails after it.

        Keyword args:
            topology: override topology hash for this solve.
            mapping: explicit variable -> node placement (else searched).

        Raises:
            QuipConnectionError: if a chain read faults (transport/decode) while
                resolving the topology, the order, or the account balance.
            QuipSubmissionError: if the account cannot cover the quote and no
                faucet is configured, the shortfall exceeds one faucet drip, the
                balance already exceeds the faucet's one-drip ceiling, or the
                drip does not land; or if proposing the job fails. The first
                three are raised before either gate is asked.
            QuipCancelledError: if the ``autoconfirm`` or ``autofund`` gate
                declines (carries the quote).
            QuipFaucetError: if the faucet refuses or cannot be reached.
            QuipTimeoutError: if the order does not finalize within ``timeout``
                (the order id is recoverable via :meth:`query`).
            QuipJobFailedError: if the order finalizes with no usable solution.
        """
        job, wire, ext_hash, quote = self._prepare(model, kwargs)
        self._display(quote)
        # A shortfall no drip can cover fails whatever the gates say, so raise
        # it before asking anyone to confirm a job that cannot go out.
        if quote.shortfall_planck > DEFAULT_DRIP_PLANCK:
            raise self._insufficient_error(quote, beyond_drip=True)
        if quote.shortfall_planck and quote.balance_planck > DEFAULT_DRIP_PLANCK:
            raise self._insufficient_error(quote, above_ceiling=True)
        if quote.shortfall_planck and not self._faucet:
            raise self._insufficient_error(quote)
        total = f"{_format_planck(quote.total_planck, quote.token_decimals)} {quote.token_symbol}"
        self._pass_gate(self._autoconfirm, quote, name="autoconfirm", question=f"Submit this job for {total}?")
        if quote.shortfall_planck:
            self._pass_gate(
                self._autofund,
                quote,
                name="autofund",
                question=f"Fund {_as_hex(self._signer.account_id)} from {self._faucet}?",
            )
            # The drip does not touch this account's nonce, so the extrinsic
            # built above stays valid.
            self._fund(quote)

        start = time.perf_counter()
        order_id = self._propose_job(wire, ext_hash)
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


_TRUE_FLAGS = frozenset({"1", "true", "yes", "on"})
_FALSE_FLAGS = frozenset({"0", "false", "no", "off"})


def _isatty(stream: object) -> bool:
    """Whether ``stream`` is a terminal; ``None`` (pythonw, some daemons) is not."""
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _env_flag(name: str) -> bool | None:
    """Read a boolean environment variable; ``None`` when unset or empty.

    Raises:
        ValueError: if the variable holds anything but a recognised spelling.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in _TRUE_FLAGS:
        return True
    if raw in _FALSE_FLAGS:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean; use one of 1/true/yes/on or 0/false/no/off")


def _resolve_gate(gate: object, name: str, env: str) -> bool | Callable[[JobQuote], bool]:
    """Resolve a consent gate: the argument, then ``env``, then ``True``.

    Raises:
        TypeError: if the argument is neither a bool, a callable, nor ``None``.
        ValueError: if ``env`` is set to an unrecognised spelling.
    """
    if gate is None:
        flag = _env_flag(env)
        return True if flag is None else flag
    if isinstance(gate, bool) or callable(gate):
        return gate  # type: ignore[return-value]
    raise TypeError(f"{name} must be a bool or a callable taking a JobQuote, not {type(gate).__name__}")


def _format_planck(planck: int, decimals: int) -> str:
    """Render ``planck`` in token units with exactly ``decimals`` fractional digits."""
    if decimals == 0:
        return str(planck)
    unit = 10**decimals
    return f"{planck // unit}.{planck % unit:0{decimals}d}"


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
