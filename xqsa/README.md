# xqsa -- Solver adapters for XQMX models

Pluggable solvers for quadratic optimisation models produced by the XQuad
toolchain. One interface, five backends:

| Solver | Class | Where it solves | Install |
|---|---|---|---|
| DWave CPU simulated annealing | `SolverDWaveCPU` | Locally, on the CPU | `pip install xqsa` |
| D-Wave Advantage QPU | `SolverDWaveQPU` | D-Wave Leap cloud | `pip install xqsa[dwave]` |
| CUDA GPU simulated annealing | `SolverCudaGPU` | Locally, NVIDIA GPU | `pip install xqsa[cuda]` |
| Metal GPU SA / Gibbs | `SolverMetalGPU` | Locally, Apple GPU (macOS) | `pip install xqsa[metal]` |
| Quip network (on-chain mempool) | `SolverQuip` | Quip network | `pip install xqsa[quip]` |

## Install

```sh
pip install xqsa               # CPU simulated annealing only
pip install xqsa[dwave]        # add D-Wave QPU support
pip install xqsa[cuda]         # add CUDA GPU support
pip install xqsa[metal]        # add Metal GPU support
pip install xqsa[quip]         # add Quip network support
```

Extras are composable: `pip install "xqsa[cuda,dwave]"`.

## A complete example

`build_solver` picks a backend by name, so a caller can stay
backend-agnostic across all five:

```python
from xqsa import SOLVERS, build_solver
from xqvm_py import XQMX

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

print(sorted(SOLVERS))
# ['cuda-gpu', 'dwave-cpu', 'dwave-qpu', 'metal-gpu', 'quip']

solver = build_solver("dwave-cpu", seed=42)
result = solver.solve(model)
print(result.sample)
print(result.energy)
# XQMX(mode=SAMPLE, domain=BINARY, size=4, linear_terms=1, quadratic_terms=0)
# -1
```

Every backend returns the same `SolverResult(sample, energy, timing,
metadata)`: `sample` is the best assignment found, `energy` is the
Hamiltonian recomputed independently in exact integer arithmetic (never
taken on faith from the backend), and `metadata`'s shape is per-backend.
`build_solver("dwave-cpu", ...)` is equivalent to constructing
`SolverDWaveCPU(...)` directly; reach for the class constructor instead of
`build_solver` when the backend is fixed at write time rather than chosen
by name at run time.

## Reference

Driver prerequisites, per-backend parameters, embedding on a D-Wave QPU,
the Quip network job lifecycle, and what the reported energy means on
fixed-precision hardware are covered in
[Solving Overview](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/solving/README.md)
and its four chapters. The normative reference is
[`spec/xqsa/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/SPEC.md);
this package follows it, and any divergence here is a bug.

## Also see

- [`xqvm_py`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm_py) -- pure-Python reference VM.
- [`xqffi`](https://gitlab.com/quip.network/xquad/-/tree/main/xqffi) -- pyo3 FFI bindings to the Rust runtime.
- [`xqcp`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcp) -- constraint-programming DSL that compiles to models this package can sample.
- [`xquad`](https://gitlab.com/quip.network/xquad/-/tree/main/xquad) -- umbrella meta-package.
- [Running Programs](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/running/README.md) -- end-to-end tour.

## License

AGPL-3.0-or-later.
