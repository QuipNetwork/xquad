# xqsa -- Solver adapters for XQMX models

Pluggable solvers for quadratic optimisation models produced by the XQuad toolchain.

| Solver | Class | Transport | Install |
|---|---|---|---|
| DWave CPU simulated annealing | `SolverDWaveCPU` | local | `pip install xqsa` |
| D-Wave Advantage QPU | `SolverDWaveQPU` | D-Wave Leap cloud | `pip install xqsa[dwave]` |
| CUDA GPU simulated annealing | `SolverCudaGPU` | local (NVIDIA GPU) | `pip install xqsa[cuda]` |
| Metal GPU SA / Gibbs | `SolverMetalGPU` | local (Apple GPU, macOS) | `pip install xqsa[metal]` |

## Install

```sh
# CPU simulated annealing only
pip install xqsa

# Add D-Wave QPU support
pip install xqsa[dwave]

# Add CUDA GPU support (requires NVIDIA GPU + CUDA driver)
pip install xqsa[cuda]

# Add Metal GPU support (requires macOS + Apple Silicon GPU)
pip install xqsa[metal]
```

Already have xqsa installed? Add an extra on top:

```sh
pip install "xqsa[cuda]"          # adds CUDA support to an existing install
```

### Driver prerequisites

| Extra | Hardware | Prerequisite | Verify |
|-------|----------|-------------|--------|
| `[cuda]` | NVIDIA GPU | CUDA 12.x driver | `nvidia-smi` |
| `[metal]` | Apple Silicon | macOS 13+ (Ventura) | built-in on supported Macs |
| `[dwave]` | -- | D-Wave Leap account + `DWAVE_API_TOKEN` env var | `dwave ping` |

After installing an extra, verify the solver class imports:

```sh
python -c "from xqsa import SolverCudaGPU; print('ok')"
python -c "from xqsa import SolverMetalGPU; print('ok')"
python -c "from xqsa import SolverDWaveQPU; print('ok')"
```

### uv workspace caveat

`uv sync` installs only base dependencies, not optional extras. To test GPU/QPU solvers locally, install the extra explicitly:

```sh
uv pip install "xqsa[cuda]"       # or [metal] / [dwave]
```

Note that a bare `uv run pytest` re-syncs the environment and drops both the extra and the maturin-built `xqffi` extension. Use `uv run --no-sync pytest` to preserve them, or re-run `maturin develop` + `uv pip install "xqsa[...]"` after each sync.

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

## Quick start -- Metal GPU (Apple Silicon)

Requires macOS with an Apple Metal GPU and `pip install xqsa[metal]`.

```python
from xqsa import SolverMetalGPU
from xqvm_py.xqmx import XQMX

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

# Simulated annealing (default), or strategy="gibbs" for block Gibbs sampling.
solver = SolverMetalGPU(strategy="sa", num_reads=200, num_sweeps=2000, seed=42)
result = solver.solve(model)
print(result.energy, result.timing)
```

The `strategy` parameter selects `"sa"` (simulated annealing) or `"gibbs"`
(block Gibbs sampling over a greedy graph colouring). `beta_schedule_type`
selects `"geometric"` (default) or `"linear"`. Coefficients are computed in
float32 on the GPU; `result.energy` is recomputed authoritatively in
integer arithmetic.

## Selecting a solver by name

`build_solver` constructs any backend from a short, CLI-friendly name, so
tools and scripts can stay backend-agnostic:

```python
from xqsa import SOLVERS, build_solver

print(sorted(SOLVERS))          # ['cuda-gpu', 'dwave-cpu', 'dwave-qpu', 'metal-gpu']
solver = build_solver("dwave-cpu", seed=42)
result = solver.solve(model)
```

`seed` is forwarded to the classical SA backends (`dwave-cpu`, `cuda-gpu`,
`metal-gpu`) and ignored for `dwave-qpu`, which is physical hardware. Every
example runner exposes this as a `--solver` flag:

```sh
uv run python examples/maxcut/runner.py --solver cuda-gpu
```

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
