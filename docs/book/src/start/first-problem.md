# First Problem

This page runs one problem from nothing to a real answer. It does not
explain how the pieces work -- [What Happened](what-happened.md) does that
once you have seen a result to explain.

## Max-Cut

Max-Cut splits a graph's nodes into two groups to maximise the total
weight of edges crossing between them.
[Quadratic Models](../concepts/quadratic-models.md#a-worked-example-max-cut)
derives its formulation by hand on a three-node graph; `examples/maxcut/`
is the same problem, runnable, on a graph you pick the size of.

## Run It

With XQuad installed and the examples checked out, per [Install
XQuad](./):

```sh
$ uv run python examples/maxcut/runner.py --seed 42
{
  "_note": "canonical CI golden",
  "_seed": 42,
  "cut_weight": 354,
  "energy": -354,
  "n": 5,
  "partition": [
    0,
    1,
    0,
    0,
    1
  ],
  "valid": 1
}
```

No flags beyond `--seed` are required: `--n` defaults to `5`, and
`--solver` defaults to `dwave-cpu`, the baseline that needs no hardware or
credentials.

## Reading the Result

`partition` is the answer: one `0` or `1` per node, five nodes here,
naming which side of the cut each node is on. `cut_weight` is the
total weight of edges connecting a `0`-side node to a `1`-side node --
the quantity Max-Cut maximises. `energy` is `-354`, the negative of
`cut_weight`, because the model minimises `-1` times the cut weight to
turn maximising into the minimising every XQuad model does; see
[Quadratic Models](../concepts/quadratic-models.md#a-worked-example-max-cut)
for why that sign flip is exact. `valid: 1` confirms every value in
`partition` is `0` or `1` -- the only thing this problem's verifier
checks, since Max-Cut declares no constraint beyond that.

## The Same Answer, a Different Machine

```sh
$ uv run python examples/maxcut/runner.py --seed 42 --interpreter rust
```

`--interpreter` picks which XQVM runs the compiled programs: the
pure-Python reference VM (the default above) or the Rust interpreter.
The encoder, verifier, and decoder are deterministic per interpreter --
same bytecode in, same output out, on either one. The solve sitting
between them is pinned only to the seed and the `dwave-samplers`
version: `solver.solve(model)` runs simulated annealing, so a different
version of that library can return a different valid sample for the same
seed. For this seed the
solve happens to land on the same sample either way, so `cut_weight`,
`energy`, and `partition` also match above. [Three
Programs](../concepts/three-programs.md#a-concrete-run) runs this exact
seed and size on both interpreters side by side and says more about what
is and is not guaranteed to match.

Next: [What Happened](what-happened.md) names each step this run took, in
order, and links to where each one is covered in full.
