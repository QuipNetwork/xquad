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
    |  XQVM executes verifier(model, sample, N)
    v
(energy, valid)
    |
    v
Decoder (.xqasm)
    |  XQVM executes decoder(sample, N)
    v
Output vectors
```

The solver receives an XQMX in MODEL mode and returns an XQMX in SAMPLE mode. The verifier independently validates the sample and recomputes energy via the XQVM `ENERGY` opcode. The decoder extracts human-readable output from valid samples.

## Plugin Architecture

XQSA uses a class-based plugin model:

- An abstract `Solver` class defines the contract (see [INTERFACE.md](INTERFACE.md))
- Concrete implementations wrap specific solvers
- Any class implementing `Solver.solve()` can drop in as a replacement
- The contract is minimal: validate the model, solve it, return the result

v0.3.0 ships `SolverDWaveCPU` in the base package and `SolverDWaveQPU`
behind the optional `[dwave]` extra. Additional solvers are in progress; see
[SOLVERS.md](SOLVERS.md) for the planned registry.

## Package Structure (v0.3.0)

| Module | Purpose |
|--------|---------|
| `solver.py` | Abstract `Solver` class, `SolverResult` dataclass, model validation, BQM conversion helpers |
| `dwave_cpu.py` | `SolverDWaveCPU` wrapping `dwave-samplers` |
| `dwave_qpu.py` | `SolverDWaveQPU` wrapping D-Wave Leap QPU access through `dwave-system` |

## Thread Safety

`solve()` is not required to be thread-safe. Callers must not call `solve()` concurrently on the same instance. Implementations may hold mutable device state (GPU contexts, QPU sessions). Concurrent use requires separate instances.

## Cross-References

- XQVM specification: [../xqvm/SPEC.md](../xqvm/SPEC.md) -- machine architecture, type system, `ENERGY` opcode
- XQVM high-level functions: [../xqvm/HLF.md](../xqvm/HLF.md) -- ENERGY computation formula
- XQCP specification: [../xqcp/SPEC.md](../xqcp/SPEC.md) -- constraint-programming DSL that produces the models solvers consume
