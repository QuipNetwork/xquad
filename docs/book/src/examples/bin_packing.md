<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make regen-docs`.
-->

# Bin Packing

Source: `examples/bin_packing/README.md`

Pack N items with given integer sizes into the minimum number of bins, each
with a fixed capacity C.

## QUBO formulation

- **Input**: N item sizes (Vec), number of bins B, bin capacity C
- **Model**: `(N + 1) * B` binary variables in an `(N + 1) x B` grid. Rows `0..N-1` are the assignment cells: `x[i,b] = 1` if item i is placed in bin b. Row N holds one indicator per bin: `y[b] = 1` if bin b is open.
- **Objective**: `+BIN_COST` on each indicator `y[b]`, so the energy counts the bins the packing opens. A bias spread over the assignment cells instead would be identically N on every feasible packing, because each item lands in exactly one bin, and could not tell a one-bin packing from a three-bin one.
- **Constraints**:
  - Assignment per item i: `sum_b x[i,b] = 1` (ONEHOTR, penalty 200)
  - Linking per (i, b): `x[i,b] -> y[b]` (IMPLIES, penalty 200)
  - Capacity per bin b: `sum_i s_i * x[i,b] <= C` (SLACK + EQUALITY, penalty 100)

The capacity inequality is encoded by appending binary slack variable entries
to the column index/coefficient vectors, converting it to a weighted equality.
Each bin gets its own slack block: bin b's entries start at
`(N + 1) * B + b * bitlen(C)`, past every model variable and past every
earlier bin's block. Sharing one start index across bins would let one bin's
slack absorb another's overflow, leaving the capacity constraint
under-constrained.

## DSL methods used

- `model.apply_onehot_row(row, penalty)` -- ONEHOTR assignment constraint
- `model.apply_implies(coord_a, coord_b, penalty)` -- IMPLIES linking constraint
- `problem.vec()` -- allocate untyped vector registers for indices and coefficients
- `problem.slack(indices, coeffs, start_index, capacity)` -- append slack entries
- `model.apply_equality(indices, coeffs, target, penalty)` -- EQUALITY constraint
- `xq_bitlen(value)` -- BITLEN, used to size each bin's slack block

## Pipeline overview

1. **CP** (`xqcp`) -- generate random item sizes, declare an `(N + 1) x B` binary grid, and add ONEHOTR assignment constraints per item, IMPLIES links per cell, and SLACK + EQUALITY capacity constraints per bin.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks the sample is binary, the per-item assignment, the bin-usage links and the per-bin capacity, then computes energy
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
[Solving Overview](../solving/). The default is `dwave-cpu`, and a
non-default solver will not reproduce the canonical result.

## Canonical output

`example-smoke` validates both interpreters produce `valid == 1` with
`--seed 42 --solver dwave-cpu`. The smoke test is invariant-based --
it checks validity, not exact output.
