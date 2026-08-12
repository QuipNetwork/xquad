# Vertex Cover

Find the minimum subset of vertices such that every edge in an undirected
graph has at least one endpoint in the subset.

## QUBO formulation

- **Input**: number of nodes N, edge list
- **Model**: N binary variables. `x_v = 1` if vertex v is in the cover.
- **Objective**: minimise `sum(x_v)`
- **Constraints**: per edge (i,j): `x_i + x_j >= 1` (ATLEAST with k=1)

The at-least-1 constraint is encoded directly with ATLEAST. For each edge,
ATLEAST allocates one slack variable at model.size and adds the penalty
`P*(x_i + x_j - 1 - s)^2`, where `s in {0,1}` accounts for the case when
both endpoints are selected (sum = 2).

## DSL methods used

- `problem.vec()` -- allocate a vector register for the two endpoint indices per edge
- `model.apply_atleast(indices, k, penalty)` -- ATLEAST constraint with k=1

## Pipeline overview

1. **CP** (`xqcp`) -- generate a random graph, declare binary variables (one per vertex), and encode per-edge coverage constraints via ATLEAST.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the edge coverage constraints: see [the generated verifier's `valid` flag](../../docs/book/src/running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the selected vertices

## Usage

```sh
uv run python examples/vertex_cover/runner.py --seed 42
uv run python examples/vertex_cover/runner.py --n 7 --interpreter rust
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
