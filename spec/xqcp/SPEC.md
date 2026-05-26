# XQCP Technical Specification

## X-Quadratic Constraint Programming

XQCP is a Python-embedded DSL for describing quadratic optimization problems symbolically and compiling them to the XQVM three-program architecture. Instead of writing `.xqasm` assembly by hand, users define problems in Python using symbolic expressions, loops, and model operations. XQCP records these operations and compiles them into assembly.

## Record-Then-Compile Pattern

XQCP uses a deferred compilation model:

1. **Record** -- DSL calls (`input`, `define_model`, `range`, `stow`, `model.linear[i].add(w)`, etc.) append `Action` objects to an ordered list (`Problem._actions`).
2. **Compile** -- `problem.compile()` runs three independent compiler passes over the action list, each emitting a `.xqasm` source string.

No assembly is generated during the recording phase. The action list is a complete, ordered trace of the user's problem definition.

## Three-Program Architecture

Each XQCP program compiles to three independent XQVM programs:

**Encoder** -- transforms problem-specific inputs into an XQMX model suitable for solving.
- Reads runtime inputs (scalars and vectors) from calldata
- Allocates the XQMX model register
- Emits objective terms (linear and quadratic coefficients)
- Emits constraint penalty terms (ONEHOTR, ONEHOTC, EXCLUDE, IMPLIES, etc.)
- Outputs the model on slot 0

**Verifier** -- validates a solution and computes its energy.
- Fixed input layout: model (r0), sample (r1), N (r2)
- Checks validity based on the constraints the encoder applied
- Computes Hamiltonian energy via the `ENERGY` opcode
- Outputs energy (slot 0) and valid flag (slot 1)

**Decoder** -- transforms a solution sample back into problem-specific outputs.
- Fixed input layout: sample (r0), N (r1)
- Extracts human-readable results (tours, partitions, etc.) into output vectors
- Each output is a separate `Vec` on its own output slot

Programs execute independently with no shared state. Communication occurs only through calldata and results.

## Problem Lifecycle

The valid call sequence for constructing a problem:

```
Problem(name) -> inputs* -> define_model() -> body* -> outputs* -> compile()
```

**Phase constraints:**
- `input()` calls must precede `define_model()`
- `define_model()` must be called exactly once
- After `define_model()`, the model is available for coefficient access and constraint application
- `output()` declarations mark the transition to the output/decoder section
- `compile()` partitions accumulated actions retroactively and generates the three programs

### `Problem(name)`

Creates a new problem container.

### `problem.input(name, type)`

Declare a runtime input. `type` is `Types.Int` (scalar) or `Types.Vec` (vector). Returns an `InputRef` bound to a register.

### `problem.define_model(size, domain, rows=None, cols=None)`

Allocate the XQMX model. `size` is the total number of variables. `domain` is `XQMXDomain.BINARY` or `XQMXDomain.SPIN`. For 2D grid models, provide `rows` and `cols`. After this call, `problem.model` and `problem.sample` become available.

`XQMXDomain.DISCRETE` raises `NotImplementedError`.

### `problem.range(start, end)`

Context manager yielding a `LoopVar`. Emits a `RANGE` loop over `[start, end)`.

### `problem.iter(vec, start, end)`

Context manager yielding `(index_var, value_var)` as `LoopVar`s. Emits an `ITER` loop over vector elements.

### `problem.stow(target, expr)`

Evaluate `expr` and store in a register. If `target` is a string, allocates a new register. If `target` is an existing `RegLoad`, overwrites that register. Returns a `RegLoad`.

### `problem.vec()`

Allocate an empty untyped `Vec` register. Returns a `VecRef`.

### `problem.branch(cond1, fn1, cond2, fn2, ..., default)`

Multi-arm conditional with first-match semantics. Variadic `(condition, callable)` pairs followed by a mandatory default (callable or `None`). Minimum 3 arguments.

### `problem.output(name, type)`

Declare a decoder output. Returns an `OutputRef` with `.append(val)` and `[idx]` access.

### `problem.compile()`

Generate the three `.xqasm` programs. Returns a `CompiledPrograms(encoder, verifier, decoder)` dataclass. If the Rust bytecode verifier is available (via `xqffi`), each program is verified before return.

## Compilation Contract

For every well-formed XQCP program:

1. The three emitted `.xqasm` programs execute without error on any XQVM conforming to [../xqvm/SPEC.md](../xqvm/SPEC.md).
2. The encoder outputs a valid XQMX model in MODEL mode.
3. The verifier correctly classifies valid vs invalid samples and computes the correct Hamiltonian energy.
4. The decoder produces correct output vectors for valid samples.

## Error Conditions

| Error | Condition | Source |
|-------|-----------|--------|
| `RuntimeError` | Register overflow (>255 allocated) | Register allocator |
| `RuntimeError` | `input()` called after `define_model()` | `Problem.input()` |
| `RuntimeError` | `problem.model` accessed before `define_model()` | `Problem.model` property |
| `RuntimeError` | `problem.sample` accessed before `define_model()` | `Problem.sample` property |
| `ValueError` | `branch()` with < 3 arguments | `Problem.branch()` |
| `ValueError` | `branch()` with even number of arguments | `Problem.branch()` |
| `TypeError` | Non-`Expr`, non-`int` value in expression position | `coerce()` |
| `TypeError` | `.get()` on non-Vec `InputRef` | `InputRef.get()` |
| `TypeError` | `.veclen()` on non-Vec `InputRef` | `InputRef.veclen()` |
| `TypeError` | `.append()` on non-Vec `OutputRef` | `OutputRef.append()` |
| `NotImplementedError` | `define_model()` with `XQMXDomain.DISCRETE` | `Problem.define_model()` |

## Cross-References

- XQVM specification: [../xqvm/SPEC.md](../xqvm/SPEC.md) -- machine architecture, type system, opcode semantics
- XQVM high-level functions: [../xqvm/HLF.md](../xqvm/HLF.md) -- constraint opcode expansion formulas
- XQVM instruction set: [../xqvm/ISA.md](../xqvm/ISA.md) -- full opcode reference
- XQSA specification: [../xqsa/SPEC.md](../xqsa/SPEC.md) -- solver interface that consumes the encoder's output model
