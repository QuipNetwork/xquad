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
Tests for the Quip Network codec (xqsa.quip_codec).

Pure-logic coverage: topology canonicalization, subgraph placement, Ising
encoding (SPIN and BINARY), spin-vector decoding, and lifecycle expiry math.
No chain or networking dependency, so these run everywhere.
"""

from __future__ import annotations

import itertools
import sys
import types
import warnings
from unittest.mock import MagicMock

import pytest

dimod = pytest.importorskip("dimod", reason="dwave-samplers / dimod not installed")

from xqsa.quip_codec import (
    DEFAULT_ISING_SPEC_ID,
    I32_MAX,
    I32_MIN,
    MAX_NATURAL_COEFFICIENT,
    MILLI_SCALE,
    QUIP_COEFFICIENTS_DOC_URL,
    AllowedValues,
    EncodingError,
    IsingJob,
    PlacementError,
    Topology,
    check_allowed_values,
    decode_solution,
    effective_expiry,
    find_placement,
    is_final,
    ising_energy_milli,
    model_to_ising,
)
from xqsa.solver import SolverResult
from xqvm_py.xqmx import XQMX, XQMXDomain, compute_energy

# ---------------------------------------------------------------------------
# Shared fixtures: small, hand-checkable topologies
# ---------------------------------------------------------------------------

# A 5-node path: 0 - 1 - 2 - 3 - 4.
PATH5 = Topology.of(range(5), [(0, 1), (1, 2), (2, 3), (3, 4)])

# A 4-cycle with both diagonals (every pair adjacent except none): a square.
SQUARE = Topology.of([0, 1, 2, 3], [(0, 1), (1, 2), (2, 3), (0, 3)])


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


class TestTopology:
    """Canonicalization and lookups."""

    def test_nodes_sorted_and_deduplicated(self) -> None:
        """of() sorts node ids ascending and drops duplicates."""
        topo = Topology.of([30, 10, 20, 10], [])
        assert topo.nodes == (10, 20, 30)

    def test_non_contiguous_node_ids(self) -> None:
        """Node ids need not be contiguous; index_of maps to dense positions."""
        topo = Topology.of([5, 2, 100], [(100, 2)])
        assert topo.nodes == (2, 5, 100)
        assert topo.index_of(2) == 0
        assert topo.index_of(5) == 1
        assert topo.index_of(100) == 2

    def test_edges_normalized_sorted_deduplicated(self) -> None:
        """Edges are (min, max)-normalized, sorted, and deduplicated."""
        topo = Topology.of([0, 1, 2], [(2, 1), (1, 0), (0, 1)])
        assert topo.edges == ((0, 1), (1, 2))

    def test_edge_index_is_order_independent(self) -> None:
        """edge_index and has_edge ignore endpoint order."""
        assert PATH5.edge_index(1, 0) == PATH5.edge_index(0, 1) == 0
        assert PATH5.has_edge(3, 2) is True
        assert PATH5.has_edge(0, 4) is False

    def test_adjacency(self) -> None:
        """adjacency returns the neighbor set; isolated/absent nodes give empty."""
        assert PATH5.adjacency(2) == frozenset({1, 3})
        assert PATH5.adjacency(0) == frozenset({1})
        assert PATH5.adjacency(999) == frozenset()

    def test_counts(self) -> None:
        """num_nodes/num_edges reflect the canonical arrays."""
        assert PATH5.num_nodes == 5
        assert PATH5.num_edges == 4

    def test_from_chain(self) -> None:
        """from_chain consumes a decoded TopologyMeta-like mapping."""
        topo = Topology.from_chain({"nodes": [2, 0, 1], "edges": [(1, 0), (2, 1)]})
        assert topo.nodes == (0, 1, 2)
        assert topo.edges == ((0, 1), (1, 2))

    def test_equality_ignores_caches(self) -> None:
        """Two topologies built from the same graph compare equal and hash."""
        a = Topology.of([0, 1, 2], [(0, 1)])
        b = Topology.of([2, 1, 0], [(1, 0)])
        assert a == b
        assert hash(a) == hash(b)

    def test_raw_constructor_rejects_non_ascending_nodes(self) -> None:
        """The direct constructor enforces strictly-ascending, unique nodes."""
        with pytest.raises(EncodingError, match="strictly ascending"):
            Topology((2, 1, 3), ())
        with pytest.raises(EncodingError, match="strictly ascending"):
            Topology((1, 1, 2), ())

    def test_raw_constructor_rejects_unnormalized_edge(self) -> None:
        """The direct constructor enforces (min, max)-normalized edges."""
        with pytest.raises(EncodingError, match="normalized"):
            Topology((0, 1), ((1, 0),))

    def test_raw_constructor_rejects_unsorted_edges(self) -> None:
        """The direct constructor enforces sorted, unique edges."""
        with pytest.raises(EncodingError, match="sorted ascending"):
            Topology((0, 1, 2), ((1, 2), (0, 1)))


class TestIsingJobGuards:
    """The raw IsingJob constructor validates array lengths against the topology."""

    def test_rejects_h_values_length_mismatch(self) -> None:
        with pytest.raises(EncodingError, match="h_values has"):
            IsingJob(
                topology=PATH5,
                h_values=(0,),
                j_values=tuple([0] * PATH5.num_edges),
                mapping={},
                domain=XQMXDomain.SPIN,
            )

    def test_rejects_j_values_length_mismatch(self) -> None:
        with pytest.raises(EncodingError, match="j_values has"):
            IsingJob(
                topology=PATH5,
                h_values=tuple([0] * PATH5.num_nodes),
                j_values=(0,),
                mapping={},
                domain=XQMXDomain.SPIN,
            )

    def test_mapping_is_immutable(self) -> None:
        """The frozen dataclass's mapping cannot be mutated in place."""
        job = IsingJob(
            topology=PATH5,
            h_values=tuple([0] * PATH5.num_nodes),
            j_values=tuple([0] * PATH5.num_edges),
            mapping={0: 0},
            domain=XQMXDomain.SPIN,
        )
        assert job.mapping == {0: 0}
        with pytest.raises(TypeError):
            job.mapping[0] = 1  # type: ignore[index]


# ---------------------------------------------------------------------------
# find_placement
# ---------------------------------------------------------------------------


class TestFindPlacement:
    """Subgraph placement: greedy search, explicit mapping, determinism."""

    def test_places_an_edge(self) -> None:
        """A single coupling lands on a real hardware edge."""
        placement = find_placement([0, 1], [(0, 1)], PATH5)
        assert len(set(placement.values())) == 2
        assert PATH5.has_edge(placement[0], placement[1])

    def test_places_a_path(self) -> None:
        """A 3-variable path embeds, injectively, onto adjacent nodes."""
        placement = find_placement([0, 1, 2], [(0, 1), (1, 2)], PATH5)
        assert len(set(placement.values())) == 3
        assert PATH5.has_edge(placement[0], placement[1])
        assert PATH5.has_edge(placement[1], placement[2])

    def test_isolated_variable_is_placed(self) -> None:
        """A coupling-free variable still receives a distinct node."""
        placement = find_placement([0, 1, 2], [(0, 1)], PATH5)
        assert set(placement) == {0, 1, 2}
        assert len(set(placement.values())) == 3

    def test_backtracks_then_succeeds(self) -> None:
        """A dead-end first candidate is unwound before a later one completes the placement.

        The path model's center variable is placed first, and ascending order
        tries node 0 first. On the claw topology node 0 is a degree-1 leaf that
        cannot host the center's two neighbors, so the search must unwind that
        assignment and advance the center to the hub (node 1) before it succeeds
        -- exercising the unwind-then-succeed backtrack branch (not just greedy
        success or terminal failure).
        """
        claw = Topology.of([0, 1, 2, 3], [(0, 1), (1, 2), (1, 3)])
        placement = find_placement([0, 1, 2], [(0, 1), (1, 2)], claw)
        assert placement[1] == 1  # center lands on the hub, not the leaf tried first
        assert claw.has_edge(placement[0], placement[1])
        assert claw.has_edge(placement[1], placement[2])
        assert len(set(placement.values())) == 3

    def test_triangle_not_a_subgraph_of_a_path(self) -> None:
        """An unplaceable model raises, listing the offending couplings."""
        triangle = [(0, 1), (1, 2), (0, 2)]
        with pytest.raises(PlacementError) as excinfo:
            find_placement([0, 1, 2], triangle, Topology.of([0, 1, 2], [(0, 1), (1, 2)]))
        assert set(excinfo.value.couplings) == {(0, 1), (1, 2), (0, 2)}

    def test_coupling_endpoints_are_placed_even_if_unlisted(self) -> None:
        """A coupling forces both endpoints into the placement."""
        placement = find_placement([], [(0, 1)], PATH5)
        assert set(placement) == {0, 1}

    def test_determinism(self) -> None:
        """Identical inputs yield an identical mapping (so query() can re-derive)."""
        args = ([0, 1, 2], [(0, 1), (1, 2)], PATH5)
        assert find_placement(*args) == find_placement(*args)

    def test_max_steps_budget_exhausted(self) -> None:
        """A starved search budget raises a distinct, actionable error."""
        with pytest.raises(PlacementError, match="budget"):
            find_placement([0, 1], [(0, 1)], PATH5, max_steps=0)

    def test_budget_exhausted_message_names_both_causes(self) -> None:
        """The budget message is honest that the model may be unplaceable, not just under-budgeted."""
        with pytest.raises(PlacementError, match="subgraph") as excinfo:
            find_placement([0, 1], [(0, 1)], PATH5, max_steps=0)
        assert "max_steps" in str(excinfo.value)

    def test_large_chain_places_without_recursion_error(self) -> None:
        """A chain longer than Python's recursion limit places via the iterative search."""
        size = 1500  # exceeds the default ~1000 interpreter recursion limit
        path_topology = Topology.of(range(size), [(i, i + 1) for i in range(size - 1)])
        couplings = [(i, i + 1) for i in range(size - 1)]
        placement = find_placement(range(size), couplings, path_topology)
        assert len(set(placement.values())) == size
        assert all(path_topology.has_edge(placement[u], placement[v]) for u, v in couplings)

    def test_explicit_mapping_accepted(self) -> None:
        """A valid explicit mapping is validated and returned verbatim."""
        placement = find_placement([0, 1], [(0, 1)], PATH5, mapping={0: 2, 1: 3})
        assert placement == {0: 2, 1: 3}

    def test_explicit_mapping_missing_variable(self) -> None:
        """An incomplete explicit mapping is rejected."""
        with pytest.raises(PlacementError, match="missing"):
            find_placement([0, 1], [(0, 1)], PATH5, mapping={0: 2})

    def test_explicit_mapping_unknown_node(self) -> None:
        """A mapping onto a node absent from the topology is rejected."""
        with pytest.raises(PlacementError, match="absent"):
            find_placement([0, 1], [(0, 1)], PATH5, mapping={0: 2, 1: 99})

    def test_explicit_mapping_not_injective(self) -> None:
        """A mapping that collides two variables on one node is rejected."""
        with pytest.raises(PlacementError, match="injective"):
            find_placement([0, 1], [(0, 1)], PATH5, mapping={0: 2, 1: 2})

    def test_explicit_mapping_coupling_without_edge(self) -> None:
        """A mapping whose coupling has no hardware edge lists that coupling."""
        with pytest.raises(PlacementError) as excinfo:
            find_placement([0, 1], [(0, 1)], PATH5, mapping={0: 0, 1: 4})
        assert excinfo.value.couplings == ((0, 1),)


# ---------------------------------------------------------------------------
# model_to_ising
# ---------------------------------------------------------------------------


class TestModelToIsing:
    """Encoding XQMX models into placed, on-chain-ready Ising jobs."""

    def test_spin_positions_and_scaling(self) -> None:
        """SPIN coefficients scatter to mapped positions, scaled by MILLI_SCALE."""
        topo = Topology.of([10, 20, 30], [(10, 20), (20, 30)])
        model = XQMX.spin_model(2)
        model.set_linear(0, 3)
        model.set_linear(1, -2)
        model.set_quadratic(0, 1, 5)

        job = model_to_ising(model, topo, mapping={0: 10, 1: 20})

        # Only the placed nodes are submitted: 10 -> position 0, 20 -> position 1.
        # Node 30 and edge (20,30) host nothing, so they are not part of the order.
        assert job.h_values == (3 * MILLI_SCALE, -2 * MILLI_SCALE)
        assert job.j_values == (5 * MILLI_SCALE,)
        assert job.domain == XQMXDomain.SPIN
        assert job.nodes == (10, 20)
        assert job.edges == ((10, 20),)

    def test_submits_the_placed_subgraph_not_the_whole_topology(self) -> None:
        """h/j span the placed subgraph, which stays a subgraph of the hardware.

        A 2-variable model must not carry every hardware node and edge into
        permanent chain storage. The submitted graph is still drawn from the
        hardware graph, so every node and edge in it is real.
        """
        model = XQMX.spin_model(2)
        model.set_quadratic(0, 1, 1)
        job = model_to_ising(model, PATH5)

        assert len(job.h_values) == job.topology.num_nodes == 2
        assert len(job.j_values) == job.topology.num_edges == 1
        assert job.topology.num_nodes < PATH5.num_nodes
        assert all(PATH5.has_node(node) for node in job.nodes)
        assert all(PATH5.has_edge(u, v) for u, v in job.edges)

    def test_linear_only_model_submits_no_edges(self) -> None:
        """A model with no couplings yields nodes and an empty edge array."""
        model = XQMX.spin_model(2)
        model.set_linear(0, 1)
        model.set_linear(1, -1)
        job = model_to_ising(model, PATH5)
        assert job.topology.num_nodes == 2
        assert job.edges == ()
        assert job.j_values == ()

    def test_empty_model_rejected(self) -> None:
        """A model with no terms has nothing to solve."""
        with pytest.raises(EncodingError, match="no linear or quadratic"):
            model_to_ising(XQMX.spin_model(3), PATH5)

    def test_single_variable_model(self) -> None:
        """A lone linear term places onto the smallest free node."""
        model = XQMX.spin_model(1)
        model.set_linear(0, 7)
        job = model_to_ising(model, PATH5)
        assert job.mapping == {0: 0}
        assert job.h_values[0] == 7 * MILLI_SCALE
        assert sum(job.h_values[1:]) == 0

    def test_i32_overflow_at_boundary(self) -> None:
        """The largest in-range coefficient encodes; one above overflows."""
        ok = XQMX.spin_model(1)
        ok.set_linear(0, MAX_NATURAL_COEFFICIENT)
        job = model_to_ising(ok, PATH5)
        assert job.h_values[0] == MAX_NATURAL_COEFFICIENT * MILLI_SCALE <= I32_MAX

        over = XQMX.spin_model(1)
        over.set_linear(0, MAX_NATURAL_COEFFICIENT + 1)
        with pytest.raises(EncodingError, match="overflows"):
            model_to_ising(over, PATH5)

    def test_i32_overflow_at_negative_boundary(self) -> None:
        """The most-negative in-range coefficient encodes; one below overflows."""
        ok = XQMX.spin_model(1)
        ok.set_linear(0, -MAX_NATURAL_COEFFICIENT)
        job = model_to_ising(ok, PATH5)
        assert job.h_values[0] == -MAX_NATURAL_COEFFICIENT * MILLI_SCALE >= I32_MIN

        over = XQMX.spin_model(1)
        over.set_linear(0, -(MAX_NATURAL_COEFFICIENT + 1))
        with pytest.raises(EncodingError, match="overflows"):
            model_to_ising(over, PATH5)

    def test_non_milli_coefficient_rejected(self) -> None:
        """A float coefficient finer than milli precision is rejected, not rounded."""
        model = XQMX.spin_model(1)
        model.set_linear(0, 0.0001)  # 0.1 milli -- finer than the 1/1000 grid
        with pytest.raises(EncodingError, match="milli precision"):
            model_to_ising(model, PATH5)

    def test_binary_path_is_milli_exact(self) -> None:
        """The BINARY->spin transform yields milli-exact i32 coefficients."""
        model = XQMX.binary_model(3)
        model.set_linear(0, -1)
        model.set_linear(1, 2)
        model.set_quadratic(0, 1, -2)
        model.set_quadratic(1, 2, 3)
        job = model_to_ising(model, PATH5)
        # Quarter-valued spin coefficients land exactly on multiples of 250.
        assert all(isinstance(v, int) for v in job.h_values)
        assert all(isinstance(v, int) for v in job.j_values)
        assert any(v != 0 for v in job.j_values)

    def test_explicit_mapping_threaded_through(self) -> None:
        """A caller mapping reaches placement and drives the scatter positions."""
        model = XQMX.spin_model(2)
        model.set_quadratic(0, 1, 1)
        job = model_to_ising(model, PATH5, mapping={0: 3, 1: 4})
        assert job.mapping == {0: 3, 1: 4}
        assert job.nodes == (3, 4)
        assert job.j_values[job.topology.edge_index(3, 4)] == MILLI_SCALE


# ---------------------------------------------------------------------------
# decode_solution
# ---------------------------------------------------------------------------


class TestDecodeSolution:
    """Reading a returned spin vector back into an XQMX sample."""

    def _job(self, topo: Topology, mapping: dict[int, int], domain: XQMXDomain) -> IsingJob:
        return IsingJob(
            topology=topo,
            h_values=tuple([0] * topo.num_nodes),
            j_values=tuple([0] * topo.num_edges),
            mapping=mapping,
            domain=domain,
        )

    def test_spin_roundtrip_positions(self) -> None:
        """Each mapped variable reads the spin at its assigned node position."""
        job = self._job(PATH5, {0: 1, 1: 3}, XQMXDomain.SPIN)
        model = XQMX.spin_model(2)
        # spins by node: node0=-1 node1=+1 node2=-1 node3=-1 node4=+1
        spins = [-1, 1, -1, -1, 1]
        sample = decode_solution(job, spins, model)
        assert sample.get_linear(0) == 1  # var0 -> node1 -> +1
        assert sample.get_linear(1) == -1  # var1 -> node3 -> -1

    def test_binary_reverse_transform(self) -> None:
        """BINARY decode maps spins back via x = (s + 1) / 2."""
        job = self._job(PATH5, {0: 0, 1: 1}, XQMXDomain.BINARY)
        model = XQMX.binary_model(2)
        sample = decode_solution(job, [1, -1, -1, -1, -1], model)
        assert sample.get_linear(0) == 1  # spin +1 -> 1
        assert sample.get_linear(1) == 0  # spin -1 -> 0

    def test_unmapped_variables_keep_defaults(self) -> None:
        """Unplaced variables stay at the sample default (-1 spin / 0 binary)."""
        spin_job = self._job(PATH5, {0: 0}, XQMXDomain.SPIN)
        spin_sample = decode_solution(spin_job, [1, 1, 1, 1, 1], XQMX.spin_model(3))
        assert spin_sample.get_linear(0) == 1
        assert spin_sample.get_linear(1) == -1
        assert spin_sample.get_linear(2) == -1

        bin_job = self._job(PATH5, {0: 0}, XQMXDomain.BINARY)
        bin_sample = decode_solution(bin_job, [1, 1, 1, 1, 1], XQMX.binary_model(3))
        assert bin_sample.get_linear(1) == 0
        assert bin_sample.get_linear(2) == 0

    def test_wrong_length_rejected(self) -> None:
        """A spin vector that does not span the topology is rejected."""
        job = self._job(PATH5, {0: 0}, XQMXDomain.SPIN)
        with pytest.raises(EncodingError, match="length"):
            decode_solution(job, [1, 1, 1], XQMX.spin_model(1))

    def test_invalid_spin_rejected(self) -> None:
        """A non-spin entry is rejected (the chain returns only -1/+1)."""
        job = self._job(PATH5, {0: 0}, XQMXDomain.SPIN)
        with pytest.raises(EncodingError, match="invalid spin"):
            decode_solution(job, [1, 0, 1, -1, 1], XQMX.spin_model(1))


# ---------------------------------------------------------------------------
# End-to-end codec: encode -> (brute-force optimum) -> decode -> energy
# ---------------------------------------------------------------------------


def _brute_force_optimum(model: XQMX) -> tuple[dict[int, int], int]:
    """Return the lowest-energy assignment and energy by exhaustive search."""
    lo, hi = (0, 1) if model.domain == XQMXDomain.BINARY else (-1, 1)
    sample_ctor = XQMX.binary_sample if model.domain == XQMXDomain.BINARY else XQMX.spin_sample
    best_assignment: dict[int, int] = {}
    best_energy: int | None = None
    for combo in itertools.product((lo, hi), repeat=model.size):
        sample = sample_ctor(model.size)
        for var, value in enumerate(combo):
            sample.set_linear(var, value)
        energy = compute_energy(model, sample)
        if best_energy is None or energy < best_energy:
            best_energy = energy
            best_assignment = dict(enumerate(combo))
    assert best_energy is not None
    return best_assignment, best_energy


@pytest.mark.parametrize("domain", [XQMXDomain.SPIN, XQMXDomain.BINARY])
def test_encode_decode_roundtrip_matches_brute_force(domain: XQMXDomain) -> None:
    """Encoding then decoding the optimal spin vector reproduces the optimum.

    Builds a small model, encodes it onto a path topology, then constructs the
    spin vector for the brute-force optimum over the submitted graph and decodes
    it back.
    The decoded sample must equal the optimum and recompute the same energy --
    proving encode/decode index alignment is self-consistent.
    """
    model = XQMX.spin_model(3) if domain == XQMXDomain.SPIN else XQMX.binary_model(3)
    model.set_linear(0, -1)
    model.set_linear(1, 2)
    model.set_linear(2, -1)
    model.set_quadratic(0, 1, -2)
    model.set_quadratic(1, 2, 3)

    job = model_to_ising(model, PATH5)
    optimum, optimum_energy = _brute_force_optimum(model)

    # Build the per-node spin vector the miner would return for the optimum.
    spin_vector = [-1] * job.topology.num_nodes
    for var, node in job.mapping.items():
        value = optimum[var]
        spin = value if domain == XQMXDomain.SPIN else (2 * value - 1)
        spin_vector[job.topology.index_of(node)] = spin

    decoded = decode_solution(job, spin_vector, model)
    assert {var: decoded.get_linear(var) for var in range(model.size)} == optimum
    assert compute_energy(model, decoded) == optimum_energy


@pytest.mark.parametrize("domain", [XQMXDomain.SPIN, XQMXDomain.BINARY])
def test_encoded_problem_argmin_decodes_to_optimum(domain: XQMXDomain) -> None:
    """The encoded problem's argmin decodes to the original model's optimum.

    This exercises the production placement, milli-quantized encoding, and
    decoding paths. The binary model is the QUI-848 TestNet ``-1`` regression:
    its true optimum is ``-2`` despite a live heuristic fleet returning ``-1``.
    """
    model = XQMX.spin_model(3) if domain == XQMXDomain.SPIN else XQMX.binary_model(3)
    if domain == XQMXDomain.SPIN:
        model.set_linear(0, 1)
        model.set_linear(1, -2)
        model.set_linear(2, 3)
        model.set_quadratic(0, 1, 1)
        model.set_quadratic(1, 2, -1)
    else:
        model.set_linear(0, -1)
        model.set_linear(1, 2)
        model.set_quadratic(0, 1, -3)
        model.set_quadratic(1, 2, 1)

    job = model_to_ising(model, PATH5)
    argmin_vector: list[int] | None = None
    argmin_energy: int | None = None
    for spins in itertools.product((-1, 1), repeat=model.size):
        vector = [-1] * job.topology.num_nodes
        for var, node in job.mapping.items():
            vector[job.topology.index_of(node)] = spins[var]
        energy = ising_energy_milli(job, vector)
        if argmin_energy is None or energy < argmin_energy:
            argmin_energy = energy
            argmin_vector = vector

    assert argmin_vector is not None
    decoded = decode_solution(job, argmin_vector, model)
    assert compute_energy(model, decoded) == _brute_force_optimum(model)[1]


# ---------------------------------------------------------------------------
# Lifecycle expiry math (mirrors pallet lifecycle.rs)
# ---------------------------------------------------------------------------


class TestExpiry:
    """effective_expiry and is_final mirror the on-chain lazy lifecycle."""

    def test_hard_deadline_without_solution(self) -> None:
        """With no first solution, expiry is created_at + deadline_blocks."""
        assert effective_expiry(100, None, 50, 10) == 150

    def test_block_wait_tightens_deadline(self) -> None:
        """A first solution tightens expiry to first + block_wait when sooner."""
        assert effective_expiry(100, 120, 50, 10) == 130

    def test_hard_deadline_wins_when_sooner(self) -> None:
        """The hard deadline still caps a late first solution's wait window."""
        assert effective_expiry(100, 145, 50, 10) == 150

    def test_expiry_boundary(self) -> None:
        """At exactly first + block_wait, that window is the binding expiry."""
        assert effective_expiry(100, 100, 50, 10) == 110

    def test_is_final_terminal_status_short_circuits(self) -> None:
        """Expired/Closed are final regardless of block height."""
        assert is_final("Expired", 0, 10**9) is True
        assert is_final("Closed", 0, 10**9) is True

    def test_is_final_by_height(self) -> None:
        """An Opened order is final once the block reaches the expiry."""
        assert is_final("Opened", 99, 100) is False
        assert is_final("Opened", 100, 100) is True
        assert is_final("Opened", 101, 100) is True


def test_default_spec_id_constant() -> None:
    """The genesis default Ising spec id is the well-known 0x-prefixed hash."""
    assert DEFAULT_ISING_SPEC_ID.startswith("0x")
    assert len(DEFAULT_ISING_SPEC_ID) == 66


class TestIsingEnergyMilli:
    """The chain-energy canary helper matches the authoritative energy for SPIN."""

    def test_matches_compute_energy_times_milli(self) -> None:
        """For a SPIN model, ising_energy_milli == compute_energy * MILLI_SCALE."""
        model = XQMX.spin_model(2)
        model.set_linear(0, 1)
        model.set_linear(1, -2)
        model.set_quadratic(0, 1, 1)
        job = model_to_ising(model, PATH5)
        for var0, var1 in itertools.product((-1, 1), repeat=2):
            vector = [1] * job.topology.num_nodes
            vector[job.topology.index_of(job.mapping[0])] = var0
            vector[job.topology.index_of(job.mapping[1])] = var1
            sample = decode_solution(job, vector, model)
            assert ising_energy_milli(job, vector) == int(compute_energy(model, sample)) * MILLI_SCALE

    def test_rejects_wrong_length_vector(self) -> None:
        """A vector that does not span the topology is rejected."""
        model = XQMX.spin_model(2)
        model.set_quadratic(0, 1, 1)
        job = model_to_ising(model, PATH5)
        with pytest.raises(EncodingError, match="does not match topology"):
            ising_energy_milli(job, [1] * (job.topology.num_nodes + 1))


# ---------------------------------------------------------------------------
# Allowed-value sets (coefficient guard, advisory only)
# ---------------------------------------------------------------------------


class TestAllowedValues:
    """Membership and chain-spec parsing for the allowed-value constraint."""

    def test_set_membership(self) -> None:
        allowed = AllowedValues(members=frozenset({-1000, 0, 1000}))
        assert allowed.contains(1000)
        assert allowed.contains(0)
        assert not allowed.contains(500)

    def test_inclusive_range_membership(self) -> None:
        allowed = AllowedValues(bounds=(-2000, 2000))
        assert allowed.contains(-2000)
        assert allowed.contains(2000)
        assert not allowed.contains(2001)

    def test_unconstrained_allows_everything(self) -> None:
        assert AllowedValues().contains(10**9)

    def test_rejects_members_and_bounds_together(self) -> None:
        """A spec is a Set OR a range, never both (all-None stays allowed)."""
        with pytest.raises(EncodingError, match="not both"):
            AllowedValues(members=frozenset({0}), bounds=(-1, 1))

    def test_from_chain_spec_set_forms(self) -> None:
        assert AllowedValues.from_chain_spec({"Set": [-1000, 1000]}).members == frozenset({-1000, 1000})
        assert AllowedValues.from_chain_spec([0, 1000]).members == frozenset({0, 1000})

    def test_from_chain_spec_range_forms(self) -> None:
        assert AllowedValues.from_chain_spec({"IntegerRange": {"min": -1000, "max": 1000}}).bounds == (-1000, 1000)
        assert AllowedValues.from_chain_spec({"ContinuousRange": [-5, 5]}).bounds == (-5, 5)

    def test_from_chain_spec_unknown_or_absent_is_none(self) -> None:
        assert AllowedValues.from_chain_spec(None) is None
        assert AllowedValues.from_chain_spec({"Mystery": 1}) is None
        assert AllowedValues.from_chain_spec({"Set": 1, "extra": 2}) is None


class TestCheckAllowedValues:
    """check_allowed_values flags out-of-spec coefficients without raising."""

    @staticmethod
    def _job_with_h2_and_coupling() -> IsingJob:
        # h[0]=1 (in {-1,0,1}), h[1]=2 (OUT), coupling (0,1)=1 (in {-1,1}).
        model = XQMX.spin_model(2)
        model.set_linear(0, 1)
        model.set_linear(1, 2)
        model.set_quadratic(0, 1, 1)
        return model_to_ising(model, PATH5)

    def test_no_offenders_when_all_in_set(self) -> None:
        model = XQMX.spin_model(2)
        model.set_linear(0, 1)
        model.set_quadratic(0, 1, 1)
        job = model_to_ising(model, PATH5)
        offenders = check_allowed_values(
            job,
            allowed_h=AllowedValues(members=frozenset({-1000, 0, 1000})),
            allowed_j=AllowedValues(members=frozenset({-1000, 1000})),
        )
        assert offenders == []

    def test_flags_out_of_spec_field(self) -> None:
        job = self._job_with_h2_and_coupling()
        offenders = check_allowed_values(
            job,
            allowed_h=AllowedValues(members=frozenset({-1000, 0, 1000})),
            allowed_j=AllowedValues(members=frozenset({-1000, 1000})),
        )
        # Exactly the h=2 field (milli 2000) is out of spec; the coupling is fine.
        assert [(kind, milli) for kind, _position, milli in offenders] == [("h", 2 * MILLI_SCALE)]

    def test_unknown_sets_return_empty(self) -> None:
        job = self._job_with_h2_and_coupling()
        assert check_allowed_values(job, allowed_h=None, allowed_j=None) == []

    def test_zero_entries_not_flagged(self) -> None:
        # The full-topology arrays are zero-filled; with a coupling set that
        # excludes 0, the unused edges must NOT be reported as offenders.
        job = self._job_with_h2_and_coupling()
        offenders = check_allowed_values(
            job,
            allowed_h=None,
            allowed_j=AllowedValues(members=frozenset({-1000, 1000})),
        )
        assert offenders == []  # only the single in-spec coupling is non-zero


def test_i32_overflow_error_carries_doc_link() -> None:
    """The i32-overflow EncodingError embeds the coefficient-encoding doc URL."""
    over = XQMX.spin_model(1)
    over.set_linear(0, MAX_NATURAL_COEFFICIENT + 1)
    with pytest.raises(EncodingError) as excinfo:
        model_to_ising(over, PATH5)
    assert QUIP_COEFFICIENTS_DOC_URL in str(excinfo.value)


# ---------------------------------------------------------------------------
# SolverQuip construction (mocked: no real chain, signer, or build required)
# ---------------------------------------------------------------------------

# A valid 32-byte seed so the real quip_signer (when built) accepts it; the
# mock accepts anything.
VALID_SEED = "0x" + "01" * 32
UNIT = 1_000_000_000_000  # 1 tQUIP in planck (chain MinReward default).
# Stand-in for a deployment's QuantumPow.DefaultTopology. The real hash is
# per-deployment and read from the chain; nothing is pinned in the codebase.
DEFAULT_TOPOLOGY_HASH = "0x" + "cb" * 32

# A topology carrying allowed-value sets, so check_allowed_values has data.
TOPO_WITH_SETS = Topology.of(
    range(5),
    [(0, 1), (1, 2), (2, 3), (3, 4)],
    allowed_h=AllowedValues(members=frozenset({-MILLI_SCALE, 0, MILLI_SCALE})),
    allowed_j=AllowedValues(members=frozenset({-MILLI_SCALE, MILLI_SCALE})),
)


class _Const:
    def __init__(self, value: object) -> None:
        self.value = value


class _StorageEntry:
    def __init__(self, value: object) -> None:
        self.value = value


class FakeSubstrate:
    """A minimal stand-in for SubstrateInterface: constants + single-entry storage.

    ``query`` ignores its key params (these tests configure one entry per
    storage map), so a non-hashable mock AccountId is fine.
    """

    def __init__(
        self,
        *,
        constants: dict | None = None,
        storage: dict | None = None,
        events: list | None = None,
        maps: dict | None = None,
        head: int = 0,
        init_runtime_raises: Exception | None = None,
        get_constant_raises: Exception | None = None,
    ) -> None:
        self.constants = dict(constants or {})
        self.storage = dict(storage or {})
        self.events = list(events or [])
        self.maps = dict(maps or {})
        self.head = head
        self._init_runtime_raises = init_runtime_raises
        self._get_constant_raises = get_constant_raises

    def init_runtime(self, block_hash: str | None = None, block_id: int | None = None) -> None:
        # SolverQuip resolves metadata explicitly at construction; the real
        # method is where an undecodable-metadata failure surfaces.
        if self._init_runtime_raises is not None:
            raise self._init_runtime_raises

    def get_constant(self, module: str, name: str):
        if self._get_constant_raises is not None:
            raise self._get_constant_raises
        # Real SubstrateInterface.get_constant returns None for a constant absent
        # from the runtime metadata (it only raises on a transport/decode fault).
        if (module, name) not in self.constants:
            return None
        return _Const(self.constants[(module, name)])

    def query(self, module: str, name: str, params: list | None = None):
        return _StorageEntry(self.storage.get((module, name)))

    def query_map(self, module: str, name: str, params: list | None = None):
        return list(self.maps.get((module, name), []))

    def get_block_header(self, block_hash: str | None = None, ignore_decoding_errors: bool = False):
        return {"header": {"number": self.head}}

    def get_events(self, block_hash: str | None = None):
        return self.events

    def close(self) -> None:
        pass


def _job_proposed_event(order_id: int, *, attrs_form: str = "mapping") -> dict:
    """Build a synthetic decoded JobProposed event record (substrate-interface shape).

    ``attrs_form`` selects the attribute encoding to exercise the defensive
    parser: ``"mapping"`` (field-keyed dict), ``"params"`` (name/value list), or
    ``"positional"`` (bare value list, order_id first).
    """
    if attrs_form == "params":
        attributes: object = [{"name": "order_id", "value": order_id}, {"name": "reward", "value": 1}]
    elif attrs_form == "positional":
        attributes = [order_id, "0xspec", "0xproposer"]
    else:
        attributes = {"order_id": order_id, "spec_id": "0xspec", "reward": 1}
    return {"event": {"module_id": "QuantumComputeMempool", "event_id": "JobProposed", "attributes": attributes}}


def _fake_substrate_module(iface: object, *, raises: Exception | None = None) -> types.ModuleType:
    module = types.ModuleType("substrateinterface")

    class _FakeSubstrateInterface:
        """A class, not a factory: ``quip_metadata`` subclasses whatever this is.

        ``__new__`` hands back the preconfigured ``iface`` rather than a fresh
        instance, so ``solver._iface`` stays the ``FakeSubstrate`` the test
        configured and asserts against.
        """

        def __new__(cls, url: str | None = None, **kwargs):
            if raises is not None:
                raise raises
            return iface

    module.SubstrateInterface = _FakeSubstrateInterface  # type: ignore[attr-defined]
    return module


def _fake_quip_signer() -> types.ModuleType:
    module = types.ModuleType("quip_signer")
    signer = MagicMock(name="HybridSigner")
    signer.account_id = b"\x00" * 32
    hybrid = MagicMock()
    hybrid.from_seed = MagicMock(return_value=signer)
    module.HybridSigner = hybrid  # type: ignore[attr-defined]
    module.verify_envelope = MagicMock(return_value=True)  # type: ignore[attr-defined]
    return module


def _default_iface(*, with_default_spec_const: bool = False, balance: int | None = None) -> FakeSubstrate:
    constants: dict = {("QuantumComputeMempool", "MinReward"): UNIT}
    if with_default_spec_const:
        constants[("QuantumComputeMempool", "DefaultIsingSpecId")] = DEFAULT_ISING_SPEC_ID
    storage: dict = {
        ("QuantumComputeMempool", "JobSpecs"): {"spec": "ok"},
        ("QuantumPow", "DefaultTopology"): DEFAULT_TOPOLOGY_HASH,
    }
    if balance is not None:
        storage[("System", "Account")] = {"data": {"free": balance}}
    return FakeSubstrate(constants=constants, storage=storage)


def _install(monkeypatch, iface: object, *, substrate_raises: Exception | None = None) -> None:
    monkeypatch.setitem(sys.modules, "substrateinterface", _fake_substrate_module(iface, raises=substrate_raises))
    monkeypatch.setitem(sys.modules, "quip_signer", _fake_quip_signer())
    # The rest of the [quip] extra: connect imports certifi for wss:// URLs.
    certifi = types.ModuleType("certifi")
    certifi.where = lambda: "/fake/cacert.pem"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "certifi", certifi)


def _clear_quip_env(monkeypatch) -> None:
    for name in ("QUIP_RPC_URL", "QUIP_SIGNER_SEED", "QUIP_KEYSTORE", "QUIP_REWARD", "QUIP_TOPOLOGY"):
        monkeypatch.delenv(name, raising=False)


def _make_solver(monkeypatch, *, iface: FakeSubstrate | None = None, **kwargs):
    from xqsa.quip import SolverQuip

    resolved = iface if iface is not None else _default_iface()
    _install(monkeypatch, resolved)
    _clear_quip_env(monkeypatch)
    kwargs.setdefault("url", "ws://fake:9944")
    kwargs.setdefault("seed", VALID_SEED)
    return SolverQuip(**kwargs)


class TestSolverQuipGuards:
    """The two lazy-import guards report distinct, actionable messages."""

    def test_missing_substrate_interface(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "substrateinterface", None)
        from xqsa.quip import SolverQuip

        with pytest.raises(ImportError, match=r"xqsa\[quip\]"):
            SolverQuip(url="ws://fake", seed=VALID_SEED)

    def test_missing_quip_signer(self, monkeypatch) -> None:
        # substrate-interface imports fine; the signer extension does not.
        monkeypatch.setitem(sys.modules, "substrateinterface", _fake_substrate_module(_default_iface()))
        monkeypatch.setitem(sys.modules, "quip_signer", None)
        from xqsa.quip import SolverQuip

        with pytest.raises(ImportError, match="quip_signer signing extension"):
            SolverQuip(url="ws://fake", seed=VALID_SEED)


class TestSolverQuipConstruction:
    """Constructor configuration resolution against a mocked chain."""

    def test_happy_path(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        assert solver._url == "ws://fake:9944"
        assert solver._spec_id == DEFAULT_ISING_SPEC_ID
        assert solver._reward == UNIT

    def test_env_resolution(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_RPC_URL", "ws://env-node:9944")
        monkeypatch.setenv("QUIP_SIGNER_SEED", VALID_SEED)
        solver = SolverQuip()
        assert solver._url == "ws://env-node:9944"

    def test_missing_url_raises(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        with pytest.raises(ValueError, match="RPC URL"):
            SolverQuip(seed=VALID_SEED)

    def test_missing_signer_raises(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        with pytest.raises(ValueError, match="signer is required"):
            SolverQuip(url="ws://fake")

    def test_connection_failure_wrapped(self, monkeypatch) -> None:
        from xqsa.quip import QuipConnectionError, SolverQuip

        _install(monkeypatch, _default_iface(), substrate_raises=OSError("refused"))
        _clear_quip_env(monkeypatch)
        with pytest.raises(QuipConnectionError, match="could not connect"):
            SolverQuip(url="ws://down", seed=VALID_SEED)

    def test_spec_id_uses_chain_constant(self, monkeypatch) -> None:
        custom_spec = "0x" + "ab" * 32
        iface = _default_iface(with_default_spec_const=True)
        iface.constants[("QuantumComputeMempool", "DefaultIsingSpecId")] = custom_spec
        solver = _make_solver(monkeypatch, iface=iface)
        assert solver._spec_id == custom_spec

    def test_spec_id_explicit_arg_wins(self, monkeypatch) -> None:
        explicit = "0x" + "cd" * 32
        solver = _make_solver(monkeypatch, spec_id=explicit)
        assert solver._spec_id == explicit

    def test_spec_id_not_registered_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipConnectionError, SolverQuip

        iface = FakeSubstrate(constants={("QuantumComputeMempool", "MinReward"): UNIT}, storage={})
        _install(monkeypatch, iface)
        _clear_quip_env(monkeypatch)
        with pytest.raises(QuipConnectionError, match="not registered"):
            SolverQuip(url="ws://fake", seed=VALID_SEED)

    def test_reward_explicit_arg_wins(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, reward=7 * UNIT)
        assert solver._reward == 7 * UNIT

    def test_reward_from_env(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_REWARD", str(3 * UNIT))
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._reward == 3 * UNIT

    def test_reward_defaults_to_min_reward(self, monkeypatch) -> None:
        # No reward arg, no QUIP_REWARD -> chain MinReward constant.
        assert _make_solver(monkeypatch)._reward == UNIT

    def test_topology_explicit_arg_beats_env(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        explicit = "0x" + "a1" * 32
        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", "0x" + "b2" * 32)
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED, topology=explicit)
        assert solver._topology_hash == explicit

    def test_for_network_uses_preset_rpc(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip
        from xqsa.quip_networks import NETWORKS

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        solver = SolverQuip.for_network("aglais", seed=VALID_SEED)
        assert solver._url == NETWORKS["aglais"].rpc

    def test_for_network_beats_env_url(self, monkeypatch) -> None:
        # The preset enters as url=, so an exported QUIP_RPC_URL cannot redirect it.
        from xqsa.quip import SolverQuip
        from xqsa.quip_networks import NETWORKS

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_RPC_URL", "ws://env-node:9944")
        solver = SolverQuip.for_network("aglais", seed=VALID_SEED)
        assert solver._url == NETWORKS["aglais"].rpc

    def test_for_network_passes_kwargs_through(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip
        from xqsa.quip_networks import NETWORKS

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        solver = SolverQuip.for_network("devnet", seed=VALID_SEED, reward=7 * UNIT)
        assert solver._url == NETWORKS["devnet"].rpc
        assert solver._reward == 7 * UNIT

    def test_for_network_unknown_name_raises_before_connecting(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface(), substrate_raises=AssertionError("must not connect"))
        _clear_quip_env(monkeypatch)
        with pytest.raises(ValueError, match="aglais, devnet"):
            SolverQuip.for_network("mainnet", seed=VALID_SEED)

    def test_for_network_rejects_url(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        with pytest.raises(TypeError):
            SolverQuip.for_network("aglais", url="ws://x", seed=VALID_SEED)

    def test_topology_from_env(self, monkeypatch) -> None:
        # QUIP_TOPOLOGY displaces the chain default. Constructed directly rather
        # than through _make_solver so no topology kwarg pre-empts the env read.
        from xqsa.quip import SolverQuip

        override = "0x" + "b2" * 32
        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", override)
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._topology_hash == override

    def test_topology_empty_env_falls_through(self, monkeypatch) -> None:
        # Truthiness check, matching _resolve_reward: an empty QUIP_TOPOLOGY is
        # not an override, so the chain default still wins.
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", "")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._topology_hash == DEFAULT_TOPOLOGY_HASH

    def test_topology_defaults_to_chain_default(self, monkeypatch) -> None:
        # No topology arg and no QUIP_TOPOLOGY -> chain QuantumPow.DefaultTopology.
        # The hash is deployment-specific; nothing is pinned in the codebase.
        chain_default = "0x" + "e6" * 32
        iface = _default_iface()
        iface.storage[("QuantumPow", "DefaultTopology")] = chain_default
        solver = _make_solver(monkeypatch, iface=iface)
        assert solver._topology_hash == chain_default

    def test_topology_unresolvable_raises(self, monkeypatch) -> None:
        # No topology arg and no chain default -> a clear error. There is no
        # third source to fall through to: a hash is per-deployment, so any
        # constant here would be one no other deployment would accept.
        iface = _default_iface()
        del iface.storage[("QuantumPow", "DefaultTopology")]
        with pytest.raises(ValueError, match="no topology hash available"):
            _make_solver(monkeypatch, iface=iface)

    @pytest.mark.parametrize(
        "bad",
        [
            "0x" + "b2" * 31 + "b",  # a dropped nibble: odd length, right-ish width
            "0x" + "b2" * 31,  # well-formed hex, one byte short
            "0xnothex" + "0" * 57,  # right width, not hex
        ],
    )
    def test_malformed_env_topology_raises_at_construction(self, monkeypatch, bad) -> None:
        # A typo in an ambient variable must fail here, naming QUIP_TOPOLOGY,
        # rather than at the first solve() as a raw scalecodec exception from
        # encoding the storage key -- which no except Quip*Error clause catches
        # and which names nothing. Surrounding whitespace is a separate case --
        # it is stripped, not rejected, and the test below pins that.
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", bad)
        with pytest.raises(ValueError, match="QUIP_TOPOLOGY"):
            SolverQuip(url="ws://fake", seed=VALID_SEED)

    def test_env_topology_whitespace_is_stripped(self, monkeypatch) -> None:
        # Stripped, not rejected: a hash pasted out of a terminal or a config
        # file routinely carries surrounding whitespace and the intent is
        # unambiguous.
        from xqsa.quip import SolverQuip

        override = "0x" + "b2" * 32
        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", f"  {override}\t")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._topology_hash == override

    def test_malformed_topology_arg_raises_at_construction(self, monkeypatch) -> None:
        # The topology= argument routes through the same guard, and the error
        # names the argument rather than the environment variable.
        with pytest.raises(ValueError, match="topology="):
            _make_solver(monkeypatch, topology="0xdeadbeef")

    def test_malformed_spec_id_raises_at_construction(self, monkeypatch) -> None:
        # Same hole one function up: _resolve_spec_id hands its id straight to
        # an unwrapped JobSpecs lookup, so a malformed one never reaches the
        # "not registered on-chain" branch that would have explained itself.
        with pytest.raises(ValueError, match="spec_id"):
            _make_solver(monkeypatch, spec_id="0xdeadbeef")

    def test_topology_explicit_arg_wins(self, monkeypatch) -> None:
        explicit = "0x" + "ab" * 32
        iface = _default_iface()
        iface.storage[("QuantumPow", "DefaultTopology")] = "0x" + "e6" * 32
        solver = _make_solver(monkeypatch, iface=iface, topology=explicit)
        assert solver._topology_hash == explicit

    def test_chain_default_topology_transport_error_raises(self, monkeypatch) -> None:
        # A transient read fault must NOT be masked as "no default configured":
        # construction would then raise a topology error that blames the chain
        # for declaring no default, when the read simply never landed.
        from xqsa.quip import QuipConnectionError

        solver = _make_solver(monkeypatch, topology="0x" + "ab" * 32)

        def _boom(module, name, params=None):
            raise RuntimeError("websocket closed")

        solver._iface.query = _boom
        with pytest.raises(QuipConnectionError, match="DefaultTopology"):
            solver._chain_default_topology()

    def test_chain_default_topology_absent_falls_back(self, monkeypatch) -> None:
        # A runtime that genuinely lacks the storage item resolves to None, which
        # the caller reports as "no chain default" rather than as a fault.
        solver = _make_solver(monkeypatch, topology="0x" + "ab" * 32)

        exc_module = types.ModuleType("substrateinterface.exceptions")

        class StorageFunctionNotFound(Exception):
            pass

        exc_module.StorageFunctionNotFound = StorageFunctionNotFound
        monkeypatch.setitem(sys.modules, "substrateinterface.exceptions", exc_module)

        def _absent(module, name, params=None):
            raise StorageFunctionNotFound("QuantumPow.DefaultTopology not in metadata")

        solver._iface.query = _absent
        assert solver._chain_default_topology() is None

    def test_chain_default_spec_id_transport_error_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipConnectionError

        solver = _make_solver(monkeypatch, spec_id=DEFAULT_ISING_SPEC_ID, topology="0x" + "ab" * 32)

        def _boom(module, name):
            raise RuntimeError("rpc timeout")

        solver._iface.get_constant = _boom
        with pytest.raises(QuipConnectionError, match="DefaultIsingSpecId"):
            solver._chain_default_spec_id()


class TestSolverQuipMetadataErrors:
    """QuipMetadataError reaches the caller instead of being renamed en route.

    Every chain read in the constructor wraps its faults with a message about
    the pallet item it was reading. Undecodable metadata is not that fault, and
    it has a different remedy (a runtime that still serves V14), so it must keep
    its own type and message rather than arrive as "could not read
    QuantumComputeMempool.DefaultIsingSpecId".
    """

    def test_construction_surfaces_it_from_init_runtime(self, monkeypatch) -> None:
        from xqsa.quip import QuipMetadataError

        iface = _default_iface()
        iface._init_runtime_raises = QuipMetadataError("serves V16 runtime metadata")
        with pytest.raises(QuipMetadataError, match="serves V16"):
            _make_solver(monkeypatch, iface=iface)

    def test_construction_surfaces_it_from_a_constant_read(self, monkeypatch) -> None:
        # Belt and braces: init_runtime re-runs on a runtime upgrade mid-session,
        # so a later read can be the first to hit it.
        from xqsa.quip import QuipMetadataError

        iface = _default_iface()
        iface._get_constant_raises = QuipMetadataError("serves V16 runtime metadata")
        with pytest.raises(QuipMetadataError, match="serves V16"):
            _make_solver(monkeypatch, iface=iface)

    def test_an_ordinary_fault_still_becomes_a_connection_error(self, monkeypatch) -> None:
        from xqsa.quip import QuipConnectionError

        iface = _default_iface()
        iface._init_runtime_raises = RuntimeError("rpc timeout")
        with pytest.raises(QuipConnectionError, match="could not connect"):
            _make_solver(monkeypatch, iface=iface)

    def test_storage_reads_propagate_it_unchanged(self, monkeypatch) -> None:
        from xqsa.quip import QuipMetadataError

        solver = _make_solver(monkeypatch, spec_id=DEFAULT_ISING_SPEC_ID, topology="0x" + "ab" * 32)

        def _undecodable(module, name, params=None):
            raise QuipMetadataError("serves V16 runtime metadata")

        solver._iface.query = _undecodable
        solver._iface.query_map = _undecodable
        with pytest.raises(QuipMetadataError, match="serves V16"):
            solver._chain_default_topology()
        with pytest.raises(QuipMetadataError, match="serves V16"):
            solver._mineable_topologies()


class TestSolverQuipChainReads:
    """_fetch_topology and _check_balance against the mocked chain."""

    def test_fetch_topology_decodes_and_caches(self, monkeypatch) -> None:
        topo_hash = "0x" + "ab" * 32
        iface = _default_iface()
        iface.storage[("QuantumPow", "RegisteredTopologies")] = {
            "nodes": [0, 1, 2],
            "edges": [(0, 1)],
            "allowed_h_values": {"Set": [-MILLI_SCALE, 0, MILLI_SCALE]},
            "allowed_j_values": {"Set": [-MILLI_SCALE, MILLI_SCALE]},
            "allowed_spin_values": {"Set": [-1, 1]},
        }
        solver = _make_solver(monkeypatch, iface=iface, topology=topo_hash)
        topo = solver._fetch_topology()
        assert topo.nodes == (0, 1, 2)
        assert topo.allowed_h is not None and topo.allowed_h.contains(MILLI_SCALE)
        assert solver._fetch_topology() is topo  # cached by hash

    def test_fetch_topology_unregistered_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipConnectionError

        solver = _make_solver(monkeypatch, topology="0x" + "ee" * 32)
        with pytest.raises(QuipConnectionError, match="not registered"):
            solver._fetch_topology()

    def test_fetch_topology_no_hash_raises(self, monkeypatch) -> None:
        # The guard still fires when no hash is resolvable (pin cleared, no override).
        solver = _make_solver(monkeypatch)
        solver._topology_hash = None
        with pytest.raises(ValueError, match="no topology hash"):
            solver._fetch_topology()

    def test_check_balance_ok(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_default_iface(balance=5 * UNIT))
        assert solver._check_balance(UNIT) == 5 * UNIT

    def test_check_balance_insufficient(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch, iface=_default_iface(balance=0))
        with pytest.raises(QuipSubmissionError, match="insufficient balance"):
            solver._check_balance(UNIT)

    def test_check_balance_unreadable_raises_connection_error(self, monkeypatch) -> None:
        # A missing/undecodable System.Account read is a chain-read fault, not a
        # zero balance -- it must raise QuipConnectionError rather than blame a
        # possibly-funded account for "insufficient balance".
        from xqsa.quip import QuipConnectionError

        solver = _make_solver(monkeypatch, iface=_default_iface())  # no System.Account entry
        with pytest.raises(QuipConnectionError, match="could not read the free balance"):
            solver._check_balance(UNIT)


def _install_storage_absent_exc(monkeypatch):
    """Install a fake ``substrateinterface.exceptions`` and return its exception.

    Mirrors ``test_chain_default_topology_absent_falls_back``: ``_is_storage_absent``
    imports ``StorageFunctionNotFound`` lazily, so a stand-in class must be
    reachable for the absence-vs-fault split to classify an absent storage item.
    """
    exc_module = types.ModuleType("substrateinterface.exceptions")

    class StorageFunctionNotFound(Exception):
        pass

    exc_module.StorageFunctionNotFound = StorageFunctionNotFound
    monkeypatch.setitem(sys.modules, "substrateinterface.exceptions", exc_module)
    return StorageFunctionNotFound


class TestSolverQuipMineableTopology:
    """_mineable_topologies as a chain reader. Nothing on the solve path gates on it."""

    def test_present_and_matches_passes(self, monkeypatch) -> None:
        iface = _default_iface()
        iface.maps[("QuantumPow", "MineableTopologies")] = [(TOPO_HASH, ())]
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        assert solver._mineable_topologies() == frozenset({"ab" * 32})

    def test_membership_is_case_and_prefix_insensitive(self, monkeypatch) -> None:
        # The chain may hand back the key as upper-case / bytes; the reader must
        # normalise, so a caller can compare on canonical hex.
        iface = _default_iface()
        iface.maps[("QuantumPow", "MineableTopologies")] = [(bytes.fromhex("ab" * 32), ())]
        solver = _make_solver(monkeypatch, iface=iface, topology="0x" + "AB" * 32)
        assert solver._mineable_topologies() == frozenset({"ab" * 32})

    def test_present_but_empty_reads_as_empty(self, monkeypatch) -> None:
        # A defined-but-empty map (present in metadata, zero entries) is an empty
        # frozenset, distinct from the None of a runtime-absent storage item.
        solver = _make_solver(monkeypatch, topology=TOPO_HASH)  # no maps entry -> empty map
        assert solver._mineable_topologies() == frozenset()

    def test_absent_storage_item_reads_as_none(self, monkeypatch) -> None:
        # The runtime genuinely lacks the storage item -> None.
        storage_absent = _install_storage_absent_exc(monkeypatch)
        solver = _make_solver(monkeypatch, topology=TOPO_HASH)

        def _absent(module, name, params=None):
            raise storage_absent("QuantumPow.MineableTopologies not in metadata")

        solver._iface.query_map = _absent
        assert solver._mineable_topologies() is None

    def test_transport_fault_raises_connection_error(self, monkeypatch) -> None:
        # A transient read fault must NOT be masked as "unset"; it surfaces as
        # QuipConnectionError, like _chain_default_topology.
        from xqsa.quip import QuipConnectionError

        solver = _make_solver(monkeypatch, topology=TOPO_HASH)

        def _boom(module, name, params=None):
            raise RuntimeError("websocket closed")

        solver._iface.query_map = _boom
        with pytest.raises(QuipConnectionError, match="MineableTopologies"):
            solver._mineable_topologies()

    def test_populated_but_undecodable_raises(self, monkeypatch) -> None:
        # Entries present but none decode to a canonical hash is a fault, not an
        # empty set: surface QuipConnectionError instead of masquerading as unset
        # (which would skip the check). An int key -> _canonical_hex returns None.
        from xqsa.quip import QuipConnectionError

        iface = _default_iface()
        iface.maps[("QuantumPow", "MineableTopologies")] = [(12345, ())]
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        with pytest.raises(QuipConnectionError, match="none decoded"):
            solver._mineable_topologies()


class TestSolverQuipAllowedValueWarning:
    """The educational allowed-value warning is advisory and fires at most once."""

    @staticmethod
    def _out_of_spec_job() -> IsingJob:
        model = XQMX.spin_model(2)
        model.set_linear(0, 1)
        model.set_linear(1, 2)  # milli 2000, outside {-1000, 0, 1000}
        model.set_quadratic(0, 1, 1)
        return model_to_ising(model, TOPO_WITH_SETS)

    def test_warns_once(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        job = self._out_of_spec_job()
        with pytest.warns(UserWarning) as record:
            solver._maybe_warn_allowed_values(job)
            solver._maybe_warn_allowed_values(job)  # suppressed by the one-time flag
        assert len(record) == 1
        assert QUIP_COEFFICIENTS_DOC_URL in str(record[0].message)

    def test_warns_on_a_topology_decoded_from_the_chain(self, monkeypatch) -> None:
        # The decode path from a chain-shaped meta dict through to the warning,
        # which nothing covered before: reading a key the meta does not carry
        # yields None and disables the warning with no error to notice. This
        # fixture spells the same literals as quip_codec, so it catches a
        # one-sided edit here, NOT a pallet-side rename -- only the chain can
        # witness that. test_allowed_value_spec_names_match_the_pallet in
        # test_quip_live.py is the guard for that half.
        topo_hash = "0x" + "ab" * 32
        iface = _default_iface()
        iface.storage[("QuantumPow", "RegisteredTopologies")] = {
            "nodes": [0, 1, 2],
            "edges": [(0, 1)],
            "allowed_h_values": {"Set": [-MILLI_SCALE, 0, MILLI_SCALE]},
            "allowed_j_values": {"Set": [-MILLI_SCALE, MILLI_SCALE]},
        }
        solver = _make_solver(monkeypatch, iface=iface, topology=topo_hash)
        topo = solver._fetch_topology()
        assert topo.allowed_h is not None
        model = XQMX.spin_model(2)
        model.set_linear(0, 2)  # milli 2000, outside {-1000, 0, 1000}
        model.set_quadratic(0, 1, 1)
        with pytest.warns(UserWarning, match="allowed"):
            solver._maybe_warn_allowed_values(model_to_ising(model, topo))

    def test_no_warning_when_in_spec(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        model = XQMX.spin_model(2)
        model.set_linear(0, 1)
        model.set_quadratic(0, 1, 1)
        job = model_to_ising(model, TOPO_WITH_SETS)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning would raise
            solver._maybe_warn_allowed_values(job)


def _patch_signing(monkeypatch, solver, *, receipt=None, build_raises: Exception | None = None) -> dict:
    """Stub the quip_signing layer on ``solver`` and capture the submitted call.

    ``build_signed_extrinsic`` records ``(call_module, call_function, call_params)``
    (raising ``build_raises`` if given) and ``submit_and_watch`` returns ``receipt``,
    so submission tests never touch real crypto or a chain. ``receipt`` may be a
    receipt or a callable ``(call_function) -> receipt`` to vary the outcome per
    call (e.g. a successful propose followed by a failing reclaim).
    """
    captured: dict = {}
    qs = solver._quip_signing

    def fake_build(iface, signer, call_module, call_function, call_params):
        captured.update(iface=iface, call_module=call_module, call_function=call_function, call_params=call_params)
        if build_raises is not None:
            raise build_raises
        return b"\x00\x01", "0xext"

    def fake_submit(iface, wire_bytes, ext_hash, wait_for="inblock"):
        captured["wait_for"] = wait_for
        return receipt(captured["call_function"]) if callable(receipt) else receipt

    monkeypatch.setattr(qs, "build_signed_extrinsic", fake_build)
    monkeypatch.setattr(qs, "submit_and_watch", fake_submit)
    return captured


def _ok_receipt(solver, *, error: str | None = None):
    return solver._quip_signing.ExtrinsicReceipt(
        extrinsic_hash="0xext", block_hash="0xblock", is_finalized=False, error=error
    )


class TestSolverQuipSubmission:
    """_propose_job / _submit_extrinsic / _wrap_bounded against the mocked signing layer."""

    @staticmethod
    def _simple_job() -> IsingJob:
        model = XQMX.spin_model(2)
        model.set_linear(0, 1)
        model.set_linear(1, -2)
        model.set_quadratic(0, 1, 1)
        return model_to_ising(model, PATH5)

    def test_wrap_bounded_is_one_tuple(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        assert solver._wrap_bounded([1, 2, 3]) == ([1, 2, 3],)

    def test_propose_job_composition_and_order_id(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        job = self._simple_job()
        solver._iface.events = [_job_proposed_event(42)]
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))

        assert solver._propose_job(job) == 42

        assert captured["call_module"] == "QuantumComputeMempool"
        assert captured["call_function"] == "propose_job"
        params = captured["call_params"]
        assert params["spec_id"] == solver._spec_id
        assert params["reward"] == solver._reward
        assert params["mode"] == "Open"
        assert params["resolution"] == "SingleBest"
        assert params["delivery"] == "OnChainOnly"
        assert params["deadline_blocks"] == solver._deadline_blocks
        assert params["block_wait"] == solver._block_wait
        ising = params["ising_params"]
        # BoundedVec fields are 1-tuple wrapped (v0.2 metadata composite quirk).
        assert ising["nodes"] == (list(job.nodes),)
        assert ising["edges"] == ([list(edge) for edge in job.edges],)
        assert ising["h_values"] == (list(job.h_values),)
        assert ising["j_values"] == (list(job.j_values),)
        assert ising["min_energy_milli"] is None
        assert ising["min_solutions"] is None

    @pytest.mark.parametrize("attrs_form", ["mapping", "params"])
    def test_order_id_extraction_tolerates_attribute_shapes(self, monkeypatch, attrs_form) -> None:
        solver = _make_solver(monkeypatch)
        solver._iface.events = [_job_proposed_event(7, attrs_form=attrs_form)]
        assert solver._read_proposed_order_id("0xblock") == 7

    def test_order_id_extraction_rejects_positional_shape(self, monkeypatch) -> None:
        # A bare-positional attribute list is deliberately NOT trusted: a
        # reordered pre-release event layout could otherwise yield a
        # plausible-but-wrong id and settle the wrong order. With no keyed
        # order_id the caller must raise rather than guess from attrs[0].
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        solver._iface.events = [_job_proposed_event(7, attrs_form="positional")]
        with pytest.raises(QuipSubmissionError, match="no JobProposed"):
            solver._read_proposed_order_id("0xblock")

    def test_submit_failure_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="System.ExtrinsicFailed: {...}"))
        with pytest.raises(QuipSubmissionError, match="failed on-chain"):
            solver._propose_job(self._simple_job())

    def test_signing_error_wrapped(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        err = solver._quip_signing.QuipSigningError("self-check failed")
        _patch_signing(monkeypatch, solver, build_raises=err)
        with pytest.raises(QuipSubmissionError, match="could not be submitted"):
            solver._propose_job(self._simple_job())

    def test_missing_job_proposed_event_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        solver._iface.events = []  # included, but no JobProposed event.
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipSubmissionError, match="no JobProposed"):
            solver._propose_job(self._simple_job())

    def test_no_block_hash_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        with pytest.raises(QuipSubmissionError, match="cannot read the order id"):
            solver._read_proposed_order_id(None)


# ---------------------------------------------------------------------------
# Monitor / retrieve / decode (Step 6) fixtures
# ---------------------------------------------------------------------------

# A 5-node path topology, supplied to the mocked chain via QuantumPow.RegisteredTopologies.
TOPO_NODES = [0, 1, 2, 3, 4]
TOPO_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4)]
TOPO_HASH = "0x" + "ab" * 32


def _model() -> XQMX:
    """A small asymmetric-field SPIN model that places onto the path topology."""
    model = XQMX.spin_model(2)
    model.set_linear(0, 1)  # h0 = +1 (milli 1000)
    model.set_linear(1, -2)  # h1 = -2 (milli -2000)
    model.set_quadratic(0, 1, 1)  # j01 = +1 (milli 1000)
    return model


def _job(solver) -> IsingJob:
    """The IsingJob solve()/query() will reproduce from the mocked topology."""
    return model_to_ising(_model(), solver._fetch_topology())


def _spin_vector(job: IsingJob, var_spins: dict[int, int], default: int = 1) -> list[int]:
    """Build a full per-node spin vector with the given variable spins."""
    vector = [default] * job.topology.num_nodes
    for var, spin in var_spins.items():
        vector[job.topology.index_of(job.mapping[var])] = spin
    return vector


def _order(
    *,
    status: str = "Opened",
    created_at: int = 0,
    first_solution_at: int | None = None,
    deadline_blocks: int = 100,
    block_wait: int = 10,
    solution_count: int = 1,
) -> dict:
    return {
        "status": status,
        "created_at": created_at,
        "first_solution_at": first_solution_at,
        "timing": {"deadline_blocks": deadline_blocks, "block_wait": block_wait},
        "solution_count": solution_count,
    }


def _submission(solver_id: str, vectors: list[list[int]], best_energy_milli: int) -> dict:
    return {
        "solver": solver_id,
        "solutions": [list(vector) for vector in vectors],
        "best_energy_milli": best_energy_milli,
        "diversity_milli": 0,
        "num_valid": len(vectors),
    }


def _force_timeout(monkeypatch) -> None:
    """Patch xqsa.quip's clock so _await_finality's first deadline check trips.

    monotonic() returns 0 for the deadline baseline, then 100 for the post-check,
    so a non-final order raises QuipTimeoutError without real sleeping.
    """
    import xqsa.quip as quip_mod

    calls = {"n": 0}

    def fake_monotonic() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 100.0

    monkeypatch.setattr(quip_mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(quip_mod.time, "sleep", lambda _seconds: None)


def _chain_iface(*, order: dict, head: int, submissions: list[tuple] | None = None, balance: int = 10 * UNIT):
    """A mocked chain with a topology, one order, head height, and OrderSolutions."""
    iface = _default_iface(balance=balance)
    iface.storage[("QuantumPow", "RegisteredTopologies")] = {"nodes": TOPO_NODES, "edges": TOPO_EDGES}
    iface.storage[("QuantumComputeMempool", "JobOrders")] = order
    iface.events = [_job_proposed_event(1)]
    iface.head = head
    if submissions is not None:
        iface.maps[("QuantumComputeMempool", "OrderSolutions")] = submissions
    return iface


class TestSolverQuipLifecycle:
    """_await_finality, _order_lifecycle, and status() against the mocked chain."""

    def test_await_finality_terminal_status_returns_immediately(self, monkeypatch) -> None:
        # Closed short-circuits regardless of height (head 5 < expiry 100).
        iface = _chain_iface(order=_order(status="Closed"), head=5)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        assert solver._await_finality(1)["status"] == "Closed"

    def test_await_finality_terminal_skips_head_read(self, monkeypatch) -> None:
        # A terminal status is final regardless of height, so the head-height
        # RPC must be skipped entirely.
        iface = _chain_iface(order=_order(status="Closed"), head=5)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)

        def _boom() -> int:
            raise AssertionError("head-height read must be skipped for a terminal order")

        monkeypatch.setattr(solver, "_current_block", _boom)
        assert solver._await_finality(1)["status"] == "Closed"

    def test_await_finality_by_height(self, monkeypatch) -> None:
        # Opened but past the hard deadline (head 200 >= expiry 100).
        iface = _chain_iface(order=_order(status="Opened"), head=200)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        assert solver._await_finality(1)["status"] == "Opened"

    def test_await_finality_times_out(self, monkeypatch) -> None:
        from xqsa.quip import QuipTimeoutError

        iface = _chain_iface(order=_order(status="Opened"), head=50)  # 50 < expiry 100 -> never final
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH, timeout=0.0)
        _force_timeout(monkeypatch)
        with pytest.raises(QuipTimeoutError) as excinfo:
            solver._await_finality(1)
        assert excinfo.value.order_id == 1

    def test_status_snapshot(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(status="Opened", solution_count=2), head=150)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        snap = solver.status(1)
        assert snap["order_id"] == 1
        assert snap["status"] == "Opened"
        assert snap["current_block"] == 150
        assert snap["effective_expiry"] == 100  # created_at 0 + deadline_blocks 100
        assert snap["is_final"] is True  # 150 >= 100
        assert snap["solution_count"] == 2

    def test_fetch_order_unknown_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipConnectionError

        solver = _make_solver(monkeypatch)  # default iface has no JobOrders entry.
        with pytest.raises(QuipConnectionError, match="not found"):
            solver._fetch_order(7)


class TestSolverQuipCollect:
    """_collect_result selection, the energy canary, and auto-reclaim."""

    def test_selects_lowest_local_energy_and_canary_true(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_chain_iface(order=_order(), head=200), topology=TOPO_HASH)
        model = _model()
        job = _job(solver)
        low = _spin_vector(job, {0: 1, 1: 1})  # E = +1 -2 +1 = 0
        high = _spin_vector(job, {0: 1, 1: -1})  # E = +1 +2 -1 = +2
        chain_best = min(ising_energy_milli(job, low), ising_energy_milli(job, high))
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"solver", _submission("0xSOLVER", [high, low], chain_best))
        ]

        result = solver._collect_result(1, job, model, elapsed=1.5)
        assert result.energy == 0  # the lower-energy vector wins
        assert result.timing == 1.5
        assert result.metadata["order_id"] == 1
        assert result.metadata["solver"] == "0xSOLVER"
        assert result.metadata["best_energy_milli"] == chain_best
        assert result.metadata["energy_matches_chain"] is True
        assert result.metadata["num_submissions"] == 1
        assert result.metadata["num_solutions"] == 2

    def test_picks_lowest_best_energy_submission(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_chain_iface(order=_order(), head=200), topology=TOPO_HASH)
        model = _model()
        job = _job(solver)
        winner = _spin_vector(job, {0: 1, 1: 1})  # E = 0
        loser = _spin_vector(job, {0: 1, 1: -1})  # E = +2
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"a", _submission("0xLOSER", [loser], ising_energy_milli(job, loser))),
            (b"b", _submission("0xWINNER", [winner], ising_energy_milli(job, winner))),
        ]
        result = solver._collect_result(1, job, model, elapsed=0.0)
        assert result.metadata["solver"] == "0xWINNER"
        assert result.energy == 0

    def test_canary_false_on_energy_mismatch(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_chain_iface(order=_order(), head=200), topology=TOPO_HASH)
        model = _model()
        job = _job(solver)
        vector = _spin_vector(job, {0: 1, 1: 1})
        wrong = ising_energy_milli(job, vector) + 1000  # chain disagrees with our recompute.
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"solver", _submission("0xSOLVER", [vector], wrong))
        ]
        result = solver._collect_result(1, job, model, elapsed=0.0)
        assert result.metadata["energy_matches_chain"] is False

    def test_missing_pallet_field_raises_typed_error(self, monkeypatch) -> None:
        # A pre-release field rename must surface as QuipJobFailedError, not a
        # bare KeyError escaping the QuipError hierarchy.
        from xqsa.quip import QuipJobFailedError

        solver = _make_solver(monkeypatch, iface=_chain_iface(order=_order(), head=200), topology=TOPO_HASH)
        job = _job(solver)
        vector = _spin_vector(job, {0: 1, 1: 1})
        broken = _submission("0xSOLVER", [vector], 0)
        del broken["best_energy_milli"]  # simulate a renamed/absent field
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [(b"solver", broken)]
        with pytest.raises(QuipJobFailedError, match="field layout"):
            solver._collect_result(1, job, _model(), elapsed=0.0)

    def test_no_solutions_auto_reclaims_then_fails(self, monkeypatch) -> None:
        from xqsa.quip import QuipJobFailedError

        iface = _chain_iface(order=_order(solution_count=0), head=200, submissions=[])
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        job = _job(solver)
        with pytest.raises(QuipJobFailedError) as excinfo:
            solver._collect_result(1, job, _model(), elapsed=0.0)
        assert excinfo.value.order_id == 1
        assert "reclaimed" in str(excinfo.value)
        assert captured["call_function"] == "reclaim_order"
        assert captured["call_params"] == {"order_id": 1}

    def test_reclaim_failure_is_tolerated(self, monkeypatch) -> None:
        from xqsa.quip import QuipJobFailedError

        iface = _chain_iface(order=_order(solution_count=0), head=200, submissions=[])
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        # reclaim_order dispatch fails (e.g. not proposer); _reclaim swallows it.
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="System.ExtrinsicFailed: NotProposer"))
        with pytest.raises(QuipJobFailedError, match="remain reserved"):
            solver._collect_result(1, _job(solver), _model(), elapsed=0.0)


class TestSolverQuipSolve:
    """End-to-end solve() over the mocked propose -> finality -> collect path."""

    def test_solve_happy_path(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_chain_iface(order=_order(), head=200), topology=TOPO_HASH)
        model = _model()
        job = _job(solver)
        vector = _spin_vector(job, {0: 1, 1: 1})
        solver._iface.maps[("QuantumPow", "MineableTopologies")] = [(TOPO_HASH, ())]
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"solver", _submission("0xSOLVER", [vector], ising_energy_milli(job, vector)))
        ]
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))

        result = solver.solve(model)
        assert isinstance(result, SolverResult)
        assert result.metadata["order_id"] == 1
        assert result.metadata["energy_matches_chain"] is True
        assert result.energy == 0

    def test_solve_timeout_carries_order_id(self, monkeypatch) -> None:
        from xqsa.quip import QuipTimeoutError

        iface = _chain_iface(order=_order(status="Opened"), head=50)  # never final
        iface.maps[("QuantumPow", "MineableTopologies")] = [(TOPO_HASH, ())]
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH, timeout=0.0)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        _force_timeout(monkeypatch)
        with pytest.raises(QuipTimeoutError) as excinfo:
            solver.solve(_model())
        assert excinfo.value.order_id == 1

    def test_solve_no_solutions_reclaims_and_fails(self, monkeypatch) -> None:
        from xqsa.quip import QuipJobFailedError

        iface = _chain_iface(order=_order(solution_count=0), head=200, submissions=[])
        iface.maps[("QuantumPow", "MineableTopologies")] = [(TOPO_HASH, ())]
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipJobFailedError):
            solver.solve(_model())
        assert captured["call_function"] == "reclaim_order"  # last submit was the reclaim

    def test_solve_insufficient_balance_raises_before_submit(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        iface = _chain_iface(order=_order(), head=200, balance=0)
        iface.maps[("QuantumPow", "MineableTopologies")] = [(TOPO_HASH, ())]
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipSubmissionError, match="insufficient balance"):
            solver.solve(_model())
        assert "call_function" not in captured  # never reached submission

    def test_solve_proceeds_when_topology_is_not_mineable(self, monkeypatch) -> None:
        # Inverted guard. solve() used to reject a registered hash absent from
        # MineableTopologies. That set is the chain's active mining set: it gates
        # submit_proof, not the compute mempool, and an order carries its nodes,
        # edges and coefficients inline with no topology hash at all, so the chain
        # cannot perceive which topology an order was built against. A
        # registered-but-non-mineable topology must therefore reach submission and
        # solve normally. Verified on aglais order 20, answered in one block with
        # 17 submissions and the global optimum.
        iface = _chain_iface(order=_order(), head=200)
        iface.maps[("QuantumPow", "MineableTopologies")] = [("0x" + "cd" * 32, ())]  # TOPO_HASH absent
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        job = _job(solver)
        vector = _spin_vector(job, {0: 1, 1: 1})
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"solver", _submission("0xSOLVER", [vector], ising_energy_milli(job, vector)))
        ]
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        assert solver.solve(_model()).metadata["order_id"] == 1
        assert captured["call_function"] == "propose_job"

    def test_solve_proceeds_when_nothing_is_mineable(self, monkeypatch) -> None:
        # The same holds for an empty mineable set, which the old gate rejected
        # outright. No maps entry -> a defined-but-empty map.
        iface = _chain_iface(order=_order(), head=200)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        assert solver._mineable_topologies() == frozenset()
        job = _job(solver)
        vector = _spin_vector(job, {0: 1, 1: 1})
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"solver", _submission("0xSOLVER", [vector], ising_energy_milli(job, vector)))
        ]
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        assert solver.solve(_model()).metadata["order_id"] == 1


class TestSolverQuipQuery:
    """query() finality gating and result recovery."""

    def test_query_returns_none_when_not_final(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(status="Opened"), head=50)  # 50 < expiry 100
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        assert solver.query(1, _model()) is None

    def test_query_returns_result_when_final(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_chain_iface(order=_order(), head=200), topology=TOPO_HASH)
        job = _job(solver)
        vector = _spin_vector(job, {0: 1, 1: 1})
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"solver", _submission("0xSOLVER", [vector], ising_energy_milli(job, vector)))
        ]
        result = solver.query(1, _model())
        assert result is not None
        assert result.metadata["order_id"] == 1
        assert result.energy == 0

    def test_query_empty_final_auto_reclaims_and_fails(self, monkeypatch) -> None:
        from xqsa.quip import QuipJobFailedError

        iface = _chain_iface(order=_order(solution_count=0), head=200, submissions=[])
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipJobFailedError):
            solver.query(1, _model())
        assert captured["call_function"] == "reclaim_order"
