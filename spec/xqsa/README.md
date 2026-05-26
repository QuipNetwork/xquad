# XQSA Specification

Authoritative specification for X-Quadratic Solver Adapters. The spec is split across focused documents; each is the single source of truth for its topic.

## Documents

| File | Content |
|------|---------|
| [SPEC.md](SPEC.md) | Architecture overview, pipeline position, plugin model |
| [INTERFACE.md](INTERFACE.md) | `Backend` abstract class, `solve()` contract, `SolverResult` type, parameter conventions |
| [ENERGY.md](ENERGY.md) | Energy computation formula, precision contract, sparse representation |
| [DOMAINS.md](DOMAINS.md) | Domain support matrix, sample encoding, grid metadata, capability negotiation |
| [SOLVERS.md](SOLVERS.md) | Solver registry, naming convention, algorithm families, dependency model |

## Reference implementation

[`../../xqsa/`](../../xqsa/) -- Python package. v0.2.0 implements `NealBackend` wrapping `dwave-neal` simulated annealing.

## Related specifications

- [XQVM](../xqvm/README.md) -- the virtual machine that executes encoder, verifier, and decoder programs around the solver
- [XQCP](../xqcp/README.md) -- the constraint-programming DSL that compiles problems into XQVM programs and XQMX models
