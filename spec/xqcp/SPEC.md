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
- Input layout: the encoder's own inputs in declaration order, then the model, then the sample
- Replays the encoder's action stream to rebuild the constraint data, which lives in registers at VM runtime rather than in the model
- Emits one check per constraint the encoder applied, plus a domain check over every declared variable
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

### `problem.define_model(size, domain, rows=None, cols=None, *, k=None, lo=None, hi=None, penalty=None)`

Allocate the XQMX model. `size` is the total number of variables. `domain` is a `Domain` member, or the `XQMXDomain` it wraps, which is normalised on entry. For 2D grid models, provide both `rows` and `cols` -- providing exactly one raises `ValueError`. After this call, `problem.model` and `problem.sample` become available.

`Domain` is xqcp's own enum. `BINARY`, `SPIN` and `INTEGER` wrap their `XQMXDomain` counterpart; `CATEGORICAL` has none, because the VM cannot allocate one. The forms:

| Form | Allocates | Read back with |
|------|-----------|----------------|
| `Domain.BINARY` | `size` variables over {0, 1}, via `BQMX` | `sample.getline(i)` |
| `Domain.SPIN` | `size` variables over {-1, +1}, via `SQMX` | `sample.getline(i)` |
| `Domain.INTEGER, k=` | `size` variables over {0, ..., k-1}, via `XQMX` | `sample.getline(i)` |
| `Domain.INTEGER, lo=, hi=` | `size` variables over {0, ..., hi-lo}, via `XQMX` | `sample.value(i)` |
| `Domain.CATEGORICAL, k=, penalty=` | a `size` x `k` binary grid, one `ONEHOTR` per row, via `BQMX` | `sample.case(v)` |

`size`, `k`, `lo` and `hi` are expressions, so any of them may come from calldata. A literal `k` below 2, or a literal `hi` below `lo + 1`, is rejected here; an expression is left to the VM's `InvalidIntegerK`.

**The ranged form shifts.** Coefficients are written over `x` in `[lo, hi]` while the model holds `y = x - lo`. Each `quadratic[i, j].add(w)` therefore also records `w*lo` against the linear coefficient of both `i` and `j`, which is what substituting `x = y + lo` produces. A write to the diagonal lands both corrections on the one index, giving the `2*w*lo` that squaring asks for. Linear writes need no correction.

The constant that substitution also produces is dropped, because XQMX has no offset field. Energies shift uniformly across assignments, so argmin is exact. `EQUALITY` drops its own `P*b^2` on the same terms; see [../xqvm/HLF.md](../xqvm/HLF.md).

**Setting is refused on a shifted model, on both proxies.** `quadratic[i, j] = w` and `linear[i] = w` both raise `ValueError`; use `.add(w)`. Setting replaces a coefficient while the corrections can only accumulate, so a quadratic set would leave a repeated pair disagreeing with its corrections, and a linear set would drop whatever corrections earlier quadratic writes had left on that index. Accumulating loses nothing: a coefficient starts at zero, so `.add(w)` on an index nothing has written is `= w`.

A runtime `lo` and a decoder loop bound compete for the decoder's single calldata scalar, and `compile()` raises naming both. Literal bounds avoid it.

**The categorical form is a recording-time macro.** It calls `define_model(size * k, Domain.BINARY, rows=size, cols=k)` and applies `ONEHOTR` to each row, through the record paths a hand-written version would use. `model.domain` reads `XQMXDomain.BINARY` afterwards and no categorical marker is kept, so coefficient access is `(variable, case)` as on any 2D model.

**Constraints are binary-only.** Every constraint kind is refused on a spin or integer model; only coefficient writes are supported. Each expansion in [../xqvm/HLF.md](../xqvm/HLF.md) is derived under `x^2 = x`, which holds for 0/1 variables alone, so off a binary model the encoder would build a penalty that does not encode the constraint written. Whether a correct per-domain expansion exists is an open VM-side question.

### `problem.sample`

The model's counterpart, bound by `define_model()`. Readers:

| Reader | Returns |
|--------|---------|
| `getline(i)` | variable `i` exactly as the model stores it |
| `value(i)` | variable `i` in the domain it was declared over: `getline(i) + lo` on a ranged model, `getline(i)` otherwise |
| `case(v)` | the column holding the 1 in row `v`, or `-1` if that row is empty |
| `rowfind(r, x)` / `colfind(c, x)` | the first column or row matching `x`, or `-1` (2D models) |
| `rowsum(r)` / `colsum(c)` | the sum of a row or column (2D models) |

`getline()` is the only raw reader and `value()` the only shifted one; nothing shifts implicitly. `case(v)` is `rowfind(v, 1)` and means the same on any 2D binary grid, whether or not `Domain.CATEGORICAL` built it.

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

### `problem.output(name, type=Types.Vec)`

Declare a decoder output. `type` must be `Types.Vec` -- every pipeline output is a vector. Returns an `OutputRef` whose only fill operation is `.append(val)`. Outputs are write-only: indexed read (`out[i]`) and indexed write (`out[i] = value`) are both rejected.

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
| `ValueError` | `define_model()` with exactly one of `rows=` / `cols=` set | `Problem.define_model()` |
| `ValueError` | `apply_onehot_row()` on a flat (non-2D) model | `ModelRef.apply_onehot_row()` |
| `ValueError` | `apply_onehot_col()` on a flat (non-2D) model | `ModelRef.apply_onehot_col()` |
| `ValueError` | `colfind()` on a flat (non-2D) model | `SampleRef.colfind()` |
| `ValueError` | `rowfind()` on a flat (non-2D) model | `SampleRef.rowfind()` |
| `ValueError` | `rowsum()` on a flat (non-2D) model | `SampleRef.rowsum()` |
| `ValueError` | `colsum()` on a flat (non-2D) model | `SampleRef.colsum()` |
| `TypeError` | Non-`Expr`, non-`int` value in expression position | `coerce()` |
| `TypeError` | `bool` value in expression position | `coerce()` |
| `TypeError` | `.get()` on non-Vec `InputRef` | `InputRef.get()` |
| `TypeError` | `.veclen()` on non-Vec `InputRef` | `InputRef.veclen()` |
| `TypeError` | `a != b` on an XQCP expression | `_ExprOps.__ne__()` |
| `TypeError` | XQCP expression in a boolean context (`and`, `or`, `not`, `if`) | `_ExprOps.__bool__()` |
| `TypeError` | Indexed read `out[i]` on an `OutputRef` | `OutputRef.__getitem__()` |
| `TypeError` | Indexed write `out[i] = value` on an `OutputRef` | `OutputRef.__setitem__()` |
| `TypeError` | `problem.output()` with a `type` other than `Types.Vec` | `Problem.output()` |
| `ValueError` | `k=`, `lo=`, `hi=` or `penalty=` with `Domain.BINARY` or `Domain.SPIN` | `Problem.define_model()` |
| `ValueError` | `penalty=` with `Domain.INTEGER` | `Problem.define_model()` |
| `ValueError` | `Domain.INTEGER` with neither `k=` nor `lo=`/`hi=` | `Problem.define_model()` |
| `ValueError` | `k=` given together with `lo=` or `hi=` | `Problem.define_model()` |
| `ValueError` | `lo=` without `hi=`, or `hi=` without `lo=` | `Problem.define_model()` |
| `ValueError` | Literal `k=` below 2, or literal `hi=` below `lo= + 1` | `Problem.define_model()` |
| `ValueError` | `Domain.CATEGORICAL` without `k=` or without `penalty=` | `Problem.define_model()` |
| `ValueError` | `Domain.CATEGORICAL` with `rows=`, `cols=`, `lo=` or `hi=` | `Problem.define_model()` |
| `ValueError` | `quadratic[i, j] = w` on a ranged integer model | `QuadraticProxy.__setitem__()` |
| `ValueError` | `linear[i] = w` on a ranged integer model | `LinearProxy.__setitem__()` |
| `RuntimeError` | Any constraint applied to a spin or integer model | `compile()` |
| `RuntimeError` | `branch()` inside a decoder output block | `compile()` |
| `RuntimeError` | `iter()` inside a decoder output block | `compile()` |
| `RuntimeError` | Any other action recorded after the first `output()` | `compile()` |
| `RuntimeError` | `problem.output()` declared inside a `range()` block | `compile()` |
| `RuntimeError` | `.append()` to an output declared before the current one | `compile()` |
| `RuntimeError` | A second, distinct scalar referenced in a decoder block, such as a runtime `lo=` meeting an output loop bound | `compile()` |
| `RuntimeError` | A vector or model coefficient read in a decoder block | `compile()` |
| `RuntimeError` | A loop variable read outside its own loop, in a decoder block | `compile()` |

## Cross-References

- XQVM specification: [../xqvm/SPEC.md](../xqvm/SPEC.md) -- machine architecture, type system, opcode semantics
- XQVM high-level functions: [../xqvm/HLF.md](../xqvm/HLF.md) -- constraint opcode expansion formulas
- XQVM instruction set: [../xqvm/ISA.md](../xqvm/ISA.md) -- full opcode reference
- XQSA specification: [../xqsa/SPEC.md](../xqsa/SPEC.md) -- solver interface that consumes the encoder's output model
