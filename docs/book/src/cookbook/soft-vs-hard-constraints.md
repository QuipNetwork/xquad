# Soft vs. Hard Constraints

Every rule in a quadratic model is a penalty term, but not every penalty
term means the same thing to the problem it belongs to. A **hard**
constraint is a rule the solution must obey. It is satisfied or it is
violated, with nothing in between, and the only question a penalty weight
answers is how expensive violating it becomes. A **soft** constraint -- a
preference is the more precise name -- is a cost a solution pays
continuously: more of the disfavoured thing costs more, but nothing marks
any amount as forbidden. `apply_equality`, `apply_onehot_row`, `apply_exclude`, and the
rest of the constraint family from [Constraints](../modelling/constraints.md)
are how a hard rule is written. A preference is written directly into the
objective, the way [Objectives](../modelling/objectives.md) covers, with
no constraint call at all.

## Both in One Problem

`examples/portfolio_opt/` carries one of each. The budget is hard --
`sum(x_i) = B` via `apply_equality`, unconditional, penalty `200` --
and the risk term is soft -- a cubic penalty added straight into the
objective for every asset triple whose combination the problem considers
risky, with no accompanying constraint call
([Portfolio Optimization](../examples/portfolio_opt.md),
`examples/portfolio_opt/runner.py`):

```python
# Cubic risk cross-terms via REDUCE
with problem.range(0, num_risk) as t:
    ...
    w = problem.model.reduce(ti, tj, _P_AUX)
    problem.model.quadratic[w, tk].add(sigma)

# Budget constraint: sum(x_i) = B  (EQUALITY with unit coefficients)
...
problem.model.apply_equality(indices, coeffs, budget_in, 200)
```

`reduce` and `ADDQUAD` build the risk cost the same way any other
objective term is built -- see [Objectives](../modelling/objectives.md)
and [High-Level Constraints](../xqvm/instructions/constraints.md#reduce-model)
for `REDUCE`'s own Rosenberg terms, which enforce the auxiliary variable's
algebraic identity, not the risk rule itself. Nothing about `sigma` is a
threshold; it is a coefficient like any other, weighing a return against a
risk directly in the same sum.

## The Discriminating Difference: A Threshold vs. a Continuous Trade-off

Fix four assets with returns `[10, 9, 8, 7]`, a budget of `3`, and one
risky triple `(0, 1, 2, sigma)`, then sweep `sigma`. `run_case` below
covers the encoder half -- compiling the problem, running the encoder to
build the model, and solving it -- the same way the earlier snippets on
this page do. Decoding the returned sample back into a portfolio runs
`programs.decoder` over `[sample, n]`, exactly as
`examples/portfolio_opt/runner.py` does it. That step is elided here
because it is the same on both sides of the comparison:

```python
from examples.portfolio_opt.runner import build_problem
from xquad.vm import VM, VMBackend
from xqsa import build_solver

def run_case(sigma):
    risk_terms = [(0, 1, 2, sigma)]
    problem = build_problem(4, [10, 9, 8, 7], 3, risk_terms)
    programs = problem.compile()
    flat_risk = [v for term in risk_terms for v in term]
    vm = VM(backend=VMBackend.RUST)
    vm.set_calldata([4, [10, 9, 8, 7], 3, 1, flat_risk])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]
    result = build_solver("dwave-cpu", seed=42).solve(model, num_reads=200)
    ...  # decode via programs.decoder and print, as below
```

Decoded and printed, `sigma=1` and `sigma=2` give:

```text
sigma=1: portfolio=[1, 1, 1, 0] total_return=27 energy=-1826
sigma=2: portfolio=[1, 1, 0, 1] total_return=26 energy=-1826
```

Across twenty solver seeds at each `sigma` (`seed in range(1, 21)`),
`sigma=2` always switches to `{0, 1, 3}` (return `26`), stable on every
seed. `sigma=1` does not behave the same way: it sits exactly at the tie,
so the sampler returns either portfolio, split close to evenly across
seeds -- `{0, 1, 2}` on 12 of 20 and `{0, 1, 3}` on 8 of 20, both at the
identical energy `-1826`.
The tie is exact, not approximate: asset `2`'s risk cost only applies
when all three of `0`, `1`, `2` are selected together (`REDUCE`'s product
term), so choosing the risky trio costs `-27 + sigma` against the safer
trio's fixed `-26`; the two costs are equal at `sigma = 1` and the risky
choice stops paying for itself once `sigma > 1`, which is exactly where
the stable switch to `{0, 1, 3}` sets in. A weight that lands exactly on
a tie is not evidence either portfolio is preferred -- it is evidence the
weight is at the boundary, and a single sample there does not tell you
which side you are on. Every selection in this sweep still has exactly
three assets -- the *hard* budget constraint held at every `sigma`,
unaffected -- while *which* three assets the soft risk cost favoured
changed at its break-even point, with a tie rather than a clean switch
exactly at that point. That contrast is what the two kinds of constraint buy:
a hard rule's own penalty weight only decides whether the rule can be
broken at all ([Quadratic Models](../concepts/quadratic-models.md#choosing-a-penalty-weight),
[Constraints](../modelling/constraints.md#enumerate-the-failure-not-the-intuition)),
while a soft term's weight decides how much of the preference the
solution actually buys.

## Soft, With No Hard Constraint at All

`examples/max3sat/` sits at the other end: every clause is a preference,
none is a rule the solution must satisfy, and the problem carries no
`apply_*` call anywhere ([Max-3-SAT](../examples/max3sat.md)). A clause
`(i, j, k)` costs `P_CLAUSE` exactly when all three of its literals are
false, added directly to the objective; a solution that satisfies zero clauses is
legal, just expensive. Running `examples/max3sat/runner.py --seed 42` (6
variables, 8 clauses) satisfies all eight (`satisfied: 8`, `energy: -80`)
for this random instance, but nothing in the model would reject a sample
that satisfied fewer -- there is no `valid` check tied to clause
satisfaction, because there is no constraint to check.

The `valid` flag draws exactly this line. A hard constraint is an
`apply_*` call, and the generated verifier emits one check per call:
`portfolio_opt`'s budget is an `apply_equality`, so a sample selecting all
four assets against a budget of `3` comes back `valid: 0`. Max-3-SAT's
clauses are objective terms, so nothing in its verifier looks at them and
a sample satisfying two clauses is as `valid` as one satisfying eight. If
a rule must hold, declare it as a constraint;
[Verification](../running/verification.md#what-the-generated-verifiers-valid-flag-covers)
says what `valid` then covers.

## Cost in Variables

Neither pattern taxes the model directly -- a soft term is ordinary
`linear`/`quadratic` coefficient writes, and a hard constraint's cost is
whatever its own instruction adds (`ATLEAST`/`ATLEASTW`/`REDUCE` grow
`model.size`; `EXCLUDE`/`IMPLIES`/`EQUALITY`-with-in-range-indices do
not). `portfolio_opt`'s risk terms cost one `REDUCE` auxiliary variable
each, so the model grows from `n` to `n + num_risk`, independent of the
hard budget constraint sharing the same model.

## Failure Mode

Writing a preference as a hard constraint at a large penalty forces an
all-or-nothing rule where a graded cost was wanted -- the model will never
choose to pay a little of the disfavoured thing even when the rest of the
objective would gain more from it, because the constraint's cost
jumps from `0` to `penalty * d^2` the instant it is touched at all
([Constraints](../modelling/constraints.md#enumerate-the-failure-not-the-intuition)).
The reverse mistake, writing a rule that must always hold as a soft
objective term, is worse: nothing stops the solver from breaking it
whenever the rest of the objective offers enough of a reward, and there is
no way to check afterward that it did not, the way `max3sat`'s missing
`valid` check above demonstrates directly.
