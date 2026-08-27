<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make regen-docs`.
-->

# Knapsack

Source: [examples/knapsack/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/knapsack/README.md)

The 0/1 Knapsack problem: given N items with integer weights and values,
select a subset maximising total value subject to a weight capacity constraint.

## QUBO formulation

- **Input**: N item weights and values, capacity W
- **Model**: N binary variables. `x_i = 1` means item i is selected.
- **Objective**: minimise `-sum(v_i * x_i)`
- **Constraints**: capacity `sum(w_i * x_i) <= W` (SLACK + EQUALITY)

The inequality is encoded via SLACK + EQUALITY. SLACK appends binary slack
variable entries (`s_j` with coefficients `2^j`) to the index and coefficient
vectors, converting the inequality to the equality `sum(w_i*x_i) + sum(s_j*2^j) = W`.
EQUALITY then adds the penalty term `P*(sum(a_k*x_k) - W)^2` to the QUBO.

## DSL methods used

- `problem.vec()` -- allocate untyped vector registers for indices and coefficients
- `problem.slack(indices, coeffs, start_index, capacity)` -- append slack entries
- `model.apply_equality(indices, coeffs, target, penalty)` -- EQUALITY constraint

## Pipeline overview

1. **CP** (`xqcp`) -- generate random item weights and values, declare binary variables, and encode the capacity inequality via SLACK + EQUALITY.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks the sample is binary and that the capacity constraint holds, then computes energy
6. **Decode** -- decoder extracts the item selection

## Usage

```sh
uv run python examples/knapsack/runner.py --seed 42
uv run python examples/knapsack/runner.py --n 6 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of items |
| `--solver` | `dwave-cpu` | Solver backend (see Choosing a solver) |
| `--interpreter` | `python` | XQVM backend: `python` or `rust` |
| `--seed` | `42` | Random seed |
| `-o` | stdout | Write JSON result to file |

## Choosing a solver

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/). The default is `dwave-cpu`, and a
non-default solver will not reproduce the output shown here.

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/knapsack/README.md).
