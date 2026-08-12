# Max-3-SAT

Given M clauses of 3 positive literals over N binary variables, find the
assignment that satisfies the maximum number of clauses.

## QUBO formulation

- **Input**: N binary variables, M clauses of 3 positive literals
- **Model**: N binary variables. `x_v in {0, 1}`.
- **Objective**: minimise `sum over clauses of P*(1-x_i)(1-x_j)(1-x_k)`

A clause (i,j,k) is violated when all three variables are 0. Expanding
the product (dropping the constant term):

    P*(-x_i - x_j - x_k + x_i*x_j + x_i*x_k + x_j*x_k - x_i*x_j*x_k)

The cubic term `-P*x_i*x_j*x_k` is degree-reduced via `REDUCE(i, j) -> w`,
introducing one auxiliary variable w per clause with Rosenberg enforcement
`P_AUX*(x_i*x_j - 2*x_i*w - 2*x_j*w + 3*w)`. The cubic term becomes the
quadratic term `-P*w*x_k`.

## DSL methods used

- `model.reduce(var_a, var_b, p_aux)` -- HOBO degree reduction; returns a
  RegLoad holding the auxiliary variable index for chaining into quadratic terms

## Pipeline overview

1. **CP** (`xqcp`) -- generate random 3-literal clauses, declare binary variables, and degree-reduce the cubic violation terms via REDUCE.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary; the Rosenberg REDUCE terms are not checked
6. **Decode** -- decoder extracts the variable assignment

## Usage

```sh
uv run python examples/max3sat/runner.py --seed 42
uv run python examples/max3sat/runner.py --n 8 --m 10 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `6` | Number of Boolean variables |
| `--m` | `8` | Number of clauses |
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
