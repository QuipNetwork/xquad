# Permutations

Assign N things to N positions so that each thing gets exactly one position
and each position gets exactly one thing. Recognise it whenever "order"
or "visit each once" appears in a problem statement: a tour, or a seating
arrangement of N guests into N chairs.

The encoding is an \\(N \times N\\) binary grid, `x[thing, position] = 1`
meaning that thing sits at that position, with a one-hot constraint on
every row and every column: `ONEHOTR` for "this thing takes exactly one
position," `ONEHOTC` for "this position holds exactly one thing." A
feasible assignment is a permutation matrix -- exactly one `1` per row and
per column -- and the two constraint families together are what force
that shape.

## The Shape, Isolated

`examples/tsp/` is the one example in this repository that embodies this
pattern: `x[city, position] = 1` means a city sits at a tour position, and
the objective adds the distance between consecutive positions' cities on
top of the same row/column one-hot pair
([TSP](../examples/tsp.md), `examples/tsp/runner.py`). Stripping the
distance objective out leaves the pattern on its own -- allocate the grid,
apply both one-hot families, decode with `colfind`:

```python
from xquad.cp import Problem, Types
from xquad.types import XQMXDomain

def build_problem(n: int) -> Problem:
    problem = Problem("PurePermutation")
    num_items = problem.input("num_items", type=Types.Int)
    problem.define_model(size=num_items * num_items, domain=XQMXDomain.BINARY,
                          rows=num_items, cols=num_items)

    with problem.range(0, num_items) as row:
        problem.model.apply_onehot_row(row, penalty=10)
    with problem.range(0, num_items) as col:
        problem.model.apply_onehot_col(col, penalty=10)

    perm = problem.output("perm", type=Types.Vec)
    with problem.range(0, num_items) as position:
        perm.append(problem.sample.colfind(col=position, value=1))
    return problem
```

Compiling for `n = 3` and running the encoder on the Rust VM, then solving
the result with `SolverDWaveCPU` at `seed=42`. All six permutations of three
things are tied at `-60`, so a different seed decodes to a different one and
both lines below still hold:

```text
decoded permutation (thing at each position): [0, 1, 2]
is a permutation of range(N): True
energy: -60
```

`xquad verify --text` accepts the compiled encoder:

```text
ok: permutation_encoder.xqasm (29 instructions)
```

and `xquad run --text ... --calldata 3` reproduces the same model
deterministically from the assembled bytecode, independent of the Python
solve above:

```text
outputs:
  [0] = Model(XqmxModel { domain: Binary, size: 9,
    linear: {0: -20, ..., 8: -20},
    quadratic: {(0, 1): 20, (0, 2): 20, ..., (7, 8): 20}, rows: 3, cols: 3 })
```

Nine linear terms at `-20` (one per cell: each cell belongs to one row and
one column, so it picks up `-penalty` twice) and eighteen quadratic terms at
`+20` (every same-row and every same-column pair) -- exactly `ONEHOTR`'s and
`ONEHOTC`'s expansions from [High-Level
Constraints](../xqvm/instructions/constraints.md) superimposed on the same
grid. `examples/tsp/` builds the identical row/column structure and adds
distance coefficients on top; nothing about the permutation half changes
when an objective joins it.

## Cost in Variables

The grid is \\(N^2\\) variables -- no slack, no auxiliary variables, since
both `ONEHOTR` and `ONEHOTC` only rewrite coefficients on cells that already
exist. That quadratic growth is the real cost of this pattern: doubling the
number of things to order quadruples the variable count, before an
objective term is added.

## Failure Mode

Applying only one axis -- `ONEHOTR` without `ONEHOTC`, or the reverse --
does not raise an error; it produces a model where each thing still picks
exactly one position, but nothing stops two things from picking the same
one. That looser rule is [Assignment](assignment.md). The other failure
is structural rather than semantic: `ONEHOTR`/`ONEHOTC` read grid
dimensions from `RESIZE`, and on a model with no grid set, both loops run
zero times and the constraint is silently absent on the Rust VM --
[High-Level Constraints](../xqvm/instructions/constraints.md) calls this
out as the single most expensive mistake in that instruction family, since
neither `xquad verify` nor `xquad run` catches it. The Python reference
VM (`xqvm_py`) does not share that silence: it raises `ValueError`
instead, so which symptom you see depends on `--interpreter`.

<!-- xquad:defect QUI-1022 -->
> **Known issue.** On the Rust VM, applying `ONEHOTR`/`ONEHOTC` with no grid set silently
> omits the constraint instead of raising; see
> [High-Level Constraints](../xqvm/instructions/constraints.md). Report problems at the
> [issue tracker](https://gitlab.com/quip.network/xquad/-/issues).
