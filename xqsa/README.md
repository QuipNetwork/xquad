# xqsa -- Solver adapters for XQMX models

Pluggable solvers for quadratic optimisation models produced by the XQuad
toolchain. One interface, five backends:

| Solver | Class | Where it solves | Install |
|---|---|---|---|
| CPU simulated annealing | `SolverCPU` | Locally, on the CPU | `pip install xqsa` |
| D-Wave Advantage QPU | `SolverDWave` | D-Wave Leap cloud | `pip install xqsa[dwave]` |
| CUDA GPU simulated annealing | `SolverGPU(engine="cuda")` | Locally, NVIDIA GPU | `pip install xqsa[cuda]` |
| Metal GPU SA / Gibbs | `SolverGPU(engine="metal")` | Locally, Apple GPU (macOS) | `pip install xqsa[metal]` |
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
from xqffi.vm import XqmxModel

model = XqmxModel.binary(4)
model.set_linear(0, -1)
model.set_quad(0, 1, 2)

print(sorted(SOLVERS))
# ['cpu', 'cuda', 'dwave', 'metal', 'quip']

solver = build_solver("cpu", seed=42)
result = solver.solve(model)
print(result.sample.values)
print(result.energy)
# [1, 0, 0, 0]
# -1
```

Every backend's `solve()` returns the same `SolverResult(sample, energy,
timing, metadata)`: `sample` is the best assignment found, `energy` is the
Hamiltonian recomputed independently in exact integer arithmetic (never
taken on faith from the backend), and `metadata` is `{"seed", "reads",
"params"}` for every backend except `SolverQuip`.
`build_solver("cpu", ...)` is equivalent to constructing
`SolverCPU(...)` directly; reach for the class constructor instead of
`build_solver` when the backend is fixed at write time rather than chosen
by name at run time.

`SolverQuip` also splits a job into steps: `create_order(model)` returns
a draft `JobOrder` whose options (payout rule, allowlist, quality
floors) are checked before anything is signed, `quote()` prices it and
`submit()` sends it. A submitted order leaves a `JobOrderReceipt`, which
any session rebuilds from the order id with `get_receipt()`. Import them
from `xqsa.quip`.

## Reference

Driver prerequisites, per-backend parameters, embedding on a D-Wave QPU,
the Quip network job lifecycle and its job orders, and what the reported
energy means on
fixed-precision hardware are covered in
[Solving Overview](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/solving/README.md)
and its chapters. The normative reference is
[`spec/xqsa/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/SPEC.md);
this package follows it, and any divergence here is a bug.

## Also see

- [`xqffi`](https://gitlab.com/quip.network/xquad/-/tree/main/xqffi) -- pyo3 FFI bindings to the Rust runtime, including the `XqmxModel` and `XqmxSample` types the solvers take and return.
- [`xqcp`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcp) -- constraint-programming DSL that compiles to models this package can sample.
- [`xquad`](https://gitlab.com/quip.network/xquad/-/tree/main/xquad) -- umbrella meta-package.
- [Running Programs](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/running/README.md) -- end-to-end tour.

## License

AGPL-3.0-or-later.
