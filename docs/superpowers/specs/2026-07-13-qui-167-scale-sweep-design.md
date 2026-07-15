# QUI-167: MaxCut & TSP Scale Sweep — Design

**Ticket:** [QUI-167](https://linear.app/quip-network/issue/QUI-167) — Test MaxCut & TSP with Larger Problem Sizes (Technical Spike, milestone v0.3.1)

## Goal

Validate the XQVM prototype at scale: run the shipped MaxCut and TSP
example encodings at growing problem sizes, measuring **correctness**
(valid, verifiable, cross-VM-consistent solutions) and **performance**
(per-stage wall time, peak memory, model shape) until each lane hits a
time/failure ceiling. Deliverable is the harness plus a findings report
posted to QUI-167.

## Scope

- **Problems:** MaxCut and TSP only (the ticket's checkboxes). The
  harness is problem-agnostic via a registry so portfolio_opt and other
  examples can be added in a follow-up. (runners must expose
  `build_problem(n, seed)` and a canonicalize helper; portfolio_opt's
  runner needs a small refactor first)
- **VM backends:** Python (reference) and Rust — cross-VM parity at
  scale is part of "validate XQVM".
- **Solvers:** `SolverDWaveCPU` (baseline, always) plus `SolverCudaGPU`
  when cupy + a CUDA device are present — verified working locally
  (RTX 4090, WSL2, `xqsa[cuda]` extra). `SolverMetalGPU` uses the same
  registry mechanism but skips here (no Metal hardware). CPU lanes run
  on both VMs; GPU lanes run on the Rust VM only, since the solver
  choice affects only the solve stage and VM parity is already covered
  by the CPU lanes.
- **Not in scope:** CI wiring, QPU lanes, new encodings or opcodes, and
  hardware-gated GPU correctness *tests* — those remain QUI-687; this
  sweep records GPU performance and validity but adds no test suite.

## Architecture

One self-contained script, `scripts/scale_sweep.py`, with two modes:

- **Orchestrator (default):** builds the sweep matrix
  (problem × VM backend × solver × n × seed, pruned per the scope
  rules above), spawns itself as a child process per point, collects
  one JSON record per child, writes JSONL and a markdown summary.
- **Point mode (`--point ...`):** measures a single
  (problem, backend, solver, n, seed) run and prints one JSON record
  to stdout.

Child-per-point gives clean peak-RSS per point
(`resource.getrusage(RUSAGE_SELF).ru_maxrss` at child exit) and crash
isolation: a native crash in the Rust FFI at large n is recorded as
`{"status": "crash", "stderr_tail": ...}` and the sweep continues.

### Problem adapters

A registry entry per problem:

| field | MaxCut | TSP |
|---|---|---|
| runner module | `examples/maxcut/runner.py` (via `importlib`) | `examples/tsp/runner.py` |
| build | `build_problem(n, seed)` | `build_problem(n, seed)` |
| size ladder | 8, 16, 32, 64, 128, 256, 512 | 4, 6, 8, 12, 16, 24, 32, 48 |
| structural check | partition is 0/1, length n | tour is a permutation of 0..n-1 |
| objective recompute | `cut_weight` | `tour_distance` |
| canonicalize | `canonicalize_partition` | `canonicalize_tour` |

Loading the example modules with `importlib.util.spec_from_file_location`
means the sweep exercises the shipped encodings byte-identical — no
duplicated model code.

### Measured stages

The point mode re-runs the examples' exact sequence with
`time.perf_counter()` around each stage:

1. `compile` — `problem.compile()`
2. `encode` — VM run of the encoder program
3. `solve` — `SolverDWaveCPU.solve(model)`
4. `verify` — VM run of the verifier program
5. `decode` — VM run of the decoder program

Each record also captures model shape (variable count, quadratic-term
count) and whole-point peak RSS.

## Correctness checks (per point)

1. Verifier output `valid == 1`.
2. Decoded solution structurally valid (see adapter table).
3. Host-side objective recomputed from the decoded solution using the
   example's own helper; recorded alongside the model energy.
4. **Cross-VM parity:** canonicalized Python-VM vs Rust-VM outputs
   compared by the orchestrator at every (problem, n, seed) where both
   lanes are still in range.

## Sweep policy

- Three seeds per point (42, 43, 44); median timing reported.
- A (problem, backend, solver) lane stops escalating when a point
  exceeds `--timeout` (default 300 s per point) or fails; the ceiling
  is a headline finding, not an error.
- GPU lanes get extended ladders — MaxCut up to 1024, TSP up to 64 —
  since the solve stage should push past the CPU ceiling; the timeout
  rule still bounds them.
- **Cross-solver comparison:** at every (problem, n, seed) where both
  solvers complete, the summary reports the energy delta
  (`cuda-gpu` vs `dwave-cpu`). Annealing is stochastic, so this is
  reported, not asserted.
- The orchestrator never aborts the whole sweep on a lane failure.

## Output

- `--out` JSONL: one record per (problem, backend, solver, n, seed).
- Generated markdown summary: per-lane max feasible n, stage-time
  breakdown, parity table, failures/crashes.
- Findings posted as a Linear comment on QUI-167; ticket checkboxes
  ticked.

## Error handling

- Stage exception in child → record `{"status": "error", "stage": ...,
  "message": ...}`; lane stops escalating.
- Child killed / nonzero exit → `{"status": "crash"}` with stderr tail.
- Child exceeding the timeout → killed by the orchestrator, recorded as
  `{"status": "timeout"}`.

## Testing

- `--smoke` flag: tiny ns (MaxCut 8, TSP 4), one seed, both backends —
  fast end-to-end check of the harness itself.
- ruff/lint clean (zero-warnings policy). No CI job.

## Logistics

- Branch `feature/qui-167` (worktree off `origin/main`); the release
  checkout stays on `release/v0.3.0`.
- Baseline verified: `make example-smoke` green (14/14 examples, both
  interpreters) before any changes.
- GPU lane environment: `uv sync --extra cuda`, then re-run
  `uv run --active maturin develop --manifest-path xqffi/Cargo.toml` —
  the sync reverts maturin's editable xqffi build to a wheel (same
  gotcha `make deps-py` documents). Verified: `SolverCudaGPU` solves
  MaxCut n=8 on the RTX 4090 (WSL2) matching the CPU golden.
