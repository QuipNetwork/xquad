<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml` by
  `scripts/gen-example-docs.py`.
  Edit the manifest, then run `make regen-docs`.
-->

# XQuad Examples

Fourteen self-contained optimisation problems, one directory each. Every
directory holds a `README.md` describing the formulation and a `runner.py`
that builds the model, solves it, and verifies the result end to end.

Run one from the repository root:

```sh
uv run python examples/maxcut/runner.py --seed 42
```

Every runner accepts `--seed`, `--interpreter` (`python` or `rust`),
`--solver`, and `-o`/`--output`. Problem size flags vary; each directory's
`README.md` carries the exact table.

## Graph problems

Examples that encode graph cuts, colouring, covers, independent sets, and tours.

- [Max-Cut](maxcut/README.md) -- Find a 2-colour partition of a weighted graph that maximises the cut weight.
- [Graph Coloring](graph_coloring/README.md) -- Assign colours to graph nodes so adjacent nodes do not share a colour.
- [Maximum Independent Set](max_independent_set/README.md) -- Select the largest subset of graph nodes with no selected edge between them.
- [Vertex Cover](vertex_cover/README.md) -- Select the smallest vertex subset that covers every graph edge.
- [Travelling Salesman Problem](tsp/README.md) -- Find the shortest Hamiltonian tour through a random symmetric distance matrix.

## Selection and packing

Examples that select subsets, cover demands, pack bins, and balance integer weights.

- [Knapsack](knapsack/README.md) -- Select items that maximise value while respecting a capacity constraint.
- [Bin Packing](bin_packing/README.md) -- Pack items into the minimum number of fixed-capacity bins.
- [Set Cover](set_cover/README.md) -- Select the minimum set collection whose union covers the universe.
- [Weighted Set Cover](weighted_set_cover/README.md) -- Select sets with capacities to cover element demands at minimum cost.
- [Number Partition](number_partition/README.md) -- Split positive integers into two subsets with nearly equal sums.
- [Portfolio Optimization](portfolio_opt/README.md) -- Select a fixed-size portfolio while penalising higher-order risk interactions.

## Satisfiability and higher-order

Examples that reduce clauses and higher-order pseudo-Boolean objectives to quadratic models.

- [Max-3-SAT](max3sat/README.md) -- Find the assignment that satisfies the maximum number of 3-literal clauses.
- [Cubic Optimization](cubic_opt/README.md) -- Minimise a cubic pseudo-Boolean objective through HOBO degree reduction.
- [Quartic Optimization](quartic_opt/README.md) -- Minimise a degree-4 pseudo-Boolean objective through two-stage REDUCE chaining.

## Further reading

- [Using the Examples](../docs/book/src/examples/using-examples.md) -- what every
  directory has in common, how to run one, and how to adapt one into a problem
  of your own
- [Modelling with XQCP](../docs/book/src/modelling/README.md) -- the DSL these
  runners are written in
- [Cookbook](../docs/book/src/cookbook/README.md) -- the recurring encoding
  patterns these examples are built from
