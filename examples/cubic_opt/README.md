# Cubic Optimization

Minimise a cubic pseudo-Boolean objective over binary variables via single-stage
HOBO degree reduction.

## QUBO formulation

- **Input**: N binary variables, M cubic interaction terms `(i, j, k, c)`
- **Model**: N binary variables. Linear bias `-1` per variable rewards selection, creating tension with the positive cubic terms.
- **Objective**: `sum(c_t * x_i * x_j * x_k) - sum(x_v)`

Each cubic term `(i, j, k, c)` is degree-reduced to quadratic via:

1. `REDUCE(i, j, P_AUX) -> w` -- allocates auxiliary variable w with Rosenberg enforcement `P_AUX*(x_i*x_j - 2*x_i*w - 2*x_j*w + 3*w)`
2. `ADDQUAD(w, k, c)` -- adds `c*w*x_k = c*x_i*x_j*x_k` to the QUBO

## DSL methods used

- `model.reduce(var_a, var_b, p_aux)` -- single-stage HOBO degree reduction

## Pipeline overview

1. **CP** (`xqcp`) -- generate random cubic interaction terms, declare binary variables with linear bias, and degree-reduce each cubic term via REDUCE.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks constraints and computes energy
6. **Decode** -- decoder extracts the variable assignment

## Usage

```sh
uv run python examples/cubic_opt/runner.py --seed 42
uv run python examples/cubic_opt/runner.py --n 5 --m 4 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `4` | Number of variables |
| `--m` | `3` | Number of cubic terms |
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

See [GPU/QPU installation](../../README.md#gpuqpu-support) for driver
prerequisites and [xqsa solver quick-starts](../../xqsa/README.md) for
per-solver parameter tuning.

Non-default solvers will not reproduce the canonical output (different
RNG/hardware). `example-smoke` always runs `dwave-cpu`.

## Canonical output

`example-smoke` validates both interpreters produce `valid == 1` with
`--seed 42 --solver dwave-cpu`. The smoke test is invariant-based --
it checks validity, not exact output.
