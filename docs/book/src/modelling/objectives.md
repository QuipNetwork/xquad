# Objectives

An objective is the part of a model's Hamiltonian you write directly,
as opposed to the part a constraint instruction adds on your behalf --
see [Quadratic Models](../concepts/quadratic-models.md) for what the
Hamiltonian \\(H\\) is and why a solver minimising it is the whole
mechanism. In XQCP terms, an objective is whatever you assign or add to
`model.linear[i]` and `model.quadratic[i, j]` before any constraint
method runs.

## Solvers Minimise. Say So.

Every backend XQuad targets minimises. If your problem is a maximisation,
you have to negate it, and getting this backwards produces a model that
runs, verifies, and confidently returns the worst answer instead of the
best one -- nothing checks that you meant to maximise.

Knapsack maximises total value. `examples/knapsack/runner.py` writes the
objective as:

```python
# Objective: minimise -sum(v_i * x_i)
with problem.range(0, num_items) as i:
    vi = problem.stow("vi", values_in.get(i))
    problem.model.linear[i].add(-vi)
```

`x_i = 1` means item `i` is selected, and `-vi` is the sign flip: instead
of rewarding selection with `+v_i`, the objective penalises it with
`-v_i`, so minimising `H` picks large values, not small ones. Isolating
just this loop -- no capacity constraint -- and running the encoder with
`num_items=5`, `values=[5, 4, 18, 3, 19]`, then computing `ENERGY` against
the sample `[1, 1, 1, 0, 1]` (items 0, 1, 2, 4 selected, values
`5 + 4 + 18 + 19 = 46`) gives:

```text
objective-only energy: -46
expected: -(sum of selected values) = -46
```

Energy is exactly the negative of total value, for any selection, because
that is what `linear[i] = -v_i` means: drop the `-` and the same solver
would minimise total value instead, selecting nothing.

## Assign or Accumulate

`model.linear[i]` and `model.quadratic[i, j]` are `CoefficientRef`
proxies, not values. Three things you can do with one:

| Syntax | Opcode | Effect |
|---|---|---|
| `model.linear[i] = w` | `SETLINE` | Coefficient becomes `w`, replacing whatever was there |
| `model.linear[i].add(w)` | `ADDLINE` | Coefficient becomes `current + w` |
| `x = model.linear[i]` | `GETLINE` | Read the current coefficient as an expression |

The same three exist for `model.quadratic[i, j]`, via `SETQUAD`/`ADDQUAD`/
`GETQUAD`. The distinction matters the moment more than one term touches
the same coefficient -- knapsack's `.add(-vi)` runs once per item, each
touching a different `linear[i]`, so assign and accumulate would agree
there. They stop agreeing as soon as two terms share a coefficient: two
`.add()` calls against the same `linear[0]` with weights `5` and `7`
leave it at `12`; two `=` assignments with the same weights leave it at
`7`, the second overwriting the first outright. Reach for `.add()` any
time a coefficient might accumulate contributions from more than one
place, which for an objective built inside a loop -- the usual shape --
is the common case; reach for `=` when you know this is the
coefficient's only source.

## Quadratic Terms

A quadratic coefficient couples two variables, and the same accumulate
pattern applies. Max-Cut's objective is the DSL rendition of the Max-Cut
coefficient rule
[Quadratic Models](../concepts/quadratic-models.md#a-worked-example-max-cut)
derives by hand -- for each weighted edge `(i, j, w)`:

```python
problem.model.linear[i].add(-w)
problem.model.linear[j].add(-w)
problem.model.quadratic[i, j].add(w * 2)
```

three `.add()` calls, one per term of
\\(\text{linear}[i] \mathrel{+}= -w,\ \text{linear}[j] \mathrel{+}= -w,\ \text{quadratic}[i,j] \mathrel{+}= 2w\\).
`.add()` is what makes this loop correct: a node touched by several edges
accumulates a `-w` contribution from each one, and `=` would let the last
edge silently overwrite every edge before it. Running
`examples/maxcut/runner.py --n 5 --seed 42` confirms the sign on the
result -- `energy: -354` against `cut_weight: 354` -- matching the same
"minimise the negative" pattern knapsack uses, for the same reason.

Coefficient access on a 2D model accepts `(row, col)` tuples in place of
flat indices, for both `linear` and `quadratic` coordinates independently
-- see [Inputs and Model Shape](inputs-and-model.md#1d-and-2d-models) for
the flattening XQCP applies; nothing about objective assignment changes
once a coordinate is a tuple instead of an int.

## Beyond Quadratic: `model.reduce()`

Everything above stops at degree two: one or two variables per term.
`model.reduce(var_a, var_b, p_aux) -> RegLoad` is the only way past that.
It performs a Rosenberg degree reduction: allocate one fresh auxiliary
variable `w` at `model.size`, grow `model.size` by one, and add
enforcement terms so `w` behaves as `var_a AND var_b`:
`p_aux * (x_a*x_b - 2*x_a*w - 2*x_b*w + 3*w)`, which is `0` when
`w == x_a AND x_b` and strictly positive otherwise. It returns `w` as a
`RegLoad`, an ordinary variable index you can use in a further quadratic
term, or feed into a second `reduce()` call to reach one degree higher
still:

```python
w = problem.model.reduce(ti, tj, p_aux)
problem.model.quadratic[w, tk].add(coeff)   # coeff * x_i * x_j * x_k
```

`examples/cubic_opt/runner.py` builds a cubic term this way, one
`reduce()` call per term inside a `problem.range()` loop; `max3sat` and
`portfolio_opt` use the same one-call pattern for their own cubic terms.
`examples/quartic_opt/runner.py` chains two `reduce()` calls to reach a
quartic term -- the reason `reduce()` returns a `RegLoad` rather than
nothing.

`p_aux` is a penalty in its own right, separate from any constraint
penalty in the same model: it has to be large enough that violating the
`w == x_a AND x_b` relationship is never worth it, the same reasoning
[Constraints](constraints.md#choosing-a-penalty-weight) applies to
constraint penalties. Each `reduce()` call that actually executes grows
`model.size` by one, so the final variable count is `size` plus the
number of `reduce()` calls executed -- loops included. `cubic_opt`
allocates one auxiliary variable per cubic term inside its loop, not one
for the whole model.

`reduce()` hangs off `model`, like every constraint form, but it is not
one: XQCP keeps it out of the DSL's constraint bookkeeping deliberately,
since it is a structural transformation of the model, not a domain rule
about a solution. That is also why it lives on this page and not
[Constraints](constraints.md).

## How Large an Objective Gets

An objective's magnitude is bounded by what its coefficients can sum to.
Knapsack's linear coefficients are `-values`, so its objective ranges from
`0` (nothing selected) down to `-49` for `values = [5, 4, 18, 3, 19]`
(everything selected, which for this instance fits the capacity: the
weights `[2, 1, 5, 4, 4]` sum to `16`, under the capacity of `18`) -- a
range fixed entirely by the problem's own numbers, before any constraint
enters the picture. [Constraints](constraints.md) picks a penalty weight relative
to a range like this one; this page stops at producing the range, not
sizing anything against it.

With an objective in place, the next step most problems need is a rule
the objective alone cannot express -- see [Constraints](constraints.md)
for folding one into the same Hamiltonian.
