<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make regen-docs`.
-->

# Set Cover

Source: `examples/set_cover/README.md`

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
5. **Verify** -- verifier checks the sample is binary and that every element is covered, then computes energy
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

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/). The default is `dwave-cpu`, and a
non-default solver will not reproduce the canonical result.

## Canonical output

`example-smoke` validates both interpreters produce `valid == 1` with
`--seed 42 --solver dwave-cpu`. The smoke test is invariant-based --
it checks validity, not exact output.
