# Local Solvers

Three of the five backends run entirely on the machine calling them,
using only local hardware and no account. `cpu` runs on the CPU;
`cuda` and `metal` run custom simulated-annealing kernels on an
NVIDIA or Apple Silicon GPU. The `metal` examples below ran for
real, on an Apple Silicon Mac; no NVIDIA GPU was available while
writing this chapter, so the `cuda` material comes from reading
[`xqsa/inprocess/cuda.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/inprocess/cuda.py)
rather than from execution.

## `SolverCPU`: The Baseline

`SolverCPU` wraps `dwave-samplers`' `SimulatedAnnealingSampler`.
It needs only a CPU and no credentials, and that is exactly what makes
`cpu` the `xqsa.DEFAULT_SOLVER`: every other backend in this chapter is worth
comparing against it before you trust a faster or more exotic answer.
It ships in the base package -- `pip install xqsa` is enough.

```python
from xqsa import SolverCPU
from xquad.types import XqmxModel

model = XqmxModel.binary(size=4)
model.set_linear(0, -1)
model.set_quad(0, 1, 2)

solver = SolverCPU(seed=42)
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

`SolverCPU` also takes `algorithm`, which is `"sa"` and has no other
value yet. A misspelled parameter raises `TypeError`, whether you pass
it to the constructor or to `solve()`.

## GPU Backends: `SolverGPU`

`SolverGPU` bypasses the D-Wave SDK entirely. It converts the model to
a dense array and runs parallel-replica simulated annealing directly on
the GPU, in custom CUDA or Metal kernels. `engine` picks which, and it
is required: `SolverGPU(engine="cuda")` or `SolverGPU(engine="metal")`,
which are what the `cuda` and `metal` names build. Picking between the
CPU backend and either GPU engine is a performance question, not a
correctness one -- see
[Backends](../concepts/backends.md#what-actually-distinguishes-them);
all three run the identical annealing algorithm family, just on
different processors.

Install the extra that matches your hardware:

```sh
pip install xqsa[cuda]     # NVIDIA GPU, CUDA 12.x driver
pip install xqsa[metal]    # Apple Silicon Mac with a Metal device
```

```python
from xqsa import SolverGPU
from xquad.types import XqmxModel

model = XqmxModel.binary(size=4)
model.set_linear(0, -1)
model.set_quad(0, 1, 2)

solver = SolverGPU(engine="metal", algorithm="sa", num_reads=200, num_sweeps=2000, seed=42)
result = solver.solve(model)
print(result.energy)
# -1
```

Wall-clock `result.timing` is not shown here: it varies by roughly 2x
between runs on identical hardware, so a single pasted number would
read as a performance claim it cannot support. The Metal engine also
supports `algorithm="gibbs"` (block Gibbs sampling over a greedy graph
coloring of the coupling graph). Both algorithms produce correct
output on real Metal hardware:
[`xqsa/tests/test_gpu_validation.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/tests/test_gpu_validation.py)
checks each against models with a known exact ground state, plus a
spin-glass model matched within 2% of the CPU reference energy, all six
of which pass.

The CUDA engine supports only `algorithm="sa"` -- CUDA has no Gibbs
kernel. Any other `algorithm` raises `ValueError` saying it is not
available in-process; further algorithms arrive with the solver binaries.
See
[`xqsa/inprocess/cuda.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/inprocess/cuda.py)
for the implementation the rest of this section describes.

| Parameter | `engine="cuda"` default | `engine="metal"` default | Meaning |
|---|---|---|---|
| `algorithm` | `"sa"` (only option) | `"sa"`; also `"gibbs"` | Kernel to dispatch; constructor-only |
| `num_reads` | 100 | 100 | Parallel replicas, one per GPU threadgroup/block |
| `num_sweeps` | 1000 | 1000 | Sweeps per replica |
| `num_sweeps_per_beta` | 1 | 1 | Sweeps held per temperature level before cooling |
| `beta_schedule_type` | `"linear"` | `"geometric"` | Shape of the cooling schedule -- note the two engines default to different shapes |
| `beta_range` | `None` | `None` | Inverse-temperature `(start, end)`; `None` auto-computes it from the model's coefficients |
| `seed` | `None` | `None` | Base seed for the on-device RNG |

Every parameter except `algorithm` is overridable per call via
`solve(**kwargs)`. These are the constructor's own defaults.
`xqsa.build_solver`, the
backend-agnostic constructor covered in
[Solving Overview](./#picking-a-backend-by-name), raises
`num_reads` to 200 and `num_sweeps` to 2000 for both GPU engines
instead of using these defaults.

The Metal engine computes coefficients in float32; the CUDA engine computes them
in float64, end to end in its kernels. `result.energy` is always
recomputed afterward in exact integer arithmetic on both, so either
GPU run and a CPU run report the same authoritative energy regardless
of which precision the search itself used. See
[Energy and Precision](energy-and-precision.md) for what the float32
downcast costs the Metal engine and when it matters.

## Driver Prerequisites

| Extra | Hardware | Prerequisite | Verify |
|---|---|---|---|
| `[cuda]` | NVIDIA GPU | CUDA 12.x driver | `nvidia-smi` |
| `[metal]` | Apple Silicon | A Metal device | built in on every Apple Silicon Mac |

After installing, confirm the solver class constructs before building a
model around it:

```sh
python -c "from xqsa import SolverGPU; SolverGPU(engine='cuda'); print('ok')"
python -c "from xqsa import SolverGPU; SolverGPU(engine='metal'); print('ok')"
```

Both raise `ImportError` with a `pip install xqsa[...]` hint if the
extra is missing, and `RuntimeError` if the extra is installed but no
matching GPU is detected -- `import xqsa` itself never fails on a
missing extra, only constructing the solver that needs it does.
