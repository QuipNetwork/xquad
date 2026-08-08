# QUI-854: `num_sweeps_per_beta` + `beta_schedule_type` for SolverCudaGPU — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `SolverCudaGPU` with the `num_sweeps_per_beta` equilibration knob and `beta_schedule_type` ("geometric"/"linear"), replacing the in-kernel hardcoded linear beta ramp with a host-built per-sweep schedule buffer, so all three SA solvers share the same API (Linear ticket QUI-854, "fuller option").

**Architecture:** The CUDA kernels (`_SA_BINARY_KERNEL`, `_SA_SPIN_KERNEL` in `xqsa/cuda_gpu.py`) currently compute `beta = beta_start + (beta_end − beta_start) · (sweep / (num_sweeps − 1))` inline. We replace the two `double` scalar kernel params with a `const double* betas` buffer of length `num_sweeps`, built host-side: `num_betas = num_sweeps // num_sweeps_per_beta` schedule points expanded via `np.repeat(..., num_sweeps_per_beta)` — mirroring `SolverMetalGPU`. The mocked test fixture `mock_cupy_env` contains a **numpy reimplementation of the kernel** (fake `RawKernel`) that must change in lockstep with the kernel signature.

**Tech Stack:** Python 3.12, CuPy (`cupy-cuda12x`), numpy, pytest. Test runner: `uv run pytest`. Mocked CUDA tests run without a GPU; real-GPU tests run locally on the RTX 4090.

## Global Constraints

- **Bit-identical default (user decision):** with `num_sweeps_per_beta=1` and `beta_schedule_type="linear"` (the new default), the schedule must reproduce the retired in-kernel formula **bit-for-bit** in float64. Do NOT use `np.linspace` for the linear schedule — use the exact expression `beta_start + (beta_end − beta_start) * (np.arange(num_betas) / np.float64(num_betas − 1))`.
- **Divide semantics (QUI-685 parity):** `num_sweeps` stays the total sweep count; `num_betas = num_sweeps // num_sweeps_per_beta`; `num_sweeps` must be divisible by `num_sweeps_per_beta`, else `ValueError`. Random-buffer size (`num_reads * num_sweeps * n`) and its OOM pre-flight are unchanged.
- **CUDA default schedule type is `"linear"`** (the historical CUDA ramp), NOT `"geometric"` (Metal's default) — changing it would break the reproduce-current-output acceptance criterion. Document the divergence.
- **`num_betas == 1` semantics:** ~~single-element schedule at `beta_start` (hottest) — matches both the retired kernel's `num_sweeps <= 1` branch and `SolverMetalGPU`.~~
  **Superseded (2026-07-20, after !90 review):** the schedule is a single element at `beta_end` (coldest) — a greedy quench matching `SolverDWaveCPU` and the revised `SolverMetalGPU` (!90 commit `c9358c9`). This intentionally departs from the retired kernel's `num_sweeps <= 1` branch, so the bit-identity guarantee above holds only for `num_sweeps >= 2`. Cross-backend consistency was preferred over bit-identity on a level that performs no annealing.
- Repo rules: 100-char lines, ruff format + lint clean, Google-style docstrings on public APIs, AGPL headers untouched. Commits: imperative mood, ≤72-char subject, **signed off (`git commit -s`)**, NO co-author bylines. Commit after every green test cycle (user preference: frequent commits, not per-task).
- All work on branch `feature/qui-854` (Linear's `gitBranchName`), branched from `origin/main`. Do not touch `xqsa/metal_gpu.py` or `xqsa/dwave_cpu.py` (MR !90 owns those).
- Mocked test command: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "CudaGPUMocked" -v`
- Real-GPU test command: `uv run --extra cuda pytest xqsa/tests/test_xqsa.py -m cuda -v` (if `cupy` is already importable in the venv, `uv run --no-sync` works too)

---

### Task 1: Branch + `_compute_beta_schedule` on SolverCudaGPU

**Files:**
- Modify: `xqsa/cuda_gpu.py` (imports ~line 35, module constants ~line 44, new method after `_auto_beta_range` ~line 315)
- Test: `xqsa/tests/test_xqsa.py` (append to `class TestSolverCudaGPUMocked`, before the real-GPU section ~line 686)

**Interfaces:**
- Consumes: nothing new.
- Produces: `SolverCudaGPU._compute_beta_schedule(num_betas: int, beta_range: tuple[float, float], schedule_type: str) -> npt.NDArray[np.float64]`; module constant `_SUPPORTED_SCHEDULES = frozenset({"geometric", "linear"})`. Task 2 calls this from `_run_sa` and validates against `_SUPPORTED_SCHEDULES`.

- [ ] **Step 1: Create the branch**

```bash
cd /home/konrad/Quip/xquad && git checkout -b feature/qui-854 origin/main
```

- [ ] **Step 2: Write the failing tests** — append inside `TestSolverCudaGPUMocked` (after `test_oom_guard`):

```python
    def test_compute_beta_schedule_mocked(self, mock_cupy_env) -> None:
        """_compute_beta_schedule returns num_betas float64 points for both types."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        solver = _Solver()
        for schedule_type in ("geometric", "linear"):
            schedule = solver._compute_beta_schedule(20, (0.05, 5.0), schedule_type)
            assert schedule.shape == (20,)
            assert schedule.dtype == np.float64

    def test_compute_beta_schedule_linear_bit_identical_mocked(self, mock_cupy_env) -> None:
        """The linear schedule reproduces the retired in-kernel ramp bit-for-bit."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        solver = _Solver()
        schedule = solver._compute_beta_schedule(200, (0.05, 5.0), "linear")
        expected = 0.05 + (5.0 - 0.05) * (np.arange(200, dtype=np.float64) / np.float64(199))
        np.testing.assert_array_equal(schedule, expected)

    def test_compute_beta_schedule_single_level_mocked(self, mock_cupy_env) -> None:
        """A single temperature level anneals at beta_start (hottest)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        solver = _Solver()
        for schedule_type in ("geometric", "linear"):
            schedule = solver._compute_beta_schedule(1, (0.05, 5.0), schedule_type)
            assert schedule.shape == (1,)
            assert schedule[0] == np.float64(0.05)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "compute_beta_schedule" -v`
Expected: 3 FAILED with `AttributeError: 'SolverCudaGPU' object has no attribute '_compute_beta_schedule'`

- [ ] **Step 4: Implement.** In `xqsa/cuda_gpu.py`:

(a) Under `import numpy as np` (line 35) add:

```python
import numpy.typing as npt
```

(b) Under `_SUPPORTED_STRATEGIES = frozenset({"sa"})` (line 44) add:

```python
_SUPPORTED_SCHEDULES = frozenset({"geometric", "linear"})
```

(c) After `_auto_beta_range` (ends line 315) add:

```python
    def _compute_beta_schedule(
        self, num_betas: int, beta_range: tuple[float, float], schedule_type: str
    ) -> npt.NDArray[np.float64]:
        """Build the per-temperature-level inverse-temperature schedule.

        Returns ``num_betas`` beta points; ``_run_sa`` expands this to the
        per-sweep kernel buffer via ``np.repeat(..., num_sweeps_per_beta)``.

        The ``"linear"`` branch reproduces the retired in-kernel ramp
        ``beta_start + (beta_end - beta_start) * (sweep / (num_sweeps - 1))``
        term-for-term in float64, so the default configuration
        (``num_sweeps_per_beta=1``) is bit-identical to the pre-buffer
        kernel output. Do not replace it with ``np.linspace``, which
        computes linear spacing differently in the last ulp.
        """
        beta_start, beta_end = beta_range
        if num_betas == 1:
            # A single temperature level anneals at beta_start (hottest),
            # matching the retired kernel's num_sweeps <= 1 branch and
            # SolverMetalGPU's single-level convention.
            return np.array([beta_start], dtype=np.float64)
        if schedule_type == "geometric":
            start = max(beta_start, 1e-12)
            return np.geomspace(start, beta_end, num_betas).astype(np.float64)
        steps = np.arange(num_betas, dtype=np.float64) / np.float64(num_betas - 1)
        return np.float64(beta_start) + (np.float64(beta_end) - np.float64(beta_start)) * steps
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "compute_beta_schedule" -v`
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add xqsa/cuda_gpu.py xqsa/tests/test_xqsa.py
git commit -s -m "feat(xqsa): add host-side beta schedule builder to SolverCudaGPU"
```

---

### Task 2: Kernel ABI change, parameter plumbing, and fixture lockstep

**Files:**
- Modify: `xqsa/cuda_gpu.py` — kernel sources (lines 50-162), `__init__` (193-217), `solve` (229-281), `_run_sa` (317-391), class docstring (166-191)
- Modify: `xqsa/tests/test_xqsa.py` — `mock_cupy_env` fake kernel (`_make_sa_kernel`, ~lines 495-556)
- Test: `xqsa/tests/test_xqsa.py` (append inside `TestSolverCudaGPUMocked`)

**Interfaces:**
- Consumes: `_compute_beta_schedule` and `_SUPPORTED_SCHEDULES` from Task 1.
- Produces: new kernel ABI — args `(h, J, samples, energies, randoms, betas, n, num_sweeps)` where `betas` is a float64 device array of length `num_sweeps` at **index 5**; new public params `num_sweeps_per_beta: int = 1` and `beta_schedule_type: str = "linear"` on `__init__` and `solve` kwargs; metadata params keys `{strategy, num_sweeps, num_sweeps_per_beta, num_betas, beta_range, beta_schedule_type, raw_energy}`. Task 3's capture tests read the buffer at args index 5.

- [ ] **Step 1: Write the failing validation/metadata tests** — append inside `TestSolverCudaGPUMocked`:

```python
    def test_num_sweeps_per_beta_validation_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta < 1 raises, and indivisible counts raise (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match="num_sweeps_per_beta"):
            _Solver().solve(model, num_sweeps_per_beta=0)
        with pytest.raises(ValueError, match="divisible"):
            _Solver().solve(model, num_sweeps=200, num_sweeps_per_beta=7)

    def test_beta_schedule_type_validation_mocked(self, mock_cupy_env) -> None:
        """Unsupported beta_schedule_type raises in constructor and solve (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        with pytest.raises(ValueError, match="beta_schedule_type"):
            _Solver(beta_schedule_type="exponential")
        with pytest.raises(ValueError, match="beta_schedule_type"):
            _Solver().solve(model, beta_schedule_type="exponential")

    def test_num_sweeps_per_beta_run_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta > 1 solves and records the schedule split (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=200, num_sweeps_per_beta=10, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["num_sweeps"] == 200
        assert result.metadata["params"]["num_sweeps_per_beta"] == 10
        assert result.metadata["params"]["num_betas"] == 20
        assert result.metadata["params"]["beta_schedule_type"] == "linear"
```

Also **extend** the existing `test_metadata_schema_mocked` (test_xqsa.py:629) by adding after its current assertion:

```python
        assert set(result.metadata["params"].keys()) == {
            "strategy",
            "num_sweeps",
            "num_sweeps_per_beta",
            "num_betas",
            "beta_range",
            "beta_schedule_type",
            "raw_energy",
        }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "CudaGPUMocked" -v`
Expected: the 3 new tests FAIL (no ValueError raised / KeyError `num_sweeps_per_beta`); `test_metadata_schema_mocked` FAILS on the key-set; all other mocked tests still PASS.

- [ ] **Step 3: Change both kernel sources.** In `_SA_BINARY_KERNEL` (lines 50-106) and `_SA_SPIN_KERNEL` (lines 108-162), apply the identical two edits:

(a) Replace the parameter list tail

```c
    int    n,
    int    num_sweeps,
    double beta_start,
    double beta_end
```

with

```c
    const double* __restrict__ betas,
    int    n,
    int    num_sweeps
```

placing `const double* __restrict__ betas,` directly after the `randoms` parameter (so the pointer params stay grouped, ABI order: h, J, samples, energies, randoms, betas, n, num_sweeps).

(b) Replace the in-loop beta computation

```c
        double beta;
        if (num_sweeps <= 1) {
            beta = beta_start;
        } else {
            beta = beta_start
                + (beta_end - beta_start)
                    * ((double)sweep / (double)(num_sweeps - 1));
        }
```

with

```c
        double beta = betas[sweep];
```

- [ ] **Step 4: Plumb the parameters through the host side.**

(a) `__init__` (line 193) — new signature and validation (mirror `SolverMetalGPU`'s ordering; add the schedule-type check after the strategy check at line 210, and store the new attributes):

```python
    def __init__(
        self,
        strategy: str = "sa",
        num_reads: int = 100,
        num_sweeps: int = 1000,
        num_sweeps_per_beta: int = 1,
        beta_range: tuple[float, float] | None = None,
        beta_schedule_type: str = "linear",
        seed: int | None = None,
    ) -> None:
```

after the existing strategy check:

```python
        if beta_schedule_type not in _SUPPORTED_SCHEDULES:
            raise ValueError(
                f"Unsupported beta_schedule_type {beta_schedule_type!r}. "
                f"Supported: {sorted(_SUPPORTED_SCHEDULES)}"
            )
```

and with the other attribute assignments:

```python
        self.num_sweeps_per_beta = num_sweeps_per_beta
        self.beta_schedule_type = beta_schedule_type
```

(b) `solve` (line 229) — read the kwargs alongside the existing ones:

```python
        num_sweeps_per_beta = kwargs.get("num_sweeps_per_beta", self.num_sweeps_per_beta)
        beta_schedule_type = kwargs.get("beta_schedule_type", self.beta_schedule_type)
```

after the existing `num_sweeps < 1` check add:

```python
        if beta_schedule_type not in _SUPPORTED_SCHEDULES:
            raise ValueError(
                f"Unsupported beta_schedule_type {beta_schedule_type!r}. "
                f"Supported: {sorted(_SUPPORTED_SCHEDULES)}"
            )
        if num_sweeps_per_beta < 1:
            raise ValueError(f"num_sweeps_per_beta must be >= 1, got {num_sweeps_per_beta}")
        num_betas, rem = divmod(num_sweeps, num_sweeps_per_beta)
        if rem != 0:
            raise ValueError(
                f"num_sweeps ({num_sweeps}) must be divisible by "
                f"num_sweeps_per_beta ({num_sweeps_per_beta})"
            )
```

pass both through the `_run_sa` call:

```python
        best_sample_dict, raw_energy = self._run_sa(
            model=model,
            h=h,
            j_matrix=j_matrix,
            num_reads=num_reads,
            num_sweeps=num_sweeps,
            num_sweeps_per_beta=num_sweeps_per_beta,
            beta_schedule_type=beta_schedule_type,
            beta_range=beta_range,
            seed=seed,
        )
```

and extend the metadata `params` dict (line 274) to:

```python
                "params": {
                    "strategy": strategy,
                    "num_sweeps": num_sweeps,
                    "num_sweeps_per_beta": num_sweeps_per_beta,
                    "num_betas": num_betas,
                    "beta_range": beta_range,
                    "beta_schedule_type": beta_schedule_type,
                    "raw_energy": raw_energy,
                },
```

(c) `_run_sa` (line 317) — make params keyword-only (it is already called with keywords) and add the two new ones:

```python
    def _run_sa(
        self,
        model: XQMX,
        *,
        h: cp.ndarray,
        j_matrix: cp.ndarray,
        num_reads: int,
        num_sweeps: int,
        num_sweeps_per_beta: int,
        beta_schedule_type: str,
        beta_range: tuple[float, float] | None,
        seed: int | None,
    ) -> tuple[dict[int, int], float]:
```

replace the two lines `beta_start, beta_end = beta_range` (line 339) with schedule construction (immediately after the `beta_range is None` auto-range block):

```python
        # Host-built per-sweep schedule: num_betas levels, each held for
        # num_sweeps_per_beta consecutive sweeps (divisibility validated
        # in solve). Replaces the retired in-kernel linear ramp.
        num_betas = num_sweeps // num_sweeps_per_beta
        betas_host = self._compute_beta_schedule(num_betas, beta_range, beta_schedule_type)
        beta_schedule = cupy.asarray(np.repeat(betas_host, num_sweeps_per_beta))
```

and change the kernel invocation args tuple (lines 370-380) to:

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

- [ ] **Step 5: Update the fake kernel in `mock_cupy_env` in lockstep.** In `_make_sa_kernel` (test_xqsa.py ~line 495), change the unpack line

```python
            h, J, samples, energies, randoms, n_val, ns_val, bs, be = args
```

to

```python
            h, J, samples, energies, randoms, betas, n_val, ns_val = args
```

delete the lines `beta_start = float(bs)` and `beta_end = float(be)`, and replace the in-loop block

```python
                for sweep in range(num_sweeps):
                    if num_sweeps <= 1:
                        beta = beta_start
                    else:
                        beta = beta_start + (beta_end - beta_start) * (sweep / (num_sweeps - 1))
```

with

```python
                for sweep in range(num_sweeps):
                    beta = float(betas[sweep])
```

- [ ] **Step 6: Run the full mocked class**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "CudaGPUMocked" -v`
Expected: ALL PASS — the 3 new tests, the extended `test_metadata_schema_mocked`, and every pre-existing mocked test (default path regression: same energies, since the default schedule is bit-identical).

- [ ] **Step 7: Commit**

```bash
git add xqsa/cuda_gpu.py xqsa/tests/test_xqsa.py
git commit -s -m "feat(xqsa): add num_sweeps_per_beta + beta_schedule_type to SolverCudaGPU"
```

---

### Task 3: Behavioral schedule-buffer tests (mocked)

**Files:**
- Test: `xqsa/tests/test_xqsa.py` (append inside `TestSolverCudaGPUMocked`)

**Interfaces:**
- Consumes: kernel ABI from Task 2 — the beta buffer is `args[5]` of the fake-kernel call; `_binary_kernel` is a `functools.cached_property`, so an instance-dict override shadows it.
- Produces: nothing for later tasks (leaf task).

- [ ] **Step 1: Write the tests** (they exercise already-implemented behavior; expected to pass — their value is regression):

```python
    @staticmethod
    def _capture_kernel_args(solver) -> dict:
        """Shadow the cached binary kernel with a wrapper that records call args."""
        captured: dict = {}
        real_kernel = solver._binary_kernel

        def wrapper(grid, block, args):
            captured["args"] = args
            return real_kernel(grid, block, args)

        solver.__dict__["_binary_kernel"] = wrapper
        return captured

    def test_num_sweeps_per_beta_schedule_buffer_mocked(self, mock_cupy_env) -> None:
        """The kernel receives a per-sweep buffer holding each level for N sweeps.

        Verifies the divide semantics end-to-end: buffer length num_sweeps,
        constant within each num_sweeps_per_beta block, and the levels are
        20 distinct, strictly increasing betas (a collapsed-constant schedule
        would fail the uniqueness assertions).
        """
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(
            num_reads=5, num_sweeps=200, num_sweeps_per_beta=10, beta_range=(0.05, 5.0), seed=7
        )
        captured = self._capture_kernel_args(solver)
        solver.solve(model)

        betas = np.asarray(captured["args"][5])
        assert betas.shape == (200,)
        levels = betas.reshape(20, 10)
        for level in levels:
            assert np.all(level == level[0])
        assert np.unique(levels[:, 0]).size == 20
        assert np.all(np.diff(levels[:, 0]) > 0)

    def test_default_schedule_bit_identical_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta=1 + linear reproduces the retired kernel ramp bit-for-bit."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=5, num_sweeps=200, beta_range=(0.05, 5.0), seed=7)
        captured = self._capture_kernel_args(solver)
        solver.solve(model)

        betas = np.asarray(captured["args"][5])
        expected = 0.05 + (5.0 - 0.05) * (np.arange(200, dtype=np.float64) / np.float64(199))
        np.testing.assert_array_equal(betas, expected)

    def test_single_beta_level_uses_beta_start_mocked(self, mock_cupy_env) -> None:
        """num_sweeps_per_beta == num_sweeps pins the whole run at beta_start."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(
            num_reads=5, num_sweeps=50, num_sweeps_per_beta=50, beta_range=(0.05, 5.0), seed=7
        )
        captured = self._capture_kernel_args(solver)
        result = solver.solve(model)

        betas = np.asarray(captured["args"][5])
        assert betas.shape == (50,)
        assert np.all(betas == np.float64(0.05))
        assert result.metadata["params"]["num_betas"] == 1

    def test_geometric_schedule_mocked(self, mock_cupy_env) -> None:
        """The geometric schedule solves a trivial model and is recorded (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, beta_schedule_type="geometric", seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["beta_schedule_type"] == "geometric"

    def test_num_sweeps_per_beta_kwargs_override_mocked(self, mock_cupy_env) -> None:
        """Per-call kwargs override non-default constructor values (mocked)."""
        from xqsa.cuda_gpu import SolverCudaGPU as _Solver

        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)

        solver = _Solver(num_reads=10, num_sweeps=100, num_sweeps_per_beta=10, seed=1)
        result = solver.solve(model, num_sweeps_per_beta=5, beta_schedule_type="geometric")

        assert result.metadata["params"]["num_sweeps_per_beta"] == 5
        assert result.metadata["params"]["num_betas"] == 20
        assert result.metadata["params"]["beta_schedule_type"] == "geometric"
```

- [ ] **Step 2: Run the tests**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "CudaGPUMocked" -v`
Expected: ALL PASS (6 new + all prior). If any new test fails, the implementation from Task 2 has a real bug — debug it, do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add xqsa/tests/test_xqsa.py
git commit -s -m "test(xqsa): verify SolverCudaGPU beta buffer semantics via mocked kernel ABI"
```

---

### Task 4: Real-GPU tests, docstring, changelog

**Files:**
- Modify: `xqsa/tests/test_xqsa.py` — `class TestSolverCudaGPU` (real hardware, starts ~line 699): extend `test_default_params` / `test_custom_params`, append 3 tests
- Modify: `xqsa/cuda_gpu.py` — class docstring (lines 166-191)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: public API from Task 2 (`num_sweeps_per_beta`, `beta_schedule_type`, metadata keys).
- Produces: nothing for later tasks.

- [ ] **Step 1: Extend the real-GPU param tests.** In `test_default_params` add after the `num_sweeps` assertion:

```python
        assert solver.num_sweeps_per_beta == 1
        assert solver.beta_schedule_type == "linear"
```

In `test_custom_params`, change the constructor call and add assertions:

```python
        solver = SolverCudaGPU(
            strategy="sa", num_reads=50, num_sweeps=500, num_sweeps_per_beta=5, seed=42
        )
        assert solver.num_reads == 50
        assert solver.num_sweeps == 500
        assert solver.num_sweeps_per_beta == 5
        assert solver.seed == 42
```

- [ ] **Step 2: Append the real-GPU behavior tests** (inside `TestSolverCudaGPU`, mirroring the Metal class pattern at test_xqsa.py:1663+ conventions):

```python
    def test_num_sweeps_per_beta_validation(self) -> None:
        """num_sweeps_per_beta < 1 raises, and indivisible counts raise."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        solver = SolverCudaGPU()
        with pytest.raises(ValueError, match="num_sweeps_per_beta"):
            solver.solve(model, num_sweeps_per_beta=0)
        with pytest.raises(ValueError, match="divisible"):
            solver.solve(model, num_sweeps=200, num_sweeps_per_beta=7)

    def test_num_sweeps_per_beta_run(self) -> None:
        """num_sweeps_per_beta > 1 solves and records the schedule split."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverCudaGPU(num_reads=20, num_sweeps=200, num_sweeps_per_beta=10, seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["num_sweeps"] == 200
        assert result.metadata["params"]["num_sweeps_per_beta"] == 10
        assert result.metadata["params"]["num_betas"] == 20

    def test_geometric_schedule(self) -> None:
        """The geometric beta schedule solves a trivial model on real hardware."""
        model = XQMX.binary_model(2)
        model.set_linear(0, 1.0)
        model.set_linear(1, 1.0)

        solver = SolverCudaGPU(num_reads=20, num_sweeps=200, beta_schedule_type="geometric", seed=42)
        result = solver.solve(model)

        assert result.energy == 0
        assert result.metadata["params"]["beta_schedule_type"] == "geometric"
```

- [ ] **Step 3: Run the real-GPU suite locally (RTX 4090)**

Run: `uv run --extra cuda pytest xqsa/tests/test_xqsa.py -m cuda -v`
(If `cupy` is already in the venv, `uv run --no-sync pytest xqsa/tests/test_xqsa.py -m cuda -v`.)
Expected: ALL PASS, including every pre-existing real-GPU test (this compiles the modified kernels with NVRTC — it is the only step that proves the new CUDA source is valid). If NVRTC compilation fails, the kernel edit has a syntax error; fix `xqsa/cuda_gpu.py`, not the test.

- [ ] **Step 4: Update the class docstring.** In `xqsa/cuda_gpu.py`, after the sentence ending "its own CUDA thread block." (line 170) insert:

```
    ``num_sweeps_per_beta`` holds each inverse-temperature (beta) for that
    many consecutive sweeps, decoupling the number of temperature levels
    from the total sweep count: ``num_betas = num_sweeps //
    num_sweeps_per_beta`` (``num_sweeps`` must be divisible by it). The
    default of 1 keeps one beta per sweep, bit-identical to the previous
    behaviour. When ``num_betas == 1`` the whole run anneals at
    ``beta_start`` (hottest), matching ``SolverMetalGPU``.

    ``beta_schedule_type`` selects the schedule shape: ``"linear"`` (the
    default, preserving the historical CUDA ramp) or ``"geometric"``.
    Note: ``SolverMetalGPU`` defaults to ``"geometric"``.
```

and extend the `Raises:` section line for ValueError to:

```
        ValueError: if ``strategy`` or ``beta_schedule_type`` is not supported.
```

- [ ] **Step 5: Add the changelog entry.** In `CHANGELOG.md`, insert directly above `## [0.3.0] - 2026-07-13`:

```markdown
## [Unreleased]

### Added
- **xqsa**: Add `num_sweeps_per_beta` and `beta_schedule_type` to SolverCudaGPU (QUI-854)
```

(If an `## [Unreleased]` section already exists by then, add the bullet to its `### Added` list instead.)

- [ ] **Step 6: Re-run mocked suite + commit**

Run: `uv run --no-sync pytest xqsa/tests/test_xqsa.py -k "CudaGPU" -v`
Expected: ALL PASS.

```bash
git add xqsa/cuda_gpu.py xqsa/tests/test_xqsa.py CHANGELOG.md
git commit -s -m "test(xqsa): cover SolverCudaGPU schedule params on real GPU; document"
```

---

### Task 5: Preflight and merge request

**Files:**
- No new files; runs checks and pushes.

**Interfaces:**
- Consumes: everything above, all committed on `feature/qui-854`.

- [ ] **Step 1: Full Python preflight**

Run: `make preflight-py` (from `/home/konrad/Quip/xquad`)
Expected: ruff format clean, ruff lint clean, pytest green (~725 passed, hardware-marked tests deselected). Fix any formatting the tooling flags, amend nothing — new commit `style(xqsa): appease ruff` only if needed.

- [ ] **Step 2: Push and open the MR**

```bash
git push -u origin feature/qui-854
glab mr create --title "feat(xqsa): add num_sweeps_per_beta and beta_schedule_type to SolverCudaGPU" \
  --description-file <path-to-description> --assignee kleczkowski
```

MR description must state: divide semantics matching QUI-685; kernel ABI change (scalars → per-sweep `betas` buffer at arg index 5); bit-identical default (linear schedule computed with the retired formula's exact float64 expression, verified by `test_default_schedule_bit_identical_mocked`); CUDA keeps `"linear"` default vs Metal's `"geometric"` and why; random buffer + OOM guard unchanged; testing evidence (mocked suite + real RTX 4090 run counts); "Implements QUI-854". Fill the repo's MR checklist honestly (preflight-py checked; preflight-rs/parity N/A; real-GPU `-m cuda` run results).

- [ ] **Step 3: Verify pipeline starts**

Run: `glab mr view feature/qui-854` and confirm the MR exists and CI is running.

---

## Self-review (done at plan time)

- Spec coverage: all five QUI-854 acceptance criteria have tasks (plumbing → Task 2; default reproduces output → Tasks 1-3 bit-identical tests; metadata → Task 2; mocked + real tests → Tasks 2-4; fuller option `beta_schedule_type` → Tasks 2-4). ✓
- No placeholders; every code step shows the code. ✓
- Type consistency: `_compute_beta_schedule` signature identical across Tasks 1/2; buffer at args index 5 consistent across Tasks 2/3; metadata keys consistent across Tasks 2/3/4. ✓
