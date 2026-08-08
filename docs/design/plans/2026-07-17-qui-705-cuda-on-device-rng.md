# QUI-705: CUDA On-Device RNG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `SolverCudaGPU`'s pre-generated `(num_reads, num_sweeps, n)` float64 acceptance-randoms buffer with an on-device per-replica xorshift64 RNG (splitmix64-seeded), removing the buffer allocation and its 80%-of-VRAM OOM guard, matching `SolverMetalGPU`'s strategy.

**Architecture:** A CUDA prelude (`splitmix64` + `seed_for_replica` + `xorshift64` + 53-bit `rand_unit`) is prepended to both SA kernels, mirroring Metal's `_RNG_PRELUDE` structure but with 64-bit state so acceptance stays float64 (per the MR !88 review decision — Metal's float32 is an MSL platform limit, not a precision choice). Kernels drop the `randoms` pointer and take a `base_seed` scalar; the host drops the buffer allocation and OOM guard. Since the sampling stream changes, validation is distributional (energy-distribution parity + best-energy regression), never exact-trajectory.

**Tech Stack:** Python 3 + CuPy RawKernel (CUDA C), numpy-mocked CuPy for CI tests, pytest.

## Global Constraints

- **Base branch: `origin/feature/qui-852`** (NOT main). QUI-705 touches the same kernels as QUI-852 (MR !92, in review, stacked on !91→!90). Branch name: `feature/qui-705`. MR target: `feature/qui-852`. GitLab will retarget as the stack merges.
- **Worktree:** create via superpowers:using-git-worktrees at `.claude/worktrees/qui-705`. Subagents pin to their launch dir — every Bash command in a subagent must start with `cd /home/konrad/Quip/xquad/.claude/worktrees/qui-705 &&` (path may differ if the skill picks another location; use the actual one).
- **Worktree env setup (known gotchas from QUI-852):** run `make deps-py` first (writes `xq-rs-workspace.pth`; bare `uv sync` does not). Then `uv sync --extra cuda && make deps-py` (re-sync installs cupy but clobbers the workspace install; the second `deps-py` repairs it). After that, ALWAYS `uv run --no-sync ...` — a plain `uv run` re-syncs and removes cupy.
- **GPU:** local RTX 4090 (WSL2). Verify with `uv run --no-sync python -c "import cupy; print(cupy.cuda.runtime.getDeviceCount())"` → `1`.
- **Commit after every file-modification step** (user's standing preference), imperative mood, ≤72-char subject, no co-author bylines.
- New/edited Python files: AGPL header block (copy verbatim from `scripts/bench_spin_packing.py` lines 1–16), 100-char lines, ≤100-line functions, Google-style docstrings on public APIs. `make fmt-py lint-py` must be clean before each commit.
- Decisions already settled in the Linear ticket (do not re-litigate): float64 acceptance on-device; splitmix64 per-replica seeding over `(base_seed, rid)`; distributional success criterion.
- Decisions taken in this plan (flag in MR description): xorshift64 as the stream generator (simplest 64-bit analogue of Metal's xorshift32; statistical quality is ample for SA acceptance); `seed=None` draws a random `base_seed` from `np.random.default_rng(None)`; parity thresholds KS α=0.01, mean-shift ≤2%, best-energy ≤2% (2% mirrors `GLASS_TOLERANCE` in `xqsa/tests/test_gpu_validation.py:68`).

---

### Task 1: Distributional-parity harness

**Files:**
- Create: `scripts/validate_rng_parity.py`

**Interfaces:**
- Produces: CLI `run --out <json> [--smoke]` and `compare <baseline.json> <candidate.json>` (exit 0 = parity holds, 1 = failed). Task 2 runs `run` on the unmodified solver; Task 6 runs `run` + `compare` on the candidate.
- Consumes: `build_instance(n, seed)` from `scripts/bench_spin_packing.py` (returns `(XQMX, h, J)`; only the model is used).

- [ ] **Step 1: Write the harness**

Create `scripts/validate_rng_parity.py` with the AGPL header (copy lines 1–16 of `scripts/bench_spin_packing.py`), then:

```python
"""QUI-705 RNG-swap validation: energy-distribution parity for SolverCudaGPU.

Swapping the acceptance RNG (pre-generated float64 buffer -> on-device
xorshift64) changes the sampling stream, so trajectories cannot match
bit-for-bit. This harness checks the swap distributionally instead:

  run      solve seeded spin-glass instances (the bench_spin_packing
           generator) across many solver seeds; dump per-cell energies
           to JSON.
  compare  baseline vs candidate per cell: two-sample KS test
           (alpha=0.01), mean shift <= 2%, best-energy regression <= 2%.

Usage (run once on the pre-change commit, once on the candidate):
    uv run --no-sync python scripts/validate_rng_parity.py run \
        --out scratch/qui-705/baseline.json
    uv run --no-sync python scripts/validate_rng_parity.py run \
        --out scratch/qui-705/candidate.json
    uv run --no-sync python scripts/validate_rng_parity.py compare \
        scratch/qui-705/baseline.json scratch/qui-705/candidate.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_spin_packing import build_instance  # noqa: E402

from xqsa.cuda_gpu import SolverCudaGPU  # noqa: E402

SIZES = (256, 1024)
INSTANCE_SEEDS = (7, 11, 13)
SOLVER_SEEDS = tuple(range(100, 124))  # 24 independent streams per cell
NUM_READS = 64
NUM_SWEEPS = 500
BETA_RANGE = (0.1, 10.0)
KS_ALPHA = 0.01
MEAN_TOLERANCE = 0.02   # mirrors GLASS_TOLERANCE in test_gpu_validation.py
BEST_TOLERANCE = 0.02

SMOKE_SIZES = (64,)
SMOKE_INSTANCE_SEEDS = (7,)
SMOKE_SOLVER_SEEDS = tuple(range(100, 106))


def run(out_path: Path, smoke: bool) -> int:
    """Solve every (size, instance) cell across the solver seeds; dump JSON."""
    sizes = SMOKE_SIZES if smoke else SIZES
    instance_seeds = SMOKE_INSTANCE_SEEDS if smoke else INSTANCE_SEEDS
    solver_seeds = SMOKE_SOLVER_SEEDS if smoke else SOLVER_SEEDS

    cells: dict[str, list[float]] = {}
    for n in sizes:
        for iseed in instance_seeds:
            model, _h, _j = build_instance(n, iseed)
            energies = [
                float(
                    SolverCudaGPU(
                        num_reads=NUM_READS,
                        num_sweeps=NUM_SWEEPS,
                        beta_range=BETA_RANGE,
                        seed=sseed,
                    )
                    .solve(model)
                    .energy
                )
                for sseed in solver_seeds
            ]
            cells[f"n{n}_i{iseed}"] = energies
            print(f"n={n} instance={iseed}: best={min(energies):.0f} mean={np.mean(energies):.2f}")

    payload = {
        "params": {
            "sizes": list(sizes),
            "instance_seeds": list(instance_seeds),
            "solver_seeds": list(solver_seeds),
            "num_reads": NUM_READS,
            "num_sweeps": NUM_SWEEPS,
            "beta_range": list(BETA_RANGE),
        },
        "cells": cells,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out_path}")
    return 0


def ks_statistic(a: np.ndarray, b: np.ndarray) -> float:
    """Two-sample Kolmogorov-Smirnov D statistic."""
    a, b = np.sort(a), np.sort(b)
    grid = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, grid, side="right") / a.size
    cdf_b = np.searchsorted(b, grid, side="right") / b.size
    return float(np.max(np.abs(cdf_a - cdf_b)))


def ks_critical(m: int, n: int, alpha: float) -> float:
    """Asymptotic two-sample KS rejection threshold at the given alpha."""
    return math.sqrt(-math.log(alpha / 2.0) / 2.0) * math.sqrt((m + n) / (m * n))


def compare_cell(name: str, base: list[float], cand: list[float]) -> list[str]:
    """Return failure messages for one cell (empty list = cell passes)."""
    failures: list[str] = []
    d = ks_statistic(np.asarray(base), np.asarray(cand))
    d_crit = ks_critical(len(base), len(cand), KS_ALPHA)
    if d > d_crit:
        failures.append(f"{name}: KS D={d:.3f} > {d_crit:.3f} (alpha={KS_ALPHA})")
    base_mean = float(np.mean(base))
    shift = abs(float(np.mean(cand)) - base_mean)
    if shift > MEAN_TOLERANCE * abs(base_mean):
        failures.append(f"{name}: mean shift {shift:.2f} > {MEAN_TOLERANCE:.0%} of |{base_mean:.2f}|")
    if min(cand) > min(base) + BEST_TOLERANCE * abs(min(base)):
        failures.append(f"{name}: best energy regressed {min(base):.0f} -> {min(cand):.0f}")
    return failures


def compare(base_path: Path, cand_path: Path) -> int:
    """Compare two run outputs; print a verdict and return a process code."""
    base = json.loads(base_path.read_text())
    cand = json.loads(cand_path.read_text())
    if base["params"] != cand["params"]:
        print(f"FAIL: parameter mismatch\n  base: {base['params']}\n  cand: {cand['params']}")
        return 1
    failures: list[str] = []
    for name in sorted(base["cells"]):
        failures.extend(compare_cell(name, base["cells"][name], cand["cells"][name]))
    if failures:
        print("FAIL: distributional parity violated")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"PASS: {len(base['cells'])} cells within KS/mean/best-energy thresholds")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QUI-705 RNG distributional parity check")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="solve the cell grid and dump energies to JSON")
    p_run.add_argument("--out", type=Path, required=True)
    p_run.add_argument("--smoke", action="store_true", help="tiny grid for a quick sanity pass")
    p_cmp = sub.add_parser("compare", help="compare a baseline run against a candidate run")
    p_cmp.add_argument("baseline", type=Path)
    p_cmp.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args.out, args.smoke)
    return compare(args.baseline, args.candidate)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Sanity-check the harness on the unmodified solver (needs the GPU)**

Run: `uv run --no-sync python scripts/validate_rng_parity.py run --smoke --out scratch/qui-705/smoke_a.json`
Expected: one `n=64 instance=7: best=... mean=...` line, `wrote scratch/qui-705/smoke_a.json`, exit 0.

Run it a second time to `scratch/qui-705/smoke_b.json`, then:
`uv run --no-sync python scripts/validate_rng_parity.py compare scratch/qui-705/smoke_a.json scratch/qui-705/smoke_b.json`
Expected: `PASS: 1 cells within KS/mean/best-energy thresholds`, exit 0. (Identical seeds on the same code give identical energies — D=0.)

- [ ] **Step 3: Lint and commit**

Run: `make fmt-py lint-py`
Expected: no diffs, no warnings.

```bash
git add scripts/validate_rng_parity.py
git commit -m "test(xqsa): add RNG distributional-parity harness for QUI-705"
```

---

### Task 2: Capture the pre-change baseline (GPU, before ANY solver edit)

The baseline must come from the unmodified solver. Do not start Task 3/4 until this task's JSON files exist.

**Files:**
- Create (not committed; `scratch/` is untracked): `scratch/qui-705/baseline.json`, `scratch/qui-705/sweep_baseline.jsonl`

- [ ] **Step 1: Full parity baseline**

Run: `uv run --no-sync python scripts/validate_rng_parity.py run --out scratch/qui-705/baseline.json`
Expected: 6 cell lines (2 sizes × 3 instances, 24 solves each) and `wrote scratch/qui-705/baseline.json`. Order minutes on the RTX 4090 with the QUI-852 kernels.

- [ ] **Step 2: QUI-167 sweep baseline (best-energy regression reference)**

```bash
mkdir -p scratch/qui-705
for n in 128 512 1024; do for s in 1 2 3; do
  uv run --no-sync python scripts/scale_sweep.py --point --problem maxcut \
    --backend rust --solver cuda-gpu --n $n --seed $s >> scratch/qui-705/sweep_baseline.jsonl
done; done
for n in 16 32; do for s in 1 2 3; do
  uv run --no-sync python scripts/scale_sweep.py --point --problem tsp \
    --backend rust --solver cuda-gpu --n $n --seed $s >> scratch/qui-705/sweep_baseline.jsonl
done; done
```

Expected: 15 JSON lines, each with `"status": "ok"` and an `energy` field. Verify: `wc -l scratch/qui-705/sweep_baseline.jsonl` → `15`; `rg -c '"status": "ok"' scratch/qui-705/sweep_baseline.jsonl` → `15`.

---

### Task 3: Update the mocked-CuPy tests to the new kernel contract (TDD red)

**Files:**
- Modify: `xqsa/tests/test_xqsa.py` (fixture `mock_cupy_env` ~line 458; `TestSolverCudaGPUMocked` ~line 562)

**Interfaces:**
- Consumes: nothing from other tasks (this is the failing-test step).
- Produces: the kernel-call contract Task 4 implements — args tuple `(h, J, samples, energies, betas, n, num_sweeps, base_seed)` with `base_seed` an `np.uint64`; Python RNG mirrors `_splitmix64`, `_seed_for_replica`, `_xorshift64`, `_rand_unit` whose constants Task 4's CUDA prelude must match bit-for-bit.

- [ ] **Step 1: Add Python mirrors of the device RNG above the `mock_cupy_env` fixture**

```python
# Python mirrors of the CUDA device RNG in xqsa.cuda_gpu._RNG_PRELUDE
# (QUI-705). Constants and update order must match the kernel bit-for-bit;
# the fake kernel below consumes these so the mocked solve reproduces the
# device sampling stream exactly.
_MASK64 = (1 << 64) - 1


def _splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & _MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _MASK64
    return x ^ (x >> 31)


def _seed_for_replica(base_seed: int, rid: int) -> int:
    s = _splitmix64(base_seed ^ ((0x9E3779B97F4A7C15 * (rid + 1)) & _MASK64))
    return s if s != 0 else 1


def _xorshift64(state: int) -> int:
    x = state
    x ^= (x << 13) & _MASK64
    x ^= x >> 7
    x ^= (x << 17) & _MASK64
    return x


def _rand_unit(state: int) -> tuple[float, int]:
    state = _xorshift64(state)
    return (state >> 11) / 9007199254740992.0, state
```

- [ ] **Step 2: Rewrite the fake kernel to the new signature and stream**

In `_make_sa_kernel`'s inner `_kernel`, replace the unpack line

```python
h, J, samples, energies, randoms, betas, n_val, ns_val = args
```

with

```python
h, J, samples, energies, betas, n_val, ns_val, base_seed = args
```

Inside the `for rid in range(num_reads):` loop, immediately after `x = samples[rid]`, add:

```python
rng_state = _seed_for_replica(int(base_seed), rid)
```

and replace the acceptance draw

```python
r = randoms[rid, sweep, i]
```

with

```python
r, rng_state = _rand_unit(rng_state)
```

- [ ] **Step 3: Delete `test_oom_guard`**

Remove the whole `test_oom_guard` method (currently `xqsa/tests/test_xqsa.py:660-680` on the base branch) — the guard it exercises is deleted in Task 4. Also remove the now-unused `_TinyDevice`-free leftovers if any (the class is local to the test; deleting the method removes it all).

- [ ] **Step 4: Re-index the captured-args tests from `args[5]` to `args[4]`**

With `randoms` gone, the betas buffer moves from index 5 to index 4. In these four tests, change `captured["args"][5]` → `captured["args"][4]`:
- `test_num_sweeps_per_beta_schedule_buffer_mocked`
- `test_default_schedule_bit_identical_mocked`
- `test_single_beta_level_uses_beta_start_mocked`
- `test_geometric_schedule_shape_mocked`

- [ ] **Step 5: Add seed-semantics tests to `TestSolverCudaGPUMocked`**

```python
    def test_same_seed_reproducible_mocked(self, mock_cupy_env) -> None:
        """Identical seeds give identical best energy and sample (QUI-705)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.spin_model(6)
        model.set_quadratic(0, 1, 1.0)
        model.set_quadratic(1, 2, -1.0)
        model.set_quadratic(2, 3, 1.0)
        model.set_quadratic(3, 4, -1.0)
        model.set_quadratic(4, 5, 1.0)
        model.set_quadratic(0, 5, 1.0)

        r1 = _Solver(num_reads=4, num_sweeps=30, seed=7).solve(model)
        r2 = _Solver(num_reads=4, num_sweeps=30, seed=7).solve(model)

        assert r1.energy == r2.energy
        assert [r1.sample.get_linear(i) for i in range(6)] == [
            r2.sample.get_linear(i) for i in range(6)
        ]

    def test_seed_none_solves_mocked(self, mock_cupy_env) -> None:
        """seed=None draws a random base seed and still solves (QUI-705)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        result = _Solver(num_reads=10, num_sweeps=100, seed=None).solve(model)

        assert result.energy == 0
        assert result.metadata["seed"] is None
```

- [ ] **Step 6: Run the CUDA-mocked tests to verify they fail against the old implementation**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "Cuda" -x -q`
Expected: FAIL. The old `_run_sa` still passes 8 old-layout args, so the fake kernel binds the 3-D `randoms` array to `betas` and `float(betas[sweep])` raises `TypeError` (or similar unpack/shape error). The point is red-before-green, not the specific exception.

- [ ] **Step 7: Commit the red tests**

```bash
git add xqsa/tests/test_xqsa.py
git commit -m "test(xqsa): retarget mocked CUDA tests at on-device RNG contract"
```

(Repo hooks permitting a red suite; if a pre-commit hook runs pytest and blocks, commit Tasks 3+4 together at the end of Task 4 instead and note it in the MR.)

---

### Task 4: Implement the on-device RNG in `SolverCudaGPU` (TDD green)

**Files:**
- Modify: `xqsa/cuda_gpu.py` (kernel sources ~lines 57–192; `_run_sa` ~lines 415–496; class docstring ~line 196)

**Interfaces:**
- Consumes: the args-tuple contract and RNG constants from Task 3.
- Produces: `SolverCudaGPU` with unchanged Python API (`seed=` still reproducible, `seed=None` still random); no `randoms` buffer, no OOM guard.

- [ ] **Step 1: Add the RNG prelude and rewrite both kernel headers**

In `xqsa/cuda_gpu.py`, above `_SA_BINARY_KERNEL`, add:

```python
# Device RNG (QUI-705): mirrors SolverMetalGPU's on-device strategy
# (xqsa/metal_gpu.py _RNG_PRELUDE) with 64-bit state so acceptance draws
# stay float64 (53-bit uniform). Metal's float32 is an MSL platform limit,
# not a precision decision. Python mirrors live in tests/test_xqsa.py;
# keep constants in sync bit-for-bit.
_RNG_PRELUDE = r"""
typedef unsigned long long u64;

__device__ __forceinline__ u64 splitmix64(u64 x) {
    x += 0x9E3779B97F4A7C15ULL;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}

/* Per-replica stream seed: Metal's seed_for_replica shape (base ^ odd
   constant * (rid+1)) widened to 64 bits, finalized through splitmix64
   for stream decorrelation. xorshift64 has a zero fixed point, so never
   return 0. */
__device__ __forceinline__ u64 seed_for_replica(u64 base_seed, int rid) {
    u64 s = splitmix64(base_seed ^ (0x9E3779B97F4A7C15ULL * (u64)(rid + 1)));
    return (s == 0ULL) ? 1ULL : s;
}

__device__ __forceinline__ u64 xorshift64(u64 &state) {
    u64 x = state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    state = x;
    return x;
}

/* Uniform double in [0, 1): top 53 bits / 2^53. */
__device__ __forceinline__ double rand_unit(u64 &state) {
    return (double)(xorshift64(state) >> 11) * (1.0 / 9007199254740992.0);
}
"""
```

Change both kernel definitions to prepend it:

```python
_SA_BINARY_KERNEL = _RNG_PRELUDE + r"""
extern "C" __global__
void sa_binary(
...
```

(same for `_SA_SPIN_KERNEL`).

In **both** kernel signatures, delete the line

```c
    const double* __restrict__ randoms,
```

and add after `int    num_sweeps`:

```c
    unsigned long long base_seed
```

(mind the comma on the previous line).

- [ ] **Step 2: Swap the in-kernel draw (both kernels)**

In `sa_binary`, replace

```c
    const double* rand_ptr = randoms + (long long)rid * num_sweeps * n;
```

with

```c
    u64 rng = seed_for_replica(base_seed, rid);
```

and replace

```c
                double r = rand_ptr[(long long)sweep * n + i];
```

with

```c
                double r = rand_unit(rng);
```

Same two edits in `sa_spin` (its pointer line reads `randoms + (long long)rid * num_sweeps * n` with `const double* rand_ptr =` as well). Note the state variable is declared per-thread but only thread 0 ever draws from it (the acceptance block is `if (tid == 0)`), so the stream is strictly sequential per replica — same property the `randoms` indexing had. Update the comment block at the top of `sa_binary` that mentions "same RNG consumption order": the guarantee is now "thread 0 owns the per-replica RNG stream".

- [ ] **Step 3: Host-side `_run_sa` changes**

Delete the OOM guard and buffer allocation (the whole block):

```python
        # Pre-generate all random acceptance thresholds.
        # Memory: num_reads * num_sweeps * n * 8 bytes (float64).
        random_bytes = num_reads * num_sweeps * n * 8
        gpu_free = cupy.cuda.Device().mem_info[0]
        if random_bytes > gpu_free * 0.8:
            raise ValueError(
                f"Random buffer requires {random_bytes / 1e9:.1f} GB "
                f"GPU memory ({gpu_free / 1e9:.1f} GB free). "
                f"Reduce num_reads or num_sweeps."
            )
        randoms = rng.random(size=(num_reads, num_sweeps, n), dtype=cupy.float64)
```

Replace it with:

```python
        # Acceptance randomness is generated on-device per replica
        # (QUI-705); only a 64-bit base seed crosses the host boundary.
        # Derived from an independent numpy stream so the cupy `rng` used
        # for replica init keeps its historical consumption order.
        base_seed = np.uint64(np.random.default_rng(seed).integers(0, 2**64, dtype=np.uint64))
```

Update the kernel launch args from

```python
            (
                h,
                j_matrix,
                samples,
                energies,
                randoms,
                beta_schedule,
                np.int32(n),
                np.int32(num_sweeps),
            ),
```

to

```python
            (
                h,
                j_matrix,
                samples,
                energies,
                beta_schedule,
                np.int32(n),
                np.int32(num_sweeps),
                base_seed,
            ),
```

- [ ] **Step 4: Docstring/comment sweep in `cuda_gpu.py`**

- Class docstring: add one sentence after the QUI-852 paragraph: "Acceptance randomness is generated on-device per replica (xorshift64 seeded via splitmix64 from a 64-bit base seed), so no `(num_reads, num_sweeps, n)` buffer is allocated (QUI-705)."
- `solve()` Raises section: the OOM `ValueError` is gone; the remaining `ValueError` causes (domain, parameter validation) still hold — no text change needed unless it mentions memory. Check with `rg -n "GPU memory|Reduce num_reads" xqsa/cuda_gpu.py` → no hits after the edit.

- [ ] **Step 5: Run the mocked suite to green**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -q`
Expected: PASS, zero failures, zero warnings. If a mocked-solve energy assertion fails (e.g. `test_solve_trivial_binary_mocked` no longer reaches ground state), the mirrors are out of sync with the prelude — diff the constants/shift orders between `_RNG_PRELUDE` and the test mirrors before touching anything else; trivial 2-var models converge under any healthy uniform stream.

- [ ] **Step 6: Lint, then commit**

Run: `make fmt-py lint-py`
Expected: clean.

```bash
git add xqsa/cuda_gpu.py
git commit -m "refactor(xqsa): move CUDA SA acceptance RNG on-device (QUI-705)"
```

---

### Task 5: Real-GPU test validation

**Files:** none modified (verification only).

- [ ] **Step 1: Real-kernel compile + medium-model validation on the RTX 4090**

Run: `CI=1 uv run --no-sync pytest xqsa/tests/test_gpu_validation.py -m cuda -q`
(`CI=1` forces hard-fail instead of silent skip if the env is broken.)
Expected: `TestCudaGPUMediumModels` — 4 passed. These are distributional assertions (exact known optima + 2% glass tolerance), so they must pass unchanged; a failure here is a real quality regression, not an expected stream change.

- [ ] **Step 2: Confirm the buffer is actually gone**

Run:
```bash
uv run --no-sync python - <<'EOF'
import cupy
from scripts_path_shim import *  # noqa: F401,F403  (not needed; see below)
EOF
```
Use this instead (single command):
```bash
uv run --no-sync python -c "
import sys; from pathlib import Path
sys.path.insert(0, 'scripts')
import cupy
from bench_spin_packing import build_instance
from xqsa.cuda_gpu import SolverCudaGPU
model, _, _ = build_instance(1024, 7)
cupy.get_default_memory_pool().free_all_blocks()
SolverCudaGPU(num_reads=64, num_sweeps=500, beta_range=(0.1, 10.0), seed=42).solve(model)
print('mempool peak MiB:', cupy.get_default_memory_pool().total_bytes() / 2**20)
"
```
Expected: peak well under 100 MiB. The retired randoms buffer alone was 64·500·1024·8 B = 250 MiB at these parameters (and QUI-704 measured it at 88.6–98.3% of the footprint), so a sub-100 MiB peak proves the allocation is gone. Record the number for the MR description.

---

### Task 6: Distributional parity + QUI-167 regression (the ticket's success criterion)

**Files:**
- Create: `scratch/qui-705/candidate.json`, `scratch/qui-705/sweep_candidate.jsonl` (not committed)
- Create: `docs/design/specs/2026-07-17-qui-705-findings.md`

- [ ] **Step 1: Candidate parity run**

Run: `uv run --no-sync python scripts/validate_rng_parity.py run --out scratch/qui-705/candidate.json`
Expected: 6 cells, exit 0.

- [ ] **Step 2: Compare against the Task 2 baseline**

Run: `uv run --no-sync python scripts/validate_rng_parity.py compare scratch/qui-705/baseline.json scratch/qui-705/candidate.json`
Expected: `PASS: 6 cells within KS/mean/best-energy thresholds`, exit 0.
If a cell fails: this is the ticket's gate — do NOT loosen thresholds to pass. Diagnose with superpowers:systematic-debugging (suspects, in order: mirror/prelude constant drift, per-replica seed collisions across `rid`, biased `rand_unit`).

- [ ] **Step 3: QUI-167 sweep candidate points + delta check**

```bash
for n in 128 512 1024; do for s in 1 2 3; do
  uv run --no-sync python scripts/scale_sweep.py --point --problem maxcut \
    --backend rust --solver cuda-gpu --n $n --seed $s >> scratch/qui-705/sweep_candidate.jsonl
done; done
for n in 16 32; do for s in 1 2 3; do
  uv run --no-sync python scripts/scale_sweep.py --point --problem tsp \
    --backend rust --solver cuda-gpu --n $n --seed $s >> scratch/qui-705/sweep_candidate.jsonl
done; done
uv run --no-sync python -c "
import json
def best(path):
    rows = [json.loads(l) for l in open(path)]
    return {(r['problem'], r['n'], r['seed']): r['energy'] for r in rows}
b, c = best('scratch/qui-705/sweep_baseline.jsonl'), best('scratch/qui-705/sweep_candidate.jsonl')
bad = []
for k in sorted(b):
    tol = 0.02 * abs(b[k])
    flag = '  REGRESSION' if c[k] > b[k] + tol else ''
    print(k, 'base', b[k], 'cand', c[k], flag)
    if flag: bad.append(k)
raise SystemExit(1 if bad else 0)
"
```
Expected: 15 comparison lines, no `REGRESSION` flags, exit 0. (If a `--point` record lacks an `energy` key, inspect one line of the baseline file and adjust the key in this snippet — the baseline from Task 2 defines the schema; both files share it.)

- [ ] **Step 4: Write the findings doc**

Create `docs/design/specs/2026-07-17-qui-705-findings.md` recording: the mempool peak before/after (Task 5 step 2 number vs QUI-704's 250 MiB-at-n=1024 / 1 GiB-at-n=4096 figures), the 6-cell parity table (baseline vs candidate mean/best per cell, KS D values), the 15-point sweep delta table, and the seed-semantics statement ("`seed=` reproducible, streams decorrelated per replica; sampling stream intentionally differs from the pre-QUI-705 solver"). Follow the structure of `docs/design/specs/2026-07-16-qui-852-findings.md`.

- [ ] **Step 5: Commit**

```bash
git add docs/design/specs/2026-07-17-qui-705-findings.md
git commit -m "docs(xqsa): record QUI-705 RNG parity validation findings"
```

---

### Task 7: Changelog, MR, Linear

**Files:**
- Modify: `CHANGELOG.md` (follow the existing unreleased-section format — check `git log -p -1 --follow CHANGELOG.md` for the house style)

- [ ] **Step 1: Changelog entry**

Add under the unreleased/v0.3.1 section, matching surrounding entry style:

```markdown
- `SolverCudaGPU` now generates acceptance randomness on-device (per-replica
  xorshift64, splitmix64-seeded), removing the `(num_reads, num_sweeps, n)`
  float64 buffer and its GPU-memory guard. Seeded solves remain reproducible;
  the sampling stream differs from previous releases (QUI-705).
```

- [ ] **Step 2: Full preflight + commit**

Run: `make preflight-py`
Expected: clean.

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for QUI-705 on-device CUDA RNG"
```

- [ ] **Step 3: Push and open the MR (stacked)**

```bash
git push -u origin feature/qui-705
glab mr create --source-branch feature/qui-705 --target-branch feature/qui-852 \
  --title "refactor(xqsa): move CUDA SA acceptance RNG on-device (QUI-705)" \
  --description "<body>"
```

MR body content (plain factual language, no superlatives): what changed (kernel prelude, signature, host allocation removal), the validation evidence (parity PASS output, sweep delta table, mempool peak numbers), the intentional behavior change (sampling stream differs; `seed=` reproducibility preserved), and the stacking note (targets `feature/qui-852`; retarget to main as !91/!92 merge). Note the plan-level decisions (xorshift64 choice, thresholds) for reviewer sign-off.

- [ ] **Step 4: Linear**

Move QUI-705 to "In Review" and comment with the MR link and the parity/regression verdicts (use `save_issue`/`save_comment` via the Linear MCP tools).

---

## Self-Review Notes

- Ticket requirement → task mapping: on-device per-replica RNG (Task 4), remove OOM guard + allocation (Task 4 step 3, proven in Task 5 step 2), float64 acceptance kept (prelude `rand_unit` returns 53-bit doubles), splitmix64 per-replica seeding (prelude `seed_for_replica`), seed semantics confirmed (Task 3 step 5 tests + Task 6 step 4 statement), CUDA tests updated (Task 3), energy-distribution parity across seeds × instances with the bench generator (Tasks 1, 2, 6), QUI-167 best-energy regression (Tasks 2, 6), sequencing with QUI-852 (Global Constraints: stacked branch).
- Type/order consistency: kernel args order `(h, J, samples, energies, betas, n, num_sweeps, base_seed)` is identical in Task 3 step 2 (mock unpack), Task 4 step 3 (launch), and Task 3 step 4 (betas at index 4). RNG constants appear in exactly two places (prelude, test mirrors) and are byte-identical.
- Known risk: the mocked fake kernel is O(reads·sweeps·n²) pure Python — the new tests use tiny models (n≤6, ≤30 sweeps) to stay fast.
