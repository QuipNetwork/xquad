# Selection Under Budget

Choose a subset of N things, each with a cost, so the total cost does not
exceed a fixed capacity, while maximising (or minimising) something else
about the chosen subset. Recognise it whenever a problem says "at most,"
"no more than," or "fits within" about a sum of per-item weights: a
knapsack, a budget of assets, a shipment under a weight limit.

An inequality (`<=`) combined with a per-item weight that is not always
`1` is what distinguishes this shape from
[Mutual Exclusion](mutual-exclusion.md)'s `<=` on a pair, or a plain
one-hot's `= 1` -- here the bound is a general capacity and the items
carry arbitrary integer weights. None of `ONEHOTR`, `ONEHOTC`, `EXCLUDE`,
or `IMPLIES` express an inequality; `ATLEAST` and `ATLEASTW` cover the
mirrored `>=` direction directly ([Vertex Cover](../examples/vertex_cover.md),
[Weighted Set Cover](../examples/weighted_set_cover.md)), but XQVM has no
`<=` counterpart over an arbitrary weighted sum. `SLACK` bridges the gap, turning the
inequality into an equality that `EQUALITY` can enforce -- see
[Constraints](../modelling/constraints.md#turning-an-inequality-into-an-equality)
for why that substitution is exact, and [SLACK](../xqvm/instructions/vector-ops.md#slack)
for the instruction itself. `model.apply_inequality(indices, coeffs, target,
capacity, penalty)` composes both calls into one -- see
[Constraints](../modelling/constraints.md#the-constraint-forms), including
the caveat there that the `target` parameter is not a target value: it is
where slack variables begin, normally the count of real variables, and
`capacity` is the actual bound. This page assumes both and covers only the
recognition and the cost.

## The Canonical Instance

`examples/knapsack/` is this pattern with nothing else mixed in --
[Knapsack](../examples/knapsack.md), `examples/knapsack/runner.py`. Calling
its own `build_problem` with a fresh instance (six items, capacity `12`)
rather than the seed-42 instance the Modelling chapter already worked
through:

```python
from examples.knapsack.runner import build_problem
from xquad.vm import VM, VMBackend

n = 6
weights = [2, 3, 4, 5, 6, 7]
values = [3, 4, 5, 8, 9, 10]
capacity = 12

problem = build_problem(n, weights, values, capacity)
programs = problem.compile()

vm = VM(backend=VMBackend.RUST)
vm.set_calldata([n, weights, values, capacity])
vm.set_output_slots(1)
vm.run(programs.encoder)
model = vm.outputs()[0]
```

`model.size` comes back `10`: six item variables plus four slack bits,
`S = floor(log2(12)) + 1 = 4`. Solving with `SolverDWaveCPU` and decoding:

```text
selection [0, 0, 0, 1, 0, 1] weight 12 capacity 12 feasible True value 18 energy -14418
verifier energy -14418 valid 1
```

Items 3 and 5 (weights `5` and `7`, values `8` and `10`), weight exactly at
capacity, value `18`. Brute-forcing all 64 subsets confirms `18` is the
true optimum, not just a feasible value: 28 of the 64 fit within capacity,
and no other reaches value `18`. `xquad verify` on the compiled encoder
(43 instructions) passes.

## Cost in Variables

\\(N\\) item variables plus \\(S = \lfloor \log_2(\text{capacity}) \rfloor + 1\\)
slack bits -- logarithmic in the capacity, not the capacity itself, which is
what makes representing "at most a million units" cost twenty extra
variables rather than a million. [Integer Scaling](integer-scaling.md)
covers a consequence of this formula worth knowing before choosing how
finely to scale a fractional capacity: the slack count grows with the
*scaled* capacity, so a needlessly large scale factor costs real variables,
not just larger coefficients.

`examples/bin_packing/` repeats this exact `SLACK` + `EQUALITY` shape once
per bin, for a per-bin capacity rather than a single global one -- see
[Assignment](assignment.md), which covers the rest of that problem's
structure. Its `start_index` argument to `SLACK` is the same fixed
`num_items * num_bins` on every bin's iteration rather than a value that
advances past each bin's own slack allocation, so the slack variables the
different bins' capacity constraints reach for end up shared rather than
distinct. Copying this block as a per-bin template needs that start index
to advance; the capacity-constraint shape itself, in isolation, is the
same one this page covers.

## Failure Mode

A `SLACK`/`EQUALITY` pair that looks right can still be infeasible for a
reason that has nothing to do with the penalty weight: if every item's
weight already exceeds capacity, or the cheapest single item does, no
slack combination rescues it, since slack only adds toward the target,
never subtracts from the real items' contribution
([SLACK](../xqvm/instructions/vector-ops.md#slack)). Check the raw
numbers -- smallest item weight against capacity -- before reaching for
[penalty-weight guidance](../modelling/constraints.md#enumerate-the-failure-not-the-intuition)
to explain a solver that keeps returning something that looks wrong.
