# Energy Computation

## Formula

The Hamiltonian energy of a sample with respect to a model is:

```
E = sum_i( linear[i] * x_i ) + sum_{i<=j}( quadratic[i,j] * x_i * x_j )
```

Where:
- `linear[i]` is the coefficient for variable `i` from the model
- `quadratic[i,j]` is the coupling coefficient for variables `i` and `j` from the model (with `i <= j`; the diagonal is legal -- see [../xqvm/HLF.md](../xqvm/HLF.md#the-diagonal))
- `x_i` is the variable assignment for `i` from the sample

This is identical to the XQVM `ENERGY` opcode (see [../xqvm/HLF.md](../xqvm/HLF.md)).

## Precision Contract

`SolverResult.energy` must equal `model.energy(sample)` exactly. This is an integer-to-integer comparison with no tolerance.

The `Solver` base class provides `_recompute_energy(model, sample)` which returns `model.energy(sample)`, already an `int`. Concrete solvers must use this instead of trusting the solver's internally reported energy. External solver libraries (dimod, dwave-samplers) typically return energy as `float`. The base class replaces this with the authoritative integer value. The solver's raw float energy is preserved under `metadata["params"]["raw_energy"]` for diagnostics.

**Why integer:** all model coefficients (linear and quadratic) and all variable assignments are signed 64-bit integers. The energy formula is a sum of integer products, so the result is an exact integer wherever it exists. Using integer energy throughout eliminates float64 mantissa overflow concerns and allows direct `==` comparison between solver-reported energy and verifier-computed energy.

**Representable range.** "Exact" is qualified by a range. `XqmxModel.energy` is the XQVM `ENERGY` opcode's own function, called from the host and is bound by the same rule: every term product and every partial sum is range-checked against the signed 64-bit range, in the accumulation order [../xqvm/HLF.md](../xqvm/HLF.md#accumulation-order) makes normative. A model and sample whose energy is representable can still raise, because a partial sum left the range on the way to it.

**Narrowed contract.** `XqmxModel.energy` therefore raises `ArithmeticOverflow` rather than returning a value computed in Python's unbounded integers. Callers must handle it. `Solver._recompute_energy` is the authoritative energy for every backend, so a solver that returns a sample whose energy is out of range fails at that call rather than reporting a wrong number.

## `XqmxModel.energy()` Reference

`XqmxModel.energy(sample)` evaluates, in this order:

```python
energy = 0

# Sorted key order throughout, and every step range-checked: the
# accumulation order is normative precisely because overflow raises.
for i, coeff in model.linear_items():
    x_i = sample.values[i]
    term = check_i64(coeff * x_i)
    energy = check_i64(energy + term)

# The quadratic term groups as (coeff * x_i) * x_j, not coeff * (x_i * x_j).
for (i, j), coeff in model.quadratic_items():
    x_i = sample.values[i]
    x_j = sample.values[j]
    term = check_i64(coeff * x_i)
    term = check_i64(term * x_j)
    energy = check_i64(energy + term)
```

`linear_items()` and `quadratic_items()` yield in sorted key order, never in insertion order. `check_i64` returns its argument when it fits the signed 64-bit range and raises `ArithmeticOverflow` when it does not.

**Precondition:** `len(sample) == model.size`, else `SizeMismatch`.

**Failure:** `ArithmeticOverflow` if any term product or partial sum leaves the signed 64-bit range.

## Sparse Representation

A model's coefficients are sparse; a sample's assignments are dense.

- `model.linear_items()` -- `[(i, coefficient), ...]`, nonzero terms only. An unset index has coefficient 0.
- `model.quadratic_items()` -- `[((i, j), coefficient), ...]` with `i <= j`, nonzero terms only. An unset pair has coefficient 0. Writing a coefficient back to 0 removes the term.
- `sample.values` -- one assignment per variable. `XqmxSample.default(domain, size)` fills it with the domain default (binary `0`, spin `-1`, integer `0`), so a solver that leaves a variable unassigned, as dimod does for a variable with no terms, returns that default for it.

The energy formula iterates over the model's coefficients, not the sample's assignments, so a variable that appears in no term does not affect the energy.
