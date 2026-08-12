# Max Independent Set

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
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the independence constraints: see [the generated verifier's `valid` flag](../../docs/book/src/running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
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
