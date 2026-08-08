# QUI-687: Larger-Model Real-GPU Validation for GPU SA Solvers — Design

**Ticket:** [QUI-687](https://linear.app/quip-network/issue/QUI-687) — test(xqsa): add
larger-model real-GPU validation for GPU SA solvers (milestone v0.3.1)

## Goal

Real-GPU coverage for `SolverCudaGPU` and `SolverMetalGPU` currently stops at 2–3 variable
toy models. Add hardware-gated tests on medium models (20–100 vars) with known or
CPU-referenced ground states, asserting the GPU result against `SolverDWaveCPU` on the same
model.

## File

`xqsa/tests/test_gpu_validation.py` — new file, mirroring `xqsa/tests/test_xqsa.py`'s
hardware-guard pattern exactly: `_IN_CI = os.environ.get("CI") is not None`, `_has_cupy` /
`_has_metal` probes, solver imports under `if _has_X or _IN_CI:`, and
`@pytest.mark.cuda|metal` + `@pytest.mark.skipif(not _IN_CI and not _has_X, ...)` on the
classes (skip locally without hardware; run and hard-fail on CI's hardware runners).

## Model suite (module-level builders, shared by both solver classes)

| builder | type | n | ground energy | exercises |
|---|---|---|---|---|
| `ferro_chain(n=64)` | spin | 64 | exactly −63 (J=−1 chain, all aligned) | sa spin, easy landscape |
| `frustrated_triangles(n=51)` | spin | 51 | exactly −17 (17 disjoint J=+1 triangles, one frustrated edge each) | frustration; triangles ⇒ interaction graph needs ≥3 colours ⇒ Metal `gibbs` colouring path |
| `bipartite_maxcut(n=20)` | binary | 20 | exactly −100 (MaxCut QUBO on K₁₀,₁₀, unit weights, perfect cut = 100) | sa binary |
| `random_spin_glass(n=100, seed=7)` | spin | 100 | unknown — CPU reference | cross-check at size ceiling |

Each builder returns `(model, ground_energy)` with `ground_energy=None` for the glass.

> **Revision (2026-07-13):** the original design used an odd antiferromagnetic *ring*
> (n=51, ground −49). Implementation showed `SolverDWaveCPU` cannot reach that optimum at
> any practical budget (plateaus ~−29 even at 200× defaults) — single-spin-flip SA suffers
> domain-wall critical slowing on rings ≥25 nodes. Replaced with disjoint AF triangles,
> which keep frustration, the ≥3-colour property, n=51, and a known exact optimum, while
> each independent 3-spin component is trivial for SA.

## Assertion policy (per approved design)

- Structured models: assert the **exact** known optimum for `SolverDWaveCPU` *and* the GPU
  solver.
- Random glass: assert `gpu_energy <= cpu_energy + 0.02 * abs(cpu_energy)` (sign-safe 2%
  tolerance; energies are negative).
- GPU solver calls pin explicit `num_reads`/`num_sweeps`/`seed` — QUI-167's sweep showed
  fixed-depth defaults degrade with size, so budgets are chosen for these sizes rather than
  inherited.

## Test classes

- `TestMediumModelReferences` — **ungated**: `SolverDWaveCPU` hits each known optimum, and
  the glass builder is deterministic for its seed. Validates builders/reference energies in
  every normal CI run.
- `TestCudaGPUMediumModels` — `@pytest.mark.cuda`: sa on spin (chain, triangles) and binary
  (maxcut) exact; glass within 2% of CPU.
- `TestMetalGPUMediumModels` — `@pytest.mark.metal`: same four via `strategy="sa"`, plus
  `strategy="gibbs"` on the triangles and maxcut models (the ≥3-colour path), glass within
  2%.

## Acceptance criteria mapping

- Medium-model real-GPU tests for both solvers → the two gated classes.
- CPU cross-check assertions → exact-optimum asserts + 2% glass tolerance vs
  `SolverDWaveCPU` on the identical model object.
- Existing skip-guards honoured → same marker + skipif idiom as `test_xqsa.py`.

## Verification on the development machine

CUDA class runs for real (CUDA GPU with cupy installed); Metal class must be *observed
skipping* locally (guard correctness) and is structurally identical to the CUDA class —
CI's Metal runner (QUI-654) provides real execution.

## Logistics

- Branch `feature/qui-687`, worktree `.claude/worktrees/feature+qui-687`, based on
  post-v0.3.0 `origin/main` (15918ed).
- Baseline verified: 76 xqsa tests pass locally including the existing real-GPU CUDA tier.
- Env: `uv sync --extra cuda` + `maturin develop` re-run (sync reverts the editable xqffi).
