# Allocators

Every problem starts here: allocate a `Model` to accumulate coefficients into,
or a `Sample` to hold a candidate assignment, or a `Vec` to stage indices and
weights before a high-level constraint call. Byte values, operand layouts and
stack effects for this family are in the
[Allocators](../opcodes.md#allocators) and
[Vector Operations](../opcodes.md#vector-operations) sections of the opcode
reference; this page is about the three domains and when each applies.

## Model Allocators

`BQMX` and `SQMX` each pop a variable count, `size`, and write a fresh
`XqmxModel` into a register, with empty linear and quadratic coefficient
maps. `XQMX` pops two values, `k` (top of stack) then `size`, since an
integer model also needs the per-variable domain width. The model holds
the Hamiltonian being built up:

$$H(x) = \sum_i \text{linear}[i] \cdot x_i + \sum_{i \le j} \text{quadratic}[i, j] \cdot x_i \cdot x_j$$

which [Coefficient Access](coefficient-access.md) instructions populate one
term at a time. What differs between the three allocators is the domain the
variables \\(x_i\\) are drawn from, and that choice is a problem-modelling
decision, not an implementation detail:

- **`BQMX`** allocates a QUBO model: variables are binary, \\(x_i \in \\{0,
  1\\}\\). This is the domain most combinatorial-optimisation formulations
  target directly (selection, assignment, one-hot encodings), and the one
  most solver backends consume without translation.
- **`SQMX`** allocates an Ising model: variables are spins, \\(x_i \in
  \\{-1, 1\\}\\). Physically motivated (each variable is a magnetic moment
  pointing up or down), and the native representation for quantum
  annealers, which minimise an Ising Hamiltonian directly. Allocate the
  domain the target backend expects rather than assuming a QUBO model can
  be handed to an Ising-only backend unchanged.
- **`XQMX`** allocates an integer model: variables take one of \\(k\\)
  values, \\(x_i \in \\{0, 1, \ldots, k{-}1\\}\\). \\(k\\) is a count and
  not a half-width. This generalises past binary and spin to give a
  variable more than two states directly, suited to a quantity with a
  natural ordering or magnitude -- a position in a small enumerated set --
  without one-hot-encoding it into several binary variables first. It does
  not encode an unordered categorical choice such as a colour: a quadratic
  form over integer variables cannot express that two values merely differ
  without also expressing by how much. `XQMX` errors with `InvalidIntegerK`
  when \\(k < 2\\), since a domain needs at least two values to carry a
  decision: \\(k = 1\\) leaves the single value \\(\\{0\\}\\), which is a
  constant rather than a variable.

## Sample Allocators

`BSMX`, `SSMX` and `XSMX` mirror the three model allocators, including the
same pop count per opcode -- `BSMX` and `SSMX` pop only `size`; `XSMX` pops
`k` then `size`, the same order as `XQMX` -- but write an `XqmxSample`: a
vector of per-variable assignments in the same domain, rather than a
coefficient map. A sample is what a solver returns, or what `ENERGY`
evaluates against a model to score a candidate solution.

The default assignment differs by domain and is chosen so it is always
in-domain without a special case: binary and integer samples default every
variable to \\(0\\), and spin samples default every variable to \\(-1\\)
(spin-down), since \\(0\\) is not a member of \\(\\{-1, 1\\}\\). The integer
default of \\(0\\) is always valid because the domain
\\(\\{0, \ldots, k{-}1\\}\\) starts at zero for every \\(k \ge 2\\).

Those defaults are not merely conventional. `SETLINE` and `ADDLINE` check
every write into a sample against its domain and raise `SampleOutOfDomain`
otherwise, so a freshly allocated sample has to be in-domain from the start
or the first read of an untouched variable would return a value the same
register could not have been written.

## Vec Allocators

`VECI` creates an empty `VecInt` and `VECX` creates an empty `VecXqmx` (a
vector of models, used to batch several sub-models together). `VEC` is an
untyped convenience form: at the bytecode level it produces exactly the same
`VecInt` as `VECI`, so `VEC r0` and `VECI r0` are interchangeable. Prefer
`VECI` in generated or reviewed bytecode, where being explicit about the
element type documents intent; `VEC` reads naturally in hand-written
assembly where the type is obvious from what gets pushed next.

## Domain Types

| Domain | Variable values | Model | Sample |
|--------|----------------|-------|--------|
| Binary | \\(\\{0, 1\\}\\) | `BQMX` | `BSMX` |
| Spin | \\(\\{-1, 1\\}\\) | `SQMX` | `SSMX` |
| Integer(\\(k\\)) | \\(\\{0, 1, \ldots, k{-}1\\}\\), \\(k \ge 2\\) | `XQMX` | `XSMX` |

## Example

```asm
PUSH 4
PUSH 1
XQMX r0     ; errors: InvalidIntegerK, k = 1 is not >= 2
```

`XQMX` and `XSMX` both check `k` only after popping both operands, so the
stack is consumed either way; on `k < 2` they fault `InvalidIntegerK`
with the message `XQMX/XSMX requires k >= 2 for the {0, ..., k-1} domain,
got k = 1`.
