<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make docs-regen`.
-->

# Maximum Independent Set

Source: [examples/max_independent_set/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/max_independent_set/README.md)

Find the largest subset of nodes in an undirected graph such that no two
selected nodes share an edge.

## QUBO formulation

- **Input**: number of nodes N, edge list
- **Model**: N binary variables. `x_i = 1` if node i is in the independent set.
- **Objective**: minimise `-sum(x_i)` (maximise set size)
- **Constraints**: per edge (i,j): `x_i + x_j <= 1` (SLACK + EQUALITY)

Each edge inequality is encoded via SLACK + EQUALITY. A single binary slack
variable s (capacity = 1) converts `x_i + x_j <= 1` into the equality
`x_i + x_j + s = 1`, and EQUALITY adds the penalty `P*(x_i + x_j + s - 1)^2`.

Slack variable indices start at num_nodes and are allocated one per edge.

## DSL methods used

- `problem.vec()` -- allocate untyped vector registers for indices and coefficients
- `problem.slack(indices, coeffs, start_index, capacity)` -- append one slack entry per edge
- `model.apply_equality(indices, coeffs, target, penalty)` -- EQUALITY constraint

## Pipeline overview

1. **CP** (`xqcp`) -- generate a random graph, declare binary variables (one per node), and encode each edge independence constraint via SLACK + EQUALITY.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the independence constraints: see [the generated verifier's `valid` flag](../running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the selected nodes

## Usage

```sh
uv run python examples/max_independent_set/runner.py --seed 42
uv run python examples/max_independent_set/runner.py --n 7 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of nodes |
| `--solver` | `dwave-cpu` | Solver backend (see Choosing a solver) |
| `--interpreter` | `python` | XQVM backend: `python` or `rust` |
| `--seed` | `42` | Random seed |
| `-o` | stdout | Write JSON result to file |

## Choosing a solver

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/). The default is `dwave-cpu`, and a
non-default solver will not reproduce the output shown here.

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/max_independent_set/README.md).
