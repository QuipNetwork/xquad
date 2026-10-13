# Solver Interface

## Solver Base Class

```python
class Solver:
    def __init__(self, impl: Implementation, **selectors: str) -> None:
        self._impl = impl
        self._selectors = selectors

    def solve(self, model: XqmxModel, **kwargs: Any) -> SolverResult:
        """Solve a quadratic model, returning the best solution found."""
        ...

    def _validate_model(self, model: XqmxModel) -> None:
        """Validate that the model is solvable."""
        ...

    def _recompute_energy(self, model: XqmxModel, sample: XqmxSample) -> int:
        """Compute authoritative integer energy for a model-sample pair."""
        ...
```

`XqmxModel`, `XqmxSample` and `Domain` are the `xqffi.vm` types, re-exported by `xquad.types`.

A solver is a public class that composes an `Implementation`. `solve()` is a template method shared by every solver: it validates, runs the implementation, and builds the result the same way whichever implementation runs. A solver subclass picks its implementation in `__init__`, passes it to `Solver.__init__` with the selectors that chose it, and does not override `solve()`. Moving a solver onto a different implementation therefore changes how it runs without changing its API.

`SolverQuip` is the one exception. It overrides `solve()` with its own network protocol and does not compose an implementation (see [Metadata Schema](#metadata-schema)).

## `Implementation` Interface

```python
@dataclass(frozen=True)
class RunOutput:
    sample: XqmxSample          # Best sample found
    seed: int | None            # Seed the run used; None when unseeded
    reads: int                  # Number of reads taken
    params: dict[str, Any]      # Everything else that describes the run

class Implementation(ABC):
    def __init__(self, **defaults: Any) -> None:
        self.defaults = defaults  # Per-call parameters and their constructor values

    @abstractmethod
    def run(self, model: XqmxModel, **params: Any) -> RunOutput:
        """Sample a validated model, returning the best sample found."""
        ...
```

An implementation turns a validated model into a sample. It does not time the run, compute the authoritative energy or assemble metadata; the template does all three. Its constructor declares every parameter it takes as an explicit keyword, so a misspelled parameter raises `TypeError` when the solver is built. It passes the per-call parameters, with their values, to `Implementation.__init__`, and `run()` declares the same names as keyword-only parameters. `run()` always receives every one of them, so a name present on one side only raises on every call.

The shipped implementations run in-process, in the Python interpreter that calls `solve()`: `InProcessCPU`, `InProcessCuda`, `InProcessMetal` and `InProcessDWave` (see [SOLVERS.md](SOLVERS.md)). A solver-binary implementation, which runs a solver as a separate executable, plugs in at the same seam.

`xqsa.solver` also provides two module functions for implementations built on `dimod`:

- `model_to_bqm(model)` converts an `XqmxModel` to a `dimod.BinaryQuadraticModel`.
- `sample_to_xqmx(model, raw_sample)` converts a `{variable: value}` dict to an `XqmxSample` with the model's grid dimensions. Variables absent from the dict keep the domain default.

## `solve()` Contract

**Input:**
- `model` -- an `XqmxModel` with a supported domain (see [DOMAINS.md](DOMAINS.md))
- `**kwargs` -- per-call overrides of constructor parameters, limited to the keys of the implementation's `defaults` (see [Parameter Convention](#parameter-convention))

**Output:**
- A `SolverResult` containing the best solution found

**Template steps:**
1. Call `_validate_model(model)`.
2. Reject any keyword not in `self._impl.defaults` with `TypeError`, before anything runs. The message names the rejected keys, the solver class and the accepted keys.
3. Run `self._impl.run(model, **(self._impl.defaults | kwargs))` and measure its wall-clock time with `time.perf_counter()`. The measurement covers the whole run, including model conversion and sample conversion, not only the sampler call.
4. Set `SolverResult.energy` to `_recompute_energy(model, out.sample)`, the authoritative integer energy on the original model (see [ENERGY.md](ENERGY.md)). That call raises `ArithmeticOverflow` when the energy of the returned sample is not representable in signed 64 bits, or when a partial sum leaves the range on the way to a total that is. The template does not catch it and never substitutes the implementation's own float energy.
5. Build `SolverResult.metadata` as `{"seed": out.seed, "reads": out.reads, "params": out.params | self._selectors}` (see [Metadata Schema](#metadata-schema)).

**Implementation requirements:**
1. `run()` must return the best (lowest-energy) sample when it takes several reads.
2. `run()` must report the seed it used in `RunOutput.seed`, or `None` when the run was unseeded or the hardware has no seed.

A solver that overrides `solve()` must still validate the model, measure wall-clock time, return the best sample, and set `energy` through `_recompute_energy()` without catching `ArithmeticOverflow`.

**Failure semantics:**
- `solve()` raises on failure; it only returns `SolverResult` on success
- An unknown per-call keyword raises `TypeError` before the implementation runs
- `ArithmeticOverflow` from `_recompute_energy()` propagates out of `solve()`. It reports that the model and the sample the backend returned have no representable energy, which is a property of the pair rather than a hardware or connectivity failure
- Hardware or connectivity failures raise implementation-specific exceptions
- "No sample produced" scenarios (e.g. QPU embedding failure) also raise
- The verifier's `valid` flag handles the separate concern of "sample returned but does not satisfy constraints"

## `_validate_model()` Contract

Validates that a model is acceptable for solving:

| Condition | Result |
|-----------|--------|
| `model` is not an `XqmxModel` | `ValueError("Expected an XqmxModel, got {type}")` |
| `model.domain` is an integer domain | `ValueError("Unsupported domain for solving: {domain.name}")`, e.g. `integer` |
| `model.domain` is `Domain.BINARY` or `Domain.SPIN` | Accepted |

Subclasses may extend validation (e.g. checking problem-size limits) but must preserve these base checks.

## `SolverResult` Type

```python
@dataclass(frozen=True)
class SolverResult:
    sample: XqmxSample         # Solution, one assignment per variable
    energy: int                 # Authoritative Hamiltonian energy
    timing: float               # Wall-clock seconds spent solving
    metadata: dict[str, Any]    # seed, reads, params (see Metadata Schema)
```

**Invariants:**
- `sample` is an `XqmxSample` holding only in-domain values
- `sample.size == model.size` (same number of variables)
- `sample.rows == model.rows` and `sample.cols == model.cols` (grid dimensions preserved)
- `sample.domain == model.domain`
- `energy == model.energy(sample)` (see [ENERGY.md](ENERGY.md)); where `model.energy` raises, no `SolverResult` exists
- `timing >= 0.0`
- Frozen (immutable after construction)

## Metadata Schema

Every solver whose `solve()` is the template returns `SolverResult.metadata` with exactly these three top-level keys and no others:

| Key | Type | Description |
|-----|------|-------------|
| `seed` | `int \| None` | The random seed the run used. `None` when unseeded, and always `None` on physical hardware, which has no seed. |
| `reads` | `int` | Number of samples/reads taken. |
| `params` | `dict[str, Any]` | The selectors that chose the implementation, the parameters the run used, and backend diagnostics. |

`params` always records the selectors: `algorithm` for `SolverCPU` and `SolverGPU`, `engine` for `SolverGPU`, `mode` for `SolverDWave`. Its other keys are backend-specific; [SOLVERS.md](SOLVERS.md) lists them per backend.

`SolverQuip` is the exception until it moves onto the template. It returns its own top-level keys -- `order_id`, `solver`, `best_energy_milli`, `energy_matches_chain`, `leader_read`, `leader`, `num_submissions`, `num_solutions`, `quantization_error` -- with no `seed`, `reads`, or `params` key. A caller handling `SolverQuip` results must index them by those keys.

**Example (`SolverCPU`):**

```python
metadata = {
    "seed": 42,
    "reads": 100,
    "params": {
        "algorithm": "sa",
        "num_sweeps": 1000,
        "num_sweeps_per_beta": 1,
        "num_betas": 1000,
        "beta_range": None,
        "num_occurrences": 5,
        "raw_energy": -42.0,
    },
}
```

**Example (`SolverDWave`):**

```python
metadata = {
    "seed": None,
    "reads": 100,
    "params": {
        "mode": "qpu",
        "solver": "Advantage_system4.1",
        "qpu_timing": {"qpu_sampling_time": 1000.0},
        "annealing_time": 20,
        "raw_energy": -42.0,
    },
}
```

The `raw_energy` key under `params` holds the backend's native float energy before integer recomputation. It is informational only; `SolverResult.energy` is authoritative.

## Parameter Convention

Solver parameters follow a two-level pattern:

1. **Constructor parameters** -- set once when creating the solver. A public solver class takes its selectors (`algorithm`, `engine`, `mode`) as keyword-only arguments and passes every other keyword through to its implementation's constructor. That constructor declares its parameters explicitly, so an unknown name raises `TypeError` when the solver is built.
2. **Per-call overrides** -- `**kwargs` on `solve()` override constructor parameters for that call only. They are limited to the keys of the implementation's `defaults`. Selectors are constructor-only: a solver's implementation is fixed when it is built.

```python
solver = SolverCPU(num_reads=100, seed=42)
result = solver.solve(model)                    # uses constructor parameters
result = solver.solve(model, num_reads=500)     # overrides num_reads for this call
result = solver.solve(model, num_raeds=500)     # TypeError: unknown keyword
```

The spec mandates the metadata keys above and the selector names. Each implementation defines its own parameter surface; [SOLVERS.md](SOLVERS.md) lists the parameters per engine and algorithm.

## Capability Reporting

`_validate_model()` is the sole gate. There is no capabilities introspection method. Callers that need to check domain support should catch `ValueError` from `_validate_model()`.

Future versions may add richer negotiation (supported domains, problem-size limits, hardware constraints) but this is not currently specified.

## SolverCPU Reference

`SolverCPU` is the reference solver. It runs `InProcessCPU`, which wraps `dwave-samplers` simulated annealing on CPU.

**Constructor parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `algorithm` | `str` | `"sa"` | Selector; `"sa"` is the only in-process value |
| `num_reads` | `int` | `100` | Number of annealing runs |
| `num_sweeps` | `int` | `1000` | Sweeps per run |
| `num_sweeps_per_beta` | `int` | `1` | Sweeps held at each temperature; must divide `num_sweeps` |
| `beta_range` | `tuple[float, float] \| None` | `None` | Temperature range (None = auto) |
| `seed` | `int \| None` | `None` | Random seed (None = non-deterministic) |

Every parameter except `algorithm` is overridable via `solve(**kwargs)`.

**Conversion pipeline (informative, not normative):**
1. `XqmxModel` -> `dimod.BinaryQuadraticModel` via `model_to_bqm()`
2. `dwave.samplers.SimulatedAnnealingSampler().sample()`
3. Best result -> `XqmxSample` via `sample_to_xqmx()`, preserving the model's grid dimensions
4. The template times the run, recomputes the energy via `_recompute_energy(model, sample)` and builds the metadata
