# Assignment

Match each of N things to exactly one of B slots, with no requirement that
every slot receives a thing and no requirement that B equals N. Recognise
it whenever one set is matched into another with no requirement that the
match run both ways: items into bins, nodes onto colours.

This is [Permutations](permutations.md) with the column half removed.
Encode it as an \\(N \times B\\) grid, `x[thing, slot] = 1` meaning that
thing occupies that slot, with a one-hot family on every row only:
each thing picks exactly one slot, but a slot may hold zero, one, or many
things. Dropping the column constraint gives a different rule, not a
weaker form of the same one, and [Permutations](permutations.md#failure-mode)
names the mistake in the other direction: applying only one axis where
both are wanted.

## Recognising the Row-Only Shape

`examples/bin_packing/` assigns each item to exactly one bin -- an
`EQUALITY` constraint per item, unit coefficients, target `1`, over that
item's row of the \\(N \times B\\) grid ([Bin
Packing](../examples/bin_packing.md), `examples/bin_packing/runner.py`):

```python
# Assignment constraint: each item i must go in exactly one bin
# sum_b x[i,b] = 1  for each i
with problem.range(0, num_items) as i:
    row_indices = problem.vec()
    row_coeffs = problem.vec()
    with problem.range(0, num_bins_in) as b:
        row_indices.push(i * num_bins_in + b)
        row_coeffs.push(1)
    problem.model.apply_equality(row_indices, row_coeffs, 1, 200)
```

`EQUALITY` with unit coefficients and target `1` over one row is the same
constraint `ONEHOTR` applies directly --
[High-Level Constraints](../xqvm/instructions/constraints.md#equality-model-indices-coeffs)
states `ONEHOTR`/`ONEHOTC` are the special case of `EQUALITY` with
\\(a_k = 1\\) and \\(b = 1\\). This loop is a by-hand `ONEHOTR`: the model is
defined with both `rows` and `cols` set (`num_items` and `num_bins_in`), so
`apply_onehot_row` would have expressed the same constraint. Bin packing
just writes it out by hand instead of calling it.

Running `examples/bin_packing/runner.py --seed 42 --interpreter rust`
(4 items, 3 bins, capacity `5`, sizes `[1, 2, 1, 1]`) decodes to
`assignment: [0, 2, 0, 1]` -- item 0 in bin 0, item 1 in bin 2, item 2 in
bin 0, item 3 in bin 1. Every item appears in exactly one bin, which is
what the row constraint above guarantees; nothing here guarantees every
bin gets used or that bins fill evenly, since there is no column
constraint pushing in that direction.

Bin packing composes this row-assignment pattern with a second, unrelated
one -- each bin's contents must not exceed its capacity, encoded with
`SLACK` plus `EQUALITY` exactly like the capacity constraint in
[Selection Under Budget](selection-under-budget.md). That capacity half is
not this page's concern; see [Selection Under Budget](selection-under-budget.md)
for the pattern on its own.

## Cost in Variables

The grid costs \\(N \times B\\) variables against
[Permutations](permutations.md)' \\(N^2\\), and the saving is entirely
the grid: it is cheaper exactly when there are fewer slots than things
(\\(B < N\\)). Dropping the column constraint costs nothing and saves
nothing in variables, since neither one-hot family allocates any --
`EQUALITY` with in-range indices does not grow `model.size` on its own
([High-Level Constraints](../xqvm/instructions/constraints.md#equality-model-indices-coeffs)).

## Failure Mode

The one this page warns about runs the other direction from
[Permutations](permutations.md#failure-mode): adding a column one-hot to
an assignment problem that does not want one silently turns "N things
into B slots" into "a bijection between things and slots," which only
has a feasible solution at all when \\(N = B\\). If a `Permutations`-shaped
model with `RESIZE`d grid dimensions comes back consistently infeasible,
check whether the problem actually needs both axes constrained or only
one.
