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

`SolverResult.energy` must equal `compute_energy(model, sample)` exactly. This is an integer-to-integer comparison with no tolerance.

The `Solver` base class provides `_recompute_energy(model, sample)` which calls `compute_energy(model, sample)` and casts the result to `int`. Concrete solvers must use this instead of trusting the solver's internally reported energy. External solver libraries (dimod, dwave-samplers) typically return energy as `float`. The base class replaces this with the authoritative integer value. The solver's raw float energy is preserved under `metadata["params"]["raw_energy"]` for diagnostics.

**Why integer:** all XQMX coefficients (`linear`, `quadratic`) and all variable assignments are integers. The energy formula is a sum of integer products, so the result is an exact integer wherever it exists. Using integer energy throughout eliminates float64 mantissa overflow concerns and allows direct `==` comparison between solver-reported energy and verifier-computed energy.

**Representable range.** "Exact" is qualified by a range. `compute_energy` is the same computation as the XQVM `ENERGY` opcode and is bound by the same rule: every term product and every partial sum is range-checked against the signed 64-bit range, in the accumulation order [../xqvm/HLF.md](../xqvm/HLF.md#accumulation-order) makes normative. A model and sample whose energy is representable can still raise, because a partial sum left the range on the way to it.

**Narrowed contract.** `compute_energy` therefore raises `ArithmeticOverflow` where earlier releases returned a value computed in Python's unbounded integers. Callers must handle it. `Solver._recompute_energy` is the authoritative energy for every backend, so a solver that returns a sample whose energy is out of range fails at that call rather than reporting a wrong number.

## `compute_energy()` Reference

```python
def compute_energy(model: XQMX, sample: XQMX) -> int:
    require_model_mode(model)
    require_sample_mode(sample)
    if model.size != sample.size:
        raise ValueError(...)

    energy = 0

    # Sorted key order throughout, and every step range-checked: the
    # accumulation order is normative precisely because overflow raises.
    for i, coeff in model.iter_linear():
        x_i = sample.get_linear(i)
        term = check_i64(coeff * x_i)
        energy = check_i64(energy + term)

    # The quadratic term groups as (coeff * x_i) * x_j, not coeff * (x_i * x_j).
    for (i, j), coeff in model.iter_quadratic():
        x_i = sample.get_linear(i)
        x_j = sample.get_linear(j)
        term = check_i64(coeff * x_i)
        term = check_i64(term * x_j)
        energy = check_i64(energy + term)

    return energy
```

`iter_linear` and `iter_quadratic` yield in sorted key order, never in insertion order. `check_i64` returns its argument when it fits the signed 64-bit range and raises `ArithmeticOverflow` when it does not.

**Preconditions:**
- `model.mode == XQMXMode.MODEL`
- `sample.mode == XQMXMode.SAMPLE`
- `model.size == sample.size` (raises `ValueError` on mismatch)

**Failure:** `ArithmeticOverflow` if any term product or partial sum leaves the signed 64-bit range.

## Sparse Representation

Both `model.linear` and `model.quadratic` are sparse dictionaries:

- `linear: dict[int, int]` -- variable index -> coefficient. Unset indices have implicit coefficient 0.
- `quadratic: dict[tuple[int, int], int]` -- `(i, j)` -> coefficient, where `i <= j`. Unset pairs have implicit coefficient 0.

For samples:
- `sample.linear: dict[int, int]` -- variable index -> assignment
- `sample.get_linear(i)` returns the default value for the domain if variable `i` is not in the dict:
  - BINARY: default `0`
  - SPIN: default `-1`
  - INTEGER: default `0`

This sparse-with-default convention means only non-default variable assignments need to be stored. The energy formula iterates over the model's coefficients (not the sample's assignments), so unset sample variables contribute their default value.
