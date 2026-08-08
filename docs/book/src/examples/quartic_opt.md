<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make docs-regen`.
-->

# Quartic Optimization

Source: [examples/quartic_opt/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/quartic_opt/README.md)

Minimise a degree-4 pseudo-Boolean objective via two-stage REDUCE chaining.

## QUBO formulation

- **Input**: N binary variables, M quartic interaction terms `(i, j, k, l, c)`
- **Model**: N binary variables. Linear bias `-1` per variable rewards selection, creating tension with the positive quartic terms.
- **Objective**: `sum(c_t * x_i * x_j * x_k * x_l) - sum(x_v)`

Each quartic term (i, j, k, l, c) is encoded via two-stage REDUCE:

1. `w = REDUCE(i, j, P_AUX)` -- introduces auxiliary w; w approximates `x_i*x_j`.
2. `v = REDUCE(w, k, P_AUX)` -- introduces auxiliary v; v approximates `w*x_k = x_i*x_j*x_k`. Here w is the variable index returned from the first REDUCE.
3. `ADDQUAD(v, l, c)` -- adds `c*v*x_l = c*x_i*x_j*x_k*x_l` to the QUBO.

Each quartic term allocates 2 auxiliary variables. With M terms, the model
grows by 2*M variables beyond the original N.

## DSL methods used

- `model.reduce(var_a, var_b, p_aux)` -- two chained HOBO degree reductions;
  the RegLoad returned by the first REDUCE is passed as var_a to the second

## Pipeline overview

1. **CP** (`xqcp`) -- generate random quartic interaction terms, declare binary variables with linear bias, and two-stage degree-reduce each quartic term via chained REDUCE.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks constraints and computes energy
6. **Decode** -- decoder extracts the variable assignment

## Usage

```sh
uv run python examples/quartic_opt/runner.py --seed 42
uv run python examples/quartic_opt/runner.py --n 6 --m 3 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of variables |
| `--m` | `2` | Number of quartic terms |
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

See [GPU/QPU installation](../start/README.md) for driver
prerequisites and [xqsa solver quick-starts](../solving/README.md) for
per-solver parameter tuning.

Non-default solvers will not reproduce the canonical output (different
RNG/hardware). `example-smoke` always runs `dwave-cpu`.

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/quartic_opt/README.md).
