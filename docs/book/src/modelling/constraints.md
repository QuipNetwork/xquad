# Constraints

A constraint is a rule a solution must obey. XQCP has no separate mechanism
for rules -- every constraint call turns into a penalty term added to the
model's Hamiltonian, the same `H` [Quadratic Models](../concepts/quadratic-models.md)
describes. This page covers the constraint forms the DSL exposes, when to
reach for each, and how to size the penalty weight in practice. For the
linear/quadratic coefficient deltas each opcode produces, see
[High-Level Constraints](../xqvm/instructions/constraints.md); this page does
not repeat that table.

Every constraint method hangs off `problem.model`, the `ModelRef` that
`define_model()` makes available, and every one takes a `penalty` argument.

## The Constraint Forms

| DSL call | Enforces | Reach for it when |
| --- | --- | --- |
| `model.apply_onehot_row(row, penalty)` | Exactly one variable in grid row `row` is 1 | A 2D model needs "exactly one choice per row" -- one city per tour position |
| `model.apply_onehot_col(col, penalty)` | Exactly one variable in grid column `col` is 1 | The column-wise mirror -- one position per city |
| `model.apply_exclude(a, b, penalty)` | `a` and `b` are not both 1 | Two choices conflict and picking both is meaningless or invalid |
| `model.apply_implies(a, b, penalty)` | If `a` is 1, `b` is 1 | One choice requires another -- selecting a route requires its start node open |
| `model.apply_equality(indices, coeffs, target, penalty)` | `sum(coeffs[k] * x[indices[k]]) == target` | A weighted sum equals an exact value -- one-hot is a special case of this (all-1 coefficients, target 1); `exclude` is not, despite the resemblance |
| `model.apply_atleast(indices, k, penalty)` | At least `k` of `indices` are 1 | A minimum count, unweighted -- cover at least `k` elements |
| `model.apply_atleastw(indices, coeffs, k, penalty)` | `sum(coeffs[j] * x[indices[j]]) >= k` | A minimum weighted sum -- cover at least `k` units of capacity |
| `model.apply_inequality(indices, coeffs, target, capacity, penalty)` | `sum(coeffs[k] * x[indices[k]]) <= capacity` | A capacity-style bound in one call -- composes `problem.slack()` and `apply_equality()`, shown separately below. The third parameter is named `target` in the source but is not a target value: it is where slack variables begin, normally the count of real variables. `capacity` is the bound. Pass it positionally |

`onehot_row`/`onehot_col`, `exclude` and `implies` take coordinates directly;
on a 2D model those coordinates can be `(row, col)` tuples, flattened
automatically. `equality`, `atleast`, `atleastw` and `inequality` instead
take two vector registers built with `problem.vec()` and `.push()`:
`indices` names which variables participate, `coeffs` (where present)
weights them.

A minimal \\(2 \times 2\\) grid problem exercises the row/column/pairwise forms together:

```python
problem.define_model(size=n * n, domain=XQMXDomain.BINARY, rows=n, cols=n)
with problem.range(0, n) as r:
    problem.model.apply_onehot_row(r, penalty=50)
with problem.range(0, n) as c:
    problem.model.apply_onehot_col(c, penalty=50)
problem.model.apply_exclude((0, 0), (1, 1), penalty=50)
problem.model.apply_implies((0, 1), (1, 0), penalty=50)
```

Compiling and running this for `n = 2` produces a model whose linear and
quadratic maps match the row/column penalties plus the two pairwise terms:

```text
linear:    {0: -100, 1: -50, 2: -100, 3: -100}
quadratic: {(0, 1): 100, (0, 2): 100, (0, 3): 50, (1, 2): -50, (1, 3): 100, (2, 3): 100}
```

`atleast` and `atleastw` allocate slack variables and grow `model.size`
directly, as part of their own expansion -- `ATLEAST`'s own expansion in
[HLF.md](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/HLF.md)
builds the slack terms itself rather than composing with the `SLACK`
instruction below. That is a real difference, not an implementation
detail: `SLACK` takes no model operand and only appends to the
`indices`/`coeffs` vectors it is given, so it never touches `model.size`
by itself. A `problem.slack()` call grows the model only later and
indirectly, when a subsequent `apply_equality()` widens it to
`max(indices) + 1` -- a `problem.slack()` result that `apply_equality()`
never consumes leaves the model unchanged. Each `atleast`/`atleastw` call,
by contrast, allocates `floor(log2(max_excess)) + 1` slack variables,
where `max_excess` is `N - k` for `atleast` and `sum(coeffs) - k` for
`atleastw`, and zero slack variables when `max_excess` is zero or less.
A 3-item
`apply_atleast(idx, 2, penalty=30)` allocates one slack variable, since
`max_excess = 3 - 2 = 1`. `apply_atleastw(idx, coeffs, 3, penalty=30)` on
the same three indices then allocates zero more for `coeffs = [1, 1, 1]`,
one more for `coeffs = [1, 1, 2]`, or two more for `coeffs = [1, 2, 3]`.

## Turning an Inequality into an Equality

None of the forms above is `<=` or `>=` on a plain sum -- `atleast`/`atleastw`
cover `>=`, but a capacity constraint like knapsack's ("total weight at most
`W`") needs the other direction. `problem.slack()` bridges the gap: it
appends binary-weighted slack variables to an `indices`/`coeffs` pair so that
an `EQUALITY` constraint over the combined vector is satisfiable exactly when
the original inequality holds. See [SLACK](../xqvm/instructions/vector-ops.md#slack)
for the bit-width formula.

`examples/knapsack/runner.py` builds its capacity constraint this way:

```python
indices = problem.vec()
coeffs = problem.vec()
with problem.range(0, num_items) as i:
    indices.push(i)
    coeffs.push(weights_in.get(i))

problem.slack(indices, coeffs, num_items, capacity_in)
problem.model.apply_equality(indices, coeffs, capacity_in, 100)
```

`indices`/`coeffs` start as the item weights, one entry per item. `slack`
appends slack variables starting at index `num_items` (the model's current
size) sized to absorb up to `capacity_in` of unused capacity. `apply_equality`
then constrains `sum(weight_i * x_i) + sum(slack bits) == capacity`. If the
items sum to less than capacity, some slack combination fills the gap
exactly. If they sum to more, no slack combination can, because slack only
adds. The compiled encoder shows the two instructions back to back:

```asm
LOAD r0
LOAD r3
SLACK r7 r8

LOAD r3
PUSH 0x64
EQUALITY r4 r7 r8
```

`model.apply_inequality(indices, coeffs, num_items, capacity_in, 100)`
composes exactly these two calls into one and produces the same model.
Reach for the two-call form when you need `indices`/`coeffs` for something
else afterward, or want the two steps visible; reach for `apply_inequality`
otherwise.

### Why the Reported Energy Is Not Just `-total_value`

`EQUALITY`'s expansion adds `penalty * a_k * (a_k - 2b)` and
`2 * penalty * a_k * a_m` terms but drops the constant `penalty * b^2`,
since it shifts every assignment's energy equally and does not change which
one is optimal -- see [HLF.md](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/HLF.md).
Dropping a constant from the model does not drop it from `ENERGY`'s result:
a feasible sample still carries `-penalty * b^2` as a fixed offset, because
the terms that remain evaluate to exactly that once the constraint holds.

Running `examples/knapsack/runner.py --seed 42` selects items worth `46` in
total against a capacity of `18`, at `penalty = 100`, and reports
`energy = -32446`. That is `-46 - 100 * 18^2 = -46 - 32400 = -32446`: the
objective contribution and the constant the equality constraint leaves
behind, added together. [Objectives](objectives.md) isolates the `-46` on
its own; this is where the other `-32400` comes from.

## Choosing a Penalty Weight

[Quadratic Models](../concepts/quadratic-models.md#choosing-a-penalty-weight)
gives a safe upper bound: `penalty` must exceed the largest objective
improvement any violating assignment can buy over the best feasible one.
It is not the tightest bound -- an equality-shaped violation (`equality`,
one-hot, `atleast`, `atleastw`) with integer excess `d` actually pays
`penalty * d^2`, and `exclude`/`implies` pay `penalty` outright, the
`d = 1` case. The rest of this section finds that tighter threshold by
accounting for `d` directly.

### Enumerate the Failure, Not the Intuition

Take a 4-item knapsack: weights `[3, 4, 5, 2]`, values `[4, 5, 6, 3]`,
capacity `7`. The best feasible selection is items `{0, 1}` or `{2, 3}`,
tied at value `9` and weight `7`. Every selection that exceeds capacity is a
candidate the solver might prefer instead, if the penalty is too cheap.
Seven of the sixteen selections exceed it:

| Items | Weight | Value | Excess over capacity | Value gap over 9 | Rejection threshold |
| --- | --- | --- | --- | --- | --- |
| `{0, 2}` | 8 | 10 | 1 | 1 | 1.00 |
| `{1, 2}` | 9 | 11 | 2 | 2 | 0.50 |
| `{0, 1, 3}` | 9 | 12 | 2 | 3 | 0.75 |
| `{0, 2, 3}` | 10 | 13 | 3 | 4 | 0.44 |
| `{1, 2, 3}` | 11 | 14 | 4 | 5 | 0.31 |
| `{0, 1, 2}` | 12 | 15 | 5 | 6 | 0.24 |
| `{0, 1, 2, 3}` | 14 | 18 | 7 | 9 | 0.18 |

The last column is `value gap / excess^2`, because `EQUALITY`'s penalty term
is `penalty * (sum - target)^2`: a selection exceeding capacity by `d` pays
`penalty * d^2`, not `penalty`. The largest entry in that column, not the
largest value gap, sets the threshold: `{0, 2}` needs `penalty > 1`, the
largest threshold in the table, while `{0, 1, 2, 3}` -- the biggest value
gain -- needs only `penalty > 0.18`, because its excess of `7` is squared
away. Reasoning from the value gap alone (`9`, from the worst
offender by value) is safe, but it oversizes the weight here: the raw gap
suggests `penalty > 9`, nine times the `penalty > 1` this problem actually
needs.

Compiling this exact problem and brute-forcing every assignment of the real
QUBO -- 4 item variables plus the 3 slack bits `SLACK` allocates for a
capacity of `7` -- confirms the table: at `penalty = 1`, the true optimum is
a three-way tie at `H = -58` between the two feasible sets and the
infeasible `{0, 2}`; at `penalty = 2`, only the two feasible sets remain,
both at `H = -107`. `penalty = 1` is the exact boundary the table predicts,
not an approximation of it.

A tie at the boundary is not a tie a sampler breaks the same way every
seed. Sampling this exact model at `penalty = 1` with `SolverDWaveCPU`
(200 reads, Rust backend) across eight seeds returns the infeasible
`{0, 2}` on two of the eight, at the identical energy, `-58`, as every
feasible seed; the other six split across the two feasible ties. A single
feasible sample at a borderline weight is not evidence the weight is
safe -- the tie means either outcome is a true optimum, and only the rate
across repeated solves, each with a different solver seed, separates a
weight sitting on the boundary from one below it. A penalty that is
actually below its threshold, rather than sitting on it, behaves
differently: building a second 4-item instance (weights `[5, 4, 2, 5]`,
values `[7, 4, 3, 9]`, capacity `6`) whose tightest threshold works out to
`penalty > 3`, the identical `penalty = 1` is now strictly below it, and
all eight seeds return the infeasible pick, every time, because there the
infeasible assignment is the unique optimum rather than one member of a
tie.

### When You Cannot Enumerate

Most problems are too large to brute-force. Enumerate a small instance of
the same shape if you can, the way the table above does, since the
qualitative lesson -- the tightest violator, not the most valuable one, sets
the threshold -- carries over. When even that is impractical, a safe but
loose fallback exists for a purely linear objective: since each variable
contributes to it at most once, no two full assignments can differ by more
than the sum of the absolute values of every linear coefficient, and any
nonzero integer excess is at least `1`, so `penalty` greater than that sum
always rejects every violation. A quadratic objective needs the same sum
extended to include the absolute value of every quadratic coefficient too,
since a `quadratic[i,j]` term can also flip between two assignments. For the
table above that sum is `4 + 5 + 6 + 3 = 18`, eighteen times the actual
boundary of `1` -- a real bound, not the largest single coefficient, and
worth using only until you can afford to enumerate a representative case.

The seed-42 knapsack run printed in
[One Problem, Once](README.md#one-problem-once) has values
`[5, 4, 18, 3, 19]`, and knapsack's objective is `-values` (see
[Objectives](objectives.md)), so the same fallback sum is
`5 + 4 + 18 + 3 + 19 = 49`. The runner's own `penalty = 100` clears that sum
outright, so it is safe by this loose bound alone, without needing the
per-instance enumeration this page otherwise recommends.

### Too High Does Not Corrupt the Model

Raising `penalty` from `2` to `1000` on the same problem leaves the gap
between the best and second-best true optimum at exactly `1` in both
cases, for the reason
[Quadratic Models](../concepts/quadratic-models.md#choosing-a-penalty-weight)
gives: the penalty term is zero on every feasible assignment, so it cannot
change their relative order.

What a large `penalty` costs instead happens outside the model, on the
hardware that has to represent it -- see
[Energy and Precision](../solving/energy-and-precision.md) for that
trade-off, and
[Selection Under Budget](../cookbook/selection-under-budget.md) for a worked
recipe built on this page.
