# XQSA Technical Specification

## X-Quadratic Solver Adapters

XQSA defines the interface between the XQVM toolchain and external quadratic optimization solvers. The goal is a single contract so that any solver -- simulated annealing, quantum annealing, gradient-based, or gate-based QAOA -- can be plugged into the XQVM execution pipeline without touching the VM.

## Pipeline Position

The solver sits between the encoder and verifier stages of the XQVM three-program architecture:

```
XQCP program
    |
    v
  compile()
    |
    v
Encoder (.xqasm)
    |  XQVM executes encoder
    v
XQMX model
    |
    v
XQSA solver.solve(model) --> SolverResult(sample, energy, timing, metadata)
    |
    v
Verifier (.xqasm)
    |  XQVM executes verifier(encoder inputs..., model, sample)
    v
(energy, valid)
    |
    v
Decoder (.xqasm)
    |  XQVM executes decoder(sample, N)
    v
Output vectors
```

The solver receives an `XqmxModel` and returns an `XqmxSample` (both `xqffi.vm` types). The verifier independently validates the sample and recomputes energy via the XQVM `ENERGY` opcode. The decoder extracts human-readable output from valid samples.

## Plugin Architecture

XQSA uses a class-based plugin model:

- A `Solver` base class defines the contract (see [INTERFACE.md](INTERFACE.md)); its `solve()` is a template shared by every solver
- Each solver composes an `Implementation` that turns a model into a sample; swapping the implementation changes how a solver runs, not its API
- A new backend drops in as a new implementation behind an existing solver class, or as a new `Solver` subclass that picks one
- The contract is minimal: validate the model, sample it, return the result with the authoritative energy and `{seed, reads, params}` metadata

The base package ships `SolverCPU`. `SolverGPU` (CUDA or Metal) and `SolverDWave` need the `[cuda]`, `[metal]` or `[dwave]` extra, and `SolverQuip` the `[quip]` extra. See [SOLVERS.md](SOLVERS.md) for the registry.

## Package Structure

| Module | Purpose |
|--------|---------|
| `solver.py` | `Solver` base class and its `solve()` template, `Implementation`, `RunOutput`, `SolverResult`, model validation, BQM conversion helpers |
| `solvers.py` | The public `SolverCPU`, `SolverGPU` and `SolverDWave` classes |
| `inprocess/cpu.py` | `InProcessCPU` wrapping `dwave-samplers` |
| `inprocess/cuda.py` | `InProcessCuda`, custom CUDA kernels through CuPy |
| `inprocess/metal.py` | `InProcessMetal`, custom Metal kernels through `pyobjc-framework-Metal` |
| `inprocess/dwave.py` | `InProcessDWave` wrapping D-Wave Leap QPU access through `dwave-system` |
| `quip/` | `SolverQuip`, the Quip Network mempool solver, and its order, receipt and codec types |
| `registry.py` | `SOLVERS`, `DEFAULT_SOLVER` and `build_solver` |

## Thread Safety

`solve()` is not required to be thread-safe. Callers must not call `solve()` concurrently on the same instance. Implementations may hold mutable device state (GPU contexts, QPU sessions). Concurrent use requires separate instances.

## Cross-References

- XQVM specification: [../xqvm/SPEC.md](../xqvm/SPEC.md) -- machine architecture, type system, `ENERGY` opcode
- XQVM high-level functions: [../xqvm/HLF.md](../xqvm/HLF.md) -- ENERGY computation formula
- XQCP specification: [../xqcp/SPEC.md](../xqcp/SPEC.md) -- constraint-programming DSL that produces the models solvers consume
