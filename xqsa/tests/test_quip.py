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
import logging
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
    native_placement,
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
# native_placement
# ---------------------------------------------------------------------------


class TestNativePlacement:
    """native_placement: building a topology from a model's own coupling graph."""

    def test_full_graph_identity_mapping(self) -> None:
        """A model already labelled 0..n-1, fully coupled, relabels to itself."""
        n = 28
        model = XQMX.spin_model(n)
        for i in range(n):
            model.set_linear(i, 1 if i % 2 == 0 else -1)
        for u, v in itertools.combinations(range(n), 2):
            model.set_quadratic(u, v, 1.0)

        topo, mapping = native_placement(model)
        assert mapping == {i: i for i in range(n)}

        job = model_to_ising(model, topo, mapping=mapping)
        assert job.nodes == tuple(range(n))
        assert len(job.edges) == 378  # C(28, 2)

    def test_sparse_variables_relabel_by_rank(self) -> None:
        """Non-contiguous participating variables relabel to dense ranks."""
        model = XQMX.spin_model(10)
        model.set_quadratic(0, 5, 1.0)
        model.set_quadratic(5, 9, -1.0)

        topo, mapping = native_placement(model)
        assert topo.nodes == (0, 1, 2)
        assert topo.edges == ((0, 1), (1, 2))
        assert mapping == {0: 0, 5: 1, 9: 2}

        job = model_to_ising(model, topo, mapping=mapping)
        spins = [1, -1, 1]  # node0(var0)=+1, node1(var5)=-1, node2(var9)=+1
        sample = decode_solution(job, spins, model)
        assert sample.get_linear(0) == 1
        assert sample.get_linear(5) == -1
        assert sample.get_linear(9) == 1

    def test_binary_matches_model_to_ising_on_a_permissive_topology(self) -> None:
        """The native placement covers the same variables and coefficients as
        model_to_ising against a topology permissive enough to admit any mapping."""
        model = XQMX.binary_model(6)
        model.set_linear(0, 1)
        model.set_linear(2, -1)
        model.set_quadratic(0, 2, 2)
        model.set_quadratic(2, 4, -3)
        model.set_quadratic(0, 4, 1)

        native_topo, native_mapping = native_placement(model)
        native_job = model_to_ising(model, native_topo, mapping=native_mapping)

        permissive = Topology.of(range(100), itertools.combinations(range(100), 2))
        placed_job = model_to_ising(model, permissive)

        assert set(native_job.mapping) == set(placed_job.mapping)

        def h_of(job: IsingJob, var: int) -> int:
            return job.h_values[job.topology.index_of(job.mapping[var])]

        def j_of(job: IsingJob, u: int, v: int) -> int:
            return job.j_values[job.topology.edge_index(job.mapping[u], job.mapping[v])]

        for var in native_job.mapping:
            assert h_of(native_job, var) == h_of(placed_job, var)
        for u, v in [(0, 2), (2, 4), (0, 4)]:
            assert j_of(native_job, u, v) == j_of(placed_job, u, v)

    def test_binary_decode_on_sparse_ids(self) -> None:
        """A BINARY model over sparse ids decodes spins back as x = (s + 1) / 2."""
        model = XQMX.binary_model(4)
        model.set_quadratic(1, 3, 1)

        topo, mapping = native_placement(model)
        assert mapping == {1: 0, 3: 1}

        job = model_to_ising(model, topo, mapping=mapping)
        sample = decode_solution(job, [1, -1], model)
        assert sample.get_linear(1) == 1
        assert sample.get_linear(3) == 0

    def test_no_allowed_value_sets(self) -> None:
        """A native topology carries no allowed-value sets."""
        model = XQMX.spin_model(2)
        model.set_quadratic(0, 1, 1)
        topo, _mapping = native_placement(model)
        assert topo.allowed_h is None
        assert topo.allowed_j is None
        assert topo.allowed_spin is None


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
    storage map), so a non-hashable mock AccountId is fine. ``token_symbol``/
    ``token_decimals`` stand in for the chain's system properties, and
    ``rpc_request`` answers from a method-keyed ``rpc`` mapping (a value may be
    the dict to return, or an ``Exception`` instance to raise) so ``quote()``'s
    ``payment_queryInfo`` call can be exercised without a real chain.
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
        token_symbol: str | None = "AGLS",
        token_decimals: int | None = 12,
        rpc: dict | None = None,
    ) -> None:
        self.constants = dict(constants or {})
        self.storage = dict(storage or {})
        self.events = list(events or [])
        self.maps = dict(maps or {})
        self.head = head
        self._init_runtime_raises = init_runtime_raises
        self._get_constant_raises = get_constant_raises
        self.token_symbol = token_symbol
        self.token_decimals = token_decimals
        self.rpc = dict(rpc) if rpc is not None else {"payment_queryInfo": {"result": {"partialFee": "2182560255"}}}
        self.rpc_calls: list[tuple[str, list | None]] = []

    def rpc_request(self, method: str, params: list | None = None):
        self.rpc_calls.append((method, params))
        value = self.rpc[method]  # KeyError on an unconfigured method, deliberately.
        if isinstance(value, Exception):
            raise value
        return value

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
    # The rest of the [quip] extra: connect imports certifi for wss:// URLs on
    # macOS. Pin the platform so every CI host takes that branch.
    monkeypatch.setattr(sys, "platform", "darwin")
    certifi = types.ModuleType("certifi")
    certifi.where = lambda: "/fake/cacert.pem"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "certifi", certifi)


def _clear_quip_env(monkeypatch) -> None:
    for name in (
        "QUIP_RPC_URL",
        "QUIP_SIGNER_SEED",
        "QUIP_KEYSTORE",
        "QUIP_REWARD",
        "QUIP_TOPOLOGY",
        "QUIP_FAUCET_URL",
        "QUIP_AUTOCONFIRM",
        "QUIP_AUTOFUND",
        # CA configuration steers connect's wss:// default; keep it out of unit tests.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "WEBSOCKET_CLIENT_CA_BUNDLE",
    ):
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
        with pytest.raises(TypeError, match="multiple values for keyword argument 'url'"):
            SolverQuip.for_network("aglais", url="ws://x", seed=VALID_SEED)

    def test_for_network_sets_preset_faucet_and_name(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip
        from xqsa.quip_networks import NETWORKS

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_FAUCET_URL", "http://env-faucet")
        solver = SolverQuip.for_network("aglais", seed=VALID_SEED)
        # The preset enters as an argument, so it beats QUIP_FAUCET_URL.
        assert solver._faucet == NETWORKS["aglais"].faucet
        assert solver._network == "aglais"

    def test_for_network_faucet_none_keeps_the_preset(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip
        from xqsa.quip_networks import NETWORKS

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_FAUCET_URL", "http://env-faucet")
        solver = SolverQuip.for_network("aglais", seed=VALID_SEED, faucet=None)
        assert solver._faucet == NETWORKS["aglais"].faucet
        solver = SolverQuip.for_network("aglais", seed=VALID_SEED, faucet="")
        assert solver._faucet == NETWORKS["aglais"].faucet

    def test_for_network_caller_faucet_overrides_preset(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        solver = SolverQuip.for_network("aglais", seed=VALID_SEED, faucet="http://mine")
        assert solver._faucet == "http://mine"

    def test_faucet_from_arg(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, faucet="http://arg-faucet")
        assert solver._faucet == "http://arg-faucet"
        # A raw url= names no network.
        assert solver._network is None

    def test_faucet_from_env(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_RPC_URL", "ws://fake")
        monkeypatch.setenv("QUIP_FAUCET_URL", "http://env-faucet")
        solver = SolverQuip(seed=VALID_SEED)
        assert solver._faucet == "http://env-faucet"

    def test_env_faucet_ignored_with_explicit_url(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_FAUCET_URL", "http://env-faucet")
        # An explicit url= may name another chain than the exported faucet serves.
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._faucet is None

    def test_faucet_arg_beats_env(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_FAUCET_URL", "http://env-faucet")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED, faucet="http://arg-faucet")
        assert solver._faucet == "http://arg-faucet"

    def test_faucet_unset_is_none(self, monkeypatch) -> None:
        assert _make_solver(monkeypatch)._faucet is None

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
    """_fetch_topology and _free_balance against the mocked chain."""

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

    def test_free_balance_ok(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_default_iface(balance=5 * UNIT))
        assert solver._free_balance() == 5 * UNIT

    def test_free_balance_low_is_returned_not_raised(self, monkeypatch) -> None:
        # No threshold any more: _free_balance is a plain read. A zero balance
        # is returned as-is; the shortfall decision moved to JobQuote/solve().
        solver = _make_solver(monkeypatch, iface=_default_iface(balance=0))
        assert solver._free_balance() == 0

    def test_free_balance_unreadable_raises_connection_error(self, monkeypatch) -> None:
        # A missing/undecodable System.Account read is a chain-read fault, not a
        # zero balance -- it must raise QuipConnectionError rather than blame a
        # possibly-funded account for "insufficient balance".
        from xqsa.quip import QuipConnectionError

        solver = _make_solver(monkeypatch, iface=_default_iface())  # no System.Account entry
        with pytest.raises(QuipConnectionError, match="could not read the free balance"):
            solver._free_balance()


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
    call (e.g. a successful propose followed by a failing reclaim). ``captured["builds"]``
    counts calls into ``build_signed_extrinsic``, so a test can assert an
    extrinsic was assembled exactly once.
    """
    captured: dict = {"builds": 0}
    qs = solver._quip_signing

    def fake_build(iface, signer, call_module, call_function, call_params):
        captured.update(iface=iface, call_module=call_module, call_function=call_function, call_params=call_params)
        captured["builds"] += 1
        if build_raises is not None:
            raise build_raises
        return b"\x00\x01", "0xext"

    def fake_submit(iface, wire_bytes, ext_hash, wait_for="inblock"):
        captured["wait_for"] = wait_for
        captured["submitted_wire"] = wire_bytes
        return receipt(captured["call_function"]) if callable(receipt) else receipt

    monkeypatch.setattr(qs, "build_signed_extrinsic", fake_build)
    monkeypatch.setattr(qs, "disarm_extrinsic", lambda wire: b"disarmed:" + wire)
    monkeypatch.setattr(qs, "submit_and_watch", fake_submit)
    return captured


def _ok_receipt(solver, *, error: str | None = None):
    return solver._quip_signing.ExtrinsicReceipt(
        extrinsic_hash="0xext", block_hash="0xblock", is_finalized=False, error=error
    )


class TestSolverQuipSubmission:
    """_propose_call_params / _build_extrinsic / _submit_built / _propose_job / _wrap_bounded.

    ``_propose_job`` now takes a prebuilt ``(wire, ext_hash)`` pair rather than
    building the extrinsic itself: ``solve()`` builds once, via ``_prepare``,
    then displays the quote before deciding whether to submit that same wire.
    """

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

    def test_propose_call_params_composition(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        job = self._simple_job()
        params = solver._propose_call_params(job)

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

    def test_build_extrinsic_returns_wire_and_hash(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        captured = _patch_signing(monkeypatch, solver)
        params = solver._propose_call_params(self._simple_job())

        wire, ext_hash = solver._build_extrinsic("QuantumComputeMempool", "propose_job", params)

        assert wire == b"\x00\x01"
        assert ext_hash == "0xext"
        assert captured["builds"] == 1
        assert captured["call_module"] == "QuantumComputeMempool"
        assert captured["call_function"] == "propose_job"
        assert captured["call_params"] is params

    def test_build_extrinsic_signing_error_wrapped(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        err = solver._quip_signing.QuipSigningError("self-check failed")
        _patch_signing(monkeypatch, solver, build_raises=err)
        with pytest.raises(QuipSubmissionError, match="could not be submitted"):
            solver._build_extrinsic("QuantumComputeMempool", "propose_job", {})

    def test_submit_built_returns_receipt(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        receipt = solver._submit_built("QuantumComputeMempool", "propose_job", b"\x00\x01", "0xext")
        assert receipt.block_hash == "0xblock"
        assert captured["wait_for"] == "inblock"
        assert captured["builds"] == 0  # _submit_built never re-builds the extrinsic

    def test_submit_built_failure_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="System.ExtrinsicFailed: {...}"))
        with pytest.raises(QuipSubmissionError, match="failed on-chain"):
            solver._submit_built("QuantumComputeMempool", "propose_job", b"\x00\x01", "0xext")

    def test_propose_job_returns_order_id(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        solver._iface.events = [_job_proposed_event(42)]
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        assert solver._propose_job(b"\x00\x01", "0xext") == 42

    def test_propose_job_submit_failure_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver, error="System.ExtrinsicFailed: {...}"))
        with pytest.raises(QuipSubmissionError, match="failed on-chain"):
            solver._propose_job(b"\x00\x01", "0xext")

    def test_propose_job_missing_event_raises(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver = _make_solver(monkeypatch)
        solver._iface.events = []  # included, but no JobProposed event.
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipSubmissionError, match="no JobProposed"):
            solver._propose_job(b"\x00\x01", "0xext")

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


def _k5_model() -> XQMX:
    """A 5-variable complete-graph SPIN model no path topology (e.g. TOPO_HASH) can place."""
    model = XQMX.spin_model(5)
    for u, v in itertools.combinations(range(5), 2):
        model.set_quadratic(u, v, 1.0)
    return model


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
        # _prepare builds the extrinsic (to quote the fee) before the balance
        # gate, so "builds" is no longer the signal; submission is.
        assert "wait_for" not in captured  # never reached submission

    def test_solve_builds_extrinsic_exactly_once(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(), head=200)
        iface.maps[("QuantumPow", "MineableTopologies")] = [(TOPO_HASH, ())]
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        job = _job(solver)
        vector = _spin_vector(job, {0: 1, 1: 1})
        solver._iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
            (b"solver", _submission("0xSOLVER", [vector], ising_energy_milli(job, vector)))
        ]
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        solver.solve(_model())
        assert captured["builds"] == 1

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


class TestSolverQuipNativeTopology:
    """topology="native" / QUIP_TOPOLOGY=native: submit over the model's own coupling graph."""

    @staticmethod
    def _iface_refusing_chain_default() -> FakeSubstrate:
        """A FakeSubstrate whose DefaultTopology read raises, to catch a stray call."""
        iface = _default_iface()

        def _boom(module, name, params=None):
            if (module, name) == ("QuantumPow", "DefaultTopology"):
                raise AssertionError("QuantumPow.DefaultTopology must not be read in native mode")
            return _StorageEntry(iface.storage.get((module, name)))

        iface.query = _boom
        return iface

    def test_env_native_resolves_without_reading_chain_default(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        iface = self._iface_refusing_chain_default()
        _install(monkeypatch, iface)
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", "native")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._topology_hash == "native"

    def test_arg_native_resolves_without_reading_chain_default(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        iface = self._iface_refusing_chain_default()
        _install(monkeypatch, iface)
        _clear_quip_env(monkeypatch)
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED, topology="native")
        assert solver._topology_hash == "native"

    def test_env_native_whitespace_resolves(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        iface = self._iface_refusing_chain_default()
        _install(monkeypatch, iface)
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", " native ")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._topology_hash == "native"

    def test_per_call_native_ignores_case_and_whitespace(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=_chain_iface(order=_order(), head=200), topology=TOPO_HASH)
        assert solver._job_for(_k5_model(), " Native ", None).nodes == (0, 1, 2, 3, 4)

    def test_explicit_hash_beats_env_native(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_TOPOLOGY", "native")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED, topology=TOPO_HASH)
        assert solver._topology_hash == TOPO_HASH

    def test_fetch_topology_refuses_native(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, topology="native")
        with pytest.raises(ValueError, match="native mode"):
            solver._fetch_topology()

    def test_native_solver_places_a_model_the_default_topology_cannot(self, monkeypatch) -> None:
        # K5 needs degree 4 at every node; TOPO_HASH (a 5-node path) tops out at
        # degree 2, so this would raise PlacementError against the chain default.
        solver = _make_solver(monkeypatch, iface=_default_iface(balance=10 * UNIT), topology="native")

        def _fail(*args, **kwargs):
            raise AssertionError("_fetch_topology must not be called in native mode")

        monkeypatch.setattr(solver, "_fetch_topology", _fail)
        _patch_signing(monkeypatch, solver)
        job = solver._prepare(_k5_model(), {})[0]
        assert job.nodes == (0, 1, 2, 3, 4)
        assert len(job.edges) == 10

    def test_per_call_native_override_places_k5_that_default_topology_rejects(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(), head=200)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        model = _k5_model()

        job = solver._job_for(model, "native", None)
        assert job.nodes == (0, 1, 2, 3, 4)

        with pytest.raises(PlacementError):
            solver._job_for(model, None, None)

    def test_per_call_hash_override_on_native_solver_uses_fetch_topology(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(), head=200)
        solver = _make_solver(monkeypatch, iface=iface, topology="native")
        model = _model()

        calls: list[str | None] = []
        original_fetch = solver._fetch_topology

        def _tracking_fetch(topology_hash=None):
            calls.append(topology_hash)
            return original_fetch(topology_hash)

        monkeypatch.setattr(solver, "_fetch_topology", _tracking_fetch)
        job = solver._job_for(model, TOPO_HASH, None)
        assert calls == [TOPO_HASH]
        assert job.nodes  # a job was actually built against the fetched topology.

    def test_query_native_rederives_the_same_job_prepare_built(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(), head=200)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        model = _k5_model()
        _patch_signing(monkeypatch, solver)

        prepared_job, _wire, _hash, _quote = solver._prepare(model, {"topology": "native"})

        captured: dict = {}

        def _fake_collect(order_id, job, model_arg, *, elapsed):
            captured["job"] = job
            return SolverResult(sample=model_arg, energy=0, timing=elapsed, metadata={})

        monkeypatch.setattr(solver, "_collect_result", _fake_collect)
        solver.query(1, model, topology="native")

        queried_job = captured["job"]
        assert queried_job.nodes == prepared_job.nodes
        assert queried_job.edges == prepared_job.edges
        assert queried_job.h_values == prepared_job.h_values
        assert queried_job.j_values == prepared_job.j_values
        assert dict(queried_job.mapping) == dict(prepared_job.mapping)

    def test_native_topology_rejects_explicit_mapping(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(), head=200)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        model = _model()
        explicit_mapping = {0: 0, 1: 1}

        with pytest.raises(ValueError, match="mapping"):
            solver._job_for(model, "native", explicit_mapping)

        with pytest.raises(ValueError, match="mapping"):
            solver.quote(model, topology="native", mapping=explicit_mapping)

        with pytest.raises(ValueError, match="mapping"):
            solver.query(1, model, topology="native", mapping=explicit_mapping)

    @pytest.mark.parametrize(("name", "limit"), [("MaxNodes", 4), ("MaxEdges", 9)])
    def test_native_order_over_the_mempool_bound_raises(self, monkeypatch, name, limit) -> None:
        iface = _default_iface()
        iface.constants[("QuantumComputeMempool", name)] = limit
        solver = _make_solver(monkeypatch, iface=iface, topology="native")
        with pytest.raises(EncodingError, match=f"QuantumComputeMempool.{name} of {limit}"):
            solver._job_for(_k5_model(), None, None)

    def test_native_order_at_the_mempool_bound_passes(self, monkeypatch) -> None:
        iface = _default_iface()
        iface.constants[("QuantumComputeMempool", "MaxNodes")] = 5
        iface.constants[("QuantumComputeMempool", "MaxEdges")] = 10
        solver = _make_solver(monkeypatch, iface=iface, topology="native")
        assert solver._job_for(_k5_model(), None, None).topology.num_edges == 10

    def test_query_rejects_native_mapping_before_the_order_is_final(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(status="Opened"), head=50)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        with pytest.raises(ValueError, match="mapping"):
            solver.query(1, _model(), topology="native", mapping={0: 0, 1: 1})


# ---------------------------------------------------------------------------
# JobQuote: arithmetic, formatting, and solver.quote() / _display / _insufficient_error
# ---------------------------------------------------------------------------


def test_format_planck_matches_the_documented_formula() -> None:
    from xqsa.quip import _format_planck

    assert _format_planck(1_002_182_560_255, 12) == "1.002182560255"
    assert _format_planck(20_000_000_000_000, 12) == "20.000000000000"
    assert _format_planck(0, 12) == "0.000000000000"
    assert _format_planck(5, 0) == "5"


class TestJobQuote:
    """JobQuote arithmetic and __str__ formatting, independent of any chain."""

    @staticmethod
    def _quote(**overrides: object):
        from xqsa.quip import JobQuote

        defaults = dict(
            network="aglais",
            reward_planck=1_000_000_000_000,
            fee_planck=2_182_560_255,
            fee_exact=True,
            balance_planck=20_000_000_000_000,
            token_symbol="AGLS",
            token_decimals=12,
        )
        defaults.update(overrides)
        return JobQuote(**defaults)

    def test_total_is_reward_plus_fee(self) -> None:
        quote = self._quote(reward_planck=1000, fee_planck=200)
        assert quote.total_planck == 1200

    def test_shortfall_zero_when_balance_covers_total(self) -> None:
        quote = self._quote(reward_planck=1000, fee_planck=200, balance_planck=1200)
        assert quote.shortfall_planck == 0

    def test_shortfall_zero_when_balance_exceeds_total(self) -> None:
        # Clamps at 0 rather than going negative.
        quote = self._quote(reward_planck=1000, fee_planck=200, balance_planck=10_000)
        assert quote.shortfall_planck == 0

    def test_shortfall_positive_when_balance_short(self) -> None:
        quote = self._quote(reward_planck=1000, fee_planck=200, balance_planck=500)
        assert quote.shortfall_planck == 700

    def test_str_lines_all_start_with_quip_tag_and_are_ascii(self) -> None:
        text = str(self._quote())
        lines = text.splitlines()
        assert lines
        assert all(line.startswith("[quip]") for line in lines)
        assert text.isascii()

    def test_str_decimal_points_align_across_amount_lines(self) -> None:
        text = str(self._quote())
        amount_lines = [line for line in text.splitlines() if "." in line]
        assert len(amount_lines) == 5  # reward, fee, total, balance, shortfall
        assert len({line.index(".") for line in amount_lines}) == 1

    def test_str_header_names_the_network(self) -> None:
        assert "network aglais" in str(self._quote(network="aglais"))

    def test_str_header_says_custom_endpoint_when_network_is_none(self) -> None:
        assert "custom endpoint" in str(self._quote(network=None))

    def test_str_header_fee_exact_vs_estimated(self) -> None:
        assert "fee exact" in str(self._quote(fee_exact=True))
        assert "fee estimated" in str(self._quote(fee_exact=False))

    def test_str_has_no_fractional_part_when_decimals_is_zero(self) -> None:
        quote = self._quote(
            token_symbol="planck", token_decimals=0, reward_planck=1000, fee_planck=0, balance_planck=1000
        )
        assert "." not in str(quote)


class TestSolverQuipQuote:
    """quote() / _query_fee / _prepare against the mocked chain.

    ``quote()`` builds the extrinsic (to price the fee via ``payment_queryInfo``)
    even though it never submits, so every test here patches the signing layer
    just like a submission test would.
    """

    @staticmethod
    def _iface(*, balance: int = 10 * UNIT) -> FakeSubstrate:
        return _chain_iface(order=_order(), head=200, balance=balance)

    def test_fee_exact_true_on_a_decimal_partial_fee(self, monkeypatch) -> None:
        iface = self._iface()
        iface.rpc["payment_queryInfo"] = {"result": {"partialFee": "2182560255"}}
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        assert quote.fee_planck == 2182560255
        assert quote.fee_exact is True

    def test_fee_exact_true_on_a_hex_partial_fee(self, monkeypatch) -> None:
        iface = self._iface()
        iface.rpc["payment_queryInfo"] = {"result": {"partialFee": "0x82260fff"}}
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        assert quote.fee_planck == int("0x82260fff", 16)
        assert quote.fee_exact is True

    def test_fee_falls_back_to_headroom_on_rpc_failure(self, monkeypatch) -> None:
        from xqsa.quip import FEE_HEADROOM_PLANCK

        iface = self._iface()
        iface.rpc["payment_queryInfo"] = RuntimeError("rpc unavailable")
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        assert quote.fee_planck == FEE_HEADROOM_PLANCK
        assert quote.fee_exact is False

    def test_token_props_none_fall_back_to_planck_and_zero_decimals(self, monkeypatch) -> None:
        iface = self._iface()
        iface.token_symbol = None
        iface.token_decimals = None
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        assert quote.token_symbol == "planck"
        assert quote.token_decimals == 0
        assert "." not in str(quote)

    def test_quote_never_submits(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=self._iface(), topology=TOPO_HASH)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        solver.quote(_model())
        assert "wait_for" not in captured  # submit_and_watch never called

    def test_quote_network_is_none_for_a_raw_url(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, iface=self._iface(), topology=TOPO_HASH)
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        assert quote.network is None
        assert "custom endpoint" in str(quote)

    def test_quote_network_carries_the_preset_name(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        iface = self._iface()
        iface.storage[("QuantumPow", "DefaultTopology")] = TOPO_HASH
        _install(monkeypatch, iface)
        _clear_quip_env(monkeypatch)
        solver = SolverQuip.for_network("aglais", seed=VALID_SEED)
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        assert quote.network == "aglais"
        assert "network aglais" in str(quote)


class TestSolverQuipInsufficientBalance:
    """_insufficient_error names a remedy, tailored to whether a faucet is known."""

    def test_names_the_faucet_when_set(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        iface = _chain_iface(order=_order(), head=200, balance=0)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH, faucet="http://myfaucet")
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        error = solver._insufficient_error(quote)
        assert isinstance(error, QuipSubmissionError)
        message = str(error)
        assert message.startswith("insufficient balance")
        assert "http://myfaucet" in message

    def test_mentions_for_network_when_faucet_unset(self, monkeypatch) -> None:
        iface = _chain_iface(order=_order(), head=200, balance=0)
        solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH)
        _patch_signing(monkeypatch, solver)
        quote = solver.quote(_model())
        message = str(solver._insufficient_error(quote))
        assert "faucet=" in message
        assert "QUIP_FAUCET_URL" in message
        assert "SolverQuip.for_network" in message


class _FakeTTY:
    """A minimal stderr stand-in whose isatty() is controllable."""

    def __init__(self, *, isatty: bool) -> None:
        self._isatty = isatty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self._isatty

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class TestSolverQuipDisplay:
    """_display logs the quote on xqsa.quip and echoes to an interactive stderr only."""

    @staticmethod
    def _quote():
        from xqsa.quip import JobQuote

        return JobQuote(
            network="aglais",
            reward_planck=UNIT,
            fee_planck=2_182_560_255,
            fee_exact=True,
            balance_planck=20 * UNIT,
            token_symbol="AGLS",
            token_decimals=12,
        )

    def test_logs_every_line_at_info_on_xqsa_quip(self, monkeypatch, caplog) -> None:
        solver = _make_solver(monkeypatch)
        quote = self._quote()
        with caplog.at_level(logging.INFO, logger="xqsa.quip"):
            solver._display(quote)
        logged_lines = [record.getMessage() for record in caplog.records if record.name == "xqsa.quip"]
        for line in str(quote).splitlines():
            assert line in logged_lines

    def test_prints_to_stderr_when_interactive(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        quote = self._quote()
        fake_stderr = _FakeTTY(isatty=True)
        monkeypatch.setattr(sys, "stderr", fake_stderr)
        solver._display(quote)
        assert "".join(fake_stderr.written) != ""

    def test_silent_on_stderr_when_not_interactive(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        quote = self._quote()
        fake_stderr = _FakeTTY(isatty=False)
        monkeypatch.setattr(sys, "stderr", fake_stderr)
        solver._display(quote)
        assert fake_stderr.written == []


def test_quip_metadata_logger_parents_under_xqsa_quip() -> None:
    """xqsa.quip_metadata's logger renamed to xqsa.quip.metadata, nesting under xqsa.quip."""
    assert logging.getLogger("xqsa.quip.metadata").parent.name == "xqsa.quip"


# ---------------------------------------------------------------------------
# _env_flag: boolean environment variable parsing
# ---------------------------------------------------------------------------


class TestEnvFlag:
    """_env_flag's spelling matrix, independent of the solver."""

    @pytest.mark.parametrize("spelling", ["1", "true", "yes", "on", "TRUE", " YES "])
    def test_true_spellings(self, monkeypatch, spelling) -> None:
        from xqsa.quip import _env_flag

        monkeypatch.setenv("QUIP_TEST_FLAG", spelling)
        assert _env_flag("QUIP_TEST_FLAG") is True

    @pytest.mark.parametrize("spelling", ["0", "false", "no", "off", "FALSE", " NO "])
    def test_false_spellings(self, monkeypatch, spelling) -> None:
        from xqsa.quip import _env_flag

        monkeypatch.setenv("QUIP_TEST_FLAG", spelling)
        assert _env_flag("QUIP_TEST_FLAG") is False

    def test_unset_is_none(self, monkeypatch) -> None:
        from xqsa.quip import _env_flag

        monkeypatch.delenv("QUIP_TEST_FLAG", raising=False)
        assert _env_flag("QUIP_TEST_FLAG") is None

    def test_empty_is_none(self, monkeypatch) -> None:
        from xqsa.quip import _env_flag

        monkeypatch.setenv("QUIP_TEST_FLAG", "")
        assert _env_flag("QUIP_TEST_FLAG") is None

    def test_garbage_raises_naming_the_variable(self, monkeypatch) -> None:
        from xqsa.quip import _env_flag

        monkeypatch.setenv("QUIP_TEST_FLAG", "maybe")
        with pytest.raises(ValueError, match="QUIP_TEST_FLAG"):
            _env_flag("QUIP_TEST_FLAG")


# ---------------------------------------------------------------------------
# autoconfirm / autofund resolution at construction (QUI-1456)
# ---------------------------------------------------------------------------


class TestSolverQuipGateResolution:
    """Constructor resolution of autoconfirm/autofund: argument, then env, then True."""

    def test_defaults_to_true_for_both(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch)
        assert solver._autoconfirm is True
        assert solver._autofund is True

    def test_autoconfirm_env_false(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_AUTOCONFIRM", "0")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._autoconfirm is False

    def test_autofund_env_false(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_AUTOFUND", "no")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED)
        assert solver._autofund is False

    def test_argument_beats_env(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_AUTOCONFIRM", "0")
        solver = SolverQuip(url="ws://fake", seed=VALID_SEED, autoconfirm=True)
        assert solver._autoconfirm is True

    def test_env_garbage_raises(self, monkeypatch) -> None:
        from xqsa.quip import SolverQuip

        _install(monkeypatch, _default_iface())
        _clear_quip_env(monkeypatch)
        monkeypatch.setenv("QUIP_AUTOCONFIRM", "maybe")
        with pytest.raises(ValueError, match="QUIP_AUTOCONFIRM"):
            SolverQuip(url="ws://fake", seed=VALID_SEED)

    def test_non_bool_non_callable_raises_type_error(self, monkeypatch) -> None:
        with pytest.raises(TypeError, match="autoconfirm"):
            _make_solver(monkeypatch, autoconfirm="yes")

    def test_callable_gate_accepted(self, monkeypatch) -> None:
        solver = _make_solver(monkeypatch, autoconfirm=lambda quote: True, autofund=lambda quote: True)
        assert callable(solver._autoconfirm)
        assert callable(solver._autofund)


# ---------------------------------------------------------------------------
# QuipCancelledError
# ---------------------------------------------------------------------------


def test_quip_cancelled_error_is_a_quip_error() -> None:
    from xqsa.quip import QuipCancelledError
    from xqsa.quip_codec import QuipError

    assert issubclass(QuipCancelledError, QuipError)


def test_quip_cancelled_error_carries_the_quote() -> None:
    from xqsa.quip import JobQuote, QuipCancelledError

    quote = JobQuote(
        network="aglais",
        reward_planck=UNIT,
        fee_planck=0,
        fee_exact=True,
        balance_planck=0,
        token_symbol="AGLS",
        token_decimals=12,
    )
    error = QuipCancelledError(quote, "declined")
    assert error.quote is quote
    assert str(error) == "declined"


# ---------------------------------------------------------------------------
# solve(): autoconfirm / autofund consent gates (QUI-1456)
# ---------------------------------------------------------------------------

# The default FakeSubstrate.rpc["payment_queryInfo"] partialFee, in planck --
# used to compute exact expected shortfalls without re-deriving a quote.
FEE_PLANCK = 2_182_560_255


def _solve_ready(monkeypatch, *, balance: int = 10 * UNIT, **solver_kwargs):
    """A solver whose account is funded, with a winning submission waiting.

    Mirrors ``TestSolverQuipSolve.test_solve_happy_path``: registers the
    order, marks the topology mineable, and seeds the winning submission once
    the solver (and therefore its deterministic placement) exists.
    """
    iface = _chain_iface(order=_order(), head=200, balance=balance)
    solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH, **solver_kwargs)
    job = _job(solver)
    vector = _spin_vector(job, {0: 1, 1: 1})
    iface.maps[("QuantumPow", "MineableTopologies")] = [(TOPO_HASH, ())]
    iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
        (b"solver", _submission("0xSOLVER", [vector], ising_energy_milli(job, vector)))
    ]
    return solver


def _solve_ready_short(
    monkeypatch, *, balance: int = 0, reward: int = UNIT, faucet: str | None = "http://faucet", **solver_kwargs
):
    """A solver whose account is short of the quote, with a winning submission waiting once funded."""
    iface = _chain_iface(order=_order(), head=200, balance=balance)
    solver = _make_solver(monkeypatch, iface=iface, topology=TOPO_HASH, reward=reward, faucet=faucet, **solver_kwargs)
    job = _job(solver)
    vector = _spin_vector(job, {0: 1, 1: 1})
    iface.maps[("QuantumComputeMempool", "OrderSolutions")] = [
        (b"solver", _submission("0xSOLVER", [vector], ising_energy_milli(job, vector)))
    ]
    return solver, iface


class TestSolveAutoconfirmGate:
    """The autoconfirm gate matrix on a funded account: submit, or QuipCancelledError."""

    def test_true_submits(self, monkeypatch) -> None:
        solver = _solve_ready(monkeypatch)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        result = solver.solve(_model())
        assert isinstance(result, SolverResult)

    def test_false_tty_answers_yes_submits(self, monkeypatch) -> None:
        solver = _solve_ready(monkeypatch, autoconfirm=False)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=True))
        monkeypatch.setattr(sys, "stderr", _FakeTTY(isatty=True))
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        result = solver.solve(_model())
        assert isinstance(result, SolverResult)

    def test_false_tty_answers_no_cancels(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        solver = _solve_ready(monkeypatch, autoconfirm=False)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=True))
        monkeypatch.setattr(sys, "stderr", _FakeTTY(isatty=True))
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        with pytest.raises(QuipCancelledError, match="answered 'n'") as excinfo:
            solver.solve(_model())
        assert excinfo.value.quote.total_planck > 0
        assert "wait_for" not in captured

    def test_false_tty_eof_declines(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        solver = _solve_ready(monkeypatch, autoconfirm=False)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=True))
        monkeypatch.setattr(sys, "stderr", _FakeTTY(isatty=True))

        def _raise_eof(prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise_eof)
        with pytest.raises(QuipCancelledError, match="at the prompt"):
            solver.solve(_model())
        assert "wait_for" not in captured

    def test_false_tty_stdin_with_both_outputs_redirected_cancels(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        solver = _solve_ready(monkeypatch, autoconfirm=False)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=True))
        monkeypatch.setattr(sys, "stderr", _FakeTTY(isatty=False))
        monkeypatch.setattr(sys, "stdout", _FakeTTY(isatty=False))
        calls: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt="": calls.append(prompt) or "y")
        # A question written into the redirect target would block unseen.
        with pytest.raises(QuipCancelledError, match="not a terminal"):
            solver.solve(_model())
        assert calls == []
        assert "wait_for" not in captured

    def test_false_no_tty_cancels_without_prompting(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        solver = _solve_ready(monkeypatch, autoconfirm=False)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=False))
        calls: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt="": calls.append(prompt) or "y")
        with pytest.raises(QuipCancelledError, match="autoconfirm=False") as excinfo:
            solver.solve(_model())
        message = str(excinfo.value)
        assert "not a terminal" in message
        assert "autoconfirm=lambda q" in message
        assert calls == []  # input() is never reached
        assert "wait_for" not in captured

    def test_callable_true_submits_and_receives_the_quote(self, monkeypatch) -> None:
        from xqsa.quip import JobQuote

        received: list[JobQuote] = []
        solver = _solve_ready(monkeypatch, autoconfirm=lambda quote: received.append(quote) or True)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        result = solver.solve(_model())
        assert isinstance(result, SolverResult)
        assert len(received) == 1
        assert isinstance(received[0], JobQuote)
        assert received[0].total_planck > 0

    def test_callable_false_cancels(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        solver = _solve_ready(monkeypatch, autoconfirm=lambda quote: False)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipCancelledError) as excinfo:
            solver.solve(_model())
        assert excinfo.value.quote.total_planck > 0
        assert "wait_for" not in captured


class TestSolveAutofundGate:
    """When the quote is short, autofund controls the faucet drip and the wait for it to land."""

    def test_true_funds_then_submits(self, monkeypatch) -> None:
        solver, iface = _solve_ready_short(monkeypatch)
        calls: list[tuple] = []

        def fake_fund(dest, *, url, amount=None):
            calls.append((dest, url, amount))
            iface.storage[("System", "Account")] = {"data": {"free": 100 * UNIT}}
            return {}

        monkeypatch.setattr("xqsa.quip.fund_from_faucet", fake_fund)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        result = solver.solve(_model())
        assert isinstance(result, SolverResult)
        assert len(calls) == 1
        assert calls[0][0] == "0x" + bytes(solver._signer.account_id).hex()
        assert calls[0][1] == "http://faucet"
        assert "wait_for" in captured  # propose_job was submitted

    def test_callable_false_cancels_and_skips_the_faucet(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        calls: list[tuple] = []
        solver, _iface = _solve_ready_short(monkeypatch, autofund=lambda quote: False)
        monkeypatch.setattr("xqsa.quip.fund_from_faucet", lambda dest, **kwargs: calls.append((dest, kwargs)))
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipCancelledError):
            solver.solve(_model())
        assert calls == []
        assert "wait_for" not in captured

    def test_requests_the_default_drip_without_an_amount(self, monkeypatch) -> None:
        solver, iface = _solve_ready_short(monkeypatch)
        calls: list[dict] = []

        def fake_fund(dest, **kwargs):
            calls.append(kwargs)
            iface.storage[("System", "Account")] = {"data": {"free": 100 * UNIT}}
            return {}

        monkeypatch.setattr("xqsa.quip.fund_from_faucet", fake_fund)
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        solver.solve(_model())
        assert calls == [{"url": "http://faucet"}]

    def test_shortfall_beyond_one_drip_raises_without_asking(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        asked: list = []
        funded: list = []
        # 20 AGLS reward on an empty account: twice a drip, the mistyped-reward case.
        solver, _iface = _solve_ready_short(
            monkeypatch,
            reward=20 * UNIT,
            autoconfirm=lambda quote: asked.append(quote) or True,
            autofund=lambda quote: asked.append(quote) or True,
        )
        monkeypatch.setattr("xqsa.quip.fund_from_faucet", lambda dest, **kwargs: funded.append(dest))
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipSubmissionError, match="more than one faucet drip"):
            solver.solve(_model())
        assert asked == []
        assert funded == []
        assert "wait_for" not in captured

    def test_balance_above_the_faucet_ceiling_raises_without_asking(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        asked: list = []
        funded: list = []
        # 12 AGLS held, 13 needed: a 1 AGLS shortfall, but the faucet refuses an account above one drip.
        solver, _iface = _solve_ready_short(
            monkeypatch,
            balance=12 * UNIT,
            reward=13 * UNIT,
            autoconfirm=lambda quote: asked.append(quote) or True,
            autofund=lambda quote: asked.append(quote) or True,
        )
        monkeypatch.setattr("xqsa.quip.fund_from_faucet", lambda dest, **kwargs: funded.append(dest))
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipSubmissionError, match="at most one drip"):
            solver.solve(_model())
        assert asked == []
        assert funded == []
        assert "wait_for" not in captured

    def test_false_no_tty_cancels_without_funding(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        calls: list = []
        solver, _iface = _solve_ready_short(monkeypatch, autofund=False)
        monkeypatch.setattr("xqsa.quip.fund_from_faucet", lambda dest, **kwargs: calls.append(dest))
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=False))
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipCancelledError, match="autofund=lambda q"):
            solver.solve(_model())
        assert calls == []
        assert "wait_for" not in captured

    def test_faucet_error_propagates_without_proposing(self, monkeypatch) -> None:
        from xqsa.quip_faucet import QuipFaucetError

        solver, _iface = _solve_ready_short(monkeypatch)

        def refuse(dest, *, url, amount=None):
            raise QuipFaucetError(403, {"error": "destination already funded"}, "refused")

        monkeypatch.setattr("xqsa.quip.fund_from_faucet", refuse)
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipFaucetError):
            solver.solve(_model())
        assert "wait_for" not in captured

    def test_prompt_goes_to_stderr(self, monkeypatch) -> None:
        solver, iface = _solve_ready_short(monkeypatch, autofund=False)

        def fake_fund(dest, *, url, amount=None):
            iface.storage[("System", "Account")] = {"data": {"free": 100 * UNIT}}
            return {}

        monkeypatch.setattr("xqsa.quip.fund_from_faucet", fake_fund)
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=True))
        stderr = _FakeTTY(isatty=True)
        monkeypatch.setattr(sys, "stderr", stderr)
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        solver.solve(_model())
        assert "Fund 0x" in "".join(stderr.written)

    def test_prompt_goes_to_stdout_when_stderr_is_redirected(self, monkeypatch) -> None:
        solver, iface = _solve_ready_short(monkeypatch, autofund=False)

        def fake_fund(dest, *, url, amount=None):
            iface.storage[("System", "Account")] = {"data": {"free": 100 * UNIT}}
            return {}

        monkeypatch.setattr("xqsa.quip.fund_from_faucet", fake_fund)
        monkeypatch.setattr(sys, "stdin", _FakeTTY(isatty=True))
        stderr, stdout = _FakeTTY(isatty=False), _FakeTTY(isatty=True)
        monkeypatch.setattr(sys, "stderr", stderr)
        monkeypatch.setattr(sys, "stdout", stdout)
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        solver.solve(_model())
        shown = "".join(stdout.written)
        assert "Fund 0x" in shown
        assert "[quip] job quote" in shown
        assert stderr.written == []

    def test_no_faucet_raises_before_the_autofund_gate(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        calls: list = []
        solver, _iface = _solve_ready_short(
            monkeypatch,
            faucet=None,
            autoconfirm=lambda quote: calls.append(quote) or True,
            autofund=lambda quote: calls.append(quote) or True,
        )
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipSubmissionError, match="insufficient balance"):
            solver.solve(_model())
        assert calls == []  # neither gate is asked when no faucet is configured
        assert "wait_for" not in captured

    def test_balance_never_rises_raises_after_the_wait(self, monkeypatch) -> None:
        from xqsa.quip import QuipSubmissionError

        solver, _iface = _solve_ready_short(monkeypatch)
        monkeypatch.setattr("xqsa.quip.fund_from_faucet", lambda dest, **kwargs: {})
        _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        _force_timeout(monkeypatch)  # xqsa.quip.time.monotonic: 0.0 then 100.0, past FUND_WAIT_SECONDS
        with pytest.raises(QuipSubmissionError, match="insufficient balance"):
            solver.solve(_model())

    def test_autoconfirm_declined_stops_before_the_autofund_gate(self, monkeypatch) -> None:
        from xqsa.quip import QuipCancelledError

        calls: list = []
        solver, _iface = _solve_ready_short(monkeypatch, autoconfirm=lambda quote: False)
        monkeypatch.setattr("xqsa.quip.fund_from_faucet", lambda dest, **kwargs: calls.append((dest, kwargs)))
        captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
        with pytest.raises(QuipCancelledError):
            solver.solve(_model())
        assert calls == []
        assert "wait_for" not in captured


def test_fee_is_priced_on_a_disarmed_copy_never_the_submitted_bytes(monkeypatch) -> None:
    solver = _solve_ready(monkeypatch)
    captured = _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
    solver.solve(_model())
    (method, params), *_ = solver._iface.rpc_calls
    assert method == "payment_queryInfo"
    assert params == ["0x" + (b"disarmed:" + b"\x00\x01").hex()]
    assert captured["submitted_wire"] == b"\x00\x01"


@pytest.mark.parametrize("stream", ["stdin", "stderr"])
def test_gates_and_display_survive_a_missing_stream(monkeypatch, stream) -> None:
    # pythonw and some daemons run with sys.stdin / sys.stderr set to None.
    from xqsa.quip import QuipCancelledError

    solver = _solve_ready(monkeypatch, autoconfirm=False)
    _patch_signing(monkeypatch, solver, receipt=_ok_receipt(solver))
    monkeypatch.setattr(sys, stream, None)
    # Not a terminal, so autoconfirm=False declines instead of raising AttributeError.
    with pytest.raises(QuipCancelledError, match="not a terminal"):
        solver.solve(_model())
