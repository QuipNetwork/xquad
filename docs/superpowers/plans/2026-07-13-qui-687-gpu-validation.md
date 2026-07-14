# QUI-687 GPU Medium-Model Validation Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `xqsa/tests/test_gpu_validation.py` — hardware-gated medium-model (20–100 var) validation for `SolverCudaGPU` and `SolverMetalGPU` with exact-optimum and CPU cross-check assertions.

**Architecture:** One new test module. Four module-level model builders return `(model, ground_energy)`. An ungated reference class proves `SolverDWaveCPU` hits each known optimum on every CI run. Two gated classes (cuda / metal markers, same skip-guard idiom as `test_xqsa.py`) assert the GPU solvers hit the exact optima on structured models and land within 2% of the CPU reference on a seeded spin glass.

**Tech Stack:** pytest, `xqvm_py.xqmx.XQMX`, `xqsa` solvers. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-13-qui-687-gpu-validation-design.md` (approved 2026-07-13).

## Global Constraints

- Work ONLY in a dedicated worktree on branch `feature/qui-687` (base 15918ed post-v0.3.0 main).
- Run Python via `uv run --no-sync ...` (plain `uv run` reverts the maturin-built xqffi).
- Development machine assumption: a CUDA GPU with cupy installed — cuda-marked tests run for real; no Metal device — metal-marked tests must be observed to skip.
- Guard idiom copied from `xqsa/tests/test_xqsa.py`: `_IN_CI = os.environ.get("CI") is not None`; probe `_has_cupy`/`_has_metal`; import solver under `if _has_X or _IN_CI:`; class decorated `@pytest.mark.cuda|metal` + `@pytest.mark.skipif(not _IN_CI and not _has_X, reason=...)`.
- Assertion policy: structured models exact on both solvers; spin glass `gpu <= cpu + 0.02 * abs(cpu)`.
- GPU calls pin `num_reads=64, num_sweeps=2000, seed=42` (never rely on defaults).
- Style: ≤100 lines/function, ≤5 positional params, 100-char lines (repo ruff allows 120 — the 100 limit still binds; check with `awk 'length > 100'`), ruff clean, full 16-line AGPL header + SPDX.
- Commits: imperative, ≤72-char subject, no co-author bylines/trailers.

## File Structure

- Create `xqsa/tests/test_gpu_validation.py` — builders, `TestMediumModelReferences` (ungated), `TestCudaGPUMediumModels` (cuda), `TestMetalGPUMediumModels` (metal). Nothing else changes.

---

### Task 1: Model builders + ungated CPU reference tests

**Files:**
- Create: `xqsa/tests/test_gpu_validation.py`

**Interfaces:**
- Produces (used verbatim by Tasks 2–3): `ferro_chain(n: int = 64) -> tuple[XQMX, int]`, `frustrated_triangles(n: int = 51) -> tuple[XQMX, int]`, `bipartite_maxcut(n: int = 20) -> tuple[XQMX, int]`, `random_spin_glass(n: int = 100, seed: int = 7) -> tuple[XQMX, None]`; module constants `_IN_CI`, `_has_cupy`, `_has_metal`; `GPU_PARAMS = {"num_reads": 64, "num_sweeps": 2000, "seed": 42}`; `cpu_reference(model) -> int` returning `SolverDWaveCPU(seed=42).solve(model).energy`.

- [ ] **Step 1: Create the file with header, guards, builders left unimplemented, and the reference tests**

Create `xqsa/tests/test_gpu_validation.py` with the full 16-line AGPL header exactly as at the top of `xqsa/tests/test_xqsa.py` (Copyright 2026 Postquant Labs Incorporated … `SPDX-License-Identifier: AGPL-3.0-or-later`), then:

```python
"""
Medium-model (20-100 var) real-GPU validation for the GPU SA solvers (QUI-687).

Models have analytically known ground states (or a seeded CPU reference for
the spin glass); the GPU best-energy is asserted against them, cross-checked
with SolverDWaveCPU on the same model object. Hardware classes follow
test_xqsa.py's guard idiom: skip locally without the device, run and
hard-fail in CI.
"""

import os
import random

import pytest

pytest.importorskip("dimod", reason="dwave-samplers / dimod not installed")

from xqsa import SolverDWaveCPU
from xqvm_py.xqmx import XQMX

# True in CI (GitLab/GitHub set CI). Hardware tests must run and hard-fail on
# a misconfigured CI environment rather than skip silently; locally they skip
# when the device/dependency is absent (no developer has every backend).
_IN_CI = os.environ.get("CI") is not None

_has_cupy = True
try:
    import cupy  # noqa: F401
except ImportError:
    _has_cupy = False

_has_metal = True
try:
    import Metal as _real_metal  # noqa: F401

    _has_metal = _real_metal.MTLCreateSystemDefaultDevice() is not None
except ImportError:
    _has_metal = False

if _has_cupy or _IN_CI:
    from xqsa.cuda_gpu import SolverCudaGPU

if _has_metal or _IN_CI:
    from xqsa.metal_gpu import SolverMetalGPU

# Explicit GPU budgets: QUI-167's sweep showed fixed-depth defaults degrade
# with model size, so these tests never rely on solver defaults.
GPU_PARAMS = {"num_reads": 64, "num_sweeps": 2000, "seed": 42}

# Spin-glass tolerance: GPU best-energy within 2% of the CPU reference.
GLASS_TOLERANCE = 0.02
```

Then the four builders as stubs raising `NotImplementedError` (implemented in Step 3), followed by the reference tests:

```python
def cpu_reference(model: XQMX) -> int:
    """Best energy SolverDWaveCPU finds on ``model`` (fixed seed)."""
    return SolverDWaveCPU(seed=42).solve(model).energy


class TestMediumModelReferences:
    """Ungated: builders and reference energies hold on every CI run."""

    def test_ferro_chain_cpu_hits_ground_state(self) -> None:
        model, ground = ferro_chain()
        assert ground == -63
        assert cpu_reference(model) == ground

    def test_frustrated_triangles_cpu_hits_ground_state(self) -> None:
        model, ground = frustrated_triangles()
        assert ground == -17
        assert cpu_reference(model) == ground

    def test_bipartite_maxcut_cpu_hits_ground_state(self) -> None:
        model, ground = bipartite_maxcut()
        assert ground == -100
        assert cpu_reference(model) == ground

    def test_random_spin_glass_is_deterministic(self) -> None:
        model_a, ground_a = random_spin_glass()
        model_b, _ = random_spin_glass()
        assert ground_a is None
        assert model_a.size == model_b.size == 100
        assert cpu_reference(model_a) == cpu_reference(model_b)
```

- [ ] **Step 2: Run to verify the reference tests fail on the stubs**

Run: `uv run --no-sync pytest xqsa/tests/test_gpu_validation.py -q -k References`
Expected: 4 FAIL with `NotImplementedError`

- [ ] **Step 3: Implement the builders**

Replace the stubs with:

```python
def ferro_chain(n: int = 64) -> tuple[XQMX, int]:
    """Ferromagnetic Ising chain: J=-1 on consecutive pairs.

    Ground state is all spins aligned; every coupling contributes -1, so
    the exact ground energy is -(n-1).
    """
    model = XQMX.spin_model(n)
    for i in range(n - 1):
        model.set_quadratic(i, i + 1, -1.0)
    return model, -(n - 1)


def frustrated_triangles(n: int = 51) -> tuple[XQMX, int]:
    """Disjoint antiferromagnetic triangles: J=+1 on all edges of n//3
    independent 3-spin triangles.

    Each triangle is frustrated — at best two of its three edges are
    satisfied (energy -1 per triangle) — so the exact ground energy is
    -(n // 3). Triangles force the Gibbs graph-colouring path to use
    >= 3 colours (QUI-687's target), and the independent components keep
    the landscape easy for single-flip SA. (An odd AF *ring* was tried
    first: analytically -(n-2), but SolverDWaveCPU cannot reach it for
    n >= 25 — domain-wall critical slowing — so it cannot serve as a
    reference model.)
    """
    model = XQMX.spin_model(n)
    for t in range(n // 3):
        a, b, c = 3 * t, 3 * t + 1, 3 * t + 2
        model.set_quadratic(a, b, 1.0)
        model.set_quadratic(a, c, 1.0)
        model.set_quadratic(b, c, 1.0)
    return model, -(n // 3)


def bipartite_maxcut(n: int = 20) -> tuple[XQMX, int]:
    """MaxCut QUBO on the complete bipartite graph K(n/2, n/2), unit weights.

    Cut edges contribute -1 (-x_i - x_j + 2 x_i x_j with x_i != x_j),
    uncut edges 0. The bipartition cuts all (n/2)^2 edges, so the exact
    ground energy is -(n/2)^2 = -cut size.
    """
    half = n // 2
    model = XQMX.binary_model(n)
    for i in range(half):
        for j in range(half, n):
            model.add_linear(i, -1.0)
            model.add_linear(j, -1.0)
            model.add_quadratic(i, j, 2.0)
    return model, -(half * half)


def random_spin_glass(n: int = 100, seed: int = 7) -> tuple[XQMX, None]:
    """Seeded random +/-1 spin glass on ~6%-density pairs; no known optimum.

    Ground energy is unknown (returned as None): tests compare the GPU
    best-energy against the SolverDWaveCPU reference on the same model.
    """
    rng = random.Random(seed)
    model = XQMX.spin_model(n)
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.06:
                model.set_quadratic(i, j, rng.choice((-1.0, 1.0)))
    return model, None
```

- [ ] **Step 4: Run to verify the reference tests pass**

Run: `uv run --no-sync pytest xqsa/tests/test_gpu_validation.py -q -k References`
Expected: 4 passed. If a structured-model reference assert fails (CPU misses the optimum at defaults), STOP and report — that invalidates the spec's reference assumption; do not loosen the assertion.

- [ ] **Step 5: Ruff, line-length check, commit**

```bash
uv run --no-sync ruff check xqsa/tests/test_gpu_validation.py
awk 'length > 100 {print FNR" ("length")"}' xqsa/tests/test_gpu_validation.py
git add xqsa/tests/test_gpu_validation.py
git commit -m "test(xqsa): add medium-model builders with CPU reference tests"
```
awk must print nothing.

---

### Task 2: CUDA gated class (runs for real on a CUDA development machine)

**Files:**
- Modify: `xqsa/tests/test_gpu_validation.py` (append)

**Interfaces:**
- Consumes: builders, `cpu_reference`, `GPU_PARAMS`, `GLASS_TOLERANCE`, `_IN_CI`, `_has_cupy`, `SolverCudaGPU` from Task 1.

- [ ] **Step 1: Append the CUDA class**

```python
@pytest.mark.cuda
@pytest.mark.skipif(
    not _IN_CI and not _has_cupy,
    reason="cupy not installed (skipped locally; runs and hard-fails in CI)",
)
class TestCudaGPUMediumModels:
    """SolverCudaGPU on 20-100 var models with known/CPU-referenced optima."""

    def test_ferro_chain_exact(self) -> None:
        model, ground = ferro_chain()
        result = SolverCudaGPU(**GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_frustrated_triangles_exact(self) -> None:
        model, ground = frustrated_triangles()
        result = SolverCudaGPU(**GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_bipartite_maxcut_exact(self) -> None:
        model, ground = bipartite_maxcut()
        result = SolverCudaGPU(**GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_random_spin_glass_within_tolerance_of_cpu(self) -> None:
        model, _ = random_spin_glass()
        cpu = cpu_reference(model)
        gpu = SolverCudaGPU(**GPU_PARAMS).solve(model).energy
        assert gpu <= cpu + GLASS_TOLERANCE * abs(cpu), (
            f"GPU best energy {gpu} more than {GLASS_TOLERANCE:.0%} above CPU reference {cpu}"
        )
```

- [ ] **Step 2: Run the CUDA class for real**

Run: `uv run --no-sync pytest xqsa/tests/test_gpu_validation.py -q -k Cuda`
Expected: 4 passed (on a machine with a CUDA GPU + cupy).

If an exact assert fails: first re-run once to distinguish flake from miss. If the GPU consistently misses a structured optimum at `num_sweeps=2000`, raise `num_sweeps` in `GPU_PARAMS` stepwise (4000 → 10000 → 20000) until all structured models pass reliably, and record the final value + observed energies in your report. If `20000` still fails, STOP and report the observed vs expected energies — that is a solver-quality finding (consistent with QUI-167's CUDA observations), not a test bug to paper over. Apply the same escalation if the glass misses the 2% band.

- [ ] **Step 3: Stability check — 3 consecutive runs**

Run: `for i in 1 2 3; do uv run --no-sync pytest xqsa/tests/test_gpu_validation.py -q -k Cuda 2>&1 | tail -1; done`
Expected: `4 passed` three times. Record the three lines in your report. If any run flakes, escalate `num_sweeps` per Step 2 and repeat until 3/3 stable.

- [ ] **Step 4: Ruff, line-length, commit**

```bash
uv run --no-sync ruff check xqsa/tests/test_gpu_validation.py
awk 'length > 100 {print FNR" ("length")"}' xqsa/tests/test_gpu_validation.py
git add xqsa/tests/test_gpu_validation.py
git commit -m "test(xqsa): add CUDA GPU medium-model validation tests"
```

---

### Task 3: Metal gated class + whole-file verification

**Files:**
- Modify: `xqsa/tests/test_gpu_validation.py` (append)

**Interfaces:**
- Consumes: builders, `cpu_reference`, `GPU_PARAMS`, `GLASS_TOLERANCE`, `_IN_CI`, `_has_metal`, `SolverMetalGPU` from Task 1. Metal's constructor additionally takes `strategy` ("sa" or "gibbs").

- [ ] **Step 1: Append the Metal class**

```python
@pytest.mark.metal
@pytest.mark.skipif(
    not _IN_CI and not _has_metal,
    reason="Metal GPU not available (skipped locally; runs and hard-fails in CI)",
)
class TestMetalGPUMediumModels:
    """SolverMetalGPU (sa + gibbs) on the same medium models."""

    def test_ferro_chain_exact_sa(self) -> None:
        model, ground = ferro_chain()
        result = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_frustrated_triangles_exact_sa(self) -> None:
        model, ground = frustrated_triangles()
        result = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_bipartite_maxcut_exact_sa(self) -> None:
        model, ground = bipartite_maxcut()
        result = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_frustrated_triangles_exact_gibbs(self) -> None:
        """The triangles' interaction graph needs >= 3 colours, so this is
        the first test to meaningfully exercise the gibbs colouring path."""
        model, ground = frustrated_triangles()
        result = SolverMetalGPU(strategy="gibbs", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_bipartite_maxcut_exact_gibbs(self) -> None:
        model, ground = bipartite_maxcut()
        result = SolverMetalGPU(strategy="gibbs", **GPU_PARAMS).solve(model)
        assert result.energy == ground

    def test_random_spin_glass_within_tolerance_of_cpu(self) -> None:
        model, _ = random_spin_glass()
        cpu = cpu_reference(model)
        gpu = SolverMetalGPU(strategy="sa", **GPU_PARAMS).solve(model).energy
        assert gpu <= cpu + GLASS_TOLERANCE * abs(cpu), (
            f"GPU best energy {gpu} more than {GLASS_TOLERANCE:.0%} above CPU reference {cpu}"
        )
```

- [ ] **Step 2: Verify the Metal class SKIPS locally (guard correctness)**

Run: `uv run --no-sync pytest xqsa/tests/test_gpu_validation.py -rs -q -k Metal`
Expected: `6 skipped` with reason "Metal GPU not available (skipped locally; runs and hard-fails in CI)". Record the skip lines in your report — observing the skip IS the local verification for this class.

- [ ] **Step 3: Whole-file + regression verification**

```bash
uv run --no-sync pytest xqsa/tests/test_gpu_validation.py -q
uv run --no-sync pytest xqsa/tests/ -q -m "not qpu and not metal and not quip"
```
Expected: file run = 8 passed, 6 skipped (4 references + 4 cuda pass; 6 metal skip). Full xqsa run: all pass (76 pre-existing + new), no new warnings.

- [ ] **Step 4: Ruff, line-length, commit**

```bash
uv run --no-sync ruff check xqsa/tests/test_gpu_validation.py
awk 'length > 100 {print FNR" ("length")"}' xqsa/tests/test_gpu_validation.py
git add xqsa/tests/test_gpu_validation.py
git commit -m "test(xqsa): add Metal GPU medium-model validation tests"
```

---

## Self-Review

- **Spec coverage:** guard idiom (Task 1 Step 1), four builders with exact energies (Task 1 Step 3), ungated reference class (Task 1), CUDA class incl. glass tolerance (Task 2), Metal sa + gibbs incl. the ≥3-colour triangles (Task 3), observed-skip verification (Task 3 Step 2), AC mapping complete.
- **Placeholder scan:** the only stubs are Task 1 Step 1's NotImplementedError builders, replaced in Step 3 of the same task — explicit TDD, not a placeholder.
- **Type consistency:** builders return `tuple[XQMX, int|None]` everywhere; `GPU_PARAMS` dict unpacks into both solver constructors (both accept `num_reads`, `num_sweeps`, `seed`; Metal adds `strategy` passed explicitly); `cpu_reference` used identically in Tasks 1–3.
- **Escalation rule** for stochastic misses is defined once (Task 2 Step 2) and referenced by the stability step; Metal cannot be escalated locally (skips) — CI will exercise it, and the budgets are shared via `GPU_PARAMS`, already validated on CUDA.
