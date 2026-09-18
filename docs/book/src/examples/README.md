<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml` by
  `scripts/gen-example-docs.py`.
  Edit the manifest, then run `make regen-docs`.
-->

# Examples

<a id="graph-problems"></a>

## Graph problems

Examples that encode graph cuts, colouring, covers, independent sets, and tours.

- [Max-Cut](maxcut.md) -- Find a 2-colour partition of a weighted graph that maximises the cut weight.
- [Graph Coloring](graph_coloring.md) -- Assign colours to graph nodes so adjacent nodes do not share a colour.
- [Maximum Independent Set](max_independent_set.md) -- Select the largest subset of graph nodes with no selected edge between them.
- [Vertex Cover](vertex_cover.md) -- Select the smallest vertex subset that covers every graph edge.
- [Travelling Salesman Problem](tsp.md) -- Find the shortest Hamiltonian tour through a random symmetric distance matrix.

<a id="selection-and-packing"></a>

## Selection and packing

Examples that select subsets, cover demands, pack bins, and balance integer weights.

- [Knapsack](knapsack.md) -- Select items that maximise value while respecting a capacity constraint.
- [Bin Packing](bin_packing.md) -- Pack items into the minimum number of fixed-capacity bins.
- [Set Cover](set_cover.md) -- Select the minimum set collection whose union covers the universe.
- [Weighted Set Cover](weighted_set_cover.md) -- Select sets with capacities to cover element demands at minimum cost.
- [Number Partition](number_partition.md) -- Split positive integers into two subsets with nearly equal sums.
- [Portfolio Optimization](portfolio_opt.md) -- Select a fixed-size portfolio while penalising higher-order risk interactions.
- [Portfolio Rebalance](portfolio_rebalance.md) -- Choose signed integer asset weights against a risk matrix and a budget.

<a id="satisfiability-and-higher-order"></a>

## Satisfiability and higher-order

Examples that reduce clauses and higher-order pseudo-Boolean objectives to quadratic models.

- [Max-3-SAT](max3sat.md) -- Find the assignment that satisfies the maximum number of 3-literal clauses.
- [Cubic Optimization](cubic_opt.md) -- Minimise a cubic pseudo-Boolean objective through HOBO degree reduction.
- [Quartic Optimization](quartic_opt.md) -- Minimise a degree-4 pseudo-Boolean objective through two-stage REDUCE chaining.
