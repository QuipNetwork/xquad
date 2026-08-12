<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make docs-regen`.
-->

# Portfolio Optimization

Source: [examples/portfolio_opt/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/portfolio_opt/README.md)

Select a portfolio of exactly B assets from N candidates to maximise expected
return while penalising higher-order risk cross-interactions.

## QUBO formulation

- **Input**: N asset returns, cubic risk interactions `(i, j, k, sigma)`, budget B
- **Model**: N binary variables. `x_i = 1` if asset i is selected.
- **Objective**: `-sum(r_i * x_i) + sum(sigma_ijk * x_i * x_j * x_k)` -- first term maximises return (minimising its negation), second penalises correlated three-asset risk interactions.
- **Constraints**: budget `sum(x_i) = B` (EQUALITY with unit coefficients, penalty 200)

### Encoding strategy

Return terms are linear: `ADDLINE(i, -r_i)` per asset.

Cubic risk terms (i, j, k, sigma) are degree-reduced:

1. `REDUCE(i, j, P_AUX) -> w` (Rosenberg enforcement for `w = x_i * x_j`)
2. `ADDQUAD(w, k, sigma)` (`sigma * w * x_k = sigma * x_i * x_j * x_k`)

Budget constraint builds uniform-coefficient index/coeff vecs then calls
EQUALITY with `target = B` and `penalty = 200`. EQUALITY is emitted after all
objective (body) actions because it lands in the constraint section.

## DSL methods used

- `model.reduce(var_a, var_b, p_aux)` -- HOBO degree reduction for cubic risk terms
- `problem.vec()` -- allocate index/coefficient vecs for the budget constraint
- `model.apply_equality(indices, coeffs, target, penalty)` -- budget EQUALITY

## Pipeline overview

1. **CP** (`xqcp`) -- generate random returns and cubic risk interactions, declare binary variables, degree-reduce risk terms via REDUCE, and add a budget EQUALITY constraint.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the budget constraint: see [the generated verifier's `valid` flag](../running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the selected assets

## Usage

```sh
uv run python examples/portfolio_opt/runner.py --seed 42
uv run python examples/portfolio_opt/runner.py --n 6 --budget 3 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of assets |
| `--budget` | `2` | Number of assets to select |
| `--solver` | `dwave-cpu` | Solver backend (see Choosing a solver) |
| `--interpreter` | `python` | XQVM backend: `python` or `rust` |
| `--seed` | `42` | Random seed |
| `-o` | stdout | Write JSON result to file |

## Choosing a solver

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/README.md). The default is `dwave-cpu`, and a
non-default solver will not reproduce the output shown here.

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/portfolio_opt/README.md).
