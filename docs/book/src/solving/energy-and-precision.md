# Energy and Precision

Every backend in this chapter returns the same kind of number,
computed the same way, no matter what found the sample: an integer.
This page covers how that number is computed and compared, and where
precision is lost or preserved as a model moves from an exact integer
formula onto real hardware.

## The Formula

A sample's energy with respect to a model is:

$$E = \sum_i \text{linear}[i] \cdot x_i \;+\; \sum_{i<j} \text{quadratic}[i,j] \cdot x_i \cdot x_j$$

identical to the model's Hamiltonian from
[Quadratic Models](../concepts/quadratic-models.md), evaluated at one
concrete assignment `x`, and identical to the XQVM `ENERGY` opcode (see
[Energy Evaluation](../xqvm/instructions/energy.md)). A verifier program
run through XQVM and `xqsa`'s own recomputation compute the same
formula two independent ways.

## The Precision Contract

`SolverResult.energy` must equal `compute_energy(model, sample)`
exactly, an integer-to-integer comparison with zero tolerance for
drift. Every XQMX
coefficient and every variable assignment is an integer, so the energy
formula is a sum of integer products -- always an exact integer, never
a float that merely rounds to the right answer. `Solver._recompute_energy()`
calls `compute_energy()` and casts to `int`; every backend in this
chapter uses it instead of trusting whatever float its underlying
library reports. That raw float, where one exists, survives only as
`metadata["params"]["raw_energy"]`, for diagnostics -- never as the
authoritative value.

A verifier program never has to take a solver's word for its own
answer. It recomputes the identical integer independently with
`ENERGY`, and `==` either holds or it does not.

## A Large Penalty Does Not Corrupt the Model

[Choosing a Penalty Weight](../concepts/quadratic-models.md#choosing-a-penalty-weight)
argues that a large \\(P\\) cannot corrupt a model, because energy
differences between feasible assignments survive exactly however large
\\(P\\) gets. Take that page's three-node Max-Cut model with a one-hot
penalty \\(P \cdot (x_0 + x_2 - 1)^2\\) added on top, and score two
feasible assignments and one infeasible one through `compute_energy()`
at three wildly different weights:

```text
P=           2 | E(feasible_a)=           -8 E(feasible_b)=           -6 E(infeasible)=  -8 diff= 2
P=        1000 | E(feasible_a)=        -1006 E(feasible_b)=        -1004 E(infeasible)=  -8 diff= 2
P=  2000000000 | E(feasible_a)=  -2000000006 E(feasible_b)=  -2000000004 E(infeasible)=  -8 diff= 2
```

The *difference* between the two feasible assignments stays exactly `2`
at every weight. The penalty term is `0` on both, so what separates them
is the underlying objective alone, and integer arithmetic never lets it
drift.

The infeasible assignment's own energy does not move either -- it sits
at `-8` at every \\(P\\) above. What moves is the two feasible
energies, and they fall without bound as \\(P\\) grows. That is the
missing `+P` constant, not the penalty term itself: the true,
unstorable Hamiltonian adds `+P` to every assignment that violates the
constraint and `+0` to every assignment that satisfies it, so dropping
that constant subtracts a uniform `P` from every stored energy. On the
infeasible sample the missing `+P` exactly cancels the penalty's own
`+P`, leaving it unchanged; on a feasible sample there is nothing to
cancel, so the stored energy falls by `P`.

## What a Large Penalty Costs Instead

Nothing in the model degrades, but two things outside it do, and both
matter to backends in this chapter.

**Fixed-precision hardware loses resolution.** `metal-gpu` computes
coefficients in float32 on the GPU, not the integer arithmetic
`ENERGY` uses -- `result.energy` is still recomputed in exact integers
afterward, but the *search* that finds the sample runs at float32
precision. `cuda-gpu` does not share this: its kernels take
`const double*` throughout, its acceptance draws stay float64, and
`result.energy` is recomputed the same way as every other backend. See
[Local Solvers](local.md#gpu-backends-cuda-gpu-and-metal-gpu) for the
per-backend split. The rest of this section applies to `metal-gpu` and
to fixed-precision annealing hardware, not to every GPU backend.

A float32 mantissa carries about seven decimal digits. Once a term in
the combined Hamiltonian is large enough that a difference of `2` no
longer changes the float, `metal-gpu`'s search cannot distinguish the
two assignments that difference separates, even though `ENERGY` still
resolves them exactly afterward:

```python
import numpy as np
np.float32(-2000000006) == np.float32(-2000000004)   # True -- the 2 vanished, at metal-gpu's width
np.float64(-2000000006) == np.float64(-2000000004)   # False -- still resolved, at cuda-gpu's width
np.float32(-1006) == np.float32(-1004)                 # False -- still resolved
```

At `P = 2000000000` the two feasible assignments above are
computationally indistinguishable to `metal-gpu`'s float32 search even
though `compute_energy()` still tells them apart exactly; at
`P = 1000` they are not. `cuda-gpu`'s float64 search keeps this pair
distinguishable at this scale, though a large enough \\(P\\) would
eventually exhaust its wider but still finite mantissa too. Neither
GPU backend's correctness ever suffers -- the returned energy is
always the exact integer -- but `metal-gpu`'s search can lose the
signal, at penalty weights where `cuda-gpu`'s still keeps it visible.

**Real annealing hardware rescales, and the Quip network encodes at
fixed precision.** A physical D-Wave annealer maps coefficients onto
its analog range with a monotonic, optimum-preserving rescaling, so an
oversized \\(P\\) is not rejected there -- it costs usable range on the
device instead. `SolverQuip` goes further: it scales every coefficient
by `MILLI_SCALE = 1000` into the chain's `i32` fields, so an oversized
coefficient can overflow `i32` and raise `EncodingError` rather than
losing precision silently. `MAX_NATURAL_COEFFICIENT = 2_147_483` is the
exact bound only for a SPIN model; a BINARY model -- the domain every
worked example on this page uses -- goes through a basis change first
that shifts the bound in both directions, and can overflow well before
any single coefficient looks anywhere near that large. See
[Quip Network](quip-network.md#coefficient-encoding) for the full
derivation.

**A large penalty also raises the barrier between feasible regions,**
independent of any hardware precision question: a heuristic search such
as simulated annealing has to cross that barrier to leave the first
feasible region it reaches, and a larger \\(P\\) makes crossing less
likely within a fixed number of sweeps. Sizing \\(P\\) in practice, and
reading a solver's output to tell which of these failure modes you hit,
belongs to
[Constraints](../modelling/constraints.md#choosing-a-penalty-weight).
