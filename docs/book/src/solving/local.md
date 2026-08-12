# Local Solvers

Three of the five backends run entirely on the machine calling them,
using only local hardware and no account. `dwave-cpu` runs on the CPU;
`cuda-gpu` and `metal-gpu` run custom simulated-annealing kernels on an
NVIDIA or Apple Silicon GPU. The `metal-gpu` examples below ran for
real, on an Apple Silicon Mac; no NVIDIA GPU was available while
writing this chapter, so the `cuda-gpu` material comes from reading
[`xqsa/cuda_gpu.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/cuda_gpu.py)
rather than from execution.

## `dwave-cpu`: The Baseline

`SolverDWaveCPU` wraps `dwave-samplers`' `SimulatedAnnealingSampler`.
It needs only a CPU and no credentials, and that is exactly what makes
it `xqsa.DEFAULT_SOLVER`: every other backend in this chapter is worth
comparing against it before you trust a faster or more exotic answer.
It ships in the base package -- `pip install xqsa` is enough.

```python
from xqsa import SolverDWaveCPU
from xqvm_py.xqmx import XQMX

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

solver = SolverDWaveCPU(seed=42)
result = solver.solve(model)
print(result.energy, result.metadata)
```

Constructor parameters, all overridable per call via `solve(**kwargs)`:

| Parameter | Default | Meaning |
|---|---|---|
| `num_reads` | 100 | Independent annealing runs; the best is kept |
| `num_sweeps` | 1000 | Sweeps per run |
| `num_sweeps_per_beta` | 1 | Sweeps held at each temperature level before cooling; `num_sweeps` must divide evenly |
| `beta_range` | `None` | Inverse-temperature `(start, end)`; `None` lets `dwave-samplers` pick |
| `seed` | `None` | RNG seed; `None` means non-deterministic |

## GPU Backends: `cuda-gpu` and `metal-gpu`

`SolverCudaGPU` and `SolverMetalGPU` bypass the D-Wave SDK entirely.
Both convert the model to a dense array and run parallel-replica
simulated annealing directly on the GPU, in custom CUDA or Metal
kernels. Picking between the CPU backend and either GPU backend is a
performance question, not a correctness one -- see
[Backends](../concepts/backends.md#what-actually-distinguishes-them);
all three run the identical annealing algorithm family, just on
different processors.

Install the extra that matches your hardware:

```sh
pip install xqsa[cuda]     # NVIDIA GPU, CUDA 12.x driver
pip install xqsa[metal]    # Apple Silicon Mac with a Metal device
```

```python
from xqsa import SolverMetalGPU
from xqvm_py.xqmx import XQMX

model = XQMX.binary_model(size=4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

solver = SolverMetalGPU(strategy="sa", num_reads=200, num_sweeps=2000, seed=42)
result = solver.solve(model)
print(result.energy)
# -1
```

Wall-clock `result.timing` is not shown here: it varies by roughly 2x
between runs on identical hardware, so a single pasted number would
read as a performance claim it cannot support. `metal-gpu` also
supports `strategy="gibbs"` (block Gibbs sampling over a greedy graph
colouring of the coupling graph). Both strategies produce correct
output on real Metal hardware:
[`xqsa/tests/test_gpu_validation.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/tests/test_gpu_validation.py)
checks each against models with a known exact ground state, plus a
spin-glass model matched within 2% of the CPU reference energy, all six
of which pass.

`cuda-gpu` supports only `strategy="sa"` -- CUDA has no Gibbs kernel.
See
[`xqsa/cuda_gpu.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/cuda_gpu.py)
for the implementation the rest of this section describes.

| Parameter | `cuda-gpu` default | `metal-gpu` default | Meaning |
|---|---|---|---|
| `strategy` | `"sa"` (only option) | `"sa"` or `"gibbs"` | Kernel to dispatch |
| `num_reads` | 100 | 100 | Parallel replicas, one per GPU threadgroup/block |
| `num_sweeps` | 1000 | 1000 | Sweeps per replica |
| `num_sweeps_per_beta` | 1 | 1 | Sweeps held per temperature level before cooling |
| `beta_schedule_type` | `"linear"` | `"geometric"` | Shape of the cooling schedule -- note the two backends default to different shapes |
| `beta_range` | `None` | `None` | Inverse-temperature `(start, end)`; `None` auto-computes it from the model's coefficients |
| `seed` | `None` | `None` | Base seed for the on-device RNG |

These are the constructor's own defaults. `xqsa.build_solver`, the
backend-agnostic constructor covered in
[Solving Overview](README.md#picking-a-backend-by-name), raises
`num_reads` to 200 and `num_sweeps` to 2000 for both GPU backends
instead of using these defaults.

`metal-gpu` computes coefficients in float32; `cuda-gpu` computes them
in float64, end to end in its kernels. `result.energy` is always
recomputed afterward in exact integer arithmetic on both, so either
GPU run and a CPU run report the same authoritative energy regardless
of which precision the search itself used. See
[Energy and Precision](energy-and-precision.md) for what the float32
downcast costs `metal-gpu` and when it matters.

## Driver Prerequisites

| Extra | Hardware | Prerequisite | Verify |
|---|---|---|---|
| `[cuda]` | NVIDIA GPU | CUDA 12.x driver | `nvidia-smi` |
| `[metal]` | Apple Silicon | A Metal device | built in on every Apple Silicon Mac |

After installing, confirm the solver class constructs before building a
model around it:

```sh
python -c "from xqsa import SolverCudaGPU; SolverCudaGPU(); print('ok')"
python -c "from xqsa import SolverMetalGPU; SolverMetalGPU(); print('ok')"
```

Both raise `ImportError` with a `pip install xqsa[...]` hint if the
extra is missing, and `RuntimeError` if the extra is installed but no
matching GPU is detected -- `import xqsa` itself never fails on a
missing extra, only constructing the solver that needs it does.
