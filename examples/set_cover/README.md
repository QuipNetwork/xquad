# Set Cover

Given a universe of E elements and a collection of S sets, find the minimum
sub-collection whose union equals the universe.

## QUBO formulation

- **Input**: number of elements E, number of sets S, coverage membership matrix (flat Vec of E*S entries, `covers[e][s] = 1` if set s covers element e)
- **Model**: S binary variables. `x_s = 1` if set s is selected.
- **Objective**: minimise `sum(x_s)`
- **Constraints**: per element e: `sum_{s: covers[e][s]=1} x_s >= 1` (ATLEAST with k=1)

For each element, the encoder iterates over all sets and uses a branch to
conditionally push only covering set indices into the element's index vector.
ATLEAST then enforces that at least one covering set is selected.

## DSL methods used

- `problem.vec()` -- allocate a vector register for each element's covering set indices
- `problem.branch(cond, arm, default)` -- conditional VECPUSH based on coverage membership
- `model.apply_atleast(indices, k, penalty)` -- ATLEAST constraint with k=1

## Pipeline overview

1. **CP** (`xqcp`) -- generate a random coverage matrix, declare binary variables (one per set), and encode per-element coverage constraints via conditional branching and ATLEAST.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier computes energy and checks the sample is binary. It does not check the coverage constraints: see [the generated verifier's `valid` flag](../../docs/book/src/running/verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
6. **Decode** -- decoder extracts the selected sets

## Usage

```sh
uv run python examples/set_cover/runner.py --seed 42
uv run python examples/set_cover/runner.py --num-sets 6 --interpreter rust
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
