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

`examples/bin_packing/` assigns each item to exactly one bin -- one
`ONEHOTR` per item row of its grid ([Bin
Packing](../examples/bin_packing.md), `examples/bin_packing/runner.py`):

```python
# Assignment constraint: each item i must go in exactly one bin
with problem.range(0, num_items) as i:
    problem.model.apply_onehot_row(i, 200)
```

`ONEHOTR` is the special case of `EQUALITY` with \\(a_k = 1\\) and
\\(b = 1\\), which
[High-Level Constraints](../xqvm/instructions/constraints.md#equality-model-indices-coeffs)
states directly. Writing it by hand -- a `vec()` pair per row, an index and
a `1` pushed per column, then `apply_equality(indices, coeffs, 1, 200)` --
expresses the same constraint and costs a loop and two vectors per row. The
model here is defined with both `rows` and `cols` set, so `apply_onehot_row`
applies and the hand-rolled form buys nothing.

Bin packing's grid has one row more than it has items. Rows `0..N-1` are
the assignment cells; the extra row `N` holds one indicator variable per
bin, and `apply_implies((i, b), (num_items, b), 200)` opens bin `b` as soon
as any item lands in it. That indicator row is what lets the objective
count bins: a bias spread over the assignment cells would sum to `N` on
every feasible packing, because each item lands in exactly one bin, so it
could not tell a one-bin packing from a three-bin one.

Running `examples/bin_packing/runner.py --seed 42` (4 items, 3 bins,
capacity `5`, sizes `[1, 2, 1, 1]`) decodes to `assignment: [2, 2, 2, 2]`
-- all four items in bin 2, total size `5` against a capacity of `5`. The
`rust` interpreter returns `[0, 0, 0, 0]` instead: which single bin gets
used is a tie, and the two interpreters break it differently. Every item
appears in exactly one bin, which is what the row constraint guarantees;
nothing requires a bin to be used, and the bin-count objective pushes the
other way.

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
