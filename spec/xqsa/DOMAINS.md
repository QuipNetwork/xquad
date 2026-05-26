# Domain Support and Sample Encoding

## Domain Matrix

| Domain | Enum | Variable values | Sample default | Model allocator | Solver support |
|--------|------|-----------------|----------------|-----------------|----------------|
| Binary | `XQMXDomain.BINARY` | `{0, 1}` | `0` | `BQMX` | Supported |
| Spin | `XQMXDomain.SPIN` | `{-1, +1}` | `-1` | `SQMX` | Supported |
| Discrete | `XQMXDomain.DISCRETE` | `{0, 1, ..., k-1}` | `0` | -- | Reserved |

**Binary (QUBO):** variables take values 0 or 1. The standard domain for Quadratic Unconstrained Binary Optimization. All solvers must support this domain.

**Spin (Ising):** variables take values -1 or +1. Maps directly to physical qubits on quantum annealers. All solvers must support this domain.

**Discrete (reserved):** variables take values 0 through `k-1`, where `k` is the model's `discrete_k` parameter. This domain is defined at the XQMX type level and the encoding semantics are specified here, but no solver currently supports it. `_validate_model()` rejects DISCRETE with `ValueError`. Future solver implementations may add support without changing this specification -- they need only relax the validation check.

## Sample Encoding

A sample is an XQMX in SAMPLE mode. Variable assignments are stored in the `linear` sparse dictionary:

```python
sample.linear: dict[int, int]   # variable_index -> assignment_value
```

**Construction:**
- Binary: `XQMX.binary_sample(size, rows=0, cols=0)` -- all variables default to 0
- Spin: `XQMX.spin_sample(size, rows=0, cols=0)` -- all variables default to -1
- Discrete: `XQMX.discrete_sample(size, k, rows=0, cols=0)` -- all variables default to 0

**Access:**
- `sample.get_linear(i)` -- returns the assignment for variable `i`, or the domain default if unset
- `sample.set_linear(i, value)` -- sets the assignment for variable `i`

Variable indices range from `0` to `size - 1`. Out-of-range access raises `IndexError`.

**Domain validation:** the spec does not require solvers to validate that returned variable assignments are within the domain's value set. The verifier program handles constraint validation independently.

## Grid Metadata

XQMX models can have optional `rows` and `cols` dimensions for 2D grid layout:

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
| Model in SAMPLE mode | `_validate_model()` raises `ValueError` |
| Hardware failure | `solve()` raises implementation-specific exception |
| No sample produced | `solve()` raises implementation-specific exception |
| Bad sample (high energy, constraint violations) | `solve()` returns `SolverResult` normally; verifier's `valid` flag distinguishes good from bad |

The key distinction: solver failures (hardware, embedding, timeout with no result) are exceptions. Bad-quality solutions are valid `SolverResult` values -- the verifier handles quality assessment.
