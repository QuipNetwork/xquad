# Cookbook

The fourteen examples under `examples/` each solve one problem --
Knapsack, TSP, Graph Coloring -- and each already contains a working
encoding. Nothing in the repository names those encodings as patterns you
can reuse on a problem that is not itself Knapsack or TSP. This chapter
names five recurring shapes instead, each grounded in the example or
examples that embody it and shown as code that actually ran. One further
page covers getting the arithmetic right once a shape is chosen.

This chapter assumes [Modelling](../modelling/) -- it builds
problems with the same `xqcp` calls that chapter introduces and does not
re-explain them. It also assumes
[Quadratic Models](../concepts/quadratic-models.md) for what a penalty
term is and why one exists at all.

## The Five Shapes

| Page | Reach for it when | Canonical example |
| --- | --- | --- |
| [Permutations](permutations.md) | N things need a full ordering: each thing gets exactly one position and each position gets exactly one thing | `examples/tsp/` |
| [Assignment](assignment.md) | N things each pick one of B slots, with no requirement that every slot is used or that N equals B | `examples/bin_packing/` |
| [Selection Under Budget](selection-under-budget.md) | Choose a subset of weighted things without a total exceeding a fixed capacity | `examples/knapsack/` |
| [Mutual Exclusion](mutual-exclusion.md) | Two choices conflict, or one requires another | `examples/graph_coloring/`, `examples/max_independent_set/` |
| [Soft vs. Hard Constraints](soft-vs-hard-constraints.md) | A rule that must hold sits next to a preference that should hold | `examples/portfolio_opt/`, `examples/max3sat/` |

Permutations and Assignment are the same grid with one constraint family
removed -- see [Assignment](assignment.md#failure-mode)
for the direction that mistake most often runs. Selection Under Budget and
Mutual Exclusion overlap at one boundary: a capacity of exactly `1` on a
pair is the same instruction family `EXCLUDE` gives you directly, covered
in [Mutual Exclusion](mutual-exclusion.md#the-same-rule-encoded-without-exclude).
`examples/bin_packing/` and `examples/graph_coloring/` each compose two of
these shapes in one problem (assignment plus a capacity, and assignment
plus exclusion, respectively) -- reading a problem as "which of these
shapes does each part look like" scales to a combined problem the same
way it does to a single-pattern one.

## The Arithmetic Page

[Integer Scaling](integer-scaling.md) is not a shape -- every pattern
above needs it, sooner or later, while turning real-valued problem data
into the integer coefficients every pattern above is written in terms of.
Sizing the penalty weight itself, and reading a solver's output to tell
whether a cheap constraint violation bought a good-looking energy, is
[Constraints](../modelling/constraints.md#enumerate-the-failure-not-the-intuition)'s
territory, not a cookbook page of its own.

## Where This Chapter Stops

None of these pages re-derives what a one-hot, `EXCLUDE`, or `EQUALITY`
constraint expands to in linear and quadratic coefficients -- that table
is [High-Level Constraints](../xqvm/instructions/constraints.md), already
written, and this chapter links to it rather than repeating it. This
chapter is about recognising which instruction family a new problem
needs and what it costs once chosen, not about the instructions
themselves.
