# Solver Registry

## Naming Convention

Concrete solver classes follow the pattern `Solver{Vendor/Technology}{ComputeTarget}`. Vendor-first grouping keeps related solvers together in sorted order.

v0.2.0 uses the legacy names `Backend` (abstract) and `NealBackend` (concrete). The rename to `Solver` / `SolverDWaveCPU` lands in v0.3.0.

## Algorithm Families

### D-Wave SDK Family

Solvers that use the `dimod` BQM format and D-Wave SDK packages. The SDK handles model conversion and sampling; solvers differ in where the BQM is solved (CPU, GPU, QPU).

Shared base helpers: `_model_to_bqm()`, `_sample_to_xqmx()` (lifted to the base class in v0.3.0).

### Custom Kernel Family

Custom GPU kernels (CUDA `.cu` / Metal `.metal`) bypassing the D-Wave SDK for the compute step. Higher performance ceiling, more code to maintain.

Algorithm selection is a configuration option on the solver class via a `strategy` parameter (e.g. `"sa"`, `"gibbs"`, `"metropolis"`), not separate solver classes. This matches the convention from the quip-protocol codebase.

### Gate-Based QAOA Family

Variational quantum circuits via Qiskit. Non-D-Wave; entirely different algorithmic approach (parameterised circuits + classical optimizer). IBM and IonQ are separate solvers because they use different Qiskit provider packages.

## Dependency Model

Each solver beyond the base `SolverDWaveCPU` lives behind an optional extra. Import guards are lazy (inside `__init__`, not at module level) so `import xqsa` never fails.

- `ImportError` with a `pip install xqsa[extra]` hint when the required extra is not installed
- `RuntimeError` when required hardware is not detected (GPU, QPU)

## Solver Registry

| Solver | File | Extra | Hardware | Added in |
|--------|------|-------|----------|----------|
| `SolverDWaveCPU` | `dwave_cpu.py` | (base) | CPU | v0.2.0 (as `NealBackend`) |
| `SolverDWaveQPU` | `dwave_qpu.py` | `[dwave]` | D-Wave QPU (cloud) | v0.3.0 |
| `SolverDWaveGPU` | `dwave_gpu.py` | `[gpu]` | NVIDIA CUDA GPU | v0.3.0 |
| `SolverCudaGPU` | `cuda_gpu.py` | `[cuda]` | NVIDIA CUDA GPU | v0.3.0 |
| `SolverMetalGPU` | `metal_gpu.py` | `[metal]` | Apple Metal GPU | v0.3.0 |
| `SolverIBMQAOA` | -- | `[ibm]` | IBM QPU / AerSimulator | v0.3.0 |
| `SolverIonQQAOA` | -- | `[ionq]` | IonQ trapped-ion QPU | v0.3.0 |

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
| GPU SA (dwave-samplers + CUDA) | `SolverDWaveGPU` (no protocol miner equivalent) |
