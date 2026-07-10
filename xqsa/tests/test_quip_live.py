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

    QUIP_RPC_URL=ws://127.0.0.1:9944 \\
    QUIP_FAUCET_URL=http://127.0.0.1:8087 \\
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

import itertools
import os
import time

import pytest

substrateinterface = pytest.importorskip(
    "substrateinterface",
    reason="substrate-interface not installed (run `uv sync --extra quip`)",
)
pytest.importorskip(
    "quip_signer",
    reason="quip_signer extension not installed (run `uv sync --extra quip`)",
)

from xqsa.quip import (
    QuipJobFailedError,
    QuipSubmissionError,
    QuipTimeoutError,
    SolverQuip,
    _as_hex,
)
from xqsa.quip_codec import (
    DEFAULT_ISING_SPEC_ID,
    model_to_ising,
)
from xqsa.quip_signing import load_or_generate_keystore
from xqvm_py.xqmx import XQMX, compute_energy

RPC_URL = os.environ.get("QUIP_RPC_URL")
FAUCET_URL = os.environ.get("QUIP_FAUCET_URL")

pytestmark = [
    pytest.mark.quip,
    pytest.mark.skipif(not RPC_URL, reason="QUIP_RPC_URL unset; live devnet tests skipped"),
]

# advantage2_system1 on the v0.2 devnet (pinned in quip_codec), confirmed live.
# (These are the live counts, not the pre-live 4578 / 41531 estimate.)
EXPECTED_NODES = 4577
EXPECTED_EDGES = 41515
UNIT = 10**12

# Tighter lifecycle bounds than the production defaults so a live solve reaches
# finality in tens of seconds rather than minutes.
SOLVER_KWARGS = dict(deadline_blocks=30, block_wait=5, poll_interval=3.0, timeout=180.0)

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
    """A read-only substrate-interface connection for direct chain assertions."""
    iface = substrateinterface.SubstrateInterface(url=RPC_URL)
    yield iface
    iface.close()


@pytest.fixture(scope="session")
def funded_keystore(tmp_path_factory):
    """A fresh keystore funded via the localdev faucet.

    Skips the whole funded tier when ``QUIP_FAUCET_URL`` is unset -- without a
    funder we cannot reserve a reward.
    """
    if not FAUCET_URL:
        pytest.skip("QUIP_FAUCET_URL unset; funded live tests skipped")
    import urllib.request

    path = str(tmp_path_factory.mktemp("quip") / "keystore.json")
    keystore = load_or_generate_keystore(path)
    dest = "0x" + keystore.account_id.hex()

    body = f'{{"dest":"{dest}","amount":{10 * UNIT}}}'.encode()
    req = urllib.request.Request(
        FAUCET_URL.rstrip("/") + "/request",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 -- localdev faucet
        assert resp.status == 200, f"faucet returned {resp.status}"

    # Wait for the transfer to land.
    iface = substrateinterface.SubstrateInterface(url=RPC_URL)
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

    Proposes a trivial job and polls ``OrderSolutions`` for any submission. Fast
    when the miner works (returns on the first solution); bounded by
    ``PROBE_TIMEOUT`` when it does not. Returns False (rather than erroring) so
    the E2E tier skips cleanly when the fleet is idle.
    """
    if not FAUCET_URL:
        return False
    import urllib.request

    path = str(tmp_path_factory.mktemp("quip-probe") / "keystore.json")
    keystore = load_or_generate_keystore(path)
    dest = "0x" + keystore.account_id.hex()
    body = f'{{"dest":"{dest}","amount":{10 * UNIT}}}'.encode()
    req = urllib.request.Request(
        FAUCET_URL.rstrip("/") + "/request",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            if resp.status != 200:
                return False
    except Exception:
        return False

    solver = SolverQuip(url=RPC_URL, keystore=path, **SOLVER_KWARGS)
    # Give the funding a moment to land before reserving a reward.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if solver._check_balance(0) > UNIT:
            break
        time.sleep(3)

    model = XQMX.spin_model(2)
    model.set_linear(0, 1)
    model.set_quadratic(0, 1, -1)
    job = model_to_ising(model, solver._fetch_topology())
    try:
        order_id = solver._propose_job(job)
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


def _brute_force_optimum(model: XQMX, spins=(-1, 1)) -> int:
    """Minimum energy over all assignments -- only for tiny models (2**size)."""
    sampler = XQMX.spin_sample if spins == (-1, 1) else XQMX.binary_sample
    best = None
    for bits in itertools.product(spins, repeat=model.size):
        sample = sampler(model.size, model.rows, model.cols)
        for i, value in enumerate(bits):
            sample.set_linear(i, value)
        energy = compute_energy(model, sample)
        best = energy if best is None else min(best, energy)
    assert best is not None
    return int(best)


def _asymmetric_spin_model() -> XQMX:
    """A 3-var SPIN model with asymmetric fields (exercises index ordering)."""
    model = XQMX.spin_model(3)
    model.set_linear(0, 1)
    model.set_linear(1, -2)
    model.set_linear(2, 3)
    model.set_quadratic(0, 1, 1)
    model.set_quadratic(1, 2, -1)
    return model


# ---------------------------------------------------------------------------
# Connectivity -- runs against any healthy devnet
# ---------------------------------------------------------------------------


class TestConnectivity:
    def test_default_ising_spec_id_matches_constant(self, chain) -> None:
        const = chain.get_constant("QuantumComputeMempool", "DefaultIsingSpecId")
        assert const is not None
        assert const.value == DEFAULT_ISING_SPEC_ID

    def test_topology_resolves_to_pinned_graph(self, make_solver) -> None:
        solver = make_solver()
        topology = solver._fetch_topology()
        assert len(topology.nodes) == EXPECTED_NODES
        assert len(topology.edges) == EXPECTED_EDGES

    def test_solver_topology_tracks_chain_default(self, chain, make_solver) -> None:
        # With no topology= override, the solver targets the chain's declared
        # default topology (deployment-agnostic; the pinned constant is only a
        # fallback and differs per network).
        default = chain.query("QuantumPow", "DefaultTopology").value
        assert default is not None
        solver = make_solver()
        assert solver._topology_hash == _as_hex(default)


# ---------------------------------------------------------------------------
# Submit + lifecycle -- runs without a returned solution
# ---------------------------------------------------------------------------


class TestSubmitPath:
    def test_insufficient_balance_raises(self, make_solver) -> None:
        # A reward far beyond the funded balance trips the pre-check before submit.
        solver = make_solver(reward=10_000 * UNIT)
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
        # This order's reward was reserved at propose and released on reclaim, so
        # the reserved balance returns to its pre-solve level (robust to rewards
        # still reserved by other orders proposed earlier in the session).
        assert reserved() == before


# ---------------------------------------------------------------------------
# End-to-end -- needs the miner to actually return a solution
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_spin_optimum_and_energy_canary(self, make_solver, solving_miner) -> None:
        model = _asymmetric_spin_model()
        optimum = _brute_force_optimum(model)
        solver = make_solver()
        result = solver.solve(model)
        assert result.energy == optimum
        assert result.metadata["energy_matches_chain"] is True
        assert isinstance(result.metadata["order_id"], int)

    def test_binary_roundtrip_optimum(self, make_solver, solving_miner) -> None:
        model = XQMX.binary_model(3)
        model.set_linear(0, -1)
        model.set_linear(1, 2)
        model.set_quadratic(0, 1, -3)
        model.set_quadratic(1, 2, 1)
        optimum = _brute_force_optimum(model, spins=(0, 1))
        solver = make_solver()
        result = solver.solve(model)
        assert result.energy == optimum

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
