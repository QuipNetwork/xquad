# QUI-704 Spin-Packing Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/bench_spin_packing.py`, benchmark dense `int32` vs bit-packed spin storage for `SolverCudaGPU` on the RTX 4090 at n = 512…4096, and produce a findings doc with an adopt/reject recommendation.

**Architecture:** One benchmark script. It builds seeded synthetic spin instances (an `XQMX` for the stock solver plus matching `h`/`J` numpy arrays for the prototype), runs the shipped `SolverCudaGPU`, and runs a bench-local bit-packed variant of the same kernel that consumes an *identical* RNG stream, identical init samples, and identical fixed `beta_range` — so trajectories are bit-identical and the prototype's faithfulness is asserted by exact best-sample equality, not statistics. Metrics: per-buffer analytic bytes, measured mempool peak, median wall time.

**Tech Stack:** cupy (RawKernel), numpy, `xqvm_py.xqmx.XQMX`, `xqsa.cuda_gpu.SolverCudaGPU`. No new dependencies. Metal is analytic-only (no hardware) — handled in the findings doc, not the script.

**Spec:** `docs/design/specs/2026-07-13-qui-704-spin-packing-eval-design.md` (approved 2026-07-13).

## Global Constraints

- Work ONLY in the worktree `/home/konrad/Quip/xquad/.claude/worktrees/feature+qui-704` (branch `feature/qui-704`, base 15918ed).
- Run Python via `uv run --no-sync ...` (plain `uv run` reverts the maturin-built xqffi).
- Bench parameters (fixed): `num_reads=64`, `num_sweeps=500`, `seed=42`, `beta_range=(0.1, 10.0)` passed explicitly to BOTH variants; sizes `n ∈ (512, 1024, 2048, 4096)`; 3 timing repetitions, median reported.
- **No changes to `xqsa/` source.** The packed kernel lives only in the bench script.
- Results JSON goes under `scratch/spin_packing/` (not committed); the script and findings doc are committed.
- Style: ≤100 lines/function, ≤5 positional params, 100-char lines (`awk 'length > 100'` — repo ruff allows 120 but 100 binds), ruff clean, full 16-line AGPL header + SPDX (copy from `scripts/scale_sweep.py` if present on this branch's base — otherwise from `xqsa/tests/test_xqsa.py`).
- Commits: imperative, ≤72-char subject, no co-author bylines/trailers.

## File Structure

- Create `scripts/bench_spin_packing.py` — instance builder, stock runner, packed prototype (kernel + driver), matrix runner, JSON output.
- Create `docs/design/specs/2026-07-13-qui-704-findings.md` — Task 3, from measured data.

---

### Task 1: Bench scaffold — instances, stock runner, CLI

**Files:**
- Create: `scripts/bench_spin_packing.py`

**Interfaces:**
- Produces (Task 2 depends on these exact signatures): `build_instance(n: int, seed: int) -> tuple[XQMX, np.ndarray, np.ndarray]` returning `(model, h, j_matrix)` where `h` is `float64[n]` zeros and `j_matrix` is the full symmetric `float64[n, n]` coupling matrix; `run_stock(model: XQMX, n: int) -> dict` returning `{"variant": "stock", "n": n, "wall_s": [3 floats], "energy": int, "sample": list[int], "mempool_peak_bytes": int}`; constants `NUM_READS = 64`, `NUM_SWEEPS = 500`, `SEED = 42`, `BETA_RANGE = (0.1, 10.0)`, `SIZES = (512, 1024, 2048, 4096)`, `REPS = 3`.

- [ ] **Step 1: Create the script**

Full 16-line AGPL header + SPDX first, then:

```python
"""
QUI-704 benchmark: dense int32 vs bit-packed spin storage for SolverCudaGPU.

Runs the shipped solver and a bench-local bit-packed variant of the same
CUDA kernel on identical seeded instances with an identical RNG stream and
fixed beta range, so trajectories match bit-for-bit. Reports per-buffer
analytic sizes, measured mempool peaks, and median wall times.

Requires an NVIDIA GPU + the xqsa[cuda] extra. Results are written as JSON
to scratch/spin_packing/ (not committed); see the QUI-704 findings doc.

Usage:
    uv run --no-sync python scripts/bench_spin_packing.py
    uv run --no-sync python scripts/bench_spin_packing.py --sizes 512
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np

from xqsa.cuda_gpu import SolverCudaGPU
from xqvm_py.xqmx import XQMX

REPO_ROOT = Path(__file__).resolve().parent.parent

NUM_READS = 64
NUM_SWEEPS = 500
SEED = 42
BETA_RANGE = (0.1, 10.0)
SIZES = (512, 1024, 2048, 4096)
REPS = 3
COUPLINGS_PER_NODE = 8  # sparse couplings keep XQMX construction fast; J stays dense n^2


def build_instance(n: int, seed: int) -> tuple[XQMX, np.ndarray, np.ndarray]:
    """Seeded spin instance: each node couples to its next COUPLINGS_PER_NODE
    neighbours with random +/-1. Returns the XQMX (for the stock solver) and
    the matching dense (h, J) arrays (for the packed prototype). J is stored
    symmetric-full, mirroring the layout SolverCudaGPU builds internally —
    verified end-to-end by Task 2's exact-trajectory check."""
    rng = random.Random(seed)
    model = XQMX.spin_model(n)
    h = np.zeros(n, dtype=np.float64)
    j_matrix = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for k in range(1, COUPLINGS_PER_NODE + 1):
            j = i + k
            if j >= n:
                break
            coupling = rng.choice((-1.0, 1.0))
            model.set_quadratic(i, j, coupling)
            j_matrix[i, j] = coupling
            j_matrix[j, i] = coupling
    return model, h, j_matrix


def _mempool_peak(cupy) -> int:
    return int(cupy.get_default_memory_pool().total_bytes())


def run_stock(model: XQMX, n: int) -> dict:
    """Run the shipped SolverCudaGPU REPS times; record wall times, the
    (deterministic) best energy/sample, and the mempool peak."""
    import cupy

    solver = SolverCudaGPU(
        num_reads=NUM_READS, num_sweeps=NUM_SWEEPS, beta_range=BETA_RANGE, seed=SEED
    )
    cupy.get_default_memory_pool().free_all_blocks()
    walls: list[float] = []
    result = None
    for _ in range(REPS):
        t0 = time.perf_counter()
        result = solver.solve(model)
        walls.append(time.perf_counter() - t0)
    sample = [int(result.sample.get_linear(i)) for i in range(n)]
    return {
        "variant": "stock",
        "n": n,
        "wall_s": walls,
        "energy": int(result.energy),
        "sample": sample,
        "mempool_peak_bytes": _mempool_peak(cupy),
    }


def analytic_bytes(n: int) -> dict:
    """Per-buffer device bytes for both variants at the bench parameters."""
    return {
        "j_matrix": n * n * 8,
        "h": n * 8,
        "randoms": NUM_READS * NUM_SWEEPS * n * 8,
        "samples_dense_int32": NUM_READS * n * 4,
        "samples_packed_uint32": NUM_READS * ((n + 31) // 32) * 4,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QUI-704 spin-packing benchmark")
    parser.add_argument("--sizes", type=int, nargs="*", default=list(SIZES))
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "scratch" / "spin_packing" / "results.json"
    )
    args = parser.parse_args(argv)

    rows: list[dict] = []
    for n in args.sizes:
        model, h, j_matrix = build_instance(n, SEED)
        stock = run_stock(model, n)
        rows.append({**stock, "analytic": analytic_bytes(n)})
        print(f"n={n} stock: median {sorted(stock['wall_s'])[1]:.2f}s energy {stock['energy']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify with a single-size run**

Run: `uv run --no-sync python scripts/bench_spin_packing.py --sizes 512 --out scratch/spin_packing/smoke1.json`
Expected: one line `n=512 stock: median ...s energy ...` (energy is a deterministic negative int; the same command re-run prints the identical energy), then the JSON path. If `SolverCudaGPU.solve` rejects any argument or the energies differ between two runs with the same seed, STOP and report — determinism is a prerequisite for Task 2's exact check.

- [ ] **Step 3: Ruff, line length, commit**

```bash
uv run --no-sync ruff check scripts/bench_spin_packing.py
awk 'length > 100 {print FNR" ("length")"}' scripts/bench_spin_packing.py
git add scripts/bench_spin_packing.py
git commit -m "bench(xqsa): add spin-packing benchmark scaffold (QUI-704)"
```
awk must print nothing.

---

### Task 2: Bit-packed prototype — kernel, driver, exact-trajectory check

**Files:**
- Modify: `scripts/bench_spin_packing.py`

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces: `run_packed(h: np.ndarray, j_matrix: np.ndarray, n: int) -> dict` (same row shape, `"variant": "packed"`); `PACKED_KERNEL` (CUDA source string, entry `sa_spin_packed`).

**Background you need:** the shipped spin kernel is `_SA_SPIN_KERNEL` in `xqsa/cuda_gpu.py:108-161` — read it first. The driver logic to replicate is `_run_sa` in the same file (`xqsa/cuda_gpu.py:318-392`): RNG stream order is (1) `rng.integers(0, 2, size=(num_reads, n), dtype=cupy.int32)` mapped `*2-1` to spins, then (2) `rng.random(size=(num_reads, num_sweeps, n), dtype=cupy.float64)`. Reproduce that order exactly with `cupy.random.default_rng(SEED)` so the packed run consumes the identical stream. Also read how `solve()` builds `h`/`j_matrix` from the XQMX (the code just above `_run_sa`) — if it builds J symmetric-full as Task 1 assumes, the exact-trajectory check will pass; if the check fails, compare your bench `j_matrix` against the solver's construction and fix `build_instance` to match (report what you found).

- [ ] **Step 1: Append the packed kernel + driver**

```python
PACKED_KERNEL = r"""
extern "C" __global__
void sa_spin_packed(
    const double*  __restrict__ h,
    const double*  __restrict__ J,
    unsigned int*  __restrict__ words,   /* packed spins: bit set -> +1 */
    double*        __restrict__ energies,
    const double*  __restrict__ randoms,
    int    n,
    int    n_words,
    int    num_sweeps,
    double beta_start,
    double beta_end
) {
    int rid = blockIdx.x;
    unsigned int* w = words + (long long)rid * n_words;
    const double* rand_ptr = randoms + (long long)rid * num_sweeps * n;

#define SPIN(i) ((double)(((w[(i) >> 5] >> ((i) & 31)) & 1u) ? 1 : -1))

    double energy = 0.0;
    for (int i = 0; i < n; i++) {
        double si = SPIN(i);
        energy += h[i] * si;
        for (int j = i + 1; j < n; j++) {
            energy += J[i * n + j] * si * SPIN(j);
        }
    }

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        double beta;
        if (num_sweeps <= 1) {
            beta = beta_start;
        } else {
            beta = beta_start
                + (beta_end - beta_start)
                    * ((double)sweep / (double)(num_sweeps - 1));
        }

        for (int i = 0; i < n; i++) {
            double local = h[i];
            for (int j = 0; j < n; j++) {
                if (j == i) continue;
                local += J[i * n + j] * SPIN(j);
            }
            double delta_E = -2.0 * SPIN(i) * local;

            double r = rand_ptr[(long long)sweep * n + i];
            if (delta_E <= 0.0 || r < exp(-delta_E * beta)) {
                w[i >> 5] ^= (1u << (i & 31));
                energy += delta_E;
            }
        }
    }
    energies[rid] = energy;
#undef SPIN
}
"""


def _pack_spins(cupy, spins) -> "cupy.ndarray":
    """Pack a (num_reads, n) +/-1 int32 array into (num_reads, n_words) uint32
    words, bit set for +1. Padding bits beyond n are zero and never read."""
    bits = (spins > 0).astype(cupy.uint32)
    num_reads, n = spins.shape
    n_words = (n + 31) // 32
    padded = cupy.zeros((num_reads, n_words * 32), dtype=cupy.uint32)
    padded[:, :n] = bits
    shifts = cupy.arange(32, dtype=cupy.uint32)
    return (padded.reshape(num_reads, n_words, 32) << shifts).sum(
        axis=2, dtype=cupy.uint32
    )


def _unpack_row(row_words: np.ndarray, n: int) -> list[int]:
    """Unpack one uint32 word row back to a +/-1 spin list of length n."""
    out: list[int] = []
    for i in range(n):
        bit = (int(row_words[i >> 5]) >> (i & 31)) & 1
        out.append(1 if bit else -1)
    return out


def run_packed(h: np.ndarray, j_matrix: np.ndarray, n: int) -> dict:
    """Bit-packed variant with the identical RNG stream, init, and betas as
    the stock solver, so trajectories are bit-identical."""
    import cupy

    kernel = cupy.RawKernel(PACKED_KERNEL, "sa_spin_packed")
    n_words = (n + 31) // 32
    d_h = cupy.asarray(h)
    d_j = cupy.asarray(j_matrix)
    cupy.get_default_memory_pool().free_all_blocks()

    walls: list[float] = []
    best_energy = 0.0
    best_row: np.ndarray | None = None
    for _ in range(REPS):
        rng = cupy.random.default_rng(SEED)
        raw = rng.integers(0, 2, size=(NUM_READS, n), dtype=cupy.int32)
        spins = (raw * 2 - 1).astype(cupy.int32)
        words = cupy.ascontiguousarray(_pack_spins(cupy, spins))
        randoms = rng.random(size=(NUM_READS, NUM_SWEEPS, n), dtype=cupy.float64)
        energies = cupy.zeros(NUM_READS, dtype=cupy.float64)

        t0 = time.perf_counter()
        kernel(
            (NUM_READS,),
            (1,),
            (
                d_h, d_j, words, energies, randoms,
                np.int32(n), np.int32(n_words), np.int32(NUM_SWEEPS),
                np.float64(BETA_RANGE[0]), np.float64(BETA_RANGE[1]),
            ),
        )
        cupy.cuda.Device().synchronize()
        walls.append(time.perf_counter() - t0)

        energies_np = cupy.asnumpy(energies)
        best_idx = int(np.argmin(energies_np))
        best_energy = float(energies_np[best_idx])
        best_row = cupy.asnumpy(words[best_idx])

    assert best_row is not None
    return {
        "variant": "packed",
        "n": n,
        "wall_s": walls,
        "energy": best_energy,
        "sample": _unpack_row(best_row, n),
        "mempool_peak_bytes": _mempool_peak(cupy),
    }
```

Note the timing asymmetry this creates: `run_stock` times the whole `solve()` call (includes RNG generation + host transfers), while the packed timer brackets only the kernel. Fix the asymmetry by moving the packed variant's `t0 = time.perf_counter()` to BEFORE the `rng = ...` line so both walls cover RNG + kernel + sync. Keep the row's `wall_s` semantics identical between variants; state remaining differences (XQMX extraction happens only in stock) in the findings doc.

- [ ] **Step 2: Wire the packed run + faithfulness assert into `main`**

Replace the loop body in `main` with:

```python
        model, h, j_matrix = build_instance(n, SEED)
        stock = run_stock(model, n)
        packed = run_packed(h, j_matrix, n)
        if packed["sample"] != stock["sample"]:
            raise SystemExit(
                f"TRAJECTORY DIVERGENCE at n={n}: packed prototype is not faithful "
                f"(first mismatch at index "
                f"{next(i for i, (a, b) in enumerate(zip(packed['sample'], stock['sample'])) if a != b)})"
            )
        analytic = analytic_bytes(n)
        rows.append({**stock, "analytic": analytic})
        rows.append({**packed, "analytic": analytic})
        med_s = sorted(stock["wall_s"])[1]
        med_p = sorted(packed["wall_s"])[1]
        print(
            f"n={n}: stock {med_s:.2f}s / packed {med_p:.2f}s "
            f"(x{med_p / med_s:.2f}), energy {stock['energy']}, samples identical"
        )
```

- [ ] **Step 3: Verify faithfulness at n=512**

Run: `uv run --no-sync python scripts/bench_spin_packing.py --sizes 512 --out scratch/spin_packing/smoke2.json`
Expected: `samples identical` in the output line. If TRAJECTORY DIVERGENCE fires: first check J-layout (read `solve()`'s array construction in `xqsa/cuda_gpu.py` and align `build_instance`), then RNG order. If it still diverges after both, STOP and report the mismatch details — do not weaken the check to a tolerance.

- [ ] **Step 4: Ruff, line length, commit**

```bash
uv run --no-sync ruff check scripts/bench_spin_packing.py
awk 'length > 100 {print FNR" ("length")"}' scripts/bench_spin_packing.py
git add scripts/bench_spin_packing.py
git commit -m "bench(xqsa): add bit-packed kernel prototype with exact-trajectory check"
```

---

### Task 3: Full matrix run + findings doc

**Files:**
- Create: `docs/design/specs/2026-07-13-qui-704-findings.md`
- Output (not committed): `scratch/spin_packing/results.json`

- [ ] **Step 1: Full run**

Run: `uv run --no-sync python scripts/bench_spin_packing.py`
Expected: four `n=...` lines, each ending `samples identical`; up to ~15 min at n=4096 (single-thread-per-block kernel is slow — that slowness is itself a documented finding from QUI-167). If n=4096 exceeds 30 min, record the sizes that completed and note the abort in the findings; do not kill smaller sizes.

- [ ] **Step 2: Write the findings doc**

Create `docs/design/specs/2026-07-13-qui-704-findings.md` with exactly these sections, filling every number from `scratch/spin_packing/results.json` (never hand-estimate):
1. Header: ticket link, date, machine (RTX 4090 / WSL2 / cupy version), bench parameters.
2. **Device-memory breakdown table** — per n: J bytes, randoms bytes, dense-samples bytes, packed-samples bytes, spins' % of total (analytic), measured mempool peaks for both variants.
3. **Throughput table** — per n: stock median wall, packed median wall, ratio.
4. **Faithfulness** — one line: exact best-sample equality held at every size (or details).
5. **Metal analysis (no hardware)** — analytic: no randoms buffer (on-device xorshift32, `metal_gpu.py` docstring), J float32 `n²×4` dominates; spins' % of Metal footprint at the same sizes; conclusion carries a fortiori.
6. **Recommendation** — adopt/reject against the ticket's criterion ("material footprint reduction without unacceptable throughput regression"), stated plainly with the measured shares.
7. **Follow-up candidates** (documented, not implemented): CUDA on-device RNG (eliminates the dominant randoms buffer, re-aligns solvers, lifts the 80%-VRAM guard); kernel occupancy (`block=(1,)` single-thread launch, consistent with QUI-167's 5× CPU-vs-GPU finding).

- [ ] **Step 3: Commit**

```bash
git add docs/design/specs/2026-07-13-qui-704-findings.md
git commit -m "docs: record QUI-704 spin-packing evaluation findings"
```

---

## Self-Review

- **Spec coverage:** instances + stock runner (Task 1), packed prototype + faithfulness (Task 2), full matrix + findings incl. Metal analytic section and follow-up candidates (Task 3). Linear comment/closeout is controller-side, per spec Deliverable 3.
- **Placeholder scan:** none — every code step is complete; findings sections enumerate exact required content sourced from the results JSON.
- **Type consistency:** row dict shape shared by `run_stock`/`run_packed`; constants used identically across tasks; `build_instance` tuple order `(model, h, j_matrix)` consistent.
- **Known risk, mitigated:** Task 1's assumption that `solve()` builds J symmetric-full is verified end-to-end by Task 2's exact-trajectory check, with an explicit diagnose-and-align instruction if it fires.
