<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make docs-regen`.
-->

# Number Partition

Source: [examples/number_partition/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/number_partition/README.md)

Given N positive integers, find a way to split them into two subsets of
equal sum (or as close as possible if an exact split does not exist).

## QUBO formulation

- **Input**: N positive integers `a_i`
- **Model**: N binary variables. `x_i = 1` puts number `a_i` in subset A.
- **Objective**: minimise `P * (sum(a_i * x_i) - S/2)^2` where `S = sum(a_i)`

An exact partition exists when S is even and the penalty evaluates to zero.
The QUBO minimiser finds the balanced partition when one exists, or the
most balanced split when the total is odd.

## DSL methods used

- `problem.vec()` -- allocate untyped vector registers for indices and coefficients
- `model.apply_equality(indices, coeffs, target, penalty)` -- EQUALITY constraint

## Pipeline overview

1. **CP** (`xqcp`) -- generate random positive integers, declare binary variables (one per number), and encode the half-sum equality constraint via EQUALITY.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks the partition constraint and computes energy
6. **Decode** -- decoder extracts the subset assignment

## Usage

```sh
uv run python examples/number_partition/runner.py --seed 42
uv run python examples/number_partition/runner.py --n 8 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `6` | Number of integers |
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

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/number_partition/README.md).
