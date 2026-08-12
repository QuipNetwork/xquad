# Bin Packing

Pack N items with given integer sizes into the minimum number of bins, each
with a fixed capacity C.

<!-- xquad:defect QUI-1029 -->
> **Known issue.** The objective places a uniform `+1` bias on every cell as a proxy for bin
> count, but that quantity is identically N on every feasible packing under the assignment
> constraint, so the objective cannot discriminate bin counts. Separately, the runner reuses
> one fixed `start_index` across the per-bin `SLACK` calls, so slack variables are allocated
> once and shared across bins instead of per bin. Read this example as a demonstration of
> assignment and capacity modelling, not a working bin-count minimiser -- the default run
> spreads four items of total size 5 across three bins of capacity 5. Report problems at the
> [issue tracker](https://gitlab.com/quip.network/xquad/-/issues).

## QUBO formulation

- **Input**: N item sizes (Vec), number of bins B, bin capacity C
- **Model**: N*B binary variables in an N x B grid. `x[i,b] = 1` if item i is placed in bin b.
- **Objective**: uniform `+1` bias on every cell. Note this is constant under the assignment constraint (`sum_b x[i,b] = 1` forces `sum_{i,b} x[i,b] = N`), so the model does not minimise the bin count -- it finds a feasible packing. A true bin-count objective needs a per-bin indicator variable.
- **Constraints**:
  - Assignment per item i: `sum_b x[i,b] = 1` (EQUALITY with unit coefficients, penalty 200)
  - Capacity per bin b: `sum_i s_i * x[i,b] <= C` (SLACK + EQUALITY, penalty 100)

The capacity inequality is encoded by appending binary slack variable entries
to the column index/coefficient vectors, converting it to a weighted equality.

## DSL methods used

- `problem.vec()` -- allocate untyped vector registers for indices and coefficients
- `problem.slack(indices, coeffs, start_index, capacity)` -- append slack entries
- `model.apply_equality(indices, coeffs, target, penalty)` -- EQUALITY constraint

## Pipeline overview

1. **CP** (`xqcp`) -- generate random item sizes, declare an N x B binary grid, and add EQUALITY assignment constraints per item plus SLACK + EQUALITY capacity constraints per bin.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the assignment or capacity constraints: see [the generated verifier's `valid` flag](../../docs/book/src/running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the bin assignments

## Usage

```sh
uv run python examples/bin_packing/runner.py --seed 42
uv run python examples/bin_packing/runner.py --n 5 --bins 4 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `4` | Number of items |
| `--bins` | `3` | Number of bins |
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
