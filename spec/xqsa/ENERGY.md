# Energy Computation

## Formula

The Hamiltonian energy of a sample with respect to a model is:

```
E = sum_i( linear[i] * x_i ) + sum_{i<j}( quadratic[i,j] * x_i * x_j )
```

Where:
- `linear[i]` is the coefficient for variable `i` from the model
- `quadratic[i,j]` is the coupling coefficient for variables `i` and `j` from the model (with `i < j`)
- `x_i` is the variable assignment for `i` from the sample

This is identical to the XQVM `ENERGY` opcode (see [../xqvm/HLF.md](../xqvm/HLF.md)).

## Precision Contract

`SolverResult.energy` must equal `compute_energy(model, sample)` exactly. This is an integer-to-integer comparison with no tolerance.

The `Backend` base class enforces this by recomputing energy via `compute_energy(model, sample)` after the concrete solver returns, rather than trusting the solver's internally reported energy. External solver libraries (dimod, dwave-neal) typically return energy as `float`. The base class replaces this with the authoritative integer value. The solver's raw float energy is preserved under `metadata["params"]["raw_energy"]` for diagnostics.

> **v0.2.0 divergence:** the reference implementation stores `energy` as `float` and does not perform base-class recomputation. QUI-573 adds the recomputation step and changes the type to `int`.

**Why integer:** all XQMX coefficients (`linear`, `quadratic`) and all variable assignments are integers. The energy formula is a sum of integer products, so the result is always an exact integer. The XQVM `ENERGY` opcode returns an integer pushed onto the stack. Using integer energy throughout eliminates float64 mantissa overflow concerns and allows direct `==` comparison between solver-reported energy and verifier-computed energy.

## `compute_energy()` Reference

```python
def compute_energy(model: XQMX, sample: XQMX) -> int:
    require_model_mode(model)
    require_sample_mode(sample)
    assert model.size == sample.size

    energy = 0

    for i, coeff in model.linear.items():
        x_i = sample.get_linear(i)
        energy += coeff * x_i

    for (i, j), coeff in model.quadratic.items():
        x_i = sample.get_linear(i)
        x_j = sample.get_linear(j)
        energy += coeff * x_i * x_j

    return energy
```

**Preconditions:**
- `model.mode == XQMXMode.MODEL`
- `sample.mode == XQMXMode.SAMPLE`
- `model.size == sample.size` (raises `ValueError` on mismatch)

## Sparse Representation

Both `model.linear` and `model.quadratic` are sparse dictionaries:

- `linear: dict[int, int]` -- variable index -> coefficient. Unset indices have implicit coefficient 0.
- `quadratic: dict[tuple[int, int], int]` -- `(i, j)` -> coefficient, where `i < j`. Unset pairs have implicit coefficient 0.

For samples:
- `sample.linear: dict[int, int]` -- variable index -> assignment
- `sample.get_linear(i)` returns the default value for the domain if variable `i` is not in the dict:
  - BINARY: default `0`
  - SPIN: default `-1`
  - DISCRETE: default `0`

This sparse-with-default convention means only non-default variable assignments need to be stored. The energy formula iterates over the model's coefficients (not the sample's assignments), so unset sample variables contribute their default value.
