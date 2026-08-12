# What Happened

[First Problem](first-problem.md) ran `examples/maxcut/runner.py` and
printed a partition, a cut weight, and an energy. This page is an index:
it names the five moves that run made, in order, and points at the page
that covers each one in full, rather than explaining them again here.
[Toolchain Map](../concepts/) has its own diagram of the same
pipeline, if a picture is what you want first.

## Five Moves, One Command

1. **A problem became three programs.** `build_problem` in
   `examples/maxcut/runner.py` describes Max-Cut once, in Python, with the
   `xqcp` DSL; `problem.compile()` turned it into an encoder, a verifier,
   and a decoder, sharing no state. [Three Programs](../concepts/three-programs.md)
   covers why there are three and not one, and walks this exact run
   [step by step](../concepts/three-programs.md#a-concrete-run).
2. **The encoder ran and built a model.** [Quadratic Models](../concepts/quadratic-models.md)
   covers the Hamiltonian it built, specialised to this graph's weights.
3. **A solver minimised it.** `dwave-cpu`, the default backend, ran
   simulated annealing over the model and returned a sample.
   [Backends](../concepts/backends.md) covers the other four solvers this
   same model could have gone to instead, unchanged.
4. **The verifier checked the sample and computed its energy
   independently.** `energy: -354` was not relayed from the solver; see
   [Quadratic Models: Energy](../concepts/quadratic-models.md#energy) for
   that computation.
5. **The decoder turned the sample into `partition`.** See
   [Outputs and Decoding](../modelling/outputs-and-decoding.md) for what
   a decoder can read and what it hands back.

`--interpreter rust` in the previous page swapped which machine ran steps
2, 4, and 5. [Three Programs](../concepts/three-programs.md#a-concrete-run)
covers what is and is not guaranteed to match between the two
interpreters when that swap happens.

## Where to Go From Here

- **[Toolchain Map](../concepts/)** -- every piece named above,
  in one table, with what each hands to the next.
- **[Modelling](../modelling/)** -- write your own `Problem`
  instead of reading someone else's; builds on a second running example,
  Knapsack, from the first input declaration onward.
- **[Running Programs](../running/)** -- drive an already-compiled
  program from Python yourself, the way `runner.py` does internally.
- **[Solving Overview](../solving/)** -- the five solvers behind
  step 3, and what each needs installed before you can reach it.
- **[Using the Examples](../examples/using-examples.md)** -- turn Max-Cut,
  or any of the other thirteen examples, into a problem of your own.
