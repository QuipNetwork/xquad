<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make docs-regen`.
-->

# Travelling Salesman Problem

Source: [examples/tsp/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/tsp/README.md)

Find the shortest Hamiltonian tour through N cities given a random symmetric
distance matrix.

## QUBO formulation

- **Input**: `num_cities` (int), `distance_matrix` (Vec, flat upper triangle, `n*(n-1)/2` entries)
- **Model**: an `n x n` binary grid. `x[i, p] = 1` means city `i` is at tour position `p`.
- **Objective**: sum of distances between consecutive positions in the tour.
- **Constraints**: one-hot row (each city at exactly one position) and one-hot column (each position holds exactly one city), both with penalty 100.

## DSL methods used

- `problem.input()` -- declare typed calldata inputs
- `problem.define_model()` -- allocate binary 2D grid XQMX model
- `problem.stow()` -- bind intermediate computations to named registers
- `problem.range()` -- emit RANGE loops
- `model.quadratic[(city_i, pos), (city_j, pos)].add()` -- accumulate quadratic coupling using 2D grid coordinates
- `model.apply_onehot_row()` -- ONEHOTR constraint per city
- `model.apply_onehot_col()` -- ONEHOTC constraint per position
- `problem.output()` -- declare typed output slots
- `problem.sample.colfind()` -- find the row index with value 1 in a given column

## Pipeline overview

1. **CP** (`xqcp`) -- build a random symmetric distance matrix, declare an `n x n` binary grid, and add quadratic distance terms plus one-hot row/column constraints.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks one-hot row/column constraints and computes energy
6. **Decode** -- decoder extracts the tour as a sequence of city indices

## Usage

```sh
uv run python examples/tsp/runner.py --seed 42
uv run python examples/tsp/runner.py --n 5 --seed 7 -o /tmp/tsp.json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `4` | Number of cities |
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

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/tsp/README.md).
