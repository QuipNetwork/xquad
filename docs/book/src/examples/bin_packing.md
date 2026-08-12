<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make docs-regen`.
-->

# Bin Packing

Source: [examples/bin_packing/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/bin_packing/README.md)

Pack N items with given integer sizes into the minimum number of bins, each
with a fixed capacity C.

## QUBO formulation

- **Input**: N item sizes (Vec), number of bins B, bin capacity C
- **Model**: N*B binary variables in an N x B grid. `x[i,b] = 1` if item i is placed in bin b.
- **Objective**: uniform `+1` bias on every cell. Note this is constant under the assignment constraint (`sum_b x[i,b] = 1` forces `sum_{i,b} x[i,b] = N`), so the model does not minimise the bin count -- it finds a feasible packing. A true bin-count objective needs a per-bin indicator variable.
- **Constraints**:
  - Assignment per item i: `sum_b x[i,b] = 1` (EQUALITY with unit coefficients, penalty 200)
  - Capacity per bin b: `sum_i s_i * x[i,b] <= C` (SLACK + EQUALITY, penalty 100)

The capacity inequality is encoded by appending binary slack variable entries
to the column index/coefficient vectors, converting it to a weighted equality.

## DSL methods used

- `problem.vec()` -- allocate untyped vector registers for indices and coefficients
- `problem.slack(indices, coeffs, start_index, capacity)` -- append slack entries
- `model.apply_equality(indices, coeffs, target, penalty)` -- EQUALITY constraint

## Pipeline overview

1. **CP** (`xqcp`) -- generate random item sizes, declare an N x B binary grid, and add EQUALITY assignment constraints per item plus SLACK + EQUALITY capacity constraints per bin.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the assignment or capacity constraints: see [the generated verifier's `valid` flag](../running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the bin assignments

## Usage

```sh
uv run python examples/bin_packing/runner.py --seed 42
uv run python examples/bin_packing/runner.py --n 5 --bins 4 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `4` | Number of items |
| `--bins` | `3` | Number of bins |
| `--solver` | `dwave-cpu` | Solver backend (see Choosing a solver) |
| `--interpreter` | `python` | XQVM backend: `python` or `rust` |
| `--seed` | `42` | Random seed |
| `-o` | stdout | Write JSON result to file |

## Choosing a solver

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/README.md). The default is `dwave-cpu`, and a
non-default solver will not reproduce the output shown here.

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/bin_packing/README.md).
