<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make regen-docs`.
-->

# Weighted Set Cover

Source: `examples/weighted_set_cover/README.md`

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
5. **Verify** -- verifier checks the sample is binary and that every demand is met, then computes energy
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

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/). The default is `dwave-cpu`, and a
non-default solver will not reproduce the canonical result.

## Canonical output

`example-smoke` validates both interpreters produce `valid == 1` with
`--seed 42 --solver dwave-cpu`. The smoke test is invariant-based --
it checks validity, not exact output.
