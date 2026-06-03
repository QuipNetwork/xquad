# xqsa -- Solver adapters for XQMX models

Pluggable solvers for quadratic optimisation models produced by the XQuad toolchain.

| Solver | Class | Transport | Install |
|---|---|---|---|
| DWave CPU simulated annealing | `SolverDWaveCPU` | local | `pip install xqsa` |
| D-Wave Advantage QPU | `SolverDWaveQPU` | D-Wave Leap cloud | `pip install xqsa[dwave]` |

## Install

```sh
# CPU simulated annealing only
pip install xqsa

# Add D-Wave QPU support
pip install xqsa[dwave]
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

## Quick start -- D-Wave Advantage QPU

Requires a [D-Wave Leap](https://cloud.dwavesys.com/leap/) account and
`pip install xqsa[dwave]`.

```python
import os
os.environ["DWAVE_API_TOKEN"] = "your-leap-token"  # or pass token= directly

from xqsa import SolverDWaveQPU
from xqvm_py.xqmx import XQMX

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

solver = SolverDWaveQPU()              # auto-selects best Advantage system
result = solver.solve(model)
print(result.metadata["solver"])       # e.g. "Advantage_system5.4"
print(result.metadata["qpu_timing"])   # QPU timing breakdown from Leap
```

Credential resolution order: `token=` constructor argument ->
`DWAVE_API_TOKEN` env var -> `ValueError`.

For a specific solver: `SolverDWaveQPU(solver="Advantage_system5.4")`.
For custom annealing: `solver.solve(model, annealing_time=100, chain_strength=2.0)`.

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
