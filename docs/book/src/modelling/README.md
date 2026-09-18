# Modelling Lifecycle

XQCP is a Python-embedded DSL that turns a problem description into three
XQVM assembly programs. You declare a `Problem`'s runtime inputs, allocate
the quadratic model those inputs fill, add objective terms and
constraints, declare what you want back, then call `compile()`. XQCP
records every call you make against `Problem` as you make it and only
turns that recording into `.xqasm` when `compile()` runs -- see
[Compiling](compiling.md) for how the recording becomes three programs.

A problem definition follows one call sequence, in order:

```text
Problem(name) -> input()* -> define_model() -> body* -> output()* -> compile()
```

`input()` calls come first: [Inputs and Model Shape](inputs-and-model.md)
covers why, and what a declared input becomes at run time. `define_model()`
runs exactly once and makes `problem.model` available for the rest of the
body: objective terms, constraints, and loops, in any order, covered
across [Expressions](expressions.md), [Objectives](objectives.md),
[Constraints](constraints.md), and [Control Flow](control-flow.md). Your
first `output()` call marks the start of the decoder section, covered in
[Outputs and Decoding](outputs-and-decoding.md).

## Why XQCP, Not Assembly

[Ways to Use XQuad](../concepts/ways-to-use.md) names six surfaces for
getting a problem into XQuad; XQCP is one of them. Reach for it when you
are modelling a combinatorial problem from scratch and would otherwise
hand-write the same loop-over-variables, add-a-coefficient pattern in
`.xqasm` three times over, once per program -- `Problem` records that
pattern once, and the compiler emits all three. Write `.xqasm` directly
instead when a program is small enough that the three-program duplication
costs nothing to write by hand. Reach for
[`xqvm::InstructionBuilder`](../concepts/ways-to-use.md#rust-embedding)
instead when a Rust host is generating programs and adding a Python step
to emit them buys nothing.

## One Problem, Once

Every page in this chapter builds on the same running example:
`examples/knapsack/runner.py`. It takes item weights, item values, and a
capacity, and chooses the subset of items maximising total value without
exceeding the capacity. `build_problem` is the whole of it:

```python
def build_problem(n: int, weights: list[int], values: list[int], capacity: int) -> Problem:
    problem = Problem("Knapsack")

    num_items = problem.input("num_items", type=Types.Int)
    weights_in = problem.input("weights", type=Types.Vec)
    values_in = problem.input("values", type=Types.Vec)
    capacity_in = problem.input("capacity", type=Types.Int)

    problem.define_model(size=num_items, domain=Domain.BINARY)

    # Objective: minimise -sum(v_i * x_i)
    with problem.range(0, num_items) as i:
        vi = problem.stow("vi", values_in.get(i))
        problem.model.linear[i].add(-vi)

    # Constraint: sum(w_i * x_i) <= W
    indices = problem.vec()
    coeffs = problem.vec()
    with problem.range(0, num_items) as i:
        indices.push(i)
        coeffs.push(weights_in.get(i))

    problem.slack(indices, coeffs, num_items, capacity_in)
    problem.model.apply_equality(indices, coeffs, capacity_in, 100)

    selected = problem.output("selected", type=Types.Vec)
    with problem.range(0, num_items) as i:
        selected.append(problem.sample.getline(i))

    return problem
```

One binary variable per item: `x_i = 1` means item `i` is selected.
Four inputs, one 1D binary model sized to `num_items`, a range loop
building the objective, a second range loop paired with `slack` and
`apply_equality` encoding the capacity constraint, and a decoder loop
reading the solved sample back into a `selected` vector. Running it end
to end:

```sh
$ uv run python examples/knapsack/runner.py --seed 42
{
  "_note": "canonical CI golden",
  "_seed": 42,
  "capacity": 18,
  "energy": -32446,
  "n": 5,
  "selection": [
    1,
    1,
    1,
    0,
    1
  ],
  "total_value": 46,
  "total_weight": 12,
  "valid": 1,
  "values": [
    5,
    4,
    18,
    3,
    19
  ],
  "weights": [
    2,
    1,
    5,
    4,
    4
  ]
}
```

`_note` and `_seed` are the runner's own bookkeeping, the same as in
[Three Programs](../concepts/three-programs.md#a-concrete-run). `energy`
is the full model's Hamiltonian, capacity constraint included, so it is
not simply `-total_value` here the way Max-Cut's `energy` is `-cut_weight`
-- [Objectives](objectives.md) isolates just the objective term's
contribution, and [Constraints](constraints.md) covers what the rest of
`energy` is paying for.

This run is not the optimum: the five weights sum to `16`, under the
capacity of `18`, so every item fits and the true best value is `49`, not
the `46` printed above. A heuristic solver returns a good sample, not a
proven best one. `valid: 1` reports only the verifier's binary-domain
check here, not the capacity constraint; see
[Compiling](compiling.md#what-compile_verifier-and-compile_decoder-do) for
what a verifier actually checks.

[Inputs and Model Shape](inputs-and-model.md) starts with the four
`problem.input()` calls and the `define_model()` line above; the rest of
the chapter works through the remaining lines in the order they appear.

## Where to Go From Here

- **[Inputs and Model Shape](inputs-and-model.md)** -- runtime inputs and
  the 1D/2D model they fill.
- **[Expressions](expressions.md)** -- the operators and functions that
  build a coefficient or a loop bound.
- **[Objectives](objectives.md)** -- turning "minimise this" into
  `linear`/`quadratic` coefficients, sign included.
- **[Constraints](constraints.md)** -- folding a rule into the objective
  as a penalty.
- **[Control Flow](control-flow.md)** -- `range`, `iter`, and `branch`.
- **[Outputs and Decoding](outputs-and-decoding.md)** -- reading a solved
  sample back into your problem's own terms.
- **[Compiling](compiling.md)** -- what `problem.compile()` actually
  produces.
