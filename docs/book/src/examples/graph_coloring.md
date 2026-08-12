<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make docs-regen`.
-->

# Graph Coloring

Source: [examples/graph_coloring/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/graph_coloring/README.md)

Assign one of C colors to each node of an undirected graph such that no two
adjacent nodes share the same color (proper C-coloring).

## QUBO formulation

- **Input**: number of nodes N, number of colors C, edge list
- **Model**: N*C binary variables in an N x C grid. `x[v,c] = 1` if node v gets color c.
- **Objective**: none. The problem is pure constraint satisfaction: a colouring is scored only by its constraint violations. A valid C-colouring scores `-penalty * N`, not `0`, because XQMX has no constant-term field and each satisfied ONEHOTR stores `-penalty` rather than `0`. That is `-1000` at the `--n 5`, penalty `200` defaults. See [Constraints](../modelling/constraints.md#why-the-reported-energy-is-not-just--total_value).
- **Constraints**:
  - One-hot per node: `sum_c x[v,c] = 1` (ONEHOTR, penalty 200)
  - Exclusion per (edge, color): `x[u,c] + x[v,c] <= 1` (EXCLUDE, penalty 200)

### Encoding strategy

ONEHOTR applies the one-hot row constraint directly: for each node v, a
single ONEHOTR instruction constrains all C variables in that row to sum to 1.

EXCLUDE is applied per (edge (u,v), color c) pair via a nested range loop.
The 2D coordinates (u, c) and (v, c) are resolved to flat indices using
IDXGRID: `u * num_colors + c` and `v * num_colors + c`.

## DSL methods used

- `problem.define_model(size=N*C, rows=N, cols=C)` -- 2D grid model layout
- `model.apply_onehot_row(node, penalty)` -- ONEHOTR per node
- `model.apply_exclude((u, c), (v, c), penalty)` -- EXCLUDE per edge per color

## Pipeline overview

1. **CP** (`xqcp`) -- generate a random graph, declare an N x C binary grid, and add ONEHOTR constraints per node plus EXCLUDE constraints per (edge, color) pair.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks the one-hot row sums and computes energy. It does not check the exclusion constraints: see [the generated verifier's `valid` flag](../running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the color assignment per node

## Usage

```sh
uv run python examples/graph_coloring/runner.py --seed 42
uv run python examples/graph_coloring/runner.py --n 6 --colors 3 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of nodes |
| `--colors` | `3` | Number of colors |
| `--solver` | `dwave-cpu` | Solver backend (see Choosing a solver) |
| `--interpreter` | `python` | XQVM backend: `python` or `rust` |
| `--seed` | `42` | Random seed |
| `-o` | stdout | Write JSON result to file |

## Choosing a solver

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/). The default is `dwave-cpu`, and a
non-default solver will not reproduce the output shown here.

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/graph_coloring/README.md).
