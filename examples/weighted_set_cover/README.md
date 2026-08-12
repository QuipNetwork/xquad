# Weighted Set Cover

A generalisation of Set Cover where each set s has a coverage capacity cap[s]
and each element e has a demand demand[e]. The goal is to select sets of
minimum total cost such that the total capacity of covering selected sets
meets each element's demand.

## QUBO formulation

- **Input**: number of elements E, number of sets S, set costs, set capacities, element demands, coverage membership matrix
- **Model**: S binary variables. `x_s = 1` if set s is selected.
- **Objective**: minimise `sum(cost[s] * x_s)`
- **Constraints**: per element e: `sum_{s: covers[e][s]=1} cap[s] * x_s >= demand[e]` (ATLEASTW)

For each element, a branch conditionally pushes (set index, capacity) pairs
into per-element index/coefficient vectors, then ATLEASTW enforces the
weighted threshold.

## DSL methods used

- `problem.vec()` -- allocate vector registers for covering set indices and capacities
- `problem.branch(cond, arm, default)` -- conditional VECPUSH based on coverage membership
- `model.apply_atleastw(indices, coeffs, k, penalty)` -- ATLEASTW constraint

## Pipeline overview

1. **CP** (`xqcp`) -- generate a random weighted coverage instance, declare binary variables (one per set), and encode per-element weighted demand constraints via conditional branching and ATLEASTW.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the demand constraints: see [the generated verifier's `valid` flag](../../docs/book/src/running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the selected sets

## Usage

```sh
uv run python examples/weighted_set_cover/runner.py --seed 42
uv run python examples/weighted_set_cover/runner.py --num-sets 6 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--num-elements` | `4` | Number of elements in the universe |
| `--num-sets` | `5` | Number of sets |
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
