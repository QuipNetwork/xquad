# QUI-852: GPU SA Kernel Occupancy (CUDA + Metal) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parallelize the O(n) local-field reduction inside the GPU SA kernels across the threads of each replica's block/threadgroup (currently 1 thread per replica), keeping spin updates strictly sequential — same proposal order, same acceptance rule, same RNG consumption order (Linear QUI-852).

**Architecture:** CUDA: launch `(num_reads,)` blocks × `(256,)` threads; each thread computes a strided partial sum of `Σⱼ J[i,j]·x[j]`, a shared-memory tree reduction combines them, and thread 0 alone applies Metropolis + flips the spin, with `__syncthreads()` barriers around the sequential section. Metal: identical shape with `threadgroup` memory and `threadgroup_barrier`, threadgroup width clamped to a power of two ≤ `maxTotalThreadsPerThreadgroup` (≤ 256). The Metal `gibbs` pipeline keeps 1 thread/threadgroup (its parallelization interacts with the coloring — out of scope). Verification uses a committed benchmark harness: on **integer-coefficient instances, float64/float32 sums are exact in any order, so the new kernels must be bit-identical to the shipped ones** — a hard gate; float instances get distribution-parity checks; n=4096 and n=512 timings are re-measured against a baseline captured at the branch base.

**Tech Stack:** CUDA via CuPy RawKernel, Metal Shading Language via pyobjc, numpy, pytest, `uv run`.

## Global Constraints

- **Branch is STACKED on `feature/qui-854`** (MR !91): branch `feature/qui-852` from `feature/qui-854`, NOT from main. The kernels being modified already take the beta buffer at arg index 5 (CUDA) / buffer 2 (Metal).
- **Markov chain definition unchanged:** proposal order (i = 0..n-1 sequential), acceptance rule, and RNG consumption order must be untouched. Only thread 0 reads RNG (CUDA: `rand_ptr[(long long)sweep * n + i]`; Metal: `rand_unit(rng)` on thread 0's state). Only floating-point *summation order* inside the local field may change.
- **Hard verification gate:** on integer-coefficient instances the new CUDA kernel must produce byte-identical results (raw_energy, energy, sample hash) to the baseline captured at the branch base. Not approximately — exactly.
- **Metal hardware validation is DEFERRED** (no Apple Silicon on this machine): MSL changes are verified structurally via the mocked suite (dispatch shape, ABI); the MR must state that real-hardware validation needs an M-series run (post-merge CI job `test:metal` or a reviewer machine). Do not claim Metal numerical verification.
- Thread counts: CUDA fixed `_THREADS_PER_REPLICA = 256`. Metal `_SA_THREADGROUP_WIDTH = 256`, clamped at dispatch to the largest power of two ≤ `pipeline.maxTotalThreadsPerThreadgroup()`; the tree reduction requires a power-of-two width.
- Repo rules: ruff format/lint at line-length 120; `uv run --no-sync pytest ...`; commits signed off (`git commit -s`), imperative ≤72-char subject, NO co-author bylines/Claude attribution. Commit after every green test cycle.
- Mocked CUDA test command: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "CudaGPUMocked" -v`
- Mocked Metal test command: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "MetalGPUMocked" -v`
- Real-GPU command: `uv run --extra cuda pytest xqsa/tests/test_xqsa.py -m cuda -v` (RTX 4090 present; first run installs cupy — allow 600s timeout)
- Bench harness runs use `uv run --extra cuda python scripts/bench_occupancy.py ...` — long timeouts (up to 1800000 ms for the timing baseline; run in foreground with that timeout).

---

### Task 1: Stacked branch + benchmark harness + baseline capture (runs the SHIPPED kernel)

**Files:**
- Create: `scripts/bench_occupancy.py`
- Create (uncommitted, scratch): `scratch/qui-852/baseline-trajectories.json`, `scratch/qui-852/baseline-parity.json`, `scratch/qui-852/baseline-timing.json`

**Interfaces:**
- Produces: `scripts/bench_occupancy.py` with subcommands `trajectories`, `parity`, `timing`, each writing JSON via `--out`, and `compare --mode {trajectories,parity,timing} BASELINE CURRENT` which exits 1 on trajectory mismatch and prints stats otherwise. Task 3 re-runs the same subcommands on the new kernel and compares.
- CRITICAL ORDERING: baselines MUST be captured in this task, before any kernel change.

- [ ] **Step 1: Verify branch state** (the controller prepared the worktree — confirm, don't create):

Run: `git branch --show-current && git log --oneline -1 && git merge-base --is-ancestor $(git rev-parse origin/feature/qui-854 2>/dev/null || git rev-parse feature/qui-854) HEAD && echo STACKED-OK`
Expected: `feature/qui-852`, and `STACKED-OK`.

- [ ] **Step 2: Write `scripts/bench_occupancy.py`** (complete file):

```python
#!/usr/bin/env python3
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
"""QUI-852 occupancy benchmark: trajectory gate, parity stats, and timing.

Captures reference outputs of ``SolverCudaGPU`` so a kernel change can be
verified against the shipped behaviour:

* ``trajectories`` -- integer-coefficient instances (h=0, J in {0,1}). All
  local-field sums are integer-valued, hence exact in float64 in ANY
  summation order, so a reduction-order change must reproduce these results
  bit-for-bit. Run with ``num_reads=1`` so the best read IS the trajectory.
* ``parity`` -- gaussian-coefficient instances, where summation order may
  legitimately flip Metropolis decisions at the float boundary. Compared
  distributionally (mean/std of best energies across seeds).
* ``timing`` -- wall-clock of representative solves (n=512 MaxCut-style,
  n=4096 gaussian), for the before/after speedup measurement.

Usage:
    uv run --extra cuda python scripts/bench_occupancy.py trajectories --out FILE
    uv run --extra cuda python scripts/bench_occupancy.py parity --out FILE
    uv run --extra cuda python scripts/bench_occupancy.py timing --out FILE
    uv run --extra cuda python scripts/bench_occupancy.py compare --mode trajectories BASE CUR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

from xqvm_py.xqmx import XQMX

TRAJ_SIZES = (64, 256)
TRAJ_SEEDS = tuple(range(30))
TRAJ_SWEEPS = 400
PARITY_SIZES = (256, 1024)
PARITY_SEEDS = tuple(range(10))
PARITY_READS = 64
PARITY_SWEEPS = 1000
TIMING_CASES = (
    {"kind": "maxcut", "n": 512, "num_reads": 200, "num_sweeps": 2000, "seed": 42},
    {"kind": "gaussian", "n": 4096, "num_reads": 200, "num_sweeps": 2000, "seed": 42},
)


def build_maxcut(n: int, seed: int, domain: str) -> XQMX:
    """Integer-coefficient instance: h=0, J[i,j] in {0.0, 1.0} at density 0.5."""
    rng = np.random.default_rng(seed)
    model = XQMX.spin_model(n) if domain == "spin" else XQMX.binary_model(n)
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.5:
                model.set_quadratic(i, j, 1.0)
    return model


def build_gaussian(n: int, seed: int) -> XQMX:
    """Float-coefficient spin instance: h, J ~ N(0, 1) at density 0.25."""
    rng = np.random.default_rng(seed)
    model = XQMX.spin_model(n)
    for i in range(n):
        model.set_linear(i, float(rng.normal()))
        for j in range(i + 1, n):
            if rng.random() < 0.25:
                model.set_quadratic(i, j, float(rng.normal()))
    return model


def _sample_hash(sample: XQMX, n: int) -> str:
    bits = ",".join(str(sample.get_linear(i)) for i in range(n))
    return hashlib.sha256(bits.encode()).hexdigest()


def _solve(model: XQMX, num_reads: int, num_sweeps: int, seed: int) -> tuple[dict, float]:
    from xqsa.cuda_gpu import SolverCudaGPU

    solver = SolverCudaGPU(num_reads=num_reads, num_sweeps=num_sweeps, seed=seed)
    t0 = time.perf_counter()
    result = solver.solve(model)
    elapsed = time.perf_counter() - t0
    record = {
        "energy": int(result.energy),
        "raw_energy": float(result.metadata["params"]["raw_energy"]),
        "sample_sha256": _sample_hash(result.sample, model.size),
    }
    return record, elapsed


def cmd_trajectories(out: Path) -> None:
    data: dict = {}
    for domain in ("binary", "spin"):
        for n in TRAJ_SIZES:
            for seed in TRAJ_SEEDS:
                model = build_maxcut(n, seed, domain)
                record, _ = _solve(model, num_reads=1, num_sweeps=TRAJ_SWEEPS, seed=seed)
                data[f"{domain}/n{n}/s{seed}"] = record
    out.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"wrote {len(data)} trajectory records -> {out}")


def cmd_parity(out: Path) -> None:
    data: dict = {}
    for n in PARITY_SIZES:
        energies = []
        for seed in PARITY_SEEDS:
            model = build_gaussian(n, seed)
            record, _ = _solve(model, num_reads=PARITY_READS, num_sweeps=PARITY_SWEEPS, seed=seed)
            energies.append(record["raw_energy"])
        data[f"n{n}"] = {"best_energies": energies}
    out.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"wrote parity records -> {out}")


def cmd_timing(out: Path) -> None:
    data: dict = {}
    for case in TIMING_CASES:
        n, seed = case["n"], case["seed"]
        model = build_maxcut(n, seed, "spin") if case["kind"] == "maxcut" else build_gaussian(n, seed)
        record, elapsed = _solve(model, case["num_reads"], case["num_sweeps"], seed)
        data[f"{case['kind']}/n{n}"] = {"seconds": elapsed, "raw_energy": record["raw_energy"]}
        print(f"{case['kind']}/n{n}: {elapsed:.1f} s")
    out.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"wrote timing records -> {out}")


def cmd_compare(mode: str, baseline: Path, current: Path) -> int:
    base = json.loads(baseline.read_text())
    cur = json.loads(current.read_text())
    if mode == "trajectories":
        mismatches = [k for k in sorted(base) if base[k] != cur.get(k)]
        if mismatches:
            for k in mismatches[:20]:
                print(f"MISMATCH {k}: base={base[k]} cur={cur.get(k)}")
            print(f"FAIL: {len(mismatches)}/{len(base)} trajectory records differ")
            return 1
        print(f"OK: all {len(base)} trajectory records bit-identical")
        return 0
    if mode == "parity":
        for key in sorted(base):
            b = np.asarray(base[key]["best_energies"])
            c = np.asarray(cur[key]["best_energies"])
            db, dc = b.mean(), c.mean()
            rel = abs(dc - db) / max(abs(db), 1e-12)
            print(f"{key}: base mean={db:.2f} std={b.std():.2f} | new mean={dc:.2f} std={c.std():.2f} | dmean={rel:.2%}")
        return 0
    for key in sorted(base):
        b, c = base[key]["seconds"], cur[key]["seconds"]
        print(f"{key}: base={b:.1f} s -> new={c:.1f} s ({b / max(c, 1e-9):.1f}x)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QUI-852 occupancy benchmark")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("trajectories", "parity", "timing"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare")
    p.add_argument("--mode", choices=["trajectories", "parity", "timing"], required=True)
    p.add_argument("baseline", type=Path)
    p.add_argument("current", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "compare":
        return cmd_compare(args.mode, args.baseline, args.current)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    {"trajectories": cmd_trajectories, "parity": cmd_parity, "timing": cmd_timing}[args.cmd](args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Smoke-check the harness** (fast, real GPU):

Run: `uv run --extra cuda python -c "import scripts.bench_occupancy as b; m=b.build_maxcut(16,0,'binary'); r,_=b._solve(m,1,50,0); print(r)"` — expect a record dict printed, no traceback. (If `scripts` is not importable as a package, run the equivalent through `python scripts/bench_occupancy.py trajectories --out /tmp/smoke.json` after temporarily reducing nothing — instead just proceed to Step 4, which exercises the same code.)

- [ ] **Step 4: Capture baselines** (SHIPPED kernel — this is why this step precedes all kernel edits):

```bash
mkdir -p scratch/qui-852
uv run --extra cuda python scripts/bench_occupancy.py trajectories --out scratch/qui-852/baseline-trajectories.json
uv run --extra cuda python scripts/bench_occupancy.py parity --out scratch/qui-852/baseline-parity.json
uv run --extra cuda python scripts/bench_occupancy.py timing --out scratch/qui-852/baseline-timing.json
```

Timeouts: 600000 ms, 900000 ms, 1800000 ms respectively (the n=4096 timing case alone runs ~10 minutes on the shipped kernel). Record the printed timing numbers in your report.

- [ ] **Step 5: Lint + commit the harness** (scratch JSONs stay uncommitted; `scratch/` is gitignored):

```bash
uv run --no-sync ruff format --check scripts/bench_occupancy.py && uv run --no-sync ruff check scripts/bench_occupancy.py
git add scripts/bench_occupancy.py
git commit -s -m "bench(xqsa): add QUI-852 occupancy trajectory/parity/timing harness"
```

If `ruff format --check` fails, run `uv run --no-sync ruff format scripts/bench_occupancy.py` first.

---

### Task 2: CUDA kernels — parallel local-field reduction

**Files:**
- Modify: `xqsa/cuda_gpu.py` — `_SA_BINARY_KERNEL`, `_SA_SPIN_KERNEL`, the module constants block, and the kernel launch in `_run_sa`
- Modify: `xqsa/tests/test_xqsa.py` — `_capture_kernel_args` helper (record grid/block), one new mocked test

**Interfaces:**
- Consumes: kernel ABI from !91 — args `(h, J, samples, energies, randoms, betas, n, num_sweeps)`; the mocked fake kernel receives `(grid, block, args)` and simulates on CPU (it ignores `block`, so it needs NO functional change).
- Produces: launch shape `(num_reads,), (_THREADS_PER_REPLICA,)` with `_THREADS_PER_REPLICA = 256`; kernel arg list UNCHANGED. Task 3 verifies numerics; Task 4 mirrors the design in MSL.

- [ ] **Step 1: Add the failing mocked test.** Extend the existing `_capture_kernel_args` staticmethod in `TestSolverCudaGPUMocked` — change its body to also record the launch shape:

```python
    @staticmethod
    def _capture_kernel_args(solver) -> dict:
        """Shadow the cached binary kernel with a wrapper that records call args."""
        captured: dict = {}
        real_kernel = solver._binary_kernel

        def wrapper(grid, block, args):
            captured["grid"] = grid
            captured["block"] = block
            captured["args"] = args
            return real_kernel(grid, block, args)

        solver.__dict__["_binary_kernel"] = wrapper
        return captured
```

Then append inside `TestSolverCudaGPUMocked`:

```python
    def test_launch_shape_parallel_reduction_mocked(self, mock_cupy_env) -> None:
        """Each replica's block launches _THREADS_PER_REPLICA threads (QUI-852)."""
        from xqsa.cuda_gpu import _THREADS_PER_REPLICA, SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=7, num_sweeps=10, seed=1)
        captured = self._capture_kernel_args(solver)
        solver.solve(model)

        assert captured["grid"] == (7,)
        assert captured["block"] == (_THREADS_PER_REPLICA,)
        assert _THREADS_PER_REPLICA == 256
```

- [ ] **Step 2: Run it — expect FAIL** (`ImportError: cannot import name '_THREADS_PER_REPLICA'`):

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "launch_shape" -v`

- [ ] **Step 3: Rewrite `_SA_BINARY_KERNEL`.** Replace the full kernel source string with:

```c
extern "C" __global__
void sa_binary(
    const double* __restrict__ h,
    const double* __restrict__ J,
    int*          __restrict__ samples,
    double*       __restrict__ energies,
    const double* __restrict__ randoms,
    const double* __restrict__ betas,
    int    n,
    int    num_sweeps
) {
    /* One block per replica; blockDim.x threads cooperate on the O(n)
       local-field reduction. Spin updates stay strictly sequential on
       thread 0: same proposal order, same acceptance rule, same RNG
       consumption order as the single-thread kernel (QUI-852). Only the
       floating-point summation order of the local field changes. */
    __shared__ double sdata[256];
    int rid = blockIdx.x;
    int tid = threadIdx.x;
    int nthreads = blockDim.x;
    int* x = samples + rid * n;
    const double* rand_ptr = randoms + (long long)rid * num_sweeps * n;

    /* initial energy: one-time O(n^2), thread 0 only */
    double energy = 0.0;
    if (tid == 0) {
        for (int i = 0; i < n; i++) {
            if (x[i] == 0) continue;
            energy += h[i];
            for (int j = i + 1; j < n; j++) {
                if (x[j] == 0) continue;
                energy += J[i * n + j];
            }
        }
    }
    __syncthreads();

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        double beta = betas[sweep];

        for (int i = 0; i < n; i++) {
            /* strided partial sums of the local field for spin i */
            double partial = 0.0;
            for (int j = tid; j < n; j += nthreads) {
                if (j == i) continue;
                partial += J[i * n + j] * (double)x[j];
            }
            sdata[tid] = partial;
            __syncthreads();

            /* power-of-two tree reduction into sdata[0] */
            for (int s = nthreads / 2; s > 0; s >>= 1) {
                if (tid < s) sdata[tid] += sdata[tid + s];
                __syncthreads();
            }

            if (tid == 0) {
                double local = h[i] + sdata[0];
                double delta_E = local * (double)(1 - 2 * x[i]);
                double r = rand_ptr[(long long)sweep * n + i];
                if (delta_E <= 0.0 || r < exp(-delta_E * beta)) {
                    x[i] = 1 - x[i];
                    energy += delta_E;
                }
            }
            __syncthreads();
        }
    }
    if (tid == 0) energies[rid] = energy;
}
```

- [ ] **Step 4: Rewrite `_SA_SPIN_KERNEL`** — identical structure; the three differences from the binary kernel are the initial-energy loop, `delta_E`, and the flip:

```c
extern "C" __global__
void sa_spin(
    const double* __restrict__ h,
    const double* __restrict__ J,
    int*          __restrict__ samples,
    double*       __restrict__ energies,
    const double* __restrict__ randoms,
    const double* __restrict__ betas,
    int    n,
    int    num_sweeps
) {
    /* See sa_binary: cooperative local-field reduction, sequential updates. */
    __shared__ double sdata[256];
    int rid = blockIdx.x;
    int tid = threadIdx.x;
    int nthreads = blockDim.x;
    int* s = samples + rid * n;
    const double* rand_ptr = randoms + (long long)rid * num_sweeps * n;

    double energy = 0.0;
    if (tid == 0) {
        for (int i = 0; i < n; i++) {
            energy += h[i] * (double)s[i];
            for (int j = i + 1; j < n; j++) {
                energy += J[i * n + j] * (double)s[i] * (double)s[j];
            }
        }
    }
    __syncthreads();

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        double beta = betas[sweep];

        for (int i = 0; i < n; i++) {
            double partial = 0.0;
            for (int j = tid; j < n; j += nthreads) {
                if (j == i) continue;
                partial += J[i * n + j] * (double)s[j];
            }
            sdata[tid] = partial;
            __syncthreads();

            for (int st = nthreads / 2; st > 0; st >>= 1) {
                if (tid < st) sdata[tid] += sdata[tid + st];
                __syncthreads();
            }

            if (tid == 0) {
                double local = h[i] + sdata[0];
                double delta_E = -2.0 * (double)s[i] * local;
                double r = rand_ptr[(long long)sweep * n + i];
                if (delta_E <= 0.0 || r < exp(-delta_E * beta)) {
                    s[i] = -s[i];
                    energy += delta_E;
                }
            }
            __syncthreads();
        }
    }
    if (tid == 0) energies[rid] = energy;
}
```

- [ ] **Step 5: Host-side launch.** Under `_SUPPORTED_SCHEDULES = ...` add:

```python
# One CUDA block per replica; this many threads cooperate on the local-field
# reduction (QUI-852). Must be a power of two (tree reduction) and match the
# kernels' `__shared__ double sdata[256]`.
_THREADS_PER_REPLICA = 256
```

In `_run_sa`, change the launch:

```python
        kernel(
            (num_reads,),  # grid: one block per replica
            (_THREADS_PER_REPLICA,),  # block: cooperative local-field reduction
            (
```

(the args tuple is unchanged). Also update the comment line `# block: single thread per replica` if it survives anywhere.

- [ ] **Step 6: Run mocked suite** — all pass, including the new launch-shape test (the fake kernel ignores `block`, so pre-existing tests are unaffected):

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "CudaGPUMocked" -v`
Expected: 22 passed (21 existing + 1 new).

- [ ] **Step 7: Run the real-GPU suite** (NVRTC-compiles the new kernels; trivial-model results are exact-sum cases and must be unchanged):

Run: `uv run --extra cuda pytest xqsa/tests/test_xqsa.py -m cuda -v` (timeout 600000)
Expected: 18 passed.

- [ ] **Step 8: Lint + commit:**

```bash
uv run --no-sync ruff format --check xqsa/cuda_gpu.py xqsa/tests/test_xqsa.py && uv run --no-sync ruff check xqsa/cuda_gpu.py xqsa/tests/test_xqsa.py
git add xqsa/cuda_gpu.py xqsa/tests/test_xqsa.py
git commit -s -m "perf(xqsa): parallelize CUDA SA local-field reduction across block"
```

---

### Task 3: CUDA verification against baselines (RTX 4090)

**Files:**
- Create (uncommitted, scratch): `scratch/qui-852/new-trajectories.json`, `scratch/qui-852/new-parity.json`, `scratch/qui-852/new-timing.json`

**Interfaces:**
- Consumes: `scripts/bench_occupancy.py` (Task 1), the baselines in `scratch/qui-852/baseline-*.json`, and the new kernels (Task 2).
- Produces: the compare outputs (paste into your report verbatim); Task 5 copies the numbers into the findings doc.

- [ ] **Step 1: Re-run the three captures on the new kernel:**

```bash
uv run --extra cuda python scripts/bench_occupancy.py trajectories --out scratch/qui-852/new-trajectories.json
uv run --extra cuda python scripts/bench_occupancy.py parity --out scratch/qui-852/new-parity.json
uv run --extra cuda python scripts/bench_occupancy.py timing --out scratch/qui-852/new-timing.json
```

(timeouts 600000 / 900000 / 900000 — the new kernel should be much faster; if a run exceeds its timeout, that is itself a finding, report it).

- [ ] **Step 2: The trajectory gate (MUST exit 0):**

Run: `uv run --no-sync python scripts/bench_occupancy.py compare --mode trajectories scratch/qui-852/baseline-trajectories.json scratch/qui-852/new-trajectories.json`
Expected: `OK: all 120 trajectory records bit-identical`, exit 0. **If ANY record differs, the kernel violates the sequential-semantics contract — STOP, report BLOCKED with the mismatch list. Do not proceed and do not relax the gate.**

- [ ] **Step 3: Parity + timing comparisons (informational, paste output):**

```bash
uv run --no-sync python scripts/bench_occupancy.py compare --mode parity scratch/qui-852/baseline-parity.json scratch/qui-852/new-parity.json
uv run --no-sync python scripts/bench_occupancy.py compare --mode timing scratch/qui-852/baseline-timing.json scratch/qui-852/new-timing.json
```

Flag in your report if any parity `dmean` exceeds 2%, or if any timing case shows < 2x speedup (the reduction should be a large win at n=4096; a small win means the launch change didn't take effect).

- [ ] **Step 4: No commit** (scratch only). Write all numbers into your report file — they are the evidence Task 5 publishes.

---

### Task 4: Metal — MSL threadgroup reduction + per-strategy dispatch width

**Files:**
- Modify: `xqsa/metal_gpu.py` — `_SA_KERNEL` MSL source, buffer-ABI comment block (~lines 63-73), `_dispatch` (~line 521), the `sa` dispatch call site inside `_run_sa`, module constants
- Modify: `xqsa/tests/test_xqsa.py` — `mock_metal_env`'s `_Encoder.dispatchThreadgroups_threadsPerThreadgroup_` (record the width), two new mocked tests

**Interfaces:**
- Consumes: MSL `sa_metal` buffer ABI (0:h 1:J 2:beta 3:samples 4:energies 5:n 6:num_sweeps 7:base_seed 8:is_spin) — UNCHANGED; `_dispatch(pipeline, encoder, num_reads)` currently hardcodes `MTLSizeMake(1, 1, 1)`.
- Produces: `_dispatch(pipeline, encoder, num_reads, threads_per_group=1)`; `sa` dispatches with the clamped power-of-two width (≤ `_SA_THREADGROUP_WIDTH = 256`), `gibbs` stays at 1. The gibbs MSL kernel is untouched.

- [ ] **Step 1: Extend the Metal mock to record the threadgroup width.** In `mock_metal_env`'s `_Encoder`, replace:

```python
        def dispatchThreadgroups_threadsPerThreadgroup_(self, grid, per_group):
            self.num_reads = int(grid[0])
```

with:

```python
        def dispatchThreadgroups_threadsPerThreadgroup_(self, grid, per_group):
            self.num_reads = int(grid[0])
            self.threads_per_group = tuple(int(v) for v in per_group)
```

- [ ] **Step 2: Add the failing mocked tests** (inside `TestSolverMetalGPUMocked`, reusing that class's existing encoder-capture pattern — see `test_num_sweeps_per_beta_run_mocked` for the `capturing_queue` scaffold, and reuse it verbatim):

```python
    def test_sa_dispatch_width_mocked(self, mock_metal_env) -> None:
        """The sa pipeline dispatches _SA_THREADGROUP_WIDTH threads per replica (QUI-852)."""
        from xqsa.metal_gpu import _SA_THREADGROUP_WIDTH, SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(strategy="sa", num_reads=5, num_sweeps=10, seed=1)
        captured: dict = {}
        original_queue = solver._device.newCommandQueue

        def capturing_queue():
            queue = original_queue()
            original_buffer = queue.commandBuffer

            def capturing_buffer():
                cb = original_buffer()
                original_encoder = cb.computeCommandEncoder

                def capturing_encoder():
                    encoder = original_encoder()
                    captured["encoder"] = encoder
                    return encoder

                cb.computeCommandEncoder = capturing_encoder
                return cb

            queue.commandBuffer = capturing_buffer
            return queue

        solver._device.newCommandQueue = capturing_queue
        solver.solve(model)

        assert _SA_THREADGROUP_WIDTH == 256
        # Mock pipeline reports maxTotalThreadsPerThreadgroup == 1024, so the
        # clamp resolves to the full width.
        assert captured["encoder"].threads_per_group == (256, 1, 1)

    def test_gibbs_dispatch_width_mocked(self, mock_metal_env) -> None:
        """The gibbs pipeline keeps one thread per replica (out of QUI-852 scope)."""
        from xqsa.metal_gpu import SolverMetalGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(strategy="gibbs", num_reads=5, num_sweeps=10, seed=1)
        captured: dict = {}
        original_queue = solver._device.newCommandQueue

        def capturing_queue():
            queue = original_queue()
            original_buffer = queue.commandBuffer

            def capturing_buffer():
                cb = original_buffer()
                original_encoder = cb.computeCommandEncoder

                def capturing_encoder():
                    encoder = original_encoder()
                    captured["encoder"] = encoder
                    return encoder

                cb.computeCommandEncoder = capturing_encoder
                return cb

            queue.commandBuffer = capturing_buffer
            return queue

        solver._device.newCommandQueue = capturing_queue
        solver.solve(model)

        assert captured["encoder"].threads_per_group == (1, 1, 1)
```

- [ ] **Step 3: Run — expect the sa test to FAIL** (`ImportError: cannot import name '_SA_THREADGROUP_WIDTH'`), the gibbs test to FAIL (`AttributeError: threads_per_group`) until Step 1's mock change lands (do Step 1 first, then gibbs fails only if the width is wrong):

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "dispatch_width" -v`

- [ ] **Step 4: Rewrite the `sa_metal` MSL kernel body** inside `_SA_KERNEL` (the `_RNG_PRELUDE` and the buffer parameter list are UNCHANGED; add the two thread-position parameters and restructure the body):

```
kernel void sa_metal(
    device const float* h          [[buffer(0)]],
    device const float* J          [[buffer(1)]],
    device const float* beta_sched [[buffer(2)]],
    device int*         samples    [[buffer(3)]],
    device float*       energies   [[buffer(4)]],
    constant int&       n          [[buffer(5)]],
    constant int&       num_sweeps [[buffer(6)]],
    constant uint&      base_seed  [[buffer(7)]],
    constant int&       is_spin    [[buffer(8)]],
    uint3 tgid  [[threadgroup_position_in_grid]],
    uint3 tpitg [[thread_position_in_threadgroup]],
    uint3 tptg  [[threads_per_threadgroup]]
) {
    /* One threadgroup per replica; tptg.x threads cooperate on the O(n)
       local-field reduction. Spin updates stay strictly sequential on
       thread 0 -- same proposal order, acceptance rule, and RNG consumption
       as the single-thread kernel (QUI-852). tptg.x is always a power of
       two <= 256 (host clamps); only float summation order changes. */
    threadgroup float sdata[256];
    int rid = (int)tgid.x;
    int tid = (int)tpitg.x;
    int nthreads = (int)tptg.x;
    device int* x = samples + rid * n;
    uint rng = seed_for_replica(base_seed, rid);

    float energy = 0.0f;
    if (tid == 0) {
        for (int i = 0; i < n; i++) {
            float xi = (float)x[i];
            energy += h[i] * xi;
            for (int j = i + 1; j < n; j++) {
                energy += J[i * n + j] * xi * (float)x[j];
            }
        }
    }
    threadgroup_barrier(mem_flags::mem_device);

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        float beta = beta_sched[sweep];
        for (int i = 0; i < n; i++) {
            float partial = 0.0f;
            for (int j = tid; j < n; j += nthreads) {
                if (j == i) continue;
                partial += J[i * n + j] * (float)x[j];
            }
            sdata[tid] = partial;
            threadgroup_barrier(mem_flags::mem_threadgroup);

            for (int s = nthreads / 2; s > 0; s >>= 1) {
                if (tid < s) sdata[tid] += sdata[tid + s];
                threadgroup_barrier(mem_flags::mem_threadgroup);
            }

            if (tid == 0) {
                float local = h[i] + sdata[0];
                float delta_E = (is_spin != 0)
                    ? (-2.0f * (float)x[i] * local)
                    : (local * (float)(1 - 2 * x[i]));
                float r = rand_unit(rng);
                if (delta_E <= 0.0f || r < exp(-delta_E * beta)) {
                    x[i] = (is_spin != 0) ? -x[i] : (1 - x[i]);
                    energy += delta_E;
                }
            }
            threadgroup_barrier(mem_flags::mem_device);
        }
    }
    if (tid == 0) energies[rid] = energy;
}
```

- [ ] **Step 5: Host-side dispatch.** Under `_SUPPORTED_SCHEDULES = ...` in metal_gpu.py add:

```python
# Threadgroup width for the sa pipeline's cooperative local-field reduction
# (QUI-852). Clamped at dispatch to the largest power of two that the
# compiled pipeline supports; must match the kernel's `threadgroup float
# sdata[256]`. The gibbs pipeline stays at one thread per replica.
_SA_THREADGROUP_WIDTH = 256
```

Change `_dispatch`:

```python
    def _dispatch(self, pipeline, encoder, num_reads: int, threads_per_group: int = 1) -> None:
        """Encode and dispatch one threadgroup per replica."""
        encoder.setComputePipelineState_(pipeline)
        grid = self._metal.MTLSizeMake(num_reads, 1, 1)
        per_group = self._metal.MTLSizeMake(threads_per_group, 1, 1)
        encoder.dispatchThreadgroups_threadsPerThreadgroup_(grid, per_group)
        encoder.endEncoding()
```

At the `sa` dispatch call site in `_run_sa`, compute the clamped width and pass it (the gibbs call site is left untouched, defaulting to 1):

```python
        max_threads = int(pipeline.maxTotalThreadsPerThreadgroup())
        width = min(_SA_THREADGROUP_WIDTH, 1 << (max_threads.bit_length() - 1))
        self._dispatch(pipeline, encoder, num_reads, threads_per_group=width)
```

(Locate the existing `self._dispatch(pipeline, encoder, num_reads)` inside the sa path; `pipeline` is the variable already in scope there — keep its existing name if it differs.)

- [ ] **Step 6: Update the buffer-ABI comment block** (metal_gpu.py ~lines 63-73): change the line `# Each threadgroup (one thread) owns one replica via threadgroup_position_in_grid.` to:

```python
# Each threadgroup owns one replica via threadgroup_position_in_grid.
# sa_metal runs _SA_THREADGROUP_WIDTH cooperating threads per replica
# (local-field reduction, QUI-852); gibbs_metal remains single-thread.
```

- [ ] **Step 7: Run the mocked Metal suite** — all pass (the CPU reference in the mock is a semantic simulation keyed on the buffer ABI, which is unchanged):

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "MetalGPUMocked" -v`
Expected: all pass, including the two new dispatch-width tests.

- [ ] **Step 8: Full-file mocked regression + lint + commit:**

```bash
uv run --no-sync pytest xqsa/tests/test_xqsa.py -q
uv run --no-sync ruff format --check xqsa/metal_gpu.py xqsa/tests/test_xqsa.py && uv run --no-sync ruff check xqsa/metal_gpu.py xqsa/tests/test_xqsa.py
git add xqsa/metal_gpu.py xqsa/tests/test_xqsa.py
git commit -s -m "perf(xqsa): parallelize Metal SA local-field reduction across threadgroup"
```

Expected pytest: same pass/skip counts as before this task plus 2 (real-GPU/Metal classes skipped on this machine).

---

### Task 5: Findings doc + docstring updates

**Files:**
- Create: `docs/design/specs/2026-07-16-qui-852-findings.md`
- Modify: `xqsa/cuda_gpu.py` (class docstring, ~line 170), `xqsa/metal_gpu.py` (class docstring, ~line 228)

**Interfaces:**
- Consumes: the Task 1 baseline numbers and Task 3 compare outputs (in `.superpowers/sdd/task-1-report.md` and `.superpowers/sdd/task-3-report.md` — read both).

- [ ] **Step 1: Write the findings doc** with this structure (fill the bracketed numbers from the task reports — every number must come from an actual run, never invent one):

```markdown
# QUI-852 Findings: GPU SA Kernel Occupancy

## 1. Change

One block/threadgroup per replica now runs 256 cooperating threads (CUDA
`_THREADS_PER_REPLICA`, Metal `_SA_THREADGROUP_WIDTH`, clamped to the
pipeline maximum) that parallelize the O(n) local-field reduction; spin
updates remain strictly sequential on thread 0. Proposal order, acceptance
rule, and RNG consumption are unchanged; only float summation order in the
local field differs. The Metal gibbs pipeline is out of scope and keeps one
thread per replica.

## 2. Correctness evidence (CUDA, RTX 4090)

- Trajectory gate: [N] integer-coefficient records (2 domains x {64,256} x
  30 seeds, num_reads=1) — bit-identical to the shipped kernel ([paste the
  compare OK line]). Integer sums are exact in any order, so this gate
  proves the sequential semantics survived the parallelization.
- Distribution parity (gaussian instances): [paste the per-size mean/std
  lines]. All dmean within [X]%.

## 3. Performance (CUDA, RTX 4090)

| case | shipped | parallel reduction | speedup |
|---|---|---|---|
| maxcut n=512 (200 reads, 2000 sweeps) | [B] s | [C] s | [D]x |
| gaussian n=4096 (200 reads, 2000 sweeps) | [E] s | [F] s | [G]x |

Baseline context: QUI-704 measured ~618-670 s per n=4096 solve; QUI-167
found CUDA ~5x slower than SolverDWaveCPU at MaxCut 512.

## 4. Metal status

MSL kernel mirrors the CUDA design (threadgroup reduction, barriers,
thread-0 sequential updates). Verified structurally via the mocked suite
(dispatch width, unchanged buffer ABI). NOT yet validated on Apple
hardware — needs an M-series run of `pytest -m metal` (post-merge CI job
`test:metal`, or a reviewer machine) before the perf claim extends to Metal.

## 5. Follow-ups

- Gibbs pipeline occupancy (parallelize within color classes) — separate
  ticket if needed.
- QUI-705 (on-device CUDA RNG) should rebase on this change; its
  distributional verification methodology is unaffected.

Methodology/harness: `scripts/bench_occupancy.py` (committed in this MR).
```

- [ ] **Step 2: Docstring touch-ups.** In `xqsa/cuda_gpu.py`, the class docstring sentence "Each replica (controlled by ``num_reads``) executes independently in its own CUDA thread block." — append: "Within a block, ``_THREADS_PER_REPLICA`` threads cooperate on the local-field reduction while spin updates remain sequential (QUI-852)." In `xqsa/metal_gpu.py`, find the equivalent per-replica sentence in the class docstring and append the analogous sentence naming `_SA_THREADGROUP_WIDTH` and noting gibbs stays single-threaded.

- [ ] **Step 3: Lint + commit:**

```bash
uv run --no-sync ruff format --check xqsa/cuda_gpu.py xqsa/metal_gpu.py && uv run --no-sync ruff check xqsa/cuda_gpu.py xqsa/metal_gpu.py
git add docs/design/specs/2026-07-16-qui-852-findings.md xqsa/cuda_gpu.py xqsa/metal_gpu.py
git commit -s -m "docs(xqsa): QUI-852 occupancy findings and solver docstrings"
```

---

### Task 6: Preflight and merge request (controller-executed)

- [ ] **Step 1:** `make preflight-py` — all green.
- [ ] **Step 2:** Push `feature/qui-852`; `glab mr create` with **target branch `feature/qui-854`** (stacked on MR !91 — GitLab retargets to main when !91 merges). Description covers: the reduction design, the trajectory gate result, parity stats, the timing table, the explicit Metal deferred-validation caveat, gibbs out-of-scope note, "Implements QUI-852", and the stacked-on-!91 note.
- [ ] **Step 3:** Verify pipeline starts; move QUI-852 to In Review on Linear with the MR link.

---

## Self-review (done at plan time)

- Ticket coverage: parallel reduction both solvers (Tasks 2, 4); sequential spins + RNG order preserved (kernel design + trajectory gate, Task 3); near-identical trajectory check → strengthened to bit-identical on integer instances (Task 3 Step 2); energy-distribution parity across seeds×instances (Tasks 1/3); best-energy + CUDA-vs-CPU gap context (timing cases n=512/4096, findings §3); gibbs/checkerboard out of scope (findings §5). ✓
- No placeholders: all kernels, harness, tests, and host changes shown in full; findings doc numbers are explicitly sourced from run reports. ✓
- Type consistency: `_THREADS_PER_REPLICA`/`_SA_THREADGROUP_WIDTH` = 256 everywhere; harness subcommand names match between Tasks 1 and 3; capture-helper keys (`grid`/`block`/`args`) match between helper and test. ✓
