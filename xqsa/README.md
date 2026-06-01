# xqsa -- Solver adapters for XQMX models

Pluggable solvers for quadratic optimisation models produced by the XQuad toolchain.

| Solver | Class | Transport | Install |
|---|---|---|---|
| DWave CPU simulated annealing | `SolverDWaveCPU` | local | `pip install xqsa` |
| CUDA GPU simulated annealing | `SolverCudaGPU` | local (NVIDIA GPU) | `pip install xqsa[cuda]` |

## Install

```sh
# CPU simulated annealing only
pip install xqsa

# Add CUDA GPU support (requires NVIDIA GPU + CUDA driver)
pip install xqsa[cuda]
```

## Quick start -- CPU simulated annealing

```python
from xqsa import SolverDWaveCPU
from xqvm_py import XQMX, XQMXDomain

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

solver = SolverDWaveCPU()
result = solver.solve(model)
# result.sample: XQMX   -- the best assignment found
# result.energy: int     -- authoritative Hamiltonian energy
# result.timing: float   -- wall-clock seconds spent solving
# result.metadata: dict  -- seed, reads, solver-specific params
```

## Quick start -- CUDA GPU simulated annealing

Requires an NVIDIA GPU with CUDA support and `pip install xqsa[cuda]`.

```python
from xqsa import SolverCudaGPU
from xqvm_py.xqmx import XQMX

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

solver = SolverCudaGPU()                # uses all defaults
result = solver.solve(model)
print(result.energy, result.timing)

# Custom parameters
solver = SolverCudaGPU(num_reads=200, num_sweeps=2000, seed=42)
result = solver.solve(model, beta_range=(0.1, 5.0))
```

Algorithm selection via the `strategy` parameter (currently only `"sa"`
is supported; `"gibbs"` and `"metropolis"` are planned).

## Solver protocol

Any class conforming to `xqsa.Solver` can drop in:

```python
class Solver(ABC):
    @abstractmethod
    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult: ...
```

`SolverResult` is a frozen dataclass of `(sample: XQMX, energy: int, timing: float, metadata: dict)`. Solvers return the best solution found for the model.

## Specification

Authoritative specification: [`../spec/xqsa/SPEC.md`](../spec/xqsa/SPEC.md). The spec is the source of truth; any divergence in this reference implementation is a bug.

## Also see

- [`xqvm_py`](../xqvm_py/) -- pure-Python reference VM.
- [`xqffi`](../xqffi/) -- pyo3 FFI bindings to the Rust runtime.
- [`xqcp`](../xqcp/) -- constraint-programming DSL that compiles to models this package can sample.
- [`xquad`](../xquad/) -- umbrella meta-package.
- [`docs/python-api-walkthrough.md`](../docs/python-api-walkthrough.md) -- end-to-end tour.

## License

AGPL-3.0-or-later.
