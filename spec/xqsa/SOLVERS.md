# Solver Registry

## Naming Convention

Concrete solver classes follow the pattern `Solver{Vendor/Technology}{ComputeTarget}`. Vendor-first grouping keeps related solvers together in sorted order.

v0.3.0 uses `Solver` (abstract) with `SolverDWaveCPU` and `SolverCudaGPU` (concrete).

## Algorithm Families

### D-Wave SDK Family

Solvers that use the `dimod` BQM format and D-Wave SDK packages. The SDK handles model conversion and sampling; solvers differ in where the BQM is solved (CPU, QPU).

Shared base helpers: `_model_to_bqm()`, `_sample_to_xqmx()` (concrete methods on the `Solver` base class).

### Custom Kernel Family

Custom GPU kernels (CUDA `.cu` / Metal `.metal`) bypassing the D-Wave SDK for the compute step. Higher performance ceiling, more code to maintain.

Algorithm selection is a configuration option on the solver class via a `strategy` parameter (e.g. `"sa"`, `"gibbs"`, `"metropolis"`), not separate solver classes. This matches the convention from the quip-protocol codebase.

`SolverCudaGPU` is the first solver in this family, using CuPy RawKernel for custom CUDA C++ kernels. It converts XQMX models directly to dense GPU arrays (skipping the BQM intermediate) and runs parallel-replica simulated annealing with Metropolis acceptance.

### Gate-Based QAOA Family

Variational quantum circuits via Qiskit. Non-D-Wave; entirely different algorithmic approach (parameterised circuits + classical optimizer). IBM and IonQ are separate solvers because they use different Qiskit provider packages.

## Dependency Model

Each solver beyond the base `SolverDWaveCPU` lives behind an optional extra. Import guards are lazy (inside `__init__`, not at module level) so `import xqsa` never fails.

- `ImportError` with a `pip install xqsa[extra]` hint when the required extra is not installed
- `RuntimeError` when required hardware is not detected (GPU, QPU)

## Solver Registry

| Solver | File | Extra | Hardware | Status |
|--------|------|-------|----------|--------|
| `SolverDWaveCPU` | `dwave_cpu.py` | (base) | CPU | implemented |
| `SolverCudaGPU` | `cuda_gpu.py` | `[cuda]` | NVIDIA CUDA GPU | implemented |
| `SolverDWaveQPU` | `dwave_qpu.py` | `[dwave]` | D-Wave QPU (cloud) | planned |
| `SolverMetalGPU` | `metal_gpu.py` | `[metal]` | Apple Metal GPU | planned |
| `SolverIBMQAOA` | -- | `[ibm]` | IBM QPU / AerSimulator | planned |
| `SolverIonQQAOA` | -- | `[ionq]` | IonQ trapped-ion QPU | planned |

## Protocol Miner Equivalence

Mapping between `quip-protocol` miner config keys and xqsa solver classes:

| Protocol miner | xqsa solver |
|----------------|-------------|
| `cpu` | `SolverDWaveCPU` |
| `qpu` | `SolverDWaveQPU` |
| `cuda` | `SolverCudaGPU(strategy="sa")` |
| `cuda-gibbs` | `SolverCudaGPU(strategy="gibbs")` |
| `metal` | `SolverMetalGPU(strategy="sa")` |
| IBM QAOA (feature branch) | `SolverIBMQAOA` |
| IonQ QAOA (feature branch) | `SolverIonQQAOA` |
| `modal` | No xqsa equivalent (orchestration layer, not a solver algorithm) |
