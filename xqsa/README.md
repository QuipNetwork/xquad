# xqsa — Solver adapters for XQMX models

Pluggable solver backends for quadratic optimisation models produced by the XQuad toolchain. Today's implementation wraps DWave's simulated annealer ([`dwave-neal`](https://docs.ocean.dwavesys.com/projects/neal/)); future backends (D-Wave QPU, other annealers, gradient-based solvers) plug into the same `Backend` protocol.

## Install

```sh
pip install xqsa
```

## Quick start

```python
from xqsa import NealBackend
from xqvm_py import XQMX, XQMXDomain

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

backend = NealBackend()
result = backend.solve(model)
# result.sample: XQMX    — the best assignment found
# result.energy: float   — model energy of that sample
# result.timing: float   — wall-clock seconds spent solving
# result.metadata: dict  — backend-specific extras (e.g. run counts)
```

## Backend protocol

Any class conforming to `xqsa.Backend` can drop in:

```python
class Backend(ABC):
    @abstractmethod
    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult: ...
```

`SolverResult` is a frozen dataclass of `(sample: XQMX, energy: float, timing: float, metadata: dict)`. Backends return the best solution found for the model.

## Specification

Authoritative specification: [`../spec/xqsa/SPEC.md`](../spec/xqsa/SPEC.md). The spec is the source of truth; any divergence in this reference implementation is a bug.

## Also see

- [`xqvm_py`](../xqvm_py/) — pure-Python reference VM.
- [`xqffi`](../xqffi/) — pyo3 FFI bindings to the Rust runtime.
- [`xqcp`](../xqcp/) — constraint-programming DSL that compiles to models this package can sample.
- [`xquad`](../xquad/) — umbrella meta-package.
- [`docs/python-api-walkthrough.md`](../docs/python-api-walkthrough.md) — end-to-end tour.

## License

AGPL-3.0-or-later.
