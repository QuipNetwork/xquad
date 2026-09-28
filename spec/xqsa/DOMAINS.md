# Domain Support and Sample Encoding

## Domain Matrix

| Domain | `xqffi.vm.Domain` | Variable values | Sample default | Model allocator | Solver support |
|--------|-------------------|-----------------|----------------|-----------------|----------------|
| Binary | `Domain.BINARY` | `{0, 1}` | `0` | `BQMX` | Supported |
| Spin | `Domain.SPIN` | `{-1, +1}` | `-1` | `SQMX` | Supported |
| Integer | `Domain.integer(k)` | `{0, 1, ..., k-1}` | `0` | `XQMX` | Reserved |

`Domain` compares by value, so `model.domain == Domain.BINARY` and `Domain.integer(3) == Domain.integer(3)` hold. `xquad.types` re-exports it.

**Binary (QUBO):** variables take values 0 or 1. The standard domain for Quadratic Unconstrained Binary Optimization. All solvers must support this domain.

**Spin (Ising):** variables take values -1 or +1. Maps directly to physical qubits on quantum annealers. All solvers must support this domain.

**Integer (reserved):** variables take values in `{0, ..., k-1}`, which lets an integer model lower into binary without an encoder-side shift. `k` is the model's `k` (`model.k`, or `model.domain.k`) and is the number of values the domain holds rather than a half-width. `spec/xqvm/SPEC.md` and `spec/xqvm/ISA.md` state the same domain. This domain is defined at the `XqmxModel` type level and the encoding semantics are specified here, but no solver currently supports it. `_validate_model()` rejects INTEGER with `ValueError`. Future solver implementations may add support without changing this specification -- they need only relax the validation check.

## Sample Encoding

A sample is an `xqffi.vm.XqmxSample`: a dense list of one assignment per variable, readable as `sample.values`. Unlike a model it is not sparse, so every variable holds a value.

**Construction:**
- `XqmxSample.default(domain, size, rows=0, cols=0)` -- every variable at the domain default: 0 for binary and integer, -1 for spin. The same value the `BSMX`, `SSMX` and `XSMX` allocators write. Raises `InvalidAllocation` when `size` exceeds `MAX_ALLOCATION_SIZE`.
- `XqmxSample(domain, values, rows=0, cols=0)`, or `XqmxSample.binary(values, ...)`, `XqmxSample.spin(values, ...)`, `XqmxSample.integer(values, k, ...)` -- from explicit values.

**Access:**
- `sample.get_linear(i)` -- the assignment of variable `i`
- `sample.set_linear(i, value)` -- sets the assignment of variable `i`

Variable indices range from `0` to `size - 1`. Out-of-range access raises `IndexOutOfBounds`.

**Domain validation:** a sample cannot hold an out-of-domain value. Every constructor and `set_linear` raise `ValueError` for one and leave the sample unchanged. Solvers build their result through `_sample_to_xqmx()`, which writes each assignment with `set_linear`, so a backend that returns an assignment outside the domain fails in `solve()` rather than producing a `SolverResult`. Inside the VM the same rule is `SampleOutOfDomain`, raised by `SETLINE` and `ADDLINE`. The verifier program handles constraint validation independently.

## Grid Metadata

Models and samples can have optional `rows` and `cols` dimensions for 2D grid layout:

- `rows` and `cols` are set to 0 by default (no grid structure)
- When set, variables are addressed in row-major order: variable at `(row, col)` has index `row * cols + col`
- Grid dimensions must be preserved through the solve pipeline: `sample.rows == model.rows` and `sample.cols == model.cols`
- Grid metadata is informational -- it does not affect energy computation

The solver constructs the return sample with the same grid dimensions as the input model.

## Capability Negotiation

v0.2.0 uses a validate-or-reject model: `_validate_model()` accepts or raises `ValueError`. There is no introspection API for querying supported domains, problem-size limits, or hardware constraints.

Solvers that impose additional constraints beyond domain validation (e.g. GPU memory limits, qubit topology restrictions) should document them and raise descriptive exceptions from `solve()`.

## Failure Modes

| Scenario | Behaviour |
|----------|-----------|
| Unsupported domain | `_validate_model()` raises `ValueError` |
| Not an `XqmxModel` (a sample, for instance) | `_validate_model()` raises `ValueError` |
| Hardware failure | `solve()` raises implementation-specific exception |
| No sample produced | `solve()` raises implementation-specific exception |
| Bad sample (high energy, constraint violations) | `solve()` returns `SolverResult` normally; the verifier's `valid` flag reports feasibility and its `energy` output reports quality |

The key distinction: solver failures (hardware, embedding, timeout with no result) are exceptions. Bad-quality solutions are valid `SolverResult` values -- the verifier handles quality assessment.
