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
Live Quip Network devnet tests for SolverQuip (QUI-569 Step 8).

Opt-in: marked ``quip`` and skipped unless ``QUIP_RPC_URL`` is set. Run against
a Quip Network devnet (a single-node localdev or a testnet), pointing the two
env vars at its RPC + faucet:

    QUIP_RPC_URL=ws://localhost:20049/rpc \\
    QUIP_FAUCET_URL=http://localhost:20049/api/faucet \\
        uv run --extra quip pytest xqsa/tests/test_quip_live.py -m quip -v

Two tiers:

* SUBMIT + lifecycle tests run against any healthy devnet. They validate the
  parts of SolverQuip that do not need a returned solution: connectivity, the
  live ``propose_job`` SCALE/extras contract (a clean submission with no
  ``System.ExtrinsicFailed``), the topology fetch, the balance pre-check, the
  timeout path, and expired-no-solution auto-reclaim.
* END-TO-END tests need the miner to actually return a solution. They are
  guarded by the ``solving_miner`` fixture, which probes the fleet once and
  skips them when no solution comes back within the probe window. They run
  whenever a live solver fleet is active (a fresh order solves in ~1-2 blocks)
  and skip cleanly when the fleet is idle or unavailable.

Crypto known-answer vectors belong to ``quip_signer``'s own ``test_parity.py``,
not here.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip(
    "substrateinterface",
    reason="substrate-interface not installed (run `uv sync --extra quip`)",
)
pytest.importorskip(
    "quip_signer",
    reason="quip_signer extension not installed (run `uv sync --extra quip`)",
)

from xqsa.quip import (
    MEMPOOL_PALLET,
    NATIVE_TOPOLOGY,
    PROPOSE_JOB_CALL,
    QuipJobFailedError,
    QuipSubmissionError,
    QuipTimeoutError,
    SolverQuip,
    _as_hex,
    _canonical_hex,
    _require_h256,
)
from xqsa.quip_codec import DEFAULT_ISING_SPEC_ID
from xqsa.quip_faucet import fund_from_faucet
from xqsa.quip_metadata import connect as connect_shimmed
from xqsa.quip_signing import SIGNED_EXTENSIONS, _extension_fields, load_or_generate_keystore
from xqvm_py.xqmx import XQMX

RPC_URL = os.environ.get("QUIP_RPC_URL")
FAUCET_URL = os.environ.get("QUIP_FAUCET_URL")
# Set QUIP_TOPOLOGY to a registered non-default hash to run the whole suite
# against it. SolverQuip reads the variable itself, in its constructor, so no
# fixture threads it -- this is only how the tests know the override is in play.
# QUIP_TOPOLOGY=native names no registered hash, so the tests that read a
# topology by hash skip under it.
TOPOLOGY_OVERRIDE = os.environ.get("QUIP_TOPOLOGY")
NATIVE_MODE = (TOPOLOGY_OVERRIDE or "").strip() == NATIVE_TOPOLOGY
requires_hash_topology = pytest.mark.skipif(
    NATIVE_MODE,
    reason="assumes a chain-registered topology hash; QUIP_TOPOLOGY=native resolves none",
)

pytestmark = [
    pytest.mark.quip,
    pytest.mark.skipif(not RPC_URL, reason="QUIP_RPC_URL unset; live chain tests skipped"),
]

UNIT = 10**12

# Production lifecycle bounds. An earlier revision tightened these to
# deadline_blocks=30 / block_wait=5 so a live solve reached finality in tens of
# seconds, on the assumption in MINER_IDLE_SKIP below that a fresh order solves
# in ~1-2 blocks. Measured against aglais on 2026-09-18 that assumption holds
# only for the median: first-solution latency ran 1, 1, 1, 2, 5, 6, 9, 12 and 25
# blocks across twelve answered orders, so a 30-block deadline sat inside the
# tail and orders were finalizing empty. poll_interval stays tight so an
# answered order still returns promptly; timeout clears 100 blocks at ~6s.
SOLVER_KWARGS = dict(deadline_blocks=100, block_wait=10, poll_interval=3.0, timeout=660.0)

# How long the capability probe waits for the miner to submit ANY solution
# before declaring the E2E tier unavailable. Overridable for slow hosts.
PROBE_TIMEOUT = float(os.environ.get("QUIP_MINER_PROBE_TIMEOUT", "60"))

MINER_IDLE_SKIP = (
    "miner returned no solution within the probe window -- the live solver "
    "fleet is idle or unavailable. E2E validation needs an active fleet; a "
    "fresh order solves in ~1-2 blocks when one is running."
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def chain():
    """A read-only substrate-interface connection for direct chain assertions.

    Built through ``xqsa.quip_metadata.connect`` for the same reason SolverQuip
    is: Quip runtimes serve metadata V16, which scalecodec cannot decode, so a
    stock ``SubstrateInterface`` fails on the first query.
    """
    iface = connect_shimmed(RPC_URL)
    yield iface
    iface.close()


@pytest.fixture(scope="session")
def funded_keystore(tmp_path_factory):
    """A fresh keystore funded via the chain's faucet.

    Skips the whole funded tier when ``QUIP_FAUCET_URL`` is unset -- without a
    funder we cannot reserve a reward.
    """
    if not FAUCET_URL:
        pytest.skip("QUIP_FAUCET_URL unset; funded live tests skipped")
    path = str(tmp_path_factory.mktemp("quip") / "keystore.json")
    keystore = load_or_generate_keystore(path)
    dest = "0x" + keystore.account_id.hex()

    fund_from_faucet(dest, url=FAUCET_URL)

    # Wait for the transfer to land.
    iface = connect_shimmed(RPC_URL)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            free = iface.query("System", "Account", [dest]).value["data"]["free"]
            if free > 0:
                break
            time.sleep(3)
        else:
            pytest.skip("faucet funding did not land within 60s")
    finally:
        iface.close()
    return path


@pytest.fixture
def make_solver(funded_keystore):
    """Factory building a SolverQuip on the funded keystore against the devnet."""
    solvers: list[SolverQuip] = []

    def _make(**overrides) -> SolverQuip:
        kwargs = {**SOLVER_KWARGS, **overrides}
        solver = SolverQuip(url=RPC_URL, keystore=funded_keystore, **kwargs)
        solvers.append(solver)
        return solver

    yield _make


@pytest.fixture(scope="session")
def _miner_solves(tmp_path_factory) -> bool:
    """Probe once whether the miner returns a solution; cached for the session.

    Proposes the gated tests' own model and polls ``OrderSolutions`` for any
    submission. Fast when the miner works (returns on the first solution);
    bounded by ``PROBE_TIMEOUT`` when it does not. Returns False (rather than
    erroring) so the E2E tier skips cleanly when the fleet is idle.
    """
    if not FAUCET_URL:
        return False
    path = str(tmp_path_factory.mktemp("quip-probe") / "keystore.json")
    keystore = load_or_generate_keystore(path)
    dest = "0x" + keystore.account_id.hex()
    try:
        fund_from_faucet(dest, url=FAUCET_URL)
    except Exception:
        return False

    solver = SolverQuip(url=RPC_URL, keystore=path, **SOLVER_KWARGS)
    # Give the funding a moment to land before reserving a reward.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if solver._free_balance() > UNIT:
            break
        time.sleep(3)

    # The same model the gated tests propose. A smaller one would place onto
    # different hardware nodes and so prove nothing about them: placement is
    # per-model, and whether an order is answered depends on the nodes it lands
    # on. A gate must exercise what it gates, native mode included.
    job = solver._job_for(_asymmetric_spin_model(), None, None)
    try:
        wire, ext_hash = solver._build_extrinsic(MEMPOOL_PALLET, PROPOSE_JOB_CALL, solver._propose_call_params(job))
        order_id = solver._propose_job(wire, ext_hash)
    except Exception:
        return False

    probe_deadline = time.monotonic() + PROBE_TIMEOUT
    while time.monotonic() < probe_deadline:
        sols = list(solver._iface.query_map("QuantumComputeMempool", "OrderSolutions", [order_id]))
        if sols:
            return True
        time.sleep(3)
    return False


@pytest.fixture
def solving_miner(_miner_solves) -> None:
    """Skip the test unless the probe confirmed the miner returns solutions."""
    if not _miner_solves:
        pytest.skip(MINER_IDLE_SKIP)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feasible(sample: XQMX, size: int, domain_values: set[int]) -> bool:
    """Return whether each sample value belongs to the model's domain."""
    return all(sample.get_linear(index) in domain_values for index in range(size))


def _asymmetric_spin_model() -> XQMX:
    """A 3-var SPIN model with asymmetric fields (exercises index ordering)."""
    model = XQMX.spin_model(3)
    model.set_linear(0, 1)
    model.set_linear(1, -2)
    model.set_linear(2, 3)
    model.set_quadratic(0, 1, 1)
    model.set_quadratic(1, 2, -1)
    return model


def _dense_spin_model() -> XQMX:
    """A K6 SPIN model: every pair of its 6 variables is coupled."""
    size = 6
    model = XQMX.spin_model(size)
    for i in range(size):
        model.set_linear(i, 1 if i % 2 == 0 else -1)
    for i in range(size):
        for j in range(i + 1, size):
            model.set_quadratic(i, j, 1 if (i + j) % 2 == 0 else -1)
    return model


# ---------------------------------------------------------------------------
# Connectivity -- runs against any healthy devnet
# ---------------------------------------------------------------------------


class TestConnectivity:
    def test_default_ising_spec_id_matches_constant(self, chain) -> None:
        const = chain.get_constant("QuantumComputeMempool", "DefaultIsingSpecId")
        assert const is not None
        assert const.value == DEFAULT_ISING_SPEC_ID

    @requires_hash_topology
    def test_topology_decodes_to_a_consistent_graph(self, make_solver) -> None:
        # Deliberately not an exact node/edge count. A count is a fingerprint of
        # one deployment -- aglais and a localdev DevNet register different
        # graphs -- so an exact assertion fails on a healthy chain that simply
        # is not the one it was recorded against. Deployment identity is already
        # covered by test_solver_topology_tracks_chain_default below, and the
        # hash it compares binds the exact node and edge arrays.
        #
        # What is asserted here is the decode contract, which holds on every
        # deployment: a non-empty graph whose edges reference real nodes.
        # Topology.__post_init__ already enforces ordering and uniqueness but
        # checks neither endpoint membership nor self-loops.
        solver = make_solver()
        topology = solver._fetch_topology()
        assert topology.nodes
        assert topology.edges
        nodes = set(topology.nodes)
        assert all(u in nodes and v in nodes for u, v in topology.edges)
        assert all(u != v for u, v in topology.edges)

    @requires_hash_topology
    def test_allowed_value_spec_names_match_the_pallet(self, chain, make_solver) -> None:
        # QUI-1374: Topology.from_chain read allowed_h / allowed_j / allowed_spin
        # while TopologyMeta serves allowed_*_values. Every lookup resolved to
        # None, from_chain_spec(None) returned None, and the out-of-spec
        # coefficient warning was dead on every real chain with nothing logged.
        # The unit fixture could not catch it -- it authored the same literals as
        # the codec -- so only the chain can witness the pallet's own names.
        solver = make_solver()
        raw = chain.query("QuantumPow", "RegisteredTopologies", [solver._topology_hash]).value
        assert raw is not None, "the resolved topology is not registered on this chain"
        # Superset, not equality. Topology.from_chain reads its three fields with
        # meta.get(), so its contract is that those names are present -- a fourth
        # allowed_* field a future runtime adds decodes exactly as well, and
        # failing on it would report a silent-None that is not happening.
        served = {key for key in raw if key.startswith("allowed_")}
        required = {"allowed_h_values", "allowed_j_values", "allowed_spin_values"}
        assert served >= required, (
            f"TopologyMeta does not serve {sorted(required - served)} (it serves {sorted(served)}); "
            "Topology.from_chain reads those names and would silently decode None"
        )
        # A served spec must survive the decode, so a name that matches but a
        # shape that changed also fails here rather than going quiet.
        topology = solver._fetch_topology()
        for field, attr in (
            ("allowed_h_values", "allowed_h"),
            ("allowed_j_values", "allowed_j"),
            ("allowed_spin_values", "allowed_spin"),
        ):
            if raw.get(field) is not None:
                assert getattr(topology, attr) is not None, f"{field} is served but decoded to None"

    def test_solver_topology_tracks_chain_default(self, chain, make_solver) -> None:
        # With no topology= override, the solver targets the chain's declared
        # default topology. This is what makes the suite deployment-agnostic:
        # nothing in the codebase carries a hash, because a hash is only ever
        # valid on the deployment that registered it.
        if TOPOLOGY_OVERRIDE:
            pytest.skip("QUIP_TOPOLOGY displaces the chain default this test asserts")
        default = chain.query("QuantumPow", "DefaultTopology").value
        assert default is not None
        solver = make_solver()
        assert solver._topology_hash == _as_hex(default)

    def test_metadata_decodes_through_the_v14_shim(self, chain) -> None:
        # The shim is what makes every other test in this file possible: Quip
        # runtimes serve metadata V16 and scalecodec stops at V14, so a stock
        # client raises "Index '16' not present in Enum type mapping" here.
        chain.init_runtime()
        assert chain.metadata is not None
        assert chain.runtime_version is not None

    def test_signed_extensions_match_chain(self, chain) -> None:
        # QUI-1257: the encoded order must equal the runtime's own, so the next
        # extension the runtime inserts fails this test rather than a submission.
        chain.init_runtime()
        assert tuple(chain.metadata.get_signed_extensions()) == SIGNED_EXTENSIONS

    def test_empty_signed_extensions_really_encode_nothing(self, chain) -> None:
        # The order check above catches an insertion or a reorder. It does not
        # catch an existing extension gaining a field: we would keep encoding
        # b"" for it and the submission, not the test, would be what fails.
        # So check the claim directly -- every half we encode as empty must be a
        # type that decodes from zero bytes.
        from scalecodec.base import ScaleBytes

        chain.init_runtime()
        definitions = chain.metadata.get_signed_extensions()
        fields = _extension_fields(nonce=0, spec_version=1, tx_version=1, genesis_bytes=b"\x00" * 32)

        for name, halves in fields.items():
            for half, blob in zip(("extrinsic", "additional_signed"), halves, strict=True):
                if blob:
                    continue  # we encode bytes for it; emptiness is not claimed.
                type_string = definitions[name][half]
                obj = chain.runtime_config.create_scale_object(type_string, data=ScaleBytes(b""))
                try:
                    obj.decode()
                except Exception as exc:  # noqa: BLE001 -- any failure means it wants bytes.
                    pytest.fail(f"{name}.{half} ({type_string}) is no longer empty on-chain: {exc}")

    def test_chain_default_topology_is_mineable(self, chain, make_solver) -> None:
        # Both localdev and testnet seed MineableTopologies with the chain's
        # default hash. Nothing on the solve path depends on that -- the mineable
        # set gates submit_proof, i.e. block production -- but the assertion still
        # exercises the real query_map decode and canonical-hex normalisation.
        # Read the hash from the chain rather than from the solver: the solver's
        # resolved hash can be a QUIP_TOPOLOGY override, which is precisely the
        # registered-but-not-mineable case.
        default = chain.query("QuantumPow", "DefaultTopology").value
        assert default is not None
        solver = make_solver()
        mineable = solver._mineable_topologies()
        if not mineable:
            pytest.skip("MineableTopologies is empty/unset on this node; expected the default topology seeded")
        assert _canonical_hex(default) in mineable

    def test_override_topology_is_registered_but_not_mineable(self, chain, make_solver) -> None:
        # The premise of the override run, asserted rather than assumed. If
        # QUIP_TOPOLOGY were also mineable, every solve below would prove nothing
        # about mineability being irrelevant to the mempool. Registered is what
        # _fetch_topology requires; absent from the mining set is the condition
        # solve() used to reject.
        if not TOPOLOGY_OVERRIDE:
            pytest.skip("QUIP_TOPOLOGY unset; the non-mineable path is opt-in")
        if NATIVE_MODE:
            pytest.skip("QUIP_TOPOLOGY=native names no registered topology")
        # Normalize once, the way the constructor does, and compare against that.
        # _as_hex only prefixes 0x, while the constructor resolves through
        # _require_h256, which also strips and lower-cases. An override spelled in
        # upper case or with surrounding whitespace is accepted by SolverQuip but
        # failed here, reporting a spelling difference as a breach of the premise.
        override = _require_h256(TOPOLOGY_OVERRIDE, "QUIP_TOPOLOGY")
        solver = make_solver()
        assert solver._topology_hash == override
        assert solver._fetch_topology().nodes  # registered: decodes to a real graph
        # The mining set and the chain default are compared in _canonical_hex
        # form (lower-case, no 0x), which is what _mineable_topologies stores.
        # Comparing the 0x-prefixed override against them directly would never
        # match, and both assertions would pass whatever the chain says.
        canonical_override = _canonical_hex(override)
        mineable = solver._mineable_topologies()
        assert mineable is not None, "MineableTopologies absent from the runtime; nothing to be outside of"
        assert canonical_override not in mineable
        default = chain.query("QuantumPow", "DefaultTopology").value
        assert canonical_override != _canonical_hex(default)


# ---------------------------------------------------------------------------
# Submit + lifecycle -- runs without a returned solution
# ---------------------------------------------------------------------------


class TestSubmitPath:
    def test_insufficient_balance_raises(self, make_solver) -> None:
        # A reward far beyond the funded balance trips the pre-check before submit.
        # The shortfall is far beyond one drip, so this raises before either gate or the faucet.
        solver = make_solver(reward=10_000 * UNIT, autofund=lambda quote: False)
        with pytest.raises(QuipSubmissionError):
            solver.solve(_asymmetric_spin_model())

    def test_propose_succeeds_then_times_out_recoverably(self, make_solver) -> None:
        # A clean propose_job (no System.ExtrinsicFailed -> SCALE shapes + extras
        # order correct) followed by a tiny timeout: the order id is recoverable.
        solver = make_solver(timeout=0.5)
        with pytest.raises(QuipTimeoutError) as excinfo:
            solver.solve(_asymmetric_spin_model())
        order_id = excinfo.value.order_id
        assert isinstance(order_id, int)
        snap = solver.status(order_id)
        assert snap["order_id"] == order_id
        assert snap["status"] in ("Opened", "Expired", "Closed")

    def test_expired_no_solution_auto_reclaims(self, make_solver) -> None:
        # A 1-block deadline finalizes empty regardless of fleet activity: the
        # order hard-expires at created_at+1, before any solver can land a
        # solution (the miner observes the JobProposed event ~1 block after
        # proposal and needs 1-2 more to solve, and its own deadline-margin
        # guard drops sub-margin orders). solve() then auto-reclaims the
        # reserved reward and raises QuipJobFailedError. A longer deadline is
        # unsafe here: once a live solver fleet is active it solves the order
        # before expiry (a 4-block deadline is beaten in ~1-2 blocks).
        solver = make_solver(deadline_blocks=1, block_wait=1, timeout=150.0)
        account = "0x" + solver._signer.account_id.hex()

        def reserved() -> int:
            return solver._iface.query("System", "Account", [account]).value["data"]["reserved"]

        before = reserved()
        with pytest.raises(QuipJobFailedError) as excinfo:
            solver.solve(_asymmetric_spin_model())
        assert isinstance(excinfo.value.order_id, int)

        # The order finalized with no solutions, which is the precondition that
        # triggers auto-reclaim. Confirm it directly on the order (account-
        # independent, so unaffected by state left by other session tests).
        snap = solver.status(excinfo.value.order_id)
        assert snap["solution_count"] == 0
        assert snap["status"] in ("Expired", "Closed")
        assert snap["is_final"] is True

        # This order's reward was reserved at propose and released on reclaim, so
        # the reserved balance does not GROW across the solve. The reclaim itself
        # is confirmed by the failure message: a non-release of this order would
        # push reserved above `before`. `<= before` (not `==`) tolerates rewards
        # reserved by other session-scoped orders (e.g. the earlier
        # deadline_blocks=30 timeout test) also being reclaimed within this
        # window on a live testnet (~6s blocks), which drops reserved below
        # `before`. The message assert prevents the bare `<=` from passing
        # falsely: both orders reserve the same default 1 UNIT, so a genuine
        # non-release could otherwise cancel against the earlier order's release.
        assert "was reclaimed" in str(excinfo.value)
        assert reserved() <= before


# ---------------------------------------------------------------------------
# End-to-end -- needs the miner to actually return a solution
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_spin_roundtrip_pipeline(self, make_solver, solving_miner) -> None:
        """The live pipeline returns a feasible, chain-consistent spin result.

        Optimality and encoding correctness are guarded deterministically by
        ``test_encoded_problem_argmin_decodes_to_optimum`` in ``test_quip.py``.
        This test covers propose, fleet solve, chain record, and decode without
        requiring the probabilistic live fleet to reach the global optimum.
        """
        model = _asymmetric_spin_model()
        solver = make_solver()
        result = solver.solve(model)
        assert result.metadata["energy_matches_chain"] is True
        assert result.metadata["num_solutions"] >= 1
        assert _feasible(result.sample, model.size, {-1, 1})

    def test_binary_roundtrip_pipeline(self, make_solver, solving_miner) -> None:
        """The live pipeline returns a feasible, chain-consistent binary result.

        Optimality and encoding correctness are guarded deterministically by
        ``test_encoded_problem_argmin_decodes_to_optimum`` in ``test_quip.py``.
        This test covers propose, fleet solve, chain record, and decode without
        requiring the probabilistic live fleet to reach the global optimum.
        """
        model = XQMX.binary_model(3)
        model.set_linear(0, -1)
        model.set_linear(1, 2)
        model.set_quadratic(0, 1, -3)
        model.set_quadratic(1, 2, 1)
        solver = make_solver()
        result = solver.solve(model)
        assert result.metadata["energy_matches_chain"] is True
        assert result.metadata["num_solutions"] >= 1
        assert _feasible(result.sample, model.size, {0, 1})

    def test_native_topology_dense_model_round_trip(self, make_solver, solving_miner) -> None:
        """A K6 model, which the default topology cannot place, solves in native mode."""
        model = _dense_spin_model()
        solver = make_solver()
        result = solver.solve(model, topology="native")
        assert result.metadata["energy_matches_chain"] is True
        assert result.metadata["num_solutions"] >= 1
        assert _feasible(result.sample, model.size, {-1, 1})

    def test_query_and_status_after_solve(self, make_solver, solving_miner) -> None:
        model = _asymmetric_spin_model()
        solver = make_solver()
        result = solver.solve(model)
        order_id = result.metadata["order_id"]

        snap = solver.status(order_id)
        assert snap["is_final"] is True

        recovered = solver.query(order_id, model)
        assert recovered is not None
        assert recovered.energy == result.energy
