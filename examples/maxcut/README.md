# Max-Cut

End-to-end XQuad pipeline demo: build a random weighted complete
graph, compile a Max-Cut QUBO via `xqcp`, run the encoder on the
chosen XQVM interpreter (Python reference or Rust), sample the
resulting model with `xqsa`'s `SolverDWaveCPU` simulated annealer, and verify +
decode the 2-colour partition.

## Run it

From the repo root:

```sh
uv run python examples/maxcut/runner.py --seed 42
uv run python examples/maxcut/runner.py --seed 42 --interpreter rust
uv run python examples/maxcut/runner.py --n 6 --seed 7 -o /tmp/mc.json
```

Flags:

- `--n N` — number of nodes in the (complete) graph (default: 5).
- `--seed S` — RNG seed for edge weights and the SA sampler (default:
  42). Both draw from the same seed so the output is reproducible.
- `--interpreter {python,rust}` — which XQVM executes the compiled
  programs (default: `python`). Both paths must produce identical
  decoded output for the same seed.
- `-o PATH` — write the result as JSON to `PATH`; stdout otherwise.

## What the pipeline does

1. **CP** (`xqcp`) — build the Max-Cut problem:
   - Input: `num_nodes` (int), `edges` (Vec of flat `(i, j, w)`
     triples, `3*|E|` entries).
   - Model: `n` binary variables, one per node. `x[v] ∈ {0, 1}`
     selects the side of the cut.
   - Objective: for each edge `(i, j, w)`, add `-w*(x_i + x_j)` and
     `+2w*x_i*x_j`. Minimising this minimises `-∑ w*[x_i ≠ x_j]`,
     i.e. maximises the cut.
2. **Assemble**, **Encode**, **Sample**, **Verify**, **Decode** — same
   shape as `examples/tsp/` (see that README for details).

## Canonical output

`golden.json` is the decoded partition for `--seed 42 --n 5`. Both
interpreters produce the same file byte-for-byte (the runner flips
the partition so node 0 sits on side 0, since Max-Cut is invariant
under global bit-flip).

The `example-smoke` CI target runs both interpreter paths against
this golden; `make regen-example-goldens` rewrites it from the
current runner output.
