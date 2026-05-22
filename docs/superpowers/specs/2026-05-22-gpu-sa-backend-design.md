# GPU Simulated Annealing Backend for XQSA

**Date:** 2026-05-22
**Status:** approved
**Author:** Konrad Kleczkowski

## Summary

Add `GPUSABackend` to `xqsa` -- a GPU-accelerated simulated annealing solver
using `dwave.samplers.SimulatedAnnealingSampler` with `use_gpu=True`. This is
the CUDA-accelerated counterpart to `NealBackend`, running thousands of
parallel SA replicas on an NVIDIA GPU instead of a single CPU thread.

## Motivation

`NealBackend` is limited to CPU execution. For large QUBO/Ising problems,
GPU-parallelised SA explores far more states per wall-clock second.
`dwave.samplers` (the upstream library `neal` already delegates to) ships
native CUDA support via its `[gpu]` extra, keeping the implementation within
the existing `dimod` ecosystem with minimal new code.

## Scope

- New file: `xqsa/gpu_sa.py` -- `GPUSABackend` class
- Modified: `xqsa/__init__.py` -- export `GPUSABackend`
- Modified: `xqsa/pyproject.toml` -- add `dwave-samplers[gpu]>=1.0` optional
  dependency under `[gpu]` extra
- Modified: `xqsa/tests/test_xqsa.py` -- add `TestGPUSABackend` with mocked
  GPU tests
- Modified: `xqsa/README.md` -- document the new backend

## Architecture

### Constructor

```python
GPUSABackend(
    num_reads: int = 100,
    num_sweeps: int = 1000,
    seed: int | None = None,
)
```

No credentials needed -- the GPU is local hardware.

### GPU detection (fail-fast)

GPU presence is verified eagerly in `__init__` via `numba.cuda.is_available()`
(brought in by the `[gpu]` extra). If no CUDA-capable GPU is found, a
`RuntimeError` is raised immediately with a clear message rather than failing
silently at solve time.

Import guard: `dwave.samplers` is imported lazily inside `__init__` with a
clear `ImportError` hint when the `[gpu]` extra is absent:

```
dwave-samplers GPU support is not installed.
Install xqsa with: pip install xqsa[gpu]
```

### solve() flow

1. `_validate_model(model)` -- inherited from `Backend`
2. `_model_to_bqm(model)` -- inherited from `Backend`
3. `sampler.sample(bqm, num_reads=..., num_sweeps=..., seed=..., use_gpu=True, **kwargs)`
4. Pick `result.first` (lowest energy)
5. Return `SolverResult(sample, energy, timing, metadata)`

### SolverResult metadata

```python
{
    "num_reads": int,
    "num_sweeps": int,
    "seed": int | None,
    "use_gpu": True,
}
```

`SolverResult.timing` is wall-clock time (`perf_counter`), consistent with
other backends.

## Dependencies

```toml
[project.optional-dependencies]
gpu = [ "dwave-samplers>=1.0", "numba>=0.57" ]
```

`dwave-samplers` is Apache 2.0 licensed and `numba` is BSD-2-Clause --
both compatible with AGPL-3.0-or-later. The NVIDIA CUDA toolkit must be
installed on the host system separately (not pip-installable).

## Testing

Tests live in `xqsa/tests/test_xqsa.py` under `TestGPUSABackend`. All tests
mock `dwave.samplers` and `numba.cuda` to avoid requiring physical GPU
hardware in CI.

Test cases:
- `test_default_params` -- constructor stores defaults
- `test_custom_params` -- constructor stores custom params
- `test_solve_binary` -- solves trivial 2-variable QUBO with mocked sampler
- `test_solve_spin` -- solves 2-variable Ising model with mocked sampler
- `test_solve_kwargs_override` -- per-call kwargs override constructor defaults
- `test_solve_rejects_sample_mode` -- raises ValueError for SAMPLE mode input
- `test_no_gpu_raises` -- raises RuntimeError when no CUDA GPU is detected
- `test_missing_gpu_extra_raises` -- ImportError with install hint when
  dwave-samplers GPU extra is absent

## Out of scope

- `beta_range` / `beta_schedule_type` tuning -- can be added when users ask
- Multi-GPU support
- QAOA / gate-based GPU optimization (separate project milestone)