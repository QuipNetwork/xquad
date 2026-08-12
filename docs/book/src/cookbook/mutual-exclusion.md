# Mutual Exclusion

Two choices conflict, and a solution may take at most one of them.
Recognise it in "not both," "at most one," or an adjacency rule that
forbids a shared property between connected things: two colours on
adjacent nodes, two overlapping bookings.

`EXCLUDE` is the direct opcode: pop `penalty`, then two variable indices,
and add `quad[i, j] += penalty` -- a pure coupling term, no linear part,
that costs `penalty` exactly when both variables are `1` and nothing
otherwise ([High-Level Constraints](../xqvm/instructions/constraints.md#exclude-reg)).
This page also covers `IMPLIES`, the directional relative: "picking `i`
requires `j`" rather than "picking both is forbidden."

## `EXCLUDE`, Direct

`examples/graph_coloring/` applies `EXCLUDE` once per edge per colour: two
adjacent nodes may not both hold the same colour
([Graph Coloring](../examples/graph_coloring.md), `examples/graph_coloring/runner.py`):

```python
with problem.range(0, num_edges) as e:
    offset = problem.stow("offset", e * 2)
    u = problem.stow("u", edges_in.get(offset))
    v = problem.stow("v", edges_in.get(offset + 1))
    with problem.range(0, num_colors_in) as c:
        problem.model.apply_exclude((u, c), (v, c), 200)
```

Running `examples/graph_coloring/runner.py --seed 1 --interpreter rust`
(5 nodes, 3 colours, 6 edges) returns `colors: [2, 0, 2, 2, 0]`, `is_valid: true`,
`energy: -1000` -- every one of the six edges checked by hand connects two
nodes with different colours. The default `--seed 42` instance is worth
running too, for what it shows rather than what it proves: with the same
`--n 5 --colors 3` defaults, that seed's random edges happen to form a
4-clique among nodes `{0, 2, 3, 4}` (every pair among them is an edge), and
a 4-clique has no valid 3-colouring at all -- `is_valid: false`, `energy:
-800`, and no adjustment to `EXCLUDE`'s penalty weight changes that.
`EXCLUDE` enforces a rule; it can only enforce a colouring that exists.
Re-running with `--colors 4` on the identical seed-42 graph succeeds
(`is_valid: true`, `energy: -1000`), confirming the instance itself, not
the encoding, was the obstacle.

## The Same Rule, Encoded Without `EXCLUDE`

`examples/max_independent_set/` needs the identical `x_i + x_j <= 1` rule
per edge -- two adjacent nodes cannot both be in the set -- but builds it
with `SLACK` plus `EQUALITY` instead of `EXCLUDE`
([Max Independent Set](../examples/max_independent_set.md)):

```python
edge_indices = problem.vec()
edge_coeffs = problem.vec()
edge_indices.push(ni)
edge_indices.push(nj)
edge_coeffs.push(1)
edge_coeffs.push(1)
problem.slack(edge_indices, edge_coeffs, num_nodes + e, 1)
problem.model.apply_equality(edge_indices, edge_coeffs, 1, 200)
```

Both encode the same inequality; `EXCLUDE` is a one-instruction
convenience for exactly the pairwise case, while `SLACK` + `EQUALITY`
costs one extra slack variable per edge to reach the identical rule
([Selection Under Budget](selection-under-budget.md)'s pattern, applied
here to a capacity of `1`). Reach for `EXCLUDE` whenever the conflict is
pairwise, which it almost always is; reach for the manual form only when
the exclusion needs to compose with other terms already riding the same
index/coefficient vectors.

Running `examples/max_independent_set/runner.py --seed 42` (same 5-node,
6-edge graph as the graph-coloring default above, node set
`{0, 2, 3, 4}` forming the clique) returns `in_set: [0, 1, 0, 0, 1]` --
nodes `{1, 4}`, `is_independent: true`. Running
`examples/vertex_cover/runner.py --seed 42` against the identical graph
returns `cover: [1, 0, 1, 1, 0]` -- nodes `{0, 2, 3}`, the exact complement
of `{1, 4}` in `{0, 1, 2, 3, 4}`. That is not a coincidence of this one
instance: a set of nodes covers every edge exactly when the nodes left out
share no edge, so a minimum vertex cover and a maximum independent set on
the same graph are always complements of each other. But `vertex_cover`'s
own constraint, `x_i + x_j >= 1` via `ATLEAST`, is not this page's pattern
-- it requires at least one endpoint, the opposite direction from
forbidding both. Do not reach for `EXCLUDE` when a problem says "at least
one of," even on the same graph shape that motivates exclusion elsewhere.

## `IMPLIES`

No example in this repository calls `apply_implies`. A minimal
demonstration, run rather than assumed, checks the sign directly:

```python
from xquad.cp import Problem, Types
from xquad.types import XQMXDomain
from xquad.vm import VM, VMBackend

problem = Problem("ImpliesDemo")
n = problem.input("n", type=Types.Int)
problem.define_model(size=n, domain=XQMXDomain.BINARY)
problem.model.apply_implies(0, 1, 50)   # picking 0 requires picking 1
problem.model.linear[0].add(-30)        # a reason to pick 0 at all

programs = problem.compile()
vm = VM(backend=VMBackend.RUST)
vm.set_calldata([2])
vm.set_output_slots(1)
vm.run(programs.encoder)
model = vm.outputs()[0]
```

Reading the resulting coefficients back and evaluating all four
assignments by hand:

```text
linear: {0: 20}, quadratic[0,1]: -50
x0=0 x1=0 -> H=0
x0=0 x1=1 -> H=0
x0=1 x1=0 -> H=20
x0=1 x1=1 -> H=-30
```

`x0=1, x1=0` -- picking `0` without `1`, the forbidden combination -- costs
`20` more than leaving both off, and exactly `50` more than the legal
`x0=1, x1=1`, matching the `penalty=50` passed in. The `-30` reward on
`x0` alone is what gives the solver a reason to pick it in the first
place; without it, `x0=0` would dominate trivially and the implication
would never be tested. `IMPLIES`' own expansion,
`linear[i] += penalty`, `quad[i, j] += -penalty`
([High-Level Constraints](../xqvm/instructions/constraints.md#implies-reg)),
is exactly what produced `20` and `-50` here: `linear[0] = -30 + 50 = 20`,
no change to `linear[1]`, `quadratic[0,1] = 0 - 50 = -50`.

## Cost in Variables

`EXCLUDE` and `IMPLIES` add no variables at all -- both rewrite an
existing coefficient pair. The `SLACK`-based encoding of the same rule
costs one extra slack bit per pair, since a capacity of `1` needs
\\(S = \lfloor \log_2(1) \rfloor + 1 = 1\\) bit
([SLACK](../xqvm/instructions/vector-ops.md#slack)).

## Failure Mode

A graph that needs more colours than it is given, as the graph-coloring
seed-42 default above shows, is not a penalty-tuning problem: no `penalty`
value turns an infeasible instance feasible, since the penalty only
controls how costly a violation is, not whether one is avoidable. If
`EXCLUDE` or `IMPLIES` constraints keep the best returned sample stuck at
a nonzero violation at more than one penalty weight, check whether
the underlying combinatorial structure allows a solution to exist before
reaching for [penalty-weight guidance](../modelling/constraints.md#enumerate-the-failure-not-the-intuition).
