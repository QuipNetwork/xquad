# QUI-704: Bit-Packed Spin Storage Evaluation — Design

**Ticket:** [QUI-704](https://linear.app/quip-network/issue/QUI-704) — perf(xqsa): evaluate
bit-packed spin storage for GPU SA solvers (milestone v0.3.1, Low)

## Goal

Measured evaluation, per the ticket's own criteria: at QUI-167-scale problem sizes, measure
device-memory footprint and throughput for dense `int32` spin storage vs a bit-packed
prototype, and recommend adopt/reject. **No solver code changes ship from this branch** —
the deliverables are a benchmark script, a findings doc, and the recommendation on the
ticket.

## Grounding analysis (code inspection, to be confirmed by measurement)

Device allocations per solve:

| buffer | SolverCudaGPU | SolverMetalGPU |
|---|---|---|
| spins (`samples`) | `num_reads × n × 4 B` (int32) | `num_reads × n × 4 B` (int32) |
| couplings (`J`) | `n² × 8 B` (float64) | `n² × 4 B` (float32) |
| acceptance randoms | `num_reads × num_sweeps × n × 8 B` (float64, pre-generated; guarded at 80% of GPU mem) | **none** — on-device xorshift32 (`metal_gpu.py` docstring) |

At n=1024, reads=100, sweeps=1000 (CUDA): randoms ≈ 819 MB, J ≈ 8.4 MB, spins ≈ 0.4 MB.
The ticket's premise ("smaller device-memory footprint at large n" from packing spins)
targets the smallest buffer on either solver; bit-packing saves 87.5% of well under 1% of
the CUDA footprint. The evaluation quantifies this precisely — and reports where the
footprint actually lives.

Two adjacent observations get documented (not fixed) as follow-up candidates:
1. **CUDA randoms buffer** — adopting Metal's on-device RNG would eliminate the dominant
   allocation entirely *and* re-align the two solvers (the alignment QUI-704's description
   values). It is also the actual blocker for large-n CUDA solves (the 80%-of-VRAM guard).
2. **CUDA kernel occupancy** — the kernel launches one *single-thread* block per replica
   (`(num_reads,), (1,)`), leaving the GPU massively underutilized; consistent with
   QUI-167's finding that CUDA solve is ~5× slower than `SolverDWaveCPU` at MaxCut 512.

## Benchmark

`scripts/bench_spin_packing.py` (committed for reproducibility):

- **Instances:** synthetic spin models built directly via `XQMX.spin_model(n)` with seeded
  random ±1 couplings on 8 neighbours per node (sparse couplings keep XQMX construction
  fast; the on-device J matrix is stored dense n² regardless, so the footprint picture is
  unaffected — but throughput is NOT comparable to QUI-167's complete-graph MaxCut
  instances; built directly so n is not limited by the VM encode step).
- **Sizes:** n ∈ {512, 1024, 2048, 4096}; `num_reads=64`, `num_sweeps=500`, `seed=42`
  (randoms buffer at n=4096 ≈ 1.05 GB — fits the 24 GB RTX 4090 with headroom).
- **Variants:**
  - *stock* — `SolverCudaGPU` as shipped.
  - *packed prototype* — bench-local copy of the raw CUDA kernel + driver with `samples`
    stored as packed `uint32` words (32 spins/word), bit-read/bit-flip inside the sweep
    loop, unpack on host. Same algorithm, same seeding structure. Lives only in the bench
    script.
- **Metrics per (variant, n):** analytic buffer breakdown; measured peak device memory
  (cupy `MemoryPool` used/total bytes around the solve); solve wall time (median of 3);
  best energy for stock vs packed on the same instance (sanity: the prototype must be a
  faithful reimplementation, energies statistically comparable — this guards against
  benchmarking a broken kernel).
- **Metal:** analytic section only (no Metal hardware here). Spins vs float32-J ratio at
  the same sizes; the conclusion is expected to carry over a fortiori (no randoms buffer,
  so J dominates even harder).

## Decision rule (from the ticket)

Adopt only if the footprint reduction is *material* at large n without unacceptable
throughput regression. Materiality is reported as: spins' share of total device footprint
(stock) and absolute MB saved by packing, per size. Throughput as packed/stock wall-time
ratio.

## Deliverables

1. `scripts/bench_spin_packing.py` + raw results JSON under `scratch/` (not committed).
2. `docs/superpowers/specs/2026-07-13-qui-704-findings.md` — tables, recommendation,
   follow-up candidates (committed).
3. Linear: findings comment + recommendation on QUI-704; follow-up tickets proposed to
   Konrad (filed only on his say-so).

## Logistics

- Branch `feature/qui-704`, worktree `.claude/worktrees/feature+qui-704`, base 15918ed
  (post-v0.3.0 main). CUDA env verified (cupy sees the 4090).
- MR at the end (branch carries the bench script + docs only).
