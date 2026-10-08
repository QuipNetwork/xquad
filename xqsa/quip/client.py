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

"""SolverQuip: the Quip Network mempool backend. See :mod:`xqsa.quip` for configuration."""

from __future__ import annotations

import logging
import os
import sys
import time
import warnings
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

from xqsa.quip import chain
from xqsa.quip import metadata as quip_metadata
from xqsa.quip.chain import (  # noqa: F401 -- FEE_HEADROOM_PLANCK and the pallet names stay importable from here.
    FEE_HEADROOM_PLANCK,
    JOB_ORDERS_STORAGE,
    JOB_PROPOSED_EVENT,
    MEMPOOL_PALLET,
    MINEABLE_TOPOLOGIES_STORAGE,
    ORDER_SOLUTIONS_STORAGE,
    PROPOSE_JOB_CALL,
    RECLAIM_ORDER_CALL,
    _status_str,
)
from xqsa.quip.codec import (
    DEFAULT_ISING_SPEC_ID,
    QUIP_COEFFICIENTS_DOC_URL,
    Topology,
    _as_hex,
    _require_h256,
    check_allowed_values,
    decode_solution,
    ising_energy_milli,
    model_to_ising,
    native_placement,
)
from xqsa.quip.errors import (  # noqa: F401 -- the Quip* errors stay importable from here.
    EncodingError,
    QuipCancelledError,
    QuipConnectionError,
    QuipError,
    QuipJobFailedError,
    QuipMetadataError,
    QuipOrderOptionError,
    QuipSubmissionError,
    QuipTimeoutError,
    QuipTopologyError,
    QuipUnconfirmedError,
)
from xqsa.quip.faucet import DEFAULT_DRIP_PLANCK, fund_from_faucet
from xqsa.quip.networks import NETWORKS
from xqsa.quip.order import JobOrder, _OrderOptions, _Sent, _SignedCall, check_client_defaults, merge_options
from xqsa.quip.quote import JobQuote, _format_planck
from xqsa.quip.receipt import JobOrderReceipt
from xqsa.solver import Solver, SolverResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from xqsa.quip.codec import IsingJob
    from xqvm_py.xqmx import XQMX

logger = logging.getLogger("xqsa.quip")

# Default signed-extension config. ``deadline_blocks``/``block_wait`` stay well
# under the pallet bounds (<= 1000 / <= 100); the lifecycle math lives in
# ``xqsa.quip.codec`` (``effective_expiry``/``is_final``).
DEFAULT_DEADLINE_BLOCKS = 100
DEFAULT_BLOCK_WAIT = 10
DEFAULT_POLL_INTERVAL = 6.0
DEFAULT_TIMEOUT = 600.0
# How long solve() waits for a faucet drip to show up in the free balance. The
# faucet answers only once the drip is on chain, so this covers a lagging RPC
# node, not block time.
FUND_WAIT_SECONDS = 30.0

# The most order ids list_orders() returns, newest first. Fixed on purpose;
# see the warning it raises past it.
LIST_ORDERS_LIMIT = 32

# Keystore used when no seed or keystore is configured; created on first use.
DEFAULT_KEYSTORE = "~/.quip/keystore.json"


# The reserved ``topology`` value that submits a model over its own coupling
# graph instead of placing it onto a registered chain topology.
NATIVE_TOPOLOGY = "native"


def _is_native(topology: str | None) -> bool:
    """Return whether a ``topology`` value selects native mode (case and whitespace ignored)."""
    return (topology or "").strip().lower() == NATIVE_TOPOLOGY


class SolverQuip(Solver):
    """Network-mempool backend: solve XQMX Ising models on the Quip Network.

    Constructing the solver connects to the node, resolves the Ising job spec,
    default reward, and target topology from chain state, and builds the hybrid
    signer from a seed or keystore. The topology is resolved from ``topology=``,
    then ``QUIP_TOPOLOGY``, then the chain's ``QuantumPow.DefaultTopology``. An
    unresolvable topology raises rather than falling through to a hash no chain
    would accept. ``topology="native"`` skips chain lookup entirely and builds
    the job's topology from the model's own coupling graph, so placement never
    fails, at the cost of excluding miners that cannot embed an arbitrary graph
    (for example QPU-backed ones). See the module docstring for configuration
    and installation.

    Raises:
        ImportError: if the ``[quip]`` extra is not installed
            (``pip install xqsa[quip]`` -- provides ``substrate-interface`` and
            the ``quip_signer`` signing extension).
        ValueError: if no RPC URL is configured, or ``mode``, ``resolution``
            or ``delivery`` names a value no order can use yet (checked before
            connecting).
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
            import quip_signer  # noqa: F401 -- presence check; used via xqsa.quip.signing.
        except ImportError as exc:
            raise ImportError(
                "the quip_signer signing extension is not installed. Install it with: pip install xqsa[quip]"
            ) from exc
        from xqsa.quip import signing as quip_signing

        resolved_url = url or os.environ.get("QUIP_RPC_URL")
        if not resolved_url:
            raise ValueError("A Quip RPC URL is required. Pass url= or set QUIP_RPC_URL.")
        check_client_defaults(mode, resolution, delivery)  # before any network I/O.

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
        self._warned_quantization = False
        self._quip_signing = quip_signing
        # The env faucet belongs to the env RPC: an explicit url= may name another
        # chain, so it never draws from whatever faucet happens to be exported.
        self._faucet = faucet or (None if url else os.environ.get("QUIP_FAUCET_URL"))
        # Set by for_network; a solver built from a raw url= names no network.
        self._network: str | None = None
        self._autoconfirm = _resolve_gate(autoconfirm, "autoconfirm", "QUIP_AUTOCONFIRM")
        self._autofund = _resolve_gate(autofund, "autofund", "QUIP_AUTOFUND")

        try:
            # Not substrateinterface.SubstrateInterface directly: Quip runtimes
            # serve metadata V16, which scalecodec cannot decode. The subclass
            # pulls V14 through the versioned runtime API instead, and confines
            # that to instances we build. See xqsa.quip.metadata.
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
        self._genesis_hash = chain.genesis_hash(self._iface)

        self._spec_id = self._resolve_spec_id(spec_id)
        self._limits = chain.read_limits(self._iface)
        self._reward = self._resolve_reward(reward)
        self._topology_hash = self._resolve_topology_hash(topology)

    @classmethod
    def for_network(cls, name: str, /, **kwargs: Any) -> Self:
        """Build a solver against a named Quip network preset.

        Looks ``name`` up in :data:`xqsa.quip.networks.NETWORKS` and passes the
        preset's RPC endpoint to the constructor as ``url`` and its faucet as
        ``faucet``. A caller's own ``faucet=`` overrides the preset's; every
        other keyword goes to the constructor unchanged. A classmethod rather than a
        constructor argument so the preset enters as ``url=``, which beats
        ``QUIP_RPC_URL``: an exported localdev URL cannot silently redirect a
        solver the caller asked to point at a named network.

        Examples:
            Connects to the network, so it is not run as a doctest::

                solver = SolverQuip.for_network("aglais")

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

        Resolution: ``seed``, ``QUIP_SIGNER_SEED``, ``keystore``, ``QUIP_KEYSTORE``,
        then :data:`DEFAULT_KEYSTORE`. Falling back to the default warns when the
        file is new, since that is a fresh, empty account a caller may not have
        meant to use, and logs its path at INFO when an existing file is loaded.
        """
        resolved_seed = seed or os.environ.get("QUIP_SIGNER_SEED")
        if resolved_seed:
            return quip_signing.signer_from_seed(resolved_seed)
        resolved_keystore = keystore or os.environ.get("QUIP_KEYSTORE")
        if resolved_keystore:
            return quip_signing.load_or_generate_keystore(resolved_keystore).signer
        default_path = Path(DEFAULT_KEYSTORE).expanduser()
        existed = default_path.exists()
        ks = quip_signing.load_or_generate_keystore(default_path)
        if existed:
            logger.info("no signer configured; using the keystore at %s", ks.path)
        else:
            logger.warning(
                "no signer configured; generated a new keystore at %s. Its account starts empty; "
                "keep the file, it is the account",
                ks.path,
            )
        return ks.signer

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
        value = self._read_constant("DefaultIsingSpecId")
        return _as_hex(value) if value is not None else None

    def _read_constant(self, name: str) -> Any | None:
        """See :func:`xqsa.quip.chain.read_constant`."""
        return chain.read_constant(self._iface, name)

    def _resolve_topology_hash(self, topology: str | None) -> str:
        """Resolve the topology hash to target.

        Prefers the explicit ``topology`` argument, then ``QUIP_TOPOLOGY``,
        then the chain's ``QuantumPow.DefaultTopology``. Resolved once at
        construction so :meth:`solve` and :meth:`query` on the same instance
        agree on the topology even if the chain default later changes.
        ``"native"``, from either source, short-circuits and returns
        :data:`NATIVE_TOPOLOGY` before the chain default is read and before
        the hash shape check below.

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
        # Native mode names no chain topology, so it wins before the chain
        # default is read and before the hash shape check.
        if _is_native(topology or env_topology):
            logger.debug("native topology resolved from %s", "topology=" if topology else "QUIP_TOPOLOGY")
            return NATIVE_TOPOLOGY
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
        """See :func:`xqsa.quip.chain.default_topology`."""
        return chain.default_topology(self._iface)

    def _resolve_reward(self, reward: int | None) -> int:
        """Resolve the proposal reward (planck): arg, then ``QUIP_REWARD``, then MinReward.

        The reward spends funds, so a chain without ``MinReward`` is an error
        rather than a silent default. ``MinReward`` comes from the limits read
        at construction, not a second constant read.

        Raises:
            QuipConnectionError: if the runtime does not define ``MinReward``.
        """
        if reward is not None:
            return int(reward)
        env_reward = os.environ.get("QUIP_REWARD")
        if env_reward:
            return int(env_reward)
        if self._limits.min_reward is None:
            raise QuipConnectionError(
                f"the chain defines no {MEMPOOL_PALLET}.MinReward constant; pass reward= or set QUIP_REWARD"
            )
        return self._limits.min_reward

    # ------------------------------------------------------------------
    # Chain reads
    # ------------------------------------------------------------------

    def _fetch_topology(self, topology_hash: str | None = None) -> Topology:
        """Fetch and cache the hardware topology from ``QuantumPow.RegisteredTopologies``.

        ``topology_hash`` defaults to the hash resolved at construction (see
        :meth:`_resolve_topology_hash`: ``topology=``, then ``QUIP_TOPOLOGY``,
        then the chain's ``QuantumPow.DefaultTopology``). The decoded
        ``TopologyMeta`` carries the graph and the allowed-value sets (captured
        for the educational warning). Never called in native mode: :meth:`_job_for`
        builds the topology from the model's own coupling graph instead of
        reading a registered one.

        Raises:
            ValueError: if no topology hash is configured, or the key is native.
            QuipConnectionError: if the topology is not registered on-chain.
        """
        key = topology_hash or self._topology_hash
        if _is_native(key):
            raise ValueError("native mode has no registered topology to fetch")
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
        """See :func:`xqsa.quip.chain.mineable_topologies`."""
        return chain.mineable_topologies(self._iface)

    def _free_balance(self) -> int:
        """See :func:`xqsa.quip.chain.free_balance`."""
        return chain.free_balance(self._iface, self._signer.account_id)

    def _query_fee(self, wire: bytes) -> tuple[int, bool]:
        """See :func:`xqsa.quip.chain.query_fee`."""
        return chain.query_fee(self._iface, self._quip_signing, wire)

    def _token(self) -> tuple[str, int]:
        """See :func:`xqsa.quip.chain.token`."""
        return chain.token(self._iface)

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
    # Coefficient warnings
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

    def _maybe_warn_quantization(self, job: IsingJob) -> None:
        """Emit a one-time warning if encoding rounded any coefficient to the milli grid.

        Rounding never blocks submission: it perturbs only the landscape the
        miners search, and every returned sample is scored on the original
        model. The warning fires at most once per solver instance; the error is
        also reported per result as ``metadata["quantization_error"]``.
        """
        if self._warned_quantization or job.quantization_error == 0.0:
            return
        self._warned_quantization = True
        warnings.warn(
            f"coefficients were rounded to the chain's milli grid (1/1000); the largest rounding error is "
            f"{job.quantization_error:.6g}. Miners search the rounded model, and returned samples are scored "
            f"on the original one. See {QUIP_COEFFICIENTS_DOC_URL}.",
            stacklevel=2,
        )

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_bounded(value: Any) -> Any:
        """See :func:`xqsa.quip.chain.wrap_bounded`."""
        return chain.wrap_bounded(value)

    def _propose_call_params(self, job: IsingJob, options: _OrderOptions) -> dict:
        """Build the ``QuantumComputeMempool.propose_job`` call params for a placed job and its options."""
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
            "reward": options.reward,
            # Unit enum variants encode from their bare variant name (the same
            # form they decode to -- see the miner's _decode_result_delivery).
            # Data-carrying variants (Bid / TopN* / Callback*) are out of v1.
            "mode": options.mode,
            "resolution": options.resolution,
            "deadline_blocks": options.deadline_blocks,
            "block_wait": options.block_wait,
            "delivery": options.delivery,
        }
        return call_params

    def _sign(self, call_params: dict, nonce: int | None = None) -> _SignedCall:
        """Sign ``call_params`` as a ``propose_job`` extrinsic with ``nonce``, else the current one.

        Raises:
            QuipSubmissionError: if assembly or signing fails.
            QuipConnectionError: if the nonce cannot be read.
        """
        if nonce is None:
            nonce = self._account_nonce()
        wire, ext_hash = self._build_extrinsic(MEMPOOL_PALLET, PROPOSE_JOB_CALL, call_params, nonce=nonce)
        return _SignedCall(wire=wire, ext_hash=ext_hash, nonce=nonce)

    def _propose(self, signed: _SignedCall, call_params: dict, reward: int) -> _Sent:
        """Send ``signed`` if its nonce is still current, else re-sign once, and report the outcome.

        The nonce is read just before sending. If it moved since ``signed``
        was signed, another transaction from this account used it, so the
        call is signed once more with the new nonce and checked again; if it
        moves a second time nothing is sent. This narrows the race to the gap
        between the last read and the send, but cannot close it: a node
        rejects a transaction that reuses a nonce before charging a fee, and
        that surfaces as a :class:`QuipSubmissionError` from the send.

        Raises:
            QuipSubmissionError: if the nonce moves twice, signing fails, or the
                extrinsic certainly did not land. Nothing was placed.
            QuipUnconfirmedError: if it was sent but its outcome or order id is
                unknown (see :meth:`_propose_job`).
            QuipConnectionError: if the nonce cannot be read.
        """
        current = self._account_nonce()
        if current != signed.nonce:
            logger.info("account nonce moved from %d to %d since signing; signing again", signed.nonce, current)
            signed = self._sign(call_params, nonce=current)
            if self._account_nonce() != signed.nonce:
                raise QuipSubmissionError(
                    "account nonce moved twice while submitting; another client is sending from this account. "
                    "Nothing was sent."
                )
        return self._propose_job(signed.wire, signed.ext_hash, reward)

    def _propose_job(self, wire: bytes, ext_hash: str, reward: int) -> _Sent:
        """Send a built ``propose_job`` extrinsic and report what it placed.

        Returns a :class:`_Sent` with the order id when the dispatch succeeded,
        or with the chain's error when it failed (the fee was paid and no
        order exists). The id is read from the ``JobProposed`` event in the
        inclusion block (the call has no return value).

        Raises:
            QuipSubmissionError: if the extrinsic certainly did not land.
            QuipUnconfirmedError: if it was sent but may or may not have landed,
                its dispatch result could not be read, or its order id could
                not be read from a successful dispatch.
        """
        receipt = chain.send_extrinsic(
            self._iface, self._quip_signing, MEMPOOL_PALLET, PROPOSE_JOB_CALL, wire, ext_hash
        )
        block_hash = receipt.block_hash
        if receipt.is_unverified:
            raise chain.unconfirmed_error(ext_hash, block_hash, receipt.error)
        included_block = self._included_block(block_hash)
        if receipt.error is not None:
            logger.warning("propose_job %s failed on chain in block %s: %s", ext_hash, block_hash, receipt.error)
            return _Sent(ext_hash=ext_hash, block_hash=block_hash, included_block=included_block, error=receipt.error)
        try:
            order_id = self._read_proposed_order_id(block_hash)
        except Exception as exc:  # noqa: BLE001 -- the order is on chain; never let it read as unsent.
            raise chain.unconfirmed_error(ext_hash, block_hash, f"the order id could not be read: {exc}") from exc
        logger.info(
            "proposed Quip job: order_id=%d spec_id=%s reward=%d planck (block %s)",
            order_id,
            self._spec_id,
            reward,
            block_hash,
        )
        return _Sent(ext_hash=ext_hash, block_hash=block_hash, included_block=included_block, order_id=order_id)

    def _included_block(self, block_hash: str | None) -> int | None:
        """Return the height of the inclusion block, or ``None`` if it cannot be read.

        Display only: the order is already proposed, so a failed read must not
        lose its id.
        """
        if not block_hash:
            return None
        try:
            return chain.block_number(self._iface, block_hash)
        except Exception as exc:  # noqa: BLE001 -- the order is on chain; never lose its id over a height.
            logger.warning("could not read the height of inclusion block %s: %s", block_hash, exc)
            return None

    def _account_nonce(self) -> int:
        """See :func:`xqsa.quip.chain.account_nonce`."""
        return chain.account_nonce(self._iface, self._signer.account_id)

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

    def _build_extrinsic(
        self, call_module: str, call_function: str, call_params: dict, nonce: int | None = None
    ) -> tuple[bytes, str]:
        """See :func:`xqsa.quip.chain.build_extrinsic`."""
        return chain.build_extrinsic(
            self._iface, self._quip_signing, self._signer, call_module, call_function, call_params, nonce=nonce
        )

    def _submit_built(
        self,
        call_module: str,
        call_function: str,
        wire: bytes,
        ext_hash: str,
        wait_for: str = "inblock",
    ) -> Any:
        """See :func:`xqsa.quip.chain.submit_built`."""
        return chain.submit_built(
            self._iface, self._quip_signing, call_module, call_function, wire, ext_hash, wait_for=wait_for
        )

    def _read_proposed_order_id(self, block_hash: str | None) -> int:
        """See :func:`xqsa.quip.chain.read_proposed_order_id`."""
        return chain.read_proposed_order_id(self._iface, block_hash)

    # ------------------------------------------------------------------
    # Monitor / retrieve / decode
    # ------------------------------------------------------------------

    def _current_block(self) -> int:
        """See :func:`xqsa.quip.chain.current_block`."""
        return chain.current_block(self._iface)

    def _fetch_order(self, order_id: int) -> Mapping[str, Any]:
        """See :func:`xqsa.quip.chain.fetch_order`."""
        return chain.fetch_order(self._iface, order_id)

    def _order_lifecycle(self, order: Mapping[str, Any], current_block: int) -> dict[str, Any]:
        """See :func:`xqsa.quip.chain.order_lifecycle`."""
        return chain.order_lifecycle(order, current_block)

    def _fetch_solutions(self, order_id: int) -> list[Mapping[str, Any]]:
        """See :func:`xqsa.quip.chain.fetch_solutions`."""
        return chain.fetch_solutions(self._iface, order_id)

    def _collect_result(self, receipt: JobOrderReceipt, job: IsingJob, model: Any, *, elapsed: float) -> SolverResult:
        """Decode the best on-chain solution for the receipt's order into a result.

        Selects the submission with the lowest chain ``best_energy_milli``,
        decodes every spin vector in it, and keeps the one with the best
        locally-recomputed (authoritative) energy on the original model. On a
        rounded job (``quantization_error > 0``) the submission ranking uses the
        chain's energies on the rounded model. With no submissions, auto-reclaims
        the reserved reward and raises.

        Raises:
            QuipJobFailedError: if the order finalized with no usable solution
                (the reward is auto-reclaimed first; the message notes the
                refund outcome).
        """
        order_id = receipt.order_id
        submissions = self._fetch_solutions(order_id)
        if not submissions:
            refund = (
                "the reserved reward was reclaimed"
                if self._try_reclaim(receipt)
                else "the reward reclaim failed (see warnings); funds remain reserved -- "
                f"retry with SolverQuip.get_receipt({order_id}).reclaim()"
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
                "quantization_error": job.quantization_error,
            },
        )

    @staticmethod
    def _try_reclaim(receipt: JobOrderReceipt) -> bool:
        """Best-effort :meth:`JobOrderReceipt.reclaim <xqsa.quip.JobOrderReceipt.reclaim>`; never raises.

        The failure path of :meth:`solve` and :meth:`query` calls it, so any
        failure is logged and swallowed: the refund must never hide the
        error that led to it.
        """
        try:
            refunded = receipt.reclaim()
        except Exception as exc:  # noqa: BLE001 -- reclaim must never mask the original failure.
            logger.warning(
                "could not reclaim the reward for order %d: %s; retry with get_receipt(%d).reclaim()",
                receipt.order_id,
                exc,
                receipt.order_id,
            )
            return False
        logger.info("reclaimed %d planck reserved for order %d", refunded, receipt.order_id)
        return True

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def _native_for(self, topology: str | None, mapping: Mapping[int, int] | None) -> bool:
        """Return whether a call runs in native mode: ``topology``, else the resolved one.

        Raises:
            ValueError: if ``mapping`` is given in native mode, which has no
                hardware graph for it to target.
        """
        native = _is_native(topology or self._topology_hash)
        if native and mapping is not None:
            raise ValueError(
                f"mapping= cannot be combined with topology={NATIVE_TOPOLOGY!r}: native mode "
                "builds the topology from the model, so there is no hardware graph to map onto."
            )
        return native

    def _check_order_size(self, topology: Topology) -> None:
        """Raise if ``topology`` exceeds the mempool's ``MaxNodes`` / ``MaxEdges``.

        A registered topology fits by construction, but a native one is as large
        as the model. Checked before quoting, so an oversized order fails here
        with the limit named rather than in SCALE encoding or on chain. A bound
        the runtime does not expose is not checked.

        Raises:
            EncodingError: if the node or edge count exceeds its bound.
            QuipConnectionError: if reading a bound fails (as opposed to the
                bound simply not being defined on this runtime).
            QuipMetadataError: if the node serves runtime metadata too old to
                decode.
        """
        bounds = (("MaxNodes", "nodes", topology.num_nodes), ("MaxEdges", "edges", topology.num_edges))
        for name, noun, count in bounds:
            limit = self._read_constant(name)
            if limit is not None and count > int(limit):
                raise EncodingError(
                    f"native order has {count} {noun}, over the mempool's {MEMPOOL_PALLET}.{name} of {int(limit)}"
                )

    def _job_for(self, model: XQMX, topology: str | None, mapping: Mapping[int, int] | None) -> IsingJob:
        """Encode ``model`` for the effective topology: ``topology``, else the resolved one.

        In native mode the topology is the model's own coupling graph, so there
        is nothing to fetch and nothing to search, and an explicit ``mapping``
        has no graph to target. Otherwise the registered topology is fetched and
        the model placed onto it. :meth:`_place` and :meth:`query` share this,
        so a recovered order re-derives the job :meth:`solve` submitted.

        Raises:
            ValueError: if ``mapping`` is given in native mode.
            EncodingError: if a native order exceeds the mempool's size bounds.
        """
        if self._native_for(topology, mapping):
            native_topology, native_mapping = native_placement(model)
            self._check_order_size(native_topology)
            return model_to_ising(model, native_topology, mapping=native_mapping)
        return model_to_ising(model, self._fetch_topology(topology), mapping=mapping)

    def _default_options(self) -> _OrderOptions:
        """Return the client defaults every order starts from."""
        return _OrderOptions(
            reward=self._reward,
            deadline_blocks=self._deadline_blocks,
            block_wait=self._block_wait,
            topology=self._topology_hash,
            mapping=None,
            mode=self._mode,
            resolution=self._resolution,
            delivery=self._delivery,
        )

    def _settle_topology(self, options: _OrderOptions) -> _OrderOptions:
        """Normalize ``options.topology``: ``None`` is the client's, ``"native"`` any case, else a checked hash.

        Raises:
            ValueError: if the topology is not a 32-byte hex hash, or
                ``mapping`` is combined with a native topology.
        """
        topology = options.topology or self._topology_hash
        topology = NATIVE_TOPOLOGY if _is_native(topology) else _require_h256(topology, "topology")
        self._native_for(topology, options.mapping)
        return replace(options, topology=topology)

    def _place(self, model: XQMX, options: _OrderOptions) -> IsingJob:
        """Place ``model`` for ``options`` and emit the one-time coefficient warnings."""
        job = self._job_for(model, options.topology, options.mapping)
        self._maybe_warn_allowed_values(job)
        self._maybe_warn_quantization(job)
        return job

    def _create_order(self, model: XQMX, options: Mapping[str, Any], *, strict: bool) -> JobOrder:
        """Build a draft from the client defaults and ``options``.

        ``strict=False`` is the deprecated ``solve``/``quote`` path: unknown
        options and raw chain dicts warn instead of raising. QUI-1608 removes it.
        """
        self._validate_model(model)
        merged = merge_options(self._default_options(), options, self._limits, strict=strict, stacklevel=4)
        merged = self._settle_topology(merged)
        return JobOrder(self, model, merged, self._place(model, merged), self._genesis_hash)

    def _quote_wire(self, wire: bytes, job: IsingJob, options: _OrderOptions) -> JobQuote:
        """Price the signed ``wire`` without sending it, reading the balance and head block now.

        :func:`~xqsa.quip.chain.query_fee` disarms a copy of ``wire`` before it
        leaves this process, so the node never holds a sendable transaction.
        """
        fee, fee_exact = self._query_fee(wire)
        symbol, decimals = self._token()
        return JobQuote(
            network=self._network,
            reward_planck=options.reward,
            fee_planck=fee,
            fee_exact=fee_exact,
            balance_planck=self._free_balance(),
            token_symbol=symbol,
            token_decimals=decimals,
            account=chain.ss58_address(self._iface, self._signer.account_id),
            quoted_at_block=self._current_block(),
            num_variables=len(job.mapping),
            num_spins=job.topology.num_nodes,
            num_couplings=job.topology.num_edges,
            placement=options.topology,
        )

    def create_order(self, model: XQMX, **options: Any) -> JobOrder:
        """Return a draft :class:`~xqsa.quip.JobOrder` for ``model``.

        The order inherits this client's defaults; ``options`` override them
        for this order. The model is validated and placed, and every option is
        checked against the chain limits read at construction, before anything
        is signed.

        Keyword args:
            reward: planck reserved at proposal, at least ``MinReward``.
            deadline_blocks: 10 up to ``MaxDeadlineBlocks``; under 100 warns.
            block_wait: 0 up to ``MaxBlockWait``; not below the deadline warns.
            topology: a registered topology hash, or ``"native"``.
            mapping: explicit variable -> node placement (else searched); not
                allowed with ``"native"``.

        Examples:
            Connects to the network, so it is not run as a doctest::

                order = solver.create_order(model, deadline_blocks=200)
                print(order.quote())
                order.submit()

        Raises:
            TypeError: for an unknown option, a non-integer amount, or a raw
                chain value, including a dict in the client's ``mode`` or
                ``resolution``.
            QuipOrderOptionError: if an option is outside its chain limit.
            ValueError: if the model cannot be solved, the topology is not a
                hash, or ``mapping`` is given with ``"native"``.
            QuipConnectionError: if a chain read faults while placing the model.
        """
        return self._create_order(model, options, strict=True)

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
        the terminal and never hangs: with no TTY on stdin, or on neither
        stderr nor stdout, it raises at once.
        """
        if gate is True:
            return
        if callable(gate):
            if gate(quote):
                return
            raise QuipCancelledError(quote, f"{name} declined: the {name} callable rejected the quote")
        # Ask on whichever output stream is the terminal, so a redirect of the
        # other cannot hide the question while the process waits. With both
        # redirected there is nowhere to ask. When stderr is the terminal,
        # _display already showed the quote there.
        out = next((stream for stream in (sys.stderr, sys.stdout) if _isatty(stream)), None)
        if not _isatty(sys.stdin) or out is None:
            raise QuipCancelledError(
                quote,
                f"{name}=False asks for confirmation but stdin, or both stderr and stdout, is not a terminal; "
                f"pass {name}=True or {name}=lambda q: ... to decide in code",
            )
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

    def _clear_gates(self, quote: JobQuote) -> None:
        """Show ``quote``, refuse what no drip can cover, then ask the gates and fund if short.

        The ``autoconfirm`` gate comes first, so the price is accepted before
        funding is considered. Only if the account is short does the
        ``autofund`` gate follow, then a faucet drip and a balance re-read.

        Raises:
            QuipSubmissionError: if the account cannot cover the quote and no
                faucet is configured, the shortfall exceeds one faucet drip, the
                balance already exceeds the faucet's one-drip ceiling, or the
                drip does not land. The first three are raised before either
                gate is asked.
            QuipCancelledError: if a gate declines.
            QuipFaucetError: if the faucet refuses or cannot be reached.
        """
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
            self._fund(quote)

    def quote(self, model: XQMX, **kwargs: Any) -> JobQuote:
        """Price ``model`` as a job without proposing it. Deprecated.

        Use ``create_order(model, **options).quote()``. This shortcut keeps
        working until QUI-1608, accepting what :meth:`solve` accepts.

        Raises:
            ValueError: if ``mapping`` is given with ``topology="native"``.
            QuipOrderOptionError: if an option is outside its chain limit.
            QuipConnectionError: if a chain read faults while resolving the
                topology or the account balance.
            QuipSubmissionError: if the extrinsic cannot be built.
        """
        warnings.warn(
            "SolverQuip.quote() is deprecated; use SolverQuip.create_order(model).quote()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._create_order(model, kwargs, strict=False).quote()

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Propose ``model`` as a job, await a solution, and decode the best one.

        Builds an order from this client's defaults and ``kwargs`` (see
        :meth:`create_order`), submits it (see :meth:`JobOrder.submit
        <xqsa.quip.JobOrder.submit>`: the quote is shown, the consent gates
        asked, and the job proposed, reserving the reward), polls for finality
        by block height, then decodes the winning solution. If the order
        finalizes with no solutions, the reward is auto-reclaimed and
        :class:`QuipJobFailedError` is raised. A faucet drip is not returned if
        the job fails after it.

        Unlike :meth:`create_order`, an unknown option or a raw chain dict
        warns with a ``DeprecationWarning`` instead of raising, until QUI-1608.

        Keyword args:
            reward, deadline_blocks, block_wait, topology, mapping: as for
                :meth:`create_order`, for this solve only.

        Raises:
            ValueError: if ``mapping`` is given with ``topology="native"``.
            QuipOrderOptionError: if an option is outside its chain limit,
                before anything is signed.
            QuipConnectionError: if a chain read faults (transport/decode) while
                resolving the topology, the order, or the account balance.
            QuipSubmissionError: if the account cannot cover the quote and no
                faucet is configured, the shortfall exceeds one faucet drip, the
                balance already exceeds the faucet's one-drip ceiling, or the
                drip does not land; if the account nonce moves twice while
                submitting; or if proposing the job fails.
            QuipCancelledError: if the ``autoconfirm`` or ``autofund`` gate
                declines (carries the quote).
            QuipFaucetError: if the faucet refuses or cannot be reached.
            QuipTimeoutError: if the order does not finalize within ``timeout``
                (it carries the order's receipt; :meth:`get_receipt` rebuilds
                it from the order id).
            QuipUnconfirmedError: if the job was sent but its outcome or order
                id is unknown. It may be on chain, so do not solve the same
                model again on that account without checking.
            QuipJobFailedError: if the order finalizes with no usable solution.
        """
        order = self._create_order(model, kwargs, strict=False).submit().wait()
        assert order._submitted_at is not None  # submit() sets it.
        elapsed = time.perf_counter() - order._submitted_at
        return self._collect_result(order.receipt(), order._job, model, elapsed=elapsed)

    def query(
        self,
        order_id: int,
        model: XQMX,
        *,
        mapping: Mapping[int, int] | None = None,
        topology: str | None = None,
    ) -> SolverResult | None:
        """Recover the result of an already-proposed order. Deprecated.

        Use :meth:`get_receipt` to follow an order without its model; decoding
        a receipt's answers onto a model is QUI-1609. This keeps working, with
        a ``DeprecationWarning``, until QUI-1608 removes it.

        Returns ``None`` if the order is not yet final (poll again later). Once
        final, re-derives the deterministic placement from ``model`` and decodes
        the winning solution -- so an order proposed in a different process (or
        recovered after a :class:`QuipTimeoutError`) can still be read, provided
        the same ``model`` (and ``mapping``/``topology`` if non-default) is
        supplied. Pass the ``topology`` the order was proposed with, ``"native"``
        included, whenever it differs from this instance's own; otherwise the
        re-derived placement targets the wrong graph. A final
        order with no solutions auto-reclaims and raises
        :class:`QuipJobFailedError`.

        Raises:
            ValueError: if ``mapping`` is given with ``topology="native"``.
        """
        warnings.warn(
            "SolverQuip.query() is deprecated; use SolverQuip.get_receipt(order_id) to follow the order",
            DeprecationWarning,
            stacklevel=2,
        )
        self._validate_model(model)
        self._native_for(topology, mapping)  # reject native + mapping= before any chain read.
        receipt = self.get_receipt(order_id)
        if receipt.status()["state"] != "finalized":
            return None
        job = self._job_for(model, topology, mapping)
        return self._collect_result(receipt, job, model, elapsed=0.0)

    def get_receipt(self, order_id: int) -> JobOrderReceipt:
        """Return the receipt of the order ``order_id`` on this client's chain.

        Works in any session: the receipt reads everything from the chain, so
        it needs neither the order object nor the model.

        Raises:
            QuipConnectionError: if no such order exists on chain.
        """
        self._fetch_order(order_id)
        return JobOrderReceipt(self, order_id, self._genesis_hash)

    def list_orders(self, state: Literal["submitted", "finalized"] | None = None, account: Any = None) -> list[int]:
        """Return the ids of an account's orders, newest first.

        Reads the chain's per-account order list, so it includes orders
        placed from any session. ``state`` keeps only the orders still open
        (``"submitted"``) or final (``"finalized"``), at one more read per
        order. ``account`` lists another account's orders instead of this
        client's signer, as SS58 text, ``0x`` hex or bytes.

        At most :data:`LIST_ORDERS_LIMIT` (32) ids are returned; past that it
        warns and returns the newest 32.

        Raises:
            ValueError: if ``state`` is not ``"submitted"`` or ``"finalized"``,
                or ``account`` cannot be read as an account.
            QuipConnectionError: if an order or the head block cannot be read.
        """
        if state not in (None, "submitted", "finalized"):
            raise ValueError(f"state={state!r}; expected 'submitted', 'finalized' or None")
        owner = self._signer.account_id if account is None else chain.account_bytes(account)
        order_ids = chain.proposer_orders(self._iface, owner)[::-1]
        if state is not None:
            current_block = self._current_block()
            order_ids = [
                order_id
                for order_id in order_ids
                if self._order_lifecycle(self._fetch_order(order_id), current_block)["is_final"]
                == (state == "finalized")
            ]
        if len(order_ids) > LIST_ORDERS_LIMIT:
            warnings.warn(
                f"showing {LIST_ORDERS_LIMIT} of {len(order_ids)} orders on this account. This "
                f"{LIST_ORDERS_LIMIT}-order page limit was imposed for the old protocol cap of 32 orders per "
                "account; please flag it to the xquad team so it can be lifted.",
                UserWarning,
                stacklevel=2,
            )
        return order_ids[:LIST_ORDERS_LIMIT]

    def status(self, order_id: int) -> dict[str, Any]:
        """Return a lightweight lifecycle snapshot of an order (no solution decode). Deprecated.

        Use ``get_receipt(order_id).status()``, whose keys follow
        :meth:`JobOrder.status <xqsa.quip.JobOrder.status>`. This keeps its
        old keys, with a ``DeprecationWarning``, until QUI-1608 removes it.

        One ``JobOrders`` read plus the chain head: status, key block heights,
        the computed ``effective_expiry``, and whether the order ``is_final``.
        """
        warnings.warn(
            "SolverQuip.status() is deprecated; use SolverQuip.get_receipt(order_id).status()",
            DeprecationWarning,
            stacklevel=2,
        )
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
