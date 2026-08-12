# Solver Interface

## Solver Abstract Class

```python
class Solver(ABC):
    @abstractmethod
    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Solve a quadratic model, returning the best solution found."""
        ...

    def _validate_model(self, model: XQMX) -> None:
        """Validate that the model is solvable."""
        ...

    def _model_to_bqm(self, model: XQMX) -> dimod.BinaryQuadraticModel:
        """Convert an XQMX model to a dimod BQM."""
        ...

    def _sample_to_xqmx(self, model: XQMX, raw_sample: dict[int, int]) -> XQMX:
        """Convert a dimod sample dict to an XQMX sample."""
        ...

    def _recompute_energy(self, model: XQMX, sample: XQMX) -> int:
        """Compute authoritative integer energy for a model-sample pair."""
        ...
```

All solver implementations inherit from `Solver` and override `solve()`.

## `solve()` Contract

**Input:**
- `model` -- an XQMX in MODEL mode with a supported domain (see [DOMAINS.md](DOMAINS.md))
- `**kwargs` -- solver-specific parameters that override constructor defaults

**Output:**
- A `SolverResult` containing the best solution found

**Requirements:**
1. Must call `_validate_model(model)` (or equivalent validation) before solving
2. Must measure wall-clock timing via `time.perf_counter()`
3. Must return the best sample (lowest energy) if multiple reads are performed
4. Must populate `SolverResult.energy` with the authoritative integer energy computed via `_recompute_energy(model, sample)` (see [ENERGY.md](ENERGY.md))
5. Must populate the metadata keys its backend documents (see [Metadata Schema](#metadata-schema))

**Failure semantics:**
- `solve()` raises on failure; it only returns `SolverResult` on success
- Hardware or connectivity failures raise implementation-specific exceptions
- "No sample produced" scenarios (e.g. QPU embedding failure) also raise
- The verifier's `valid` flag handles the separate concern of "sample returned but does not satisfy constraints"

## `_validate_model()` Contract

Validates that a model is acceptable for solving:

| Condition | Result |
|-----------|--------|
| `model.mode != XQMXMode.MODEL` | `ValueError("Expected MODEL mode, got {mode}")` |
| `model.domain == XQMXDomain.DISCRETE` | `ValueError("Unsupported domain for solving: {domain}")` |
| `model.domain` is `BINARY` or `SPIN` | Accepted |

Subclasses may extend validation (e.g. checking problem-size limits) but must preserve these base checks.

## `SolverResult` Type

```python
@dataclass(frozen=True)
class SolverResult:
    sample: XQMX               # Solution as XQMX in SAMPLE mode
    energy: int                 # Authoritative Hamiltonian energy
    timing: float               # Wall-clock seconds spent solving
    metadata: dict[str, Any]    # Solver-specific keys
```

**Invariants:**
- `sample.mode == XQMXMode.SAMPLE`
- `sample.size == model.size` (same number of variables)
- `sample.rows == model.rows` and `sample.cols == model.cols` (grid dimensions preserved)
- `sample.domain == model.domain`
- `energy == compute_energy(model, sample)` (see [ENERGY.md](ENERGY.md))
- `timing >= 0.0`
- Frozen (immutable after construction)

## Metadata Schema

`SolverResult.metadata`'s shape is solver-specific; there is no single schema every solver honours. `SolverDWaveCPU`, `SolverCudaGPU`, and `SolverMetalGPU` populate exactly three top-level keys:

| Key | Type | Description |
|-----|------|-------------|
| `seed` | `int \| None` | The random seed actually used. `None` if non-deterministic. |
| `reads` | `int` | Number of samples/reads taken by the solver. |
| `params` | `dict[str, Any]` | Solver-specific parameters and diagnostics. |

`SolverDWaveQPU` omits `seed` (no seed exists on physical hardware) and adds `solver` and `qpu_timing` as top-level keys rather than nesting them under `params`. `SolverQuip` returns a different set of top-level keys entirely -- `order_id`, `solver`, `best_energy_milli`, `energy_matches_chain`, `num_submissions`, `num_solutions` -- with no `seed`, `reads`, or `params` key at all. A caller must know which solver produced a `SolverResult` before indexing into `metadata`.

**Example (SolverDWaveCPU):**

```python
metadata = {
    "seed": 42,
    "reads": 100,
    "params": {
        "num_sweeps": 1000,
        "beta_range": None,
        "num_occurrences": 5,
        "raw_energy": -42.0,
    },
}
```

The `raw_energy` key under `params` holds the solver's native float energy before integer recomputation. This is informational only.

## Parameter Convention

Solver parameters follow a two-level pattern:

1. **Constructor defaults** -- set once when creating the solver instance
2. **Per-call overrides** -- `**kwargs` on `solve()` override constructor defaults for that call only

```python
solver = SolverDWaveCPU(num_reads=100, seed=42)
result = solver.solve(model)                    # uses constructor defaults
result = solver.solve(model, num_reads=500)     # overrides num_reads for this call
```

The spec does not mandate any specific parameter names beyond the metadata keys above. Each solver defines its own parameter surface.

## Capability Reporting

`_validate_model()` is the sole gate. There is no capabilities introspection method. Callers that need to check domain support should catch `ValueError` from `_validate_model()`.

Future versions may add richer negotiation (supported domains, problem-size limits, hardware constraints) but this is not currently specified.

## SolverDWaveCPU Reference

`SolverDWaveCPU` is the reference implementation, wrapping `dwave-samplers` simulated annealing on CPU.

**Constructor parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_reads` | `int` | `100` | Number of annealing runs |
| `num_sweeps` | `int` | `1000` | Sweeps per run |
| `beta_range` | `tuple[float, float] \| None` | `None` | Temperature range (None = auto) |
| `seed` | `int \| None` | `None` | Random seed (None = non-deterministic) |

All four parameters are overridable via `solve(**kwargs)`.

**Conversion pipeline (informative, not normative):**
1. XQMX model -> `dimod.BinaryQuadraticModel` via `_model_to_bqm()`
2. `dwave.samplers.SimulatedAnnealingSampler().sample()` with timing measurement
3. Best result -> XQMX sample via `_sample_to_xqmx()`
4. Energy recomputed via `_recompute_energy(model, sample)`
5. Grid dimensions (rows/cols) preserved from original model
