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
        uv run --extra quip pytest xqsa/tests/quip/test_live.py -m quip -v

Three kinds of order:

* NETWORK orders (``TestNetwork``) go through ``solve()`` with open access and
  test that the network answers. When nobody answers before the deadline they
  skip with a ``network:`` reason.
* SELF-SOLVED orders (``TestSelfSolved``) test chain rules. They go through the
  real submit, admit only our own two accounts (or a solver type), and are
  answered by those accounts in separate extrinsics, so ranks, energies and
  leaders are fixed by the test.
* UNANSWERED orders expire with nothing touching them: the timeout path,
  auto-reclaim, and reclaiming through a receipt.

The read-only connectivity tests run against any healthy devnet. Every skip
reason starts with ``network:`` or ``designed:``; ``conftest.py`` counts both.

Crypto known-answer vectors belong to ``quip_signer``'s own ``test_parity.py``,
not here.
"""

from __future__ import annotations

import logging
import os
import time

import pytest

pytest.importorskip(
    "substrateinterface",
    reason="designed: substrate-interface not installed (run `uv sync --extra quip`)",
)
pytest.importorskip(
    "quip_signer",
    reason="designed: quip_signer extension not installed (run `uv sync --extra quip`)",
)

from xqsa import SolverResult
from xqsa.quip import (
    AccessAllow,
    JobOrder,
    QuipFaucetError,
    QuipJobFailedError,
    QuipOrderOptionError,
    QuipSubmissionError,
    QuipTimeoutError,
    ResolutionTopNEqual,
    ResolutionTopNWeighted,
    SolverQuip,
)
from xqsa.quip import chain as quip_chain
from xqsa.quip.client import _is_native
from xqsa.quip.codec import (
    DEFAULT_ISING_SPEC_ID,
    PlacementError,
    _as_hex,
    _canonical_hex,
    _event_ids,
    _require_h256,
    ising_energy_milli,
)
from xqsa.quip.display import Floors, floors_text
from xqsa.quip.faucet import fund_from_faucet
from xqsa.quip.metadata import connect as connect_shimmed
from xqsa.quip.resolution import MIN_RANK_WEIGHTED_SPEC
from xqsa.quip.signing import check_runtime, load_or_generate_keystore
from xqsa.quip.solution import decode_answers, pick_best
from xqvm_py.xqmx import XQMX

RPC_URL = os.environ.get("QUIP_RPC_URL")
FAUCET_URL = os.environ.get("QUIP_FAUCET_URL")
# Set QUIP_TOPOLOGY to a registered non-default hash to run the whole suite
# against it. SolverQuip reads the variable itself, in its constructor, so no
# fixture threads it -- this is only how the tests know the override is in play.
# QUIP_TOPOLOGY=native names no registered hash, so the tests that read a
# topology by hash skip under it.
TOPOLOGY_OVERRIDE = os.environ.get("QUIP_TOPOLOGY")
NATIVE_MODE = _is_native(TOPOLOGY_OVERRIDE)
requires_hash_topology = pytest.mark.skipif(
    NATIVE_MODE,
    reason="designed: assumes a chain-registered topology hash; QUIP_TOPOLOGY=native resolves none",
)

pytestmark = [
    pytest.mark.quip,
    pytest.mark.skipif(not RPC_URL, reason="designed: QUIP_RPC_URL unset; live chain tests skipped"),
]

UNIT = 10**12

# Production lifecycle bounds, which the network orders keep. An earlier
# revision tightened these to deadline_blocks=30 / block_wait=5 so a live solve
# reached finality in tens of seconds, on the assumption that a fresh order
# solves in ~1-2 blocks. Measured against aglais on 2026-09-18 that assumption holds
# only for the median: first-solution latency ran 1, 1, 1, 2, 5, 6, 9, 12 and 25
# blocks across twelve answered orders, so a 30-block deadline sat inside the
# tail and orders were finalizing empty. poll_interval stays tight so an
# answered order still returns promptly; timeout clears 100 blocks at ~6s.
SOLVER_KWARGS = dict(deadline_blocks=100, block_wait=10, poll_interval=3.0, timeout=660.0)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chain():
    """A read-only substrate-interface connection for direct chain assertions.

    Built through ``xqsa.quip.metadata.connect`` for the same reason SolverQuip
    is: Quip runtimes serve metadata V16, which scalecodec cannot decode, so a
    stock ``SubstrateInterface`` fails on the first query. Opened per test: the
    node drops a websocket left idle while funded fixtures run.
    """
    iface = connect_shimmed(RPC_URL)
    yield iface
    iface.close()


def _new_funded_keystore(tmp_path_factory, name: str) -> str:
    """Generate a keystore under ``name``, fund it from the faucet and wait for the transfer; returns its path."""
    if not FAUCET_URL:
        pytest.skip("designed: QUIP_FAUCET_URL unset; funded live tests skipped")
    path = str(tmp_path_factory.mktemp(name) / "keystore.json")
    keystore = load_or_generate_keystore(path)
    dest = "0x" + keystore.account_id.hex()

    try:
        fund_from_faucet(dest, url=FAUCET_URL)
    except QuipFaucetError as exc:
        pytest.skip(f"network: the faucet refused funding: {exc}")

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
            pytest.skip("network: faucet funding did not land within 60s")
    finally:
        iface.close()
    return path


# The first account's solver type. The network's solvers register as Cpu or
# Gpu, so an allowlist of this type admits our account and nobody else's.
OWN_SOLVER_TYPE = "QpuPasqal"


def _new_solver_keystore(tmp_path_factory, name: str, solver_type: str) -> str:
    """A funded keystore whose account is registered as a ``solver_type`` solver; returns its path.

    Registration is permanent, so it happens once, here, before any test answers an order.
    """
    path = _new_funded_keystore(tmp_path_factory, name)
    solver = SolverQuip(url=RPC_URL, keystore=path, **SOLVER_KWARGS)
    solver._submit_extrinsic("QuantumComputeMempool", "register_solver", {"solver_type": solver_type})
    return path


@pytest.fixture(scope="session")
def funded_keystore(tmp_path_factory):
    """A fresh keystore funded via the chain's faucet, its account a solver of ``OWN_SOLVER_TYPE``.

    Skips the whole funded tier when ``QUIP_FAUCET_URL`` is unset -- without a
    funder we cannot reserve a reward.
    """
    return _new_solver_keystore(tmp_path_factory, "quip", OWN_SOLVER_TYPE)


@pytest.fixture(scope="session")
def second_funded_keystore(tmp_path_factory):
    """Another funded keystore, for a second solver account on one order, registered as a ``Gpu`` solver.

    Its type is what lets an allowlist of ``OWN_SOLVER_TYPE`` refuse it.
    """
    return _new_solver_keystore(tmp_path_factory, "quip-second", "Gpu")


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


@pytest.fixture
def second_solver(second_funded_keystore) -> SolverQuip:
    """A SolverQuip on the second funded keystore, whose account is a ``Gpu`` solver."""
    return SolverQuip(url=RPC_URL, keystore=second_funded_keystore, **SOLVER_KWARGS)


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


def _model_vector(job, spins: dict[int, int]) -> list[int]:
    """A per-node spin vector giving each model variable its spin in ``spins``; unplaced nodes read +1."""
    vector = [1] * job.topology.num_nodes
    for var, node in job.mapping.items():
        vector[job.topology.index_of(node)] = spins[var]
    return vector


def _ranked_vectors(job) -> list[list[int]]:
    """Every assignment of the placed variables as a per-node vector, lowest chain energy first.

    Works for SPIN and BINARY models alike, because the chain stores spins.
    """
    variables = sorted(job.mapping)
    vectors = [
        _model_vector(job, {var: 1 if bits >> i & 1 else -1 for i, var in enumerate(variables)})
        for bits in range(2 ** len(variables))
    ]
    return sorted(vectors, key=lambda vector: ising_energy_milli(job, vector))


def _place(solver: SolverQuip, model: XQMX, **options) -> JobOrder:
    """Create and submit an order for ``model`` with ``options`` through the real submit; returns the order."""
    return solver.create_order(model, **options).submit()


def _submit_answer(solver: SolverQuip, order_id: int, vectors: list[list[int]]):
    """Answer ``order_id`` with ``vectors`` from ``solver``'s registered account; returns the extrinsic receipt.

    A refusal raises QuipSubmissionError naming ``QuantumComputeMempool.<Error>``.
    An answer that arrives after the order expired fails the test as a timing
    failure, naming the order's timing, rather than as the error it expected.
    """
    pallet = "QuantumComputeMempool"
    solutions = quip_chain.wrap_bounded([quip_chain.wrap_bounded(list(vector)) for vector in vectors])

    def late(where: str) -> None:
        timing = solver._fetch_order(order_id)["timing"]
        pytest.fail(
            f"the answer to order {order_id} arrived after its expiry ({where}); raise this order's "
            f"deadline_blocks ({timing['deadline_blocks']}) or block_wait ({timing['block_wait']})"
        )

    try:
        sent = solver._submit_extrinsic(pallet, "submit_solution", {"order_id": order_id, "solutions": solutions})
    except QuipSubmissionError as exc:
        if f"{pallet}.OrderNotOpen" in str(exc):
            late(str(exc))
        raise
    landed = quip_chain.block_number(solver._iface, sent.block_hash)
    expiry = solver.get_receipt(order_id).status()["effective_expiry"]
    if landed > expiry:
        late(f"landed in block {landed}, expiry {expiry}")
    return sent


def _events_named(iface, block_hash: str, name: str) -> list[tuple[object, object]]:
    """The ``QuantumComputeMempool`` events called ``name`` in a block, as ``(phase, attributes)``.

    The phase is the record's own: ``Initialization``, ``Finalization`` or an
    ``ApplyExtrinsic`` index. ``quip_chain._events`` reads it as an extrinsic
    index, which reports both block phases as ``None``.
    """
    events = []
    for record in iface.get_events(block_hash=block_hash) or []:
        value = record.value if hasattr(record, "value") else record
        inner = value.get("event", value)
        if _event_ids(inner) == ("QuantumComputeMempool", name):
            events.append((value.get("phase"), inner.get("attributes", inner.get("params"))))
    return events


def _claim_from(solver: SolverQuip, order_id: int) -> tuple[dict[bytes, int], str]:
    """Send ``claim_reward`` for ``order_id`` from ``solver``'s account.

    Returns what the claim paid, ``{account bytes: amount}`` from the block's
    ``RewardClaimed`` events for the order, and the block's hash.
    """
    sent = solver._submit_extrinsic("QuantumComputeMempool", "claim_reward", {"order_id": order_id})
    paid = {
        quip_chain.account_bytes(attrs["solver"]): int(attrs["amount"])
        for _phase, attrs in _events_named(solver._iface, sent.block_hash, "RewardClaimed")
        if int(attrs["order_id"]) == order_id
    }
    return paid, sent.block_hash


def _ss58(solver: SolverQuip) -> str:
    """``solver``'s account in SS58, as receipts name solvers."""
    return quip_chain.ss58_address(solver._iface, bytes(solver._signer.account_id))


def _solve_or_skip(solver: SolverQuip, model: XQMX, **kwargs):
    """``solver.solve(model, **kwargs)``, skipping the test when the network left the order unanswered."""
    try:
        return solver.solve(model, **kwargs)
    except QuipJobFailedError as exc:
        if "finalized with no solutions" not in str(exc):
            raise
        pytest.skip(f"network: no solver answered order {exc.order_id} before it expired")


def _account(solver: SolverQuip) -> str:
    """``solver``'s account as ``0x`` hex, as an allowlist takes it."""
    return "0x" + bytes(solver._signer.account_id).hex()


# ---------------------------------------------------------------------------
# Connectivity -- runs against any healthy devnet
# ---------------------------------------------------------------------------


class TestConnectivity:
    def test_default_ising_spec_id_matches_constant(self, chain) -> None:
        const = chain.get_constant("QuantumComputeMempool", "DefaultIsingSpecId")
        assert const is not None
        assert const.value == DEFAULT_ISING_SPEC_ID

    def test_endpoint_keeps_state_history(self, chain) -> None:
        # Settling a sent propose_job reads the nonce and events of past
        # blocks; a pruning node refuses them and every submit stops sending.
        try:
            chain.get_events(block_hash=chain.get_block_hash(1))
        except Exception as exc:  # noqa: BLE001 -- the node's refusal is the finding.
            pytest.fail(f"this endpoint prunes state; settling needs --state-pruning=archive ({exc})")

    @requires_hash_topology
    def test_topology_decodes_to_a_consistent_graph(self, make_solver, caplog) -> None:
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
        with caplog.at_level(logging.INFO, logger="xqsa.quip"):
            solver = make_solver()
        # QUI-1510: the connect checked the runtime and says what it reached.
        assert any(
            m.startswith(f"connected to Quip custom endpoint at {RPC_URL}: specVersion ") and solver._genesis_hash in m
            for m in caplog.messages
        )
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
            pytest.skip("designed: QUIP_TOPOLOGY displaces the chain default this test asserts")
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

    def test_client_can_sign_for_the_runtime(self, chain) -> None:
        # QUI-1257, QUI-1510: the envelope and every signed extension, in the
        # runtime's order, decode exactly with the chain's types; a runtime
        # change fails here rather than as a rejected signature.
        chain.init_runtime()
        check_runtime(chain)

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
            pytest.skip(
                "designed: MineableTopologies is empty/unset on this node; expected the default topology seeded"
            )
        assert _canonical_hex(default) in mineable

    def test_override_topology_is_registered_but_not_mineable(self, chain, make_solver) -> None:
        # The premise of the override run, asserted rather than assumed. If
        # QUIP_TOPOLOGY were also mineable, every solve below would prove nothing
        # about mineability being irrelevant to the mempool. Registered is what
        # _fetch_topology requires; absent from the mining set is the condition
        # solve() used to reject.
        if not TOPOLOGY_OVERRIDE:
            pytest.skip("designed: QUIP_TOPOLOGY unset; the non-mineable path is opt-in")
        if NATIVE_MODE:
            pytest.skip("designed: QUIP_TOPOLOGY=native names no registered topology")
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


class TestMortalEra:
    """``propose_job`` is signed mortal from the finalized head; the runtime must accept it."""

    def test_mortal_propose_job_validates(self, make_solver) -> None:
        # Read-only: the pool's own validity check, at the best block as the
        # pool runs it. Not at the checkpoint: the finalized head can lag the
        # faucet drip and read as Invalid::Payment (0x010001). Ok is a leading
        # 0x00 in the SCALE Result.
        solver = make_solver()
        order = solver.create_order(_asymmetric_spin_model())
        order.quote()
        signed = order._signed
        assert signed is not None
        assert signed.window_end == signed.checkpoint + solver._inclusion_window_blocks - 1
        at = solver._iface.get_chain_head()
        source_external = b"\x02"
        payload = source_external + signed.wire + bytes.fromhex(at[2:])
        result = solver._iface.rpc_request(
            "state_call", ["TaggedTransactionQueue_validate_transaction", "0x" + payload.hex(), at]
        )["result"]
        assert result.startswith("0x00"), f"the runtime refused the mortal era: {result}"


class TestUnanswered:
    """Orders that expire with nothing touching them."""

    def test_timeout_expiry_and_reclaim(self, make_solver) -> None:
        """An order only our account may answer times out in solve(), expires on its own, and reclaims by receipt.

        Nobody else may answer and we never do, so the order expires unanswered
        whatever the network does. Its deadline is long enough that solve()
        is still waiting when its timeout fires; a shorter one is already
        expired once submit() returns, and solve() then reclaims it itself.
        """
        solver = make_solver(timeout=0.5)
        account = "0x" + solver._signer.account_id.hex()
        rule = AccessAllow(accounts=[account])
        with pytest.raises(QuipTimeoutError) as excinfo:
            solver.solve(_asymmetric_spin_model(), access=rule, deadline_blocks=10, block_wait=1)
        order_id = excinfo.value.order_id
        assert isinstance(order_id, int)
        assert excinfo.value.receipt.order_id == order_id

        receipt = make_solver().get_receipt(order_id).wait()
        snap = receipt.status()
        assert (snap["state"], snap["solution_count"]) == ("finalized", 0)
        # on_initialize finalised it: Expired at the expiry block, by an event outside any extrinsic.
        expiry_block = quip_chain.block_hash(solver._iface, snap["effective_expiry"])
        stored = solver._iface.query("QuantumComputeMempool", "JobOrders", [order_id], block_hash=expiry_block).value
        assert quip_chain._status_str(stored["status"]) == "Expired"
        phases = [
            phase
            for phase, attrs in _events_named(solver._iface, expiry_block, "OrderExpired")
            if int(attrs["order_id"]) == order_id
        ]
        assert phases == ["Initialization"]
        assert solver.list_orders()[0] == order_id
        assert order_id in solver.list_orders(state="finalized")

        def reserved() -> int:
            return solver._iface.query("System", "Account", [account]).value["data"]["reserved"]

        reward = receipt._facts().reward
        before = reserved()
        assert receipt.reclaim() == reward
        assert before - reserved() >= reward
        assert receipt.status()["chain_status"] == "Closed"
        assert receipt.settlement() == {"claimed": False, "winners": []}
        with pytest.raises(QuipSubmissionError, match="is already closed"):
            receipt.reclaim()

        text = str(receipt)
        assert {len(line) for line in text.splitlines()} == {72}
        assert "│ Reward      reclaimed " in text
        assert "unknown" not in text  # the fee and submission time were read from the chain.
        assert receipt._genesis_hash not in text

    def test_expired_order_auto_reclaims(self, make_solver, monkeypatch) -> None:
        # A 1-block deadline finalizes empty regardless of fleet activity: the
        # order hard-expires at created_at+1, before any solver can land a
        # solution (the miner observes the JobProposed event ~1 block after
        # proposal and needs 1-2 more to solve, and its own deadline-margin
        # guard drops sub-margin orders). solve() then auto-reclaims the
        # reserved reward and raises QuipJobFailedError. A longer deadline is
        # unsafe here: once a live solver fleet is active it solves the order
        # before expiry (a 4-block deadline is beaten in ~1-2 blocks). Orders
        # refuse deadlines under 10 blocks, so the floor is lowered for this
        # test only: it exercises the reclaim, not the option checks.
        monkeypatch.setattr("xqsa.quip.order.MIN_DEADLINE_BLOCKS", 1)
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
        snap = solver.get_receipt(excinfo.value.order_id).status()
        assert snap["solution_count"] == 0
        assert snap["chain_status"] == "Closed"
        assert snap["state"] == "finalized"

        # This order's reward was reserved at propose and released on reclaim, so
        # the reserved balance does not GROW across the solve. The reclaim itself
        # is confirmed by the failure message: a non-release of this order would
        # push reserved above `before`. `<= before` (not `==`) tolerates rewards
        # reserved by other session-scoped orders (e.g. another test's order
        # being claimed or reclaimed) also being released within this
        # window on a live testnet (~6s blocks), which drops reserved below
        # `before`. The message assert prevents the bare `<=` from passing
        # falsely: both orders reserve the same default 1 UNIT, so a genuine
        # non-release could otherwise cancel against the earlier order's release.
        assert "was reclaimed" in str(excinfo.value)
        assert reserved() <= before


# ---------------------------------------------------------------------------
# Network, self-solved and payout-rule orders
# ---------------------------------------------------------------------------


class TestNetwork:
    """Open orders through ``solve()``: the network answers them, or they skip with a ``network:`` reason."""

    def test_network_solves_our_order(self, make_solver) -> None:
        """The live pipeline returns a feasible, chain-consistent result, and the deprecated pair reads it back.

        Optimality and encoding correctness are guarded deterministically by
        ``test_encoded_problem_argmin_decodes_to_optimum`` in ``test_client.py``.
        This test covers propose, network solve, chain record, and decode without
        requiring the probabilistic live network to reach the global optimum.
        """
        model = _asymmetric_spin_model()
        solver = make_solver()
        result = _solve_or_skip(solver, model)
        assert isinstance(result, SolverResult)
        assert result.metadata["energy_matches_chain"] is True
        assert result.metadata["num_solutions"] >= 1
        assert _feasible(result.sample, model.size, {-1, 1})
        order_id = result.metadata["order_id"]
        snap = solver.get_receipt(order_id).status()
        assert snap["first_solution_at"] > snap["created_at"]

        # The deprecated pair keeps working until QUI-1608.
        with pytest.warns(DeprecationWarning):
            assert solver.status(order_id)["is_final"] is True
        with pytest.warns(DeprecationWarning):
            recovered = solver.query(order_id, model)
        assert recovered is not None
        assert recovered.energy == result.energy

    def test_network_solves_a_native_order(self, make_solver) -> None:
        """A K6 model, which the default topology cannot place, solves in native mode."""
        model = _dense_spin_model()
        solver = make_solver()
        if not TOPOLOGY_OVERRIDE:  # the chain default; a registered override may hold K6.
            with pytest.raises(PlacementError):
                solver._job_for(model, None, None)
        result = _solve_or_skip(solver, model, topology="native")
        assert result.metadata["energy_matches_chain"] is True
        assert result.metadata["num_solutions"] >= 1
        assert _feasible(result.sample, model.size, {-1, 1})


class TestSelfSolved:
    """Orders only our two accounts can answer, through the real submit, answered in separate extrinsics.

    Nobody else can answer, so ranks, energies and leaders are fixed by the
    test. Refusals go before any accepted answer: a refused answer does not
    start the ``block_wait`` countdown, while an accepted one does.
    """

    def test_order_lifecycle_reads_back(self, make_solver, second_solver) -> None:
        """One order through its draft lifecycle, answered with four vectors, read back here and on a fresh client."""
        model = XQMX.binary_model(3)
        model.set_linear(0, -1)
        model.set_linear(1, 2)
        model.set_quadratic(0, 1, -3)
        model.set_quadratic(1, 2, 1)
        solver = make_solver()
        rule = AccessAllow(accounts=[_account(solver), _account(second_solver)])
        order = solver.create_order(model, access=rule)
        assert isinstance(order, JobOrder)
        order.set(deadline_blocks=30, block_wait=4)

        quote = order.quote()
        assert quote.affordable
        assert quote.reward_planck == order.reward
        assert quote.account is not None and not quote.account.startswith("0x")  # SS58, not hex.
        assert {len(line) for line in str(quote).splitlines()} == {72}
        signed = order._signed
        assert quote.inclusion_window_blocks == 64
        assert signed.window_end - signed.checkpoint == 63

        order.submit()
        assert order.status()["state"] in ("submitted", "finalized")
        order_id = order.order_id()
        assert isinstance(order_id, int)
        assert signed.checkpoint < order._included_block <= signed.window_end  # landed inside its window.
        assert solver.list_orders()[0] == order_id
        stored = solver._fetch_order(order_id)
        assert int(stored["timing"]["deadline_blocks"]) == 30
        assert int(stored["timing"]["block_wait"]) == 4
        with pytest.raises(QuipSubmissionError, match="works only on a draft"):
            order.set(deadline_blocks=200)

        # The best four, sent worst first: the decode puts them back in order.
        job = order._job
        vectors = _ranked_vectors(job)[: min(4, solver._limits.max_solutions or 4)]
        _submit_answer(solver, order_id, vectors[::-1])
        receipt = order.wait().receipt()
        snap = receipt.status()
        assert snap["state"] == "finalized"
        assert snap["first_solution_at"] > snap["created_at"]

        ours = _ss58(solver)
        solutions = decode_answers(receipt, job, model, solver=ours)
        assert [s.energy_milli for s in solutions] == [ising_energy_milli(job, v) for v in vectors]
        assert [solutions[0].sample.get_linear(var) for var in range(model.size)] == [1, 1, 0]  # the ground state.

        assert receipt._facts().access == rule
        text = str(receipt)
        print(text)
        assert {len(line) for line in text.splitlines()} == {72}
        assert "│ Access      allowlist (accounts " in text
        assert "unknown" not in text  # the fee and submission time were read from the chain.
        assert receipt._genesis_hash not in text

        rebuilt = make_solver().get_receipt(order_id)
        stable = ("state", "order_id", "created_at", "first_solution_at", "effective_expiry", "solution_count")
        assert rebuilt._genesis_hash == receipt._genesis_hash
        assert [rebuilt.status()[key] for key in stable] == [snap[key] for key in stable]
        assert rebuilt.solvers() == receipt.solvers()
        settlement = receipt.settlement()
        assert rebuilt.settlement() == settlement
        assert [entry["solver"] for entry in receipt.solvers()] == [ours]
        assert settlement == {
            "claimed": False,
            "winners": [{"solver": ours, "energy_milli": solutions[0].energy_milli, "allocation_planck": order.reward}],
        }

        paid, _block = _claim_from(solver, order_id)
        assert paid == {bytes(solver._signer.account_id): order.reward}

    def test_rank_weighted_split_on_runtime_119(self, make_solver, second_solver) -> None:
        """A mixed-sign TopNWeighted(2) order pays by rank alone; the second account's claim pays the split."""
        solver = make_solver()
        if (solver._limits.spec_version or 0) < MIN_RANK_WEIGHTED_SPEC:
            pytest.skip(f"designed: runtime {solver._limits.spec_version} weights by energy, not by rank")
        model = _asymmetric_spin_model()
        rule = AccessAllow(accounts=[_account(solver), _account(second_solver)])
        # An odd reward, so rank 0 takes a remainder.
        order = _place(
            solver,
            model,
            resolution=ResolutionTopNWeighted(2),
            access=rule,
            reward=solver._reward + 1,
            deadline_blocks=30,
            block_wait=8,
        )
        order_id = order.order_id()
        vectors = _ranked_vectors(order._job)
        ground, positive = vectors[0], vectors[5]  # -6 and +2 on this model.
        assert ising_energy_milli(order._job, ground) < 0 < ising_energy_milli(order._job, positive)
        _submit_answer(solver, order_id, [ground])
        _submit_answer(second_solver, order_id, [positive])
        receipt = order.wait().receipt()

        ours, theirs = _ss58(solver), _ss58(second_solver)
        assert [entry["solver"] for entry in receipt.solvers()] == [ours, theirs]
        # Weights k - r over total k(k+1)/2 = 3 with k = 2 ranked, remainder to rank 0.
        reward = order.reward
        second_share = (reward // 3) * 1 + (reward % 3) * 1 // 3
        expected = [reward - second_share, second_share]
        settlement = receipt.settlement()
        assert [(w["solver"], w["allocation_planck"]) for w in settlement["winners"]] == list(
            zip([ours, theirs], expected, strict=True)
        )
        text = str(receipt)
        print(text)
        assert "│ Payout      top 2, weighted by rank" in text

        paid, block = _claim_from(second_solver, order_id)  # any account may claim.
        assert paid == {
            bytes(solver._signer.account_id): expected[0],
            bytes(second_solver._signer.account_id): expected[1],
        }
        assert _events_named(solver._iface, block, "ResultReady") == []

    def test_tie_splits_equally_and_keeps_our_lead(self, make_solver, second_solver) -> None:
        """Both accounts answer the ground state; the first answer keeps rank 0 and the reward splits equally."""
        model = _asymmetric_spin_model()
        solver = make_solver()
        rule = AccessAllow(accounts=[_account(solver), _account(second_solver)])
        order = _place(
            solver,
            model,
            resolution=ResolutionTopNEqual(2),
            access=rule,
            reward=solver._reward + 1,
            deadline_blocks=30,
            block_wait=8,
        )
        order_id, job = order.order_id(), order._job
        ground = _ranked_vectors(job)[0]
        _submit_answer(solver, order_id, [ground])
        _submit_answer(second_solver, order_id, [ground])
        receipt = order.wait().receipt()

        ours, theirs = _ss58(solver), _ss58(second_solver)
        assert [entry["solver"] for entry in receipt.solvers()] == [ours, theirs]
        pick = pick_best(receipt, job, model)
        assert (pick.solution.solver, pick.solution.leader, pick.leader_read) == (ours, True, True)
        assert pick.energy_matches_chain is True
        with pytest.warns(DeprecationWarning):
            result = solver.query(order_id, model)
        assert result is not None
        assert result.metadata["solver"] == ours

        reward = order.reward
        expected = [reward - reward // 2, reward // 2]
        settlement = receipt.settlement()
        assert [(w["solver"], w["allocation_planck"]) for w in settlement["winners"]] == list(
            zip([ours, theirs], expected, strict=True)
        )
        text = str(receipt)
        print(text)
        assert "│ Payout      top 2, equal split" in text

        paid, _block = _claim_from(solver, order_id)
        assert paid == {
            bytes(solver._signer.account_id): expected[0],
            bytes(second_solver._signer.account_id): expected[1],
        }

    def test_type_allowlist_admits_and_refuses_by_type(self, make_solver, second_solver) -> None:
        """An allowlist of our account's solver type refuses the Gpu second account and admits ours alone."""
        model = _asymmetric_spin_model()
        solver = make_solver()
        rule = AccessAllow(types=[OWN_SOLVER_TYPE])
        order = _place(solver, model, access=rule, deadline_blocks=30, block_wait=4)
        order_id = order.order_id()
        ground = _ranked_vectors(order._job)[0]
        with pytest.raises(QuipSubmissionError, match="QuantumComputeMempool.NotEligibleSolver"):
            _submit_answer(second_solver, order_id, [ground])
        _submit_answer(solver, order_id, [ground])
        receipt = order.wait().receipt()

        assert receipt._facts().access == rule
        assert [entry["solver"] for entry in receipt.solvers()] == [_ss58(solver)]
        paid, _block = _claim_from(solver, order_id)
        assert paid == {bytes(solver._signer.account_id): order.reward}

    def test_quality_floors_and_ceiling(self, make_solver, second_solver) -> None:
        """The second account's answers each miss one floor; ours meets them all once the ceiling drops a vector."""
        model = _asymmetric_spin_model()
        solver = make_solver()
        rule = AccessAllow(accounts=[_account(solver), _account(second_solver)])
        order = _place(
            solver,
            model,
            access=rule,
            max_energy=0,
            min_solutions=2,
            min_diversity=0.1,
            deadline_blocks=30,
            block_wait=6,
        )
        order_id, job = order.order_id(), order._job
        vectors = _ranked_vectors(job)
        ground, low, high = vectors[0], vectors[1], vectors[-1]  # -6, -2 and +6 on this model.
        positive = vectors[5]  # +2
        # The chain measures diversity over the order's placed nodes, here 3: two
        # copies of one vector score 0, and ground against low scores at least 1/3.

        refusals = (
            ([ground], "InsufficientSolutions"),
            ([ground, ground], "InsufficientDiversity"),
            ([positive, high], "InsufficientEnergy"),  # the ceiling drops both.
        )
        for answer, error in refusals:
            with pytest.raises(QuipSubmissionError, match=f"QuantumComputeMempool.{error}"):
                _submit_answer(second_solver, order_id, answer)
        _submit_answer(solver, order_id, [ground, low, high])
        receipt = order.wait().receipt()

        params = solver._fetch_order(order_id)["ising_params"]
        assert params.get("max_energy_milli", params.get("min_energy_milli")) == 0
        assert (params["min_solutions"], params["min_diversity_milli"]) == (2, 100)
        assert receipt._floors_text() == floors_text(Floors(0, 2, 100))
        (answer,) = solver._fetch_solutions(order_id)
        stored = [[int(spin) for spin in vector] for vector in answer["solutions"]]
        assert len(stored) == 2
        assert all(ising_energy_milli(job, vector) <= 0 for vector in stored)
        assert int(answer["diversity_milli"]) >= 100
        paid, _block = _claim_from(solver, order_id)
        assert paid == {bytes(solver._signer.account_id): order.reward}


class TestPayoutRules:
    def test_top_n_weighted_refused_below_runtime_119(self, make_solver) -> None:
        """create_order refuses rank weighting on a runtime that weights by energy, before anything is signed."""
        solver = make_solver()
        if (solver._limits.spec_version or 0) >= MIN_RANK_WEIGHTED_SPEC:
            pytest.skip(
                f"designed: the chain runs runtime {solver._limits.spec_version}; "
                "TestSelfSolved::test_rank_weighted_split_on_runtime_119 covers it"
            )
        with pytest.raises(QuipOrderOptionError, match=f"runtime {solver._limits.spec_version}"):
            solver.create_order(_asymmetric_spin_model(), resolution=ResolutionTopNWeighted(2))
