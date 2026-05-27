# TSP — Travelling Salesman Problem

End-to-end XQuad pipeline demo: build a random symmetric distance
matrix, compile a TSP QUBO via `xqcp`, run the encoder on the chosen
XQVM interpreter (Python reference or Rust), sample the resulting
model with `xqsa`'s `SolverDWaveCPU` simulated annealer, and verify + decode the
tour.

## Run it

From the repo root:

```sh
uv run python examples/tsp/runner.py --seed 42
uv run python examples/tsp/runner.py --seed 42 --interpreter rust
uv run python examples/tsp/runner.py --n 5 --seed 7 -o /tmp/tsp.json
```

Flags:

- `--n N` — number of cities (default: 4).
- `--seed S` — RNG seed for the distance matrix and the SA sampler
  (default: 42). Both draw from the same seed so the output is
  reproducible.
- `--interpreter {python,rust}` — which XQVM executes the compiled
  programs (default: `python`). Both paths must produce identical
  decoded output for the same seed.
- `-o PATH` — write the result as JSON to `PATH`; stdout otherwise.

## What the pipeline does

1. **CP** (`xqcp`) — build the TSP problem:
   - Input: `num_cities` (int), `distance_matrix` (Vec, flat upper
     triangle, `n*(n-1)/2` entries).
   - Model: an `n×n` binary grid. Variable `x[i, p] = 1` means city
     `i` is at tour position `p`.
   - Objective: sum of distances between consecutive positions in the
     tour. One-hot row/column penalties (weight 100) pin each city to
     exactly one position and each position to exactly one city.
2. **Assemble** — `xquad.asm.assemble_source` (Python path) /
   `xquad.asm.parse_xqasm` (via `program_from_xqasm`) turns the
   `.xqasm` text into bytecode or a `Program` dataclass.
3. **Encode** — run the encoder program on the chosen VM to produce
   the fully-populated `XqmxModel`.
4. **Sample** -- `xqsa.SolverDWaveCPU(seed=seed)` runs dwave-samplers
   simulated annealing over the model, returning a candidate `Sample`.
5. **Verify** — run the verifier program (same VM) to check one-hot
   row/col constraints and compute the Hamiltonian energy.
6. **Decode** — run the decoder program to extract the tour as a
   sequence of city indices.

## Canonical output

`golden.json` is the decoded result for `--seed 42 --n 4`. Both
interpreters produce the same file byte-for-byte (the runner rotates
the tour so city 0 leads, since TSP tours are cyclic and SA's
variable-ordering heuristic can emit rotations of the same cycle).

The `example-smoke` CI target runs both interpreter paths against
this golden; `make regen-example-goldens` rewrites it from the
current runner output.
