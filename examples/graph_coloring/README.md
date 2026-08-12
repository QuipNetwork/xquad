# Graph Coloring

Assign one of C colors to each node of an undirected graph such that no two
adjacent nodes share the same color (proper C-coloring).

## QUBO formulation

- **Input**: number of nodes N, number of colors C, edge list
- **Model**: N*C binary variables in an N x C grid. `x[v,c] = 1` if node v gets color c.
- **Objective**: none. The problem is pure constraint satisfaction: a colouring is scored only by its constraint violations. A valid C-colouring scores `-penalty * N`, not `0`, because XQMX has no constant-term field and each satisfied ONEHOTR stores `-penalty` rather than `0`. That is `-1000` at the `--n 5`, penalty `200` defaults. See [Constraints](../../docs/book/src/modelling/constraints.md#why-the-reported-energy-is-not-just--total_value).
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
5. **Verify** -- verifier checks the one-hot row sums and computes energy. It does not check the exclusion constraints: see [the generated verifier's `valid` flag](../../docs/book/src/running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
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

| Name | Hardware | Install |
|------|----------|---------|
| `dwave-cpu` | CPU (default) | `pip install xquad` |
| `dwave-qpu` | D-Wave Leap account | `pip install xquad[dwave]` |
| `cuda-gpu` | NVIDIA CUDA GPU | `pip install xquad[cuda]` |
| `metal-gpu` | Apple Silicon (macOS) | `pip install xquad[metal]` |
| `quip` | Quip Network (remote miner, env-configured) | `pip install xqsa[quip]` |

See [GPU/QPU installation](../../README.md#gpuqpu-support) for driver
prerequisites and [xqsa solver quick-starts](../../xqsa/README.md) for
per-solver parameter tuning.

Non-default solvers will not reproduce the canonical output (different
RNG/hardware). `example-smoke` always runs `dwave-cpu`.

## Canonical output

`example-smoke` validates both interpreters produce `valid == 1` with
`--seed 42 --solver dwave-cpu`. The smoke test is invariant-based --
it checks validity, not exact output.
