# Integer Scaling

Every XQMX coefficient is an `i64`, and every arithmetic operation on one
is exact -- [Quadratic Models](../concepts/quadratic-models.md) and
[Energy and Precision](../solving/energy-and-precision.md) both rest on
that fact. Real problem data rarely arrives as integers: prices, weights
in kg, fractional returns. This page covers turning that data into
`i64` coefficients without losing precision the problem needs, and what a
scale factor costs once it is chosen. No example in this repository has
fractional inputs to scale, so everything below is a fresh, executed
worked instance rather than a citation.

## Choosing the Scale Factor

Multiply every fractional value by a factor large enough that the result
is exactly an integer, and use that integer as the coefficient. The
factor has to be large enough for *every* value in the problem, not just
the ones that look round. A four-item knapsack with quarter-kilogram
weights and cent-precision values:

```python
weights_f = [2.50, 3.75, 1.25, 4.00]
values_f = [10.20, 15.75, 8.40, 20.00]
capacity_f = 7.50

def scale(values, factor):
    scaled = [round(v * factor) for v in values]
    for v, s in zip(values, scaled):
        assert abs(v * factor - s) < 1e-9, (v, factor, s)   # catch silent rounding
    return scaled
```

Scaling by `4` -- enough for the weights, which are all quarters -- fails
the assertion the moment it reaches the values: `10.2 * 4 = 40.8` is not
integral (rounds to `41`), so `scale()` raises before the demo builds a
model from this factor:

```text
AssertionError: (10.2, 4, 41)
```

The values are cent-precision but not all quarters (`10.20`, `8.40` are
not multiples of `0.25`), so `4` is not enough. The minimal factor that
is exact for every value here is the least common multiple of every
value's decimal denominator -- `20` (every one of these numbers is a
multiple of `0.05`), not the naive `100` a "two decimal places" guess
would reach for:

```python
from fractions import Fraction
from math import lcm
denoms = [Fraction(v).limit_denominator(10000).denominator
          for v in weights_f + values_f + [capacity_f]]
lcm(*denoms)   # 20
```

At `factor = 20`: `weights = [50, 75, 25, 80]`, `values = [204, 315, 168,
400]`, `capacity = 150`. Building this through `xqcp` (the same
`SLACK` + `EQUALITY` shape [Selection Under Budget](selection-under-budget.md)
covers, at the penalty weight of `4` derived in [Coefficient
Magnitude](#what-scaling-costs-coefficient-magnitude-and-two-backends-that-disagree)
below), compiling, running the encoder on the Rust VM, solving with
`SolverDWaveCPU`, and dividing the decoded totals back by the scale
factor:

```text
selection=[1, 1, 1, 0] total_weight=7.5 total_value=34.35 capacity_ok=True energy=-90687
```

`7.5` and `34.35` are exact -- not rounded back, computed by an integer
sum divided by `20` with no remainder, since `20` was chosen to be exact
for every input. `xquad verify` accepts the compiled encoder (43
instructions), and the same selection, `{0, 1, 2}`, comes back
identically across five different solver seeds.

## What Scaling Costs: Slack Bits

`SLACK`'s bit count is \\(S = \lfloor \log_2(\text{capacity}) \rfloor + 1\\)
([SLACK](../xqvm/instructions/vector-ops.md#slack)) -- logarithmic in the
capacity, but the capacity that matters is the *scaled* one. The naive
`factor = 100` scaling of the same instance needs `10` slack bits
(`capacity = 750`); the minimal exact `factor = 20` needs `8`
(`capacity = 150`). Scaling five times more finely than the data
requires costs two extra variables here, and the gap widens as the
capacity grows -- an unnecessarily large scale factor is not free, even
before its effect on coefficient magnitude below.

## What Scaling Costs: Coefficient Magnitude, and Two Backends That Disagree

A penalty weight multiplies every constraint coefficient
([High-Level Constraints](../xqvm/instructions/constraints.md#equality-model-indices-coeffs)),
so a scale factor and a penalty compound. Using the *loose* safe penalty
[Constraints](../modelling/constraints.md#when-you-cannot-enumerate) gives
as a fallback -- one more than the sum of absolute linear coefficients,
`1088` for the values above, since the rule is `penalty` greater than
that sum -- against the `factor = 20` model:

```text
loose_penalty=1088: max |coefficient| = 23,953,408
fits MAX_NATURAL_COEFFICIENT (2,147,483): False
```

[Quip Network](../solving/quip-network.md#coefficient-encoding) confirms
the exact bound directly from `xqsa.quip_codec`:

```python
from xqsa.quip_codec import MILLI_SCALE, MAX_NATURAL_COEFFICIENT
print(MILLI_SCALE, MAX_NATURAL_COEFFICIENT)
# 1000 2147483
```

The loose bound, safe on its own terms, overflows `SolverQuip`'s
milli-scale `i32` encoding more than tenfold at this scale factor.
Enumerating this specific instance the way
[Constraints](../modelling/constraints.md#enumerate-the-failure-not-the-intuition)
enumerates its own -- sixteen subsets, tightest violator `{0, 2, 3}` at
weight `155`, value gap `85` over an excess of `5` -- gives a tight
threshold of `85 / 5^2 = 3.4`, so the smallest safe integer penalty is
`4`, not `1088`:

```text
tight penalty=4: max |coefficient| = 88,064
fits MAX_NATURAL_COEFFICIENT: True
```

`4` clears the tight threshold and the solver still returns the correct
optimum, `{0, 1, 2}`, identically across five seeds -- the two hundred
seventy-two-fold gap between the loose and tight penalty is exactly what
made the difference between overflowing `SolverQuip`'s encoding and
fitting it comfortably. This is [Constraints](../modelling/constraints.md#when-you-cannot-enumerate)'s
own warning about the loose bound being loose, made concrete by a scale
factor large enough to expose it: a bound that is merely *safe* at
natural scale can become the deciding factor once a scale factor
multiplies every coefficient it touches.

The same coefficient growth affects `metal-gpu`'s float32 search
differently from `cuda-gpu`'s float64 one, the way
[Energy and Precision](../solving/energy-and-precision.md#what-a-large-penalty-costs-instead)
covers for an unscaled model. At the loose penalty above, the resulting
solved energy (`-24,480,687`, order \\(10^7\\)) is past float32's roughly
seven decimal digits of resolution for a difference of `1`, but not for a
difference of `100`:

```python
import numpy as np
e = -24480687   # this model's actual solved energy at the loose penalty
np.float32(e) != np.float32(e - 1)     # False -- the 1-unit difference is lost
np.float32(e) != np.float32(e - 100)   # True -- a 100-unit difference still resolves
```

A scaling choice that looks safe by natural-scale reasoning can cost
`metal-gpu` resolution `cuda-gpu` keeps, at the identical model -- check
the actual coefficient magnitudes your factor produces against the target
backend, not just against the input data's precision.

## Cost in Variables

\\(S = \lfloor \log_2(\text{scaled capacity}) \rfloor + 1\\) slack bits on
top of the item count, same formula as
[Selection Under Budget](selection-under-budget.md), with the scaled
capacity -- not the original -- as the input.

## Failure Mode

Picking a scale factor from "how many decimal places does this look
like" rather than the actual least common multiple of every value's
denominator either drops precision silently (a factor too small, caught
here only because the demo asserts the rounding was exact) or costs
variables and coefficient headroom for no reason (a factor larger than
any value needs, the `100` vs. `20` case above). Compute the minimal
exact factor once, from every value the problem uses, rather than
guessing a round number.
