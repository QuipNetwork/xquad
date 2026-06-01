# D-Wave QPU Solver for XQSA

**Date:** 2026-05-22
**Status:** approved
**Author:** Konrad Kleczkowski

## Summary

Add `SolverDWaveQPU` to `xqsa` -- a solver adapter that submits XQMX quadratic
models to a real D-Wave Advantage QPU via the D-Wave Leap cloud API. This is
the first hardware solver in xqsa; the existing `SolverDWaveCPU` is a CPU
simulated annealer.

## Motivation

`SolverDWaveCPU` provides classical simulated annealing for local testing.
Production workloads benefit from real quantum annealing hardware (D-Wave
Advantage) for larger QUBO/Ising problems where the physical annealing process
can find better solutions faster than CPU simulation.

## Scope

- New file: `xqsa/dwave_qpu.py` -- `SolverDWaveQPU` class
- Modified: `xqsa/__init__.py` -- export `SolverDWaveQPU`
- Modified: `xqsa/pyproject.toml` -- add `dwave-system>=1.0` optional dependency
  under `[dwave]` extra
- Modified: `xqsa/tests/test_xqsa.py` -- add `TestSolverDWaveQPU` with mocked
  hardware tests
- Modified: `xqsa/README.md` -- document the new backend

## Architecture

### Credential resolution

Constructor signature:

```python
SolverDWaveQPU(
    token: str | None = None,       # fallback: DWAVE_API_TOKEN env var
    endpoint: str | None = None,    # fallback: DWAVE_API_ENDPOINT env var
    solver: str | None = None,      # None = auto-select Advantage (Pegasus topology)
    num_reads: int = 100,
    annealing_time: int = 20,       # microseconds
)
```

Resolution order for `token`: constructor argument -> `DWAVE_API_TOKEN` env
var -> `ValueError` with a clear message.

Resolution order for `endpoint`: constructor argument -> `DWAVE_API_ENDPOINT`
env var -> `None` (uses D-Wave Leap default endpoint).

The sampler is constructed eagerly in `__init__` so credential errors surface
at construction time, not at `solve()` time.

### Solver selection

`solver=None` maps to `{"topology__type": "pegasus"}` as the D-Wave solver
filter, which auto-selects the best available Advantage system on the account.
A named solver string (e.g. `"Advantage_system5.4"`) is passed directly.

### solve() flow

1. `_validate_model(model)` -- inherited from `Solver`, checks BINARY/SPIN
2. `_model_to_bqm(model)` -- same dimod BQM conversion as `SolverDWaveCPU`
3. `sampler.sample(bqm, num_reads=..., annealing_time=..., **kwargs)` --
   `**kwargs` passes through for escape-hatch parameters (e.g.
   `annealing_schedule`, `chain_strength`)
4. Pick `result.first` (lowest energy sample across all reads)
5. Return `SolverResult(sample, energy, timing, metadata)`

### SolverResult metadata

```python
{
    "num_reads": int,
    "annealing_time": int,      # microseconds
    "solver": str,              # actual solver name used
    "qpu_timing": dict | None,  # result.info["timing"] from D-Wave, when present
}
```

`SolverResult.timing` is wall-clock time (`perf_counter`), consistent with
`SolverDWaveCPU`.

### Import guard

`dwave.system` is imported inside `SolverDWaveQPU.__init__` (not at module level)
so that importing `xqsa` without the `[dwave]` extra installed does not raise
`ImportError`. The error is raised lazily with a clear message:

```
dwave-system is not installed. Install xqsa with: pip install xqsa[dwave]
```

## Dependencies

```toml
[project.optional-dependencies]
dwave = ["dwave-system>=1.0"]
```

`dwave-system` is Apache 2.0 licensed -- compatible with AGPL-3.0-or-later.

## Testing

Tests live in `xqsa/tests/test_xqsa.py` under `TestSolverDWaveQPU`. All tests
mock `dwave.system.DWaveSampler` and `dwave.system.EmbeddingComposite` since
CI has no hardware credentials.

Test cases:
- `test_default_params` -- constructor stores defaults
- `test_custom_params` -- constructor stores custom params
- `test_solve_binary` -- solves trivial 2-variable QUBO with mocked sampler
- `test_solve_spin` -- solves 2-variable Ising model with mocked sampler
- `test_solve_kwargs_override` -- per-call kwargs override constructor defaults
- `test_solve_rejects_sample_mode` -- raises ValueError for SAMPLE mode input
- `test_missing_token_raises` -- raises ValueError when token absent from both
  constructor arg and env var
- `test_token_from_env` -- token resolved from DWAVE_API_TOKEN env var
- `test_missing_dwave_system_raises` -- ImportError with install hint when
  dwave-system is not installed

## Out of scope

- Fixed embedding (`FixedEmbeddingComposite`) -- can be added later if users
  need repeated-solve optimization with pre-computed embedding
- GPU-based simulated annealing -- separate solver, tracked separately
- D-Wave Leap hybrid solvers -- separate backend
