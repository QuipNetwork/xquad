# Graph Coloring

Assign one of C colors to each node of an undirected graph such that no two
adjacent nodes share the same color (proper C-coloring).

## QUBO formulation

- **Input**: number of nodes N, number of colors C, edge list
- **Model**: N categorical variables over C cases. `Domain.CATEGORICAL` records that as an N x C binary grid, `x[v,c] = 1` if node v gets color c, with a one-hot row per node.
- **Objective**: none. The problem is pure constraint satisfaction: a colouring is scored only by its constraint violations. A valid C-colouring scores `-penalty * N`, not `0`, because XQMX has no constant-term field and each satisfied ONEHOTR stores `-penalty` rather than `0`. That is `-1000` at the `--n 5`, penalty `200` defaults. The colour count defaults to `4` because a G(5, 0.5) graph routinely contains a 4-clique -- the default seed's does -- and a 4-clique has no 3-colouring, so `--colors 3` would make the canonical instance unsatisfiable. See [Constraints](../../docs/book/src/modelling/constraints.md#why-the-reported-energy-is-not-just--total_value).
- **Constraints**:
  - One-hot per node: `sum_c x[v,c] = 1` (ONEHOTR, penalty 200) -- implied by the domain, not written
  - Exclusion per (edge, color): `x[u,c] + x[v,c] <= 1` (EXCLUDE, penalty 200)

### Encoding strategy

The one-hot row per node is what makes a categorical variable categorical, so
`define_model(domain=Domain.CATEGORICAL, k=C, penalty=200)` emits it: the grid
and one ONEHOTR per row, through the same record paths a hand-written loop
would use. The compiled program is what this example emitted before the
domain existed.

EXCLUDE is applied per (edge (u,v), color c) pair via a nested range loop.
The 2D coordinates (u, c) and (v, c) are resolved to flat indices using
IDXGRID: `u * num_colors + c` and `v * num_colors + c`.

Reading the answer back is `sample.case(v)`, a ROWFIND for the 1 in node v's
row. ROWFIND returns `-1` where a row holds no 1, which is the uncoloured
sentinel this example used to produce in host code.

## DSL methods used

- `problem.define_model(size=N, domain=Domain.CATEGORICAL, k=C, penalty=200)` -- the grid and its one-hot rows
- `model.apply_exclude((u, c), (v, c), penalty)` -- EXCLUDE per edge per color
- `sample.case(node)` -- the case a node took, or `-1`

## Pipeline overview

1. **CP** (`xqcp`) -- generate a random graph, declare N categorical variables over C cases, and add EXCLUDE constraints per (edge, color) pair.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks the sample is binary, the one-hot row sums, and the per-edge exclusions, then computes energy
6. **Decode** -- decoder reads each node's case with ROWFIND, giving one color index per node

## Usage

```sh
uv run python examples/graph_coloring/runner.py --seed 42
uv run python examples/graph_coloring/runner.py --n 6 --colors 4 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of nodes |
| `--colors` | `4` | Number of colors |
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
