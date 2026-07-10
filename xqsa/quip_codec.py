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
Pure encoding and placement logic for the Quip Network mempool backend.

This module is deliberately free of any chain or networking dependency so it
can be unit-tested anywhere. It owns the four transforms that sit between an
XQMX model and the on-chain ``QuantumComputeMempool`` representation:

1. :class:`Topology` -- the hardware graph in the chain's canonical order
   (sorted nodes, ``(min, max)``-normalized sorted edges). The order mirrors
   ``shared/topology_hash.py`` in ``quip-protocol`` so that the spin vector the
   miner returns aligns position-for-position with our node array.
2. :func:`find_placement` -- subgraph PLACEMENT: an injective mapping from
   model variables onto topology nodes such that every coupling lands on a
   real hardware edge. Greedy DFS with backtracking; deterministic so
   ``SolverQuip.query()`` can re-derive the same mapping from an order id.
3. :func:`model_to_ising` -- scatter the (placed) model coefficients into the
   full-length, zero-filled milli-scale ``i32`` arrays the pallet expects.
4. :func:`decode_solution` -- read a returned spin vector back into an XQMX
   sample over the original variables.

Coefficient convention: XQMX coefficients are natural-scale integers (``5``
means ``5.0``); the chain stores ``h``/``j`` as milli-scale ``i32`` (multiply
by :data:`MILLI_SCALE`). ``propose_job`` validates only structural consistency
(no duplicate nodes, ``len(h) == len(nodes)``, ``len(j) == len(edges)``, every
edge endpoint present in ``nodes``); it does NOT enforce allowed-value sets, so
the only value constraint here is that the milli value fits ``i32``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

import dimod

from xqvm_py.xqmx import XQMX, XQMXDomain, XQMXMode

if TYPE_CHECKING:
    from collections.abc import Sequence

# Milli-precision scale factor. Mirrors ``quantum_validation::fixed::MILLI_SCALE``
# (and ``shared/allowed_value_spec.py``): on-chain h/j are integers read as
# ``value / MILLI_SCALE``.
MILLI_SCALE = 1000

# On-chain h/j are SCALE ``i32`` (pallet ``FieldsOf``/``CouplingsOf``).
I32_MIN = -(2**31)
I32_MAX = 2**31 - 1

# Largest natural-scale coefficient that still fits ``i32`` after scaling by
# ``MILLI_SCALE`` (``2_147_483 * 1000 = 2_147_483_000 <= I32_MAX``).
MAX_NATURAL_COEFFICIENT = I32_MAX // MILLI_SCALE

# Coefficient-encoding doc: natural<->milli scaling, the i32 limit, and why
# out-of-spec coefficients are accepted on-chain (the pallet does not enforce
# allowed-value sets), solved by the SA miner, and monotonically rescaled by
# real hardware. Embedded in the i32-overflow ``EncodingError`` and in
# ``SolverQuip``'s one-time allowed-value warning.
QUIP_COEFFICIENTS_DOC_URL = (
    "https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/SOLVERS.md#coefficient-encoding-and-allowed-values"
)

# Genesis default plain-Ising job spec (QUI-567). Verified against localdev
# ``QuantumComputeMempool.JobSpecs`` during live validation.
DEFAULT_ISING_SPEC_ID = "0x8f46f3a31321d1d093314fc769c42cbe7a83d71a0b69e6571a0f68e2a04067f0"

# BLAKE2b-256 topology hash of advantage2_system1, pinned from live validation
# (QUI-569 Step 8) against the v0.2 localdev devnet. Two derivations agree:
#   1. Chain: ``QuantumPow.DefaultTopology`` (== the ``RegisteredTopologies``
#      storage key and the ``MineableTopologies`` entry the miner matches on).
#   2. ``quip-protocol shared/topology_hash.py`` over the registered
#      ``(sorted nodes, sorted edges, canonical allowed-value specs)`` -- the
#      Python mirror of the pallet's ``hash_topology``.
# The v0.2 image's advantage2_system1 dataset is 4577 nodes / 41515 edges; the
# hash binds those exact arrays (an earlier 4578 / 41531 figure was pre-live).
ADVANTAGE2_SYSTEM1_TOPOLOGY_HASH: str | None = "0xfb91813bc4268d00e35813c8fcdb67675a08ef74240dbb25bcd35dd1478c7ec4"

# On-chain order statuses (``QuantumComputeMempool`` ``OrderStatus``).
ORDER_STATUS_OPENED = "Opened"
ORDER_STATUS_EXPIRED = "Expired"
ORDER_STATUS_CLOSED = "Closed"
# Statuses past which an order never accepts more solutions.
_TERMINAL_STATUSES = frozenset({ORDER_STATUS_EXPIRED, ORDER_STATUS_CLOSED})


class QuipError(Exception):
    """Base class for all Quip Network codec/backend errors."""


class PlacementError(QuipError):
    """Raised when a model cannot be placed onto the hardware topology.

    Carries the couplings (model variable pairs) that could not be satisfied,
    so the caller can report exactly which interactions have no hardware edge.
    """

    def __init__(self, message: str, couplings: Iterable[tuple[int, int]] = ()) -> None:
        super().__init__(message)
        self.couplings: tuple[tuple[int, int], ...] = tuple(couplings)


class EncodingError(QuipError):
    """Raised when a model cannot be encoded into the on-chain representation."""


class QuipSigningError(QuipError):
    """Raised when extrinsic assembly, keystore handling, or submission fails.

    Defined here in the dependency-free codec module (rather than in
    ``quip_signing``, which does ``import quip_signer`` at module top) so it can
    be re-exported from ``xqsa`` without pulling in the optional ``[quip]``
    extra -- ``import xqsa`` stays healthy without the extension installed.
    """


@dataclass(frozen=True)
class AllowedValues:
    """A topology's allowed milli-values for a coefficient field (h, j, or spin).

    Either a finite ``members`` set of milli values or an inclusive ``bounds``
    ``(min, max)`` range; mirrors the chain's ``AllowedValueSpec`` enum (its
    ``Set`` / ``IntegerRange`` / ``ContinuousRange`` variants -- the two range
    variants share one membership test at milli precision). An all-``None``
    instance is unconstrained. The chain does NOT enforce these sets on
    ``propose_job``; they drive only the educational warning, never a rejection.
    """

    members: frozenset[int] | None = None
    bounds: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        # Mirror the chain enum: a spec is a Set OR a range, never both. All-None
        # (unconstrained) is allowed. Guard the raw constructor so an ambiguous
        # instance cannot make ``contains`` silently prefer ``members``.
        if self.members is not None and self.bounds is not None:
            raise EncodingError("AllowedValues accepts members or bounds, not both")

    def contains(self, milli: int) -> bool:
        """Whether the milli-scale ``milli`` value is permitted by this constraint."""
        if self.members is not None:
            return milli in self.members
        if self.bounds is not None:
            low, high = self.bounds
            return low <= milli <= high
        return True

    @classmethod
    def from_chain_spec(cls, spec: object) -> AllowedValues | None:
        """Parse a SCALE-decoded ``AllowedValueSpec`` into an :class:`AllowedValues`.

        Tolerates the shapes ``substrate-interface`` may decode the enum into:
        ``{"Set": [..]}``, ``{"IntegerRange": {"min":..,"max":..}}``,
        ``{"ContinuousRange": {..}}`` (or list-form ranges ``[min, max]``), or a
        bare list of members. Returns ``None`` for an absent or unrecognized
        shape, so callers treat the set as unknown and skip the warning rather
        than guess. The exact live shape is pinned during live validation.
        """
        if spec is None:
            return None
        if isinstance(spec, (list, tuple)):
            return cls(members=frozenset(int(value) for value in spec))
        if isinstance(spec, Mapping):
            if len(spec) != 1:
                return None
            ((tag, inner),) = spec.items()
            if tag in ("Set", "set"):
                if isinstance(inner, (list, tuple)):
                    return cls(members=frozenset(int(value) for value in inner))
                return None
            if tag in ("IntegerRange", "ContinuousRange"):
                bounds = _range_bounds(inner)
                return cls(bounds=bounds) if bounds is not None else None
        return None


@dataclass(frozen=True)
class Topology:
    """A hardware interaction graph in the chain's canonical order.

    ``nodes`` are sorted ascending and ``edges`` are ``(min, max)``-normalized
    and sorted lexicographically, matching ``hash_topology`` so a returned spin
    vector indexes position-for-position against ``nodes``. Construct via
    :meth:`of` or :meth:`from_chain`; the direct constructor assumes its inputs
    are already canonical.

    ``allowed_h`` / ``allowed_j`` / ``allowed_spin`` are the chain's allowed
    milli-value sets for the field, coupling, and spin coefficients. They are
    ``None`` unless populated by :meth:`from_chain` from a ``TopologyMeta``;
    they feed only the one-time educational warning (the pallet never enforces
    them), so an unknown set simply means no warning.
    """

    nodes: tuple[int, ...]
    edges: tuple[tuple[int, int], ...]
    allowed_h: AllowedValues | None = None
    allowed_j: AllowedValues | None = None
    allowed_spin: AllowedValues | None = None
    _node_index: dict[int, int] = field(init=False, repr=False, compare=False)
    _edge_index: dict[tuple[int, int], int] = field(init=False, repr=False, compare=False)
    _adjacency: dict[int, frozenset[int]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Enforce the canonical-order invariants the docstring promises but the
        # raw constructor never checked: nodes strictly ascending + unique, edges
        # (min, max)-normalized and sorted ascending + unique. Cheap O(n) scans;
        # Topology.of / from_chain output (and canonical chain data) passes
        # without re-sorting. Without this, a non-canonical direct construction
        # mis-indexes returned spin vectors position-for-position.
        nodes = self.nodes
        if any(nodes[i] >= nodes[i + 1] for i in range(len(nodes) - 1)):
            raise EncodingError("Topology nodes must be strictly ascending and unique")
        edges = self.edges
        for u, v in edges:
            if u > v:
                raise EncodingError(f"Topology edge ({u}, {v}) is not (min, max)-normalized")
        if any(edges[i] >= edges[i + 1] for i in range(len(edges) - 1)):
            raise EncodingError("Topology edges must be sorted ascending and unique")

        node_index = {node: position for position, node in enumerate(self.nodes)}
        edge_index: dict[tuple[int, int], int] = {}
        adjacency: dict[int, set[int]] = defaultdict(set)
        for position, (u, v) in enumerate(self.edges):
            edge_index[(u, v)] = position
            adjacency[u].add(v)
            adjacency[v].add(u)
        object.__setattr__(self, "_node_index", node_index)
        object.__setattr__(self, "_edge_index", edge_index)
        object.__setattr__(self, "_adjacency", {node: frozenset(adjacency[node]) for node in self.nodes})

    @classmethod
    def of(
        cls,
        nodes: Iterable[int],
        edges: Iterable[tuple[int, int]],
        *,
        allowed_h: AllowedValues | None = None,
        allowed_j: AllowedValues | None = None,
        allowed_spin: AllowedValues | None = None,
    ) -> Topology:
        """Build a canonical topology from arbitrary node/edge iterables.

        Deduplicates, sorts nodes ascending, and normalizes each edge to
        ``(min, max)`` before sorting the edge list lexicographically. The
        allowed-value sets, if known, are carried through unchanged.
        """
        sorted_nodes = tuple(sorted({int(node) for node in nodes}))
        normalized_edges = tuple(sorted({(min(int(u), int(v)), max(int(u), int(v))) for u, v in edges}))
        return cls(sorted_nodes, normalized_edges, allowed_h, allowed_j, allowed_spin)

    @classmethod
    def from_chain(cls, meta: Mapping[str, object]) -> Topology:
        """Build a topology from a decoded ``QuantumPow.RegisteredTopologies`` entry.

        ``meta`` is the SCALE-decoded ``TopologyMeta`` storage value: its
        ``nodes`` and ``edges`` define the graph, and its ``allowed_h`` /
        ``allowed_j`` / ``allowed_spin`` (each an ``AllowedValueSpec``) are
        captured when present so :func:`check_allowed_values` can flag
        out-of-spec coefficients for the educational warning.
        """
        nodes = meta["nodes"]
        edges = meta["edges"]
        if not isinstance(nodes, Iterable) or not isinstance(edges, Iterable):
            raise EncodingError("topology meta must carry iterable 'nodes' and 'edges'")
        return cls.of(
            nodes,  # type: ignore[arg-type]
            edges,  # type: ignore[arg-type]
            allowed_h=AllowedValues.from_chain_spec(meta.get("allowed_h")),
            allowed_j=AllowedValues.from_chain_spec(meta.get("allowed_j")),
            allowed_spin=AllowedValues.from_chain_spec(meta.get("allowed_spin")),
        )

    def index_of(self, node: int) -> int:
        """Return the canonical position of ``node`` (KeyError if absent)."""
        return self._node_index[node]

    def has_node(self, node: int) -> bool:
        """Return whether ``node`` is part of the topology."""
        return node in self._node_index

    def edge_index(self, u: int, v: int) -> int:
        """Return the canonical position of edge ``(u, v)`` (KeyError if absent)."""
        return self._edge_index[(min(u, v), max(u, v))]

    def has_edge(self, u: int, v: int) -> bool:
        """Return whether ``(u, v)`` is a hardware edge (order-independent)."""
        return (min(u, v), max(u, v)) in self._edge_index

    def adjacency(self, node: int) -> frozenset[int]:
        """Return the neighbor set of ``node`` (empty if isolated/absent)."""
        return self._adjacency.get(node, frozenset())

    @property
    def num_nodes(self) -> int:
        """Number of nodes in the topology."""
        return len(self.nodes)

    @property
    def num_edges(self) -> int:
        """Number of edges in the topology."""
        return len(self.edges)


@dataclass(frozen=True)
class IsingJob:
    """A model placed onto a topology, ready for ``propose_job``.

    ``h_values``/``j_values`` are the full-topology, zero-filled milli-scale
    ``i32`` arrays (one entry per node/edge). ``mapping`` records the chosen
    model-variable -> topology-node assignment; ``domain`` is the original
    model domain so :func:`decode_solution` can reverse a BINARY transform.
    """

    topology: Topology
    h_values: tuple[int, ...]
    j_values: tuple[int, ...]
    mapping: dict[int, int]
    domain: XQMXDomain

    def __post_init__(self) -> None:
        # h/j are the full-topology, zero-filled arrays -- one entry per
        # node/edge. A length mismatch means a caller built a job against a
        # different topology than it thinks; catch it here rather than
        # mis-encoding the on-chain payload.
        if len(self.h_values) != self.topology.num_nodes:
            raise EncodingError(
                f"h_values has {len(self.h_values)} entries but the topology has {self.topology.num_nodes} nodes"
            )
        if len(self.j_values) != self.topology.num_edges:
            raise EncodingError(
                f"j_values has {len(self.j_values)} entries but the topology has {self.topology.num_edges} edges"
            )
        # Freeze the mapping so this frozen dataclass is actually immutable -- a
        # bare dict field would still be mutable in place.
        object.__setattr__(self, "mapping", MappingProxyType(dict(self.mapping)))

    @property
    def nodes(self) -> tuple[int, ...]:
        """Full-topology node array submitted on-chain."""
        return self.topology.nodes

    @property
    def edges(self) -> tuple[tuple[int, int], ...]:
        """Full-topology edge array submitted on-chain."""
        return self.topology.edges


def _placement_order(variables: Sequence[int], var_adjacency: Mapping[int, set[int]]) -> list[int]:
    """Order variables for backtracking: most-constrained-first, deterministic.

    Greedily grows an ordering by repeatedly picking the unplaced variable with
    the most neighbors already in the ordering, breaking ties by higher total
    degree then lower index. This keeps each newly placed variable adjacent to
    something already placed (so its candidate nodes are tightly constrained),
    and pushes coupling-free variables to the end.
    """
    remaining = set(variables)
    ordered: list[int] = []
    placed_neighbor_count = {var: 0 for var in variables}
    while remaining:
        chosen = max(
            sorted(remaining),
            key=lambda var: (placed_neighbor_count[var], len(var_adjacency[var])),
        )
        ordered.append(chosen)
        remaining.discard(chosen)
        for neighbor in var_adjacency[chosen]:
            if neighbor in placed_neighbor_count:
                placed_neighbor_count[neighbor] += 1
    return ordered


def _validate_explicit_mapping(
    variables: Sequence[int],
    couplings: Iterable[tuple[int, int]],
    topology: Topology,
    mapping: Mapping[int, int],
) -> dict[int, int]:
    """Validate a caller-supplied variable -> node mapping.

    Raises :class:`PlacementError` if any variable is unmapped, any target node
    is absent, the mapping is not injective, or any coupling fails to land on a
    hardware edge (the offending couplings are attached to the error).
    """
    missing = [var for var in variables if var not in mapping]
    if missing:
        raise PlacementError(f"explicit mapping is missing variables: {sorted(missing)}")

    unknown = [var for var in variables if not topology.has_node(int(mapping[var]))]
    if unknown:
        raise PlacementError(
            f"explicit mapping targets nodes absent from the topology for variables: {sorted(unknown)}"
        )

    seen: dict[int, int] = {}
    for var in variables:
        node = int(mapping[var])
        if node in seen:
            raise PlacementError(
                f"explicit mapping is not injective: variables {seen[node]} and {var} both map to node {node}"
            )
        seen[node] = var

    unplaceable = [(u, v) for u, v in couplings if not topology.has_edge(int(mapping[u]), int(mapping[v]))]
    if unplaceable:
        raise PlacementError(
            f"explicit mapping leaves {len(unplaceable)} coupling(s) without a hardware edge",
            couplings=unplaceable,
        )

    return {int(var): int(mapping[var]) for var in variables}


def find_placement(
    variables: Iterable[int],
    couplings: Iterable[tuple[int, int]],
    topology: Topology,
    *,
    mapping: Mapping[int, int] | None = None,
    max_steps: int = 200_000,
) -> dict[int, int]:
    """Place model variables onto topology nodes (subgraph placement).

    Returns an injective ``{variable: node}`` mapping such that every coupling
    ``(u, v)`` maps to a hardware edge. With ``mapping`` supplied, the explicit
    assignment is validated instead of searched. The greedy search is
    deterministic (candidate nodes are tried in ascending order), so the same
    inputs always yield the same placement -- which lets ``query()`` re-derive a
    placement from a stored order without persisting it.

    Raises:
        PlacementError: if no placement exists (the coupling graph is not a
            subgraph of the topology) or the search budget ``max_steps`` is
            exhausted first. The model couplings are attached to the error.
    """
    coupling_set = {(min(int(u), int(v)), max(int(u), int(v))) for u, v in couplings}
    # A coupling forces both endpoints to be placed, even if the caller did not
    # list them among ``variables``.
    variable_list = sorted({int(var) for var in variables} | {node for edge in coupling_set for node in edge})

    var_adjacency: dict[int, set[int]] = {var: set() for var in variable_list}
    for u, v in coupling_set:
        var_adjacency.setdefault(u, set()).add(v)
        var_adjacency.setdefault(v, set()).add(u)

    if mapping is not None:
        return _validate_explicit_mapping(variable_list, coupling_set, topology, mapping)

    if not variable_list:
        return {}

    order = _placement_order(variable_list, var_adjacency)
    assignment: dict[int, int] = {}
    used_nodes: set[int] = set()
    budget = max_steps
    num_vars = len(order)

    def candidate_nodes(var: int) -> list[int]:
        placed_neighbor_images = [assignment[neighbor] for neighbor in var_adjacency[var] if neighbor in assignment]
        if not placed_neighbor_images:
            return [node for node in topology.nodes if node not in used_nodes]
        candidates = set(topology.adjacency(placed_neighbor_images[0]))
        for image in placed_neighbor_images[1:]:
            candidates &= topology.adjacency(image)
        return sorted(candidates - used_nodes)

    # Explicit backtracking stack instead of recursion: ``stack[depth]`` is the
    # iterator of remaining candidate nodes for ``order[depth]``. A recursive
    # backtracker costs one Python frame per variable and raises RecursionError
    # for models with more variables than the interpreter's recursion limit
    # (~1000) -- well inside the advantage2 topology's 4577-node capacity -- so
    # the search runs iteratively to remove that ceiling. Candidate order is
    # unchanged, so the placement found is identical to the recursive form (which
    # query() relies on to re-derive a mapping deterministically).
    stack: list[Iterator[int]] = [iter(candidate_nodes(order[0]))]
    placed = False
    while stack:
        depth = len(stack) - 1
        advanced = False
        for node in stack[depth]:
            if budget <= 0:
                raise PlacementError(
                    f"placement search budget exhausted after {max_steps} steps; the model may be "
                    "too large to place within this budget, or its coupling graph may not be a "
                    "subgraph of the topology. Raise max_steps to search longer, or supply an "
                    "explicit mapping.",
                    couplings=coupling_set,
                )
            budget -= 1
            assignment[order[depth]] = node
            used_nodes.add(node)
            if depth + 1 == num_vars:
                placed = True
            else:
                stack.append(iter(candidate_nodes(order[depth + 1])))
            advanced = True
            break
        if placed:
            break
        if not advanced:
            # No candidate left at this depth: drop it and undo the parent's
            # placement so the parent advances to its next candidate.
            stack.pop()
            if stack:
                parent_var = order[len(stack) - 1]
                used_nodes.discard(assignment[parent_var])
                del assignment[parent_var]

    if not placed:
        raise PlacementError(
            "model coupling graph is not a subgraph of the topology",
            couplings=coupling_set,
        )
    return assignment


def _to_milli(value: float, label: str) -> int:
    """Scale a natural-scale coefficient to a milli ``i32``, exactly.

    Raises:
        EncodingError: if the value is not representable at milli precision or
            the scaled value does not fit ``i32``.
    """
    scaled = value * MILLI_SCALE
    milli = round(scaled)
    if abs(scaled - milli) > 1e-6:
        raise EncodingError(f"{label} = {value} is not representable at milli precision (1/{MILLI_SCALE})")
    if not (I32_MIN <= milli <= I32_MAX):
        raise EncodingError(
            f"{label} = {value} overflows the encodable range "
            f"(milli {milli} outside i32 [{I32_MIN}, {I32_MAX}]); see {QUIP_COEFFICIENTS_DOC_URL}"
        )
    return milli


def _range_bounds(inner: object) -> tuple[int, int] | None:
    """Extract ``(min, max)`` from a decoded range variant, or ``None``.

    Accepts a mapping with ``min``/``max`` keys or a 2-element list/tuple.
    """
    if isinstance(inner, Mapping):
        low = inner.get("min")
        high = inner.get("max")
        if low is None or high is None:
            return None
        return int(low), int(high)
    if isinstance(inner, (list, tuple)) and len(inner) == 2:
        return int(inner[0]), int(inner[1])
    return None


def check_allowed_values(
    job: IsingJob,
    *,
    allowed_h: AllowedValues | None,
    allowed_j: AllowedValues | None,
) -> list[tuple[str, int, int]]:
    """Return encoded coefficients that fall outside the topology's allowed sets.

    Each offender is ``(kind, position, milli)`` with ``kind`` in ``{"h", "j"}``
    and ``position`` the index into ``job.h_values`` / ``job.j_values``. Zero
    entries (unused nodes/edges, and genuine zero coefficients) are skipped --
    they carry no constraint and a zero is not always a member of the set (the
    default coupling set is ``{-1000, +1000}``, which excludes 0).

    Returns ``[]`` when the relevant allowed-value set is unknown (``None``).
    The chain does not enforce these sets, so this is advisory only: SolverQuip
    submits as-is and uses the result solely to build a one-time educational
    warning. It never raises.
    """
    offenders: list[tuple[str, int, int]] = []
    if allowed_h is not None:
        for position, milli in enumerate(job.h_values):
            if milli != 0 and not allowed_h.contains(milli):
                offenders.append(("h", position, milli))
    if allowed_j is not None:
        for position, milli in enumerate(job.j_values):
            if milli != 0 and not allowed_j.contains(milli):
                offenders.append(("j", position, milli))
    return offenders


def _ising_coefficients(
    model: XQMX,
) -> tuple[dict[int, float], dict[tuple[int, int], float]]:
    """Return ``(h, j)`` spin coefficients for a model.

    SPIN models pass through directly; BINARY models are converted to the spin
    basis via dimod (``s = 2x - 1``), which folds quadratic terms into the
    fields. The energy offset is discarded -- it shifts energy uniformly and we
    recompute authoritative energy on the original model anyway.
    """
    if model.domain == XQMXDomain.SPIN:
        return dict(model.linear), {(min(u, v), max(u, v)): float(c) for (u, v), c in model.quadratic.items()}

    bqm = dimod.BinaryQuadraticModel(model.linear, model.quadratic, 0.0, dimod.BINARY)
    spin = bqm.change_vartype(dimod.SPIN, inplace=False)
    h = {int(var): float(bias) for var, bias in spin.linear.items()}
    j = {(min(int(u), int(v)), max(int(u), int(v))): float(bias) for (u, v), bias in spin.quadratic.items()}
    return h, j


def model_to_ising(
    model: XQMX,
    topology: Topology,
    *,
    mapping: Mapping[int, int] | None = None,
) -> IsingJob:
    """Encode an XQMX model into a placed, on-chain-ready :class:`IsingJob`.

    Computes spin coefficients (converting BINARY to the spin basis), places the
    participating variables onto the topology, and scatters the milli-scaled
    coefficients into full-length zero-filled ``h``/``j`` arrays.

    Raises:
        EncodingError: if the model has no terms, or a coefficient is not
            milli-representable or overflows ``i32``.
        PlacementError: if the model cannot be placed onto the topology.
    """
    if model.mode != XQMXMode.MODEL:
        raise EncodingError(f"expected a MODEL-mode XQMX, got {model.mode.name}")
    if model.domain not in (XQMXDomain.BINARY, XQMXDomain.SPIN):
        raise EncodingError(f"unsupported domain for the Quip backend: {model.domain.name}")
    if not model.linear and not model.quadratic:
        raise EncodingError("model has no linear or quadratic terms; nothing to solve")

    h, j = _ising_coefficients(model)

    variables = set(h) | {index for edge in j for index in edge}
    placement = find_placement(variables, j.keys(), topology, mapping=mapping)

    h_values = [0] * topology.num_nodes
    for var, bias in h.items():
        if bias == 0:
            continue
        position = topology.index_of(placement[var])
        h_values[position] = _to_milli(bias, f"h[{var}]")

    j_values = [0] * topology.num_edges
    for (u, v), bias in j.items():
        if bias == 0:
            continue
        position = topology.edge_index(placement[u], placement[v])
        j_values[position] = _to_milli(bias, f"j[({u}, {v})]")

    return IsingJob(
        topology=topology,
        h_values=tuple(h_values),
        j_values=tuple(j_values),
        mapping=placement,
        domain=model.domain,
    )


def decode_solution(job: IsingJob, spin_vector: Sequence[int], model: XQMX) -> XQMX:
    """Decode a returned spin vector into an XQMX sample over the model.

    ``spin_vector`` is the miner's per-node solution: ``spin_vector[k]`` is the
    spin of ``job.nodes[k]`` (the chain returns one ``i8`` per topology node, in
    the same canonical order this codec submits). Each placed variable reads the
    spin of its assigned node; BINARY variables are mapped back via
    ``x = (s + 1) / 2``. Variables that were not placed keep the sample default
    (``-1`` for spin, ``0`` for binary) since they carry no energy.

    Raises:
        EncodingError: if the vector length does not match the topology or any
            entry is not a valid spin.
    """
    if len(spin_vector) != job.topology.num_nodes:
        raise EncodingError(
            f"spin vector length {len(spin_vector)} does not match topology node count {job.topology.num_nodes}"
        )
    for position, spin in enumerate(spin_vector):
        if spin not in (-1, 1):
            raise EncodingError(f"invalid spin {spin} at position {position}; expected -1 or +1")

    is_binary = model.domain == XQMXDomain.BINARY
    sample = (
        XQMX.binary_sample(model.size, model.rows, model.cols)
        if is_binary
        else XQMX.spin_sample(model.size, model.rows, model.cols)
    )

    for var, node in job.mapping.items():
        spin = int(spin_vector[job.topology.index_of(node)])
        sample.set_linear(var, (spin + 1) // 2 if is_binary else spin)

    return sample


def ising_energy_milli(job: IsingJob, spin_vector: Sequence[int]) -> int:
    """Recompute the chain's milli-scale Ising energy from a per-node spin vector.

    ``E_milli = sum_k h_values[k] * s[k] + sum_e j_values[e] * s[u] * s[v]`` over
    the full-topology milli arrays this codec submitted, with ``s[k]`` the spin
    of ``job.nodes[k]``. Spins are +/-1, so the result is the exact integer
    milli-energy the pallet reports as ``best_energy_milli``.

    ``SolverQuip`` compares this against the chain's reported value as an
    ``energy_matches_chain`` canary: it is domain-independent (both sides use the
    same milli arrays and spin vector, so a BINARY model's dropped offset does
    not enter) and so confirms both spin-vector index alignment and coefficient
    encoding in one check.

    Raises:
        EncodingError: if the vector length does not match the topology.
    """
    topology = job.topology
    if len(spin_vector) != topology.num_nodes:
        raise EncodingError(
            f"spin vector length {len(spin_vector)} does not match topology node count {topology.num_nodes}"
        )
    energy = 0
    for position, milli in enumerate(job.h_values):
        if milli:
            energy += milli * int(spin_vector[position])
    for position, (u, v) in enumerate(topology.edges):
        milli = job.j_values[position]
        if milli:
            energy += milli * int(spin_vector[topology.index_of(u)]) * int(spin_vector[topology.index_of(v)])
    return energy


def effective_expiry(
    created_at: int,
    first_solution_at: int | None,
    deadline_blocks: int,
    block_wait: int,
) -> int:
    """Block height at which an order stops accepting solutions.

    Mirrors the pallet's ``lifecycle::effective_expiry``: the hard deadline
    (``created_at + deadline_blocks``), tightened to ``first_solution_at +
    block_wait`` once a first solution has arrived.
    """
    hard_deadline = created_at + deadline_blocks
    if first_solution_at is None:
        return hard_deadline
    return min(hard_deadline, first_solution_at + block_wait)


def is_final(status: str, current_block: int, expiry: int) -> bool:
    """Whether an order is final and will accept no further solutions.

    Final when the chain status is terminal (``Expired``/``Closed``) or the
    current block has reached the effective expiry. The lazy lifecycle means an
    order can be past expiry while still reported ``Opened``, so the height
    check is what actually decides finality (mirrors ``lifecycle::is_expired``).
    """
    if status in _TERMINAL_STATUSES:
        return True
    return current_block >= expiry


# ---------------------------------------------------------------------------
# Shared normalizers for chain-decoded hex/int/event shapes.
#
# ``quip.py`` and ``quip_signing.py`` both massage the same substrate-interface
# decode shapes; these pure helpers live here (the dependency-free module both
# already import) so int/hex/event normalization has a single home.
# ---------------------------------------------------------------------------


def _strip_0x(value: str) -> str:
    """Return ``value`` without a leading ``0x`` prefix."""
    return value[2:] if value.startswith("0x") else value


def _as_hex(value: object) -> str:
    """Normalize a hash/id (``0x``-hex string or raw bytes) to a ``0x``-hex string."""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    text = str(value)
    return text if text.startswith("0x") else "0x" + text


def _canonical_hex(value: object) -> str | None:
    """Lower-case hex (no ``0x``) for ``bytes``/``str`` inputs, else ``None``."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, str):
        return _strip_0x(value).lower()
    return None


def _as_int_or_none(value: object) -> int | None:
    """Best-effort ``int`` coercion; ``None`` when the value is absent or non-numeric."""
    try:
        return int(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _event_ids(inner: Mapping[str, object]) -> tuple[object, object]:
    """Extract ``(module_id, event_id)`` from a decoded event mapping.

    Tolerates the differing key names substrate-interface uses for the pallet and
    variant across metadata versions and decode paths.
    """
    module_id = inner.get("module_id") or inner.get("event_module") or inner.get("pallet") or inner.get("pallet_name")
    event_id = inner.get("event_id") or inner.get("event_name") or inner.get("variant")
    return module_id, event_id
