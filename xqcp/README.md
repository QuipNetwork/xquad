# XQCP — X-Quadratic Constraint Programmer

A constraint programming DSL that compiles high-level problem descriptions into three XQVM assembly programs: **encoder**, **verifier**, and **decoder**.

## Overview

Instead of writing `.xqasm` by hand, define your problem in Python using symbolic expressions, loops, and model operations. XQCP records these operations and compiles them into assembly that the XQVM executor can run directly.

```python
from xqcp import Problem, Types
from xqvm_py import XQMXDomain

problem = Problem("MyProblem")

n = problem.input("n", type=Types.Int)
problem.define_model(size=n, domain=XQMXDomain.BINARY)

# ... define objective, constraints, decoder ...

programs = problem.compile()
# programs.encoder   -> str (.xqasm source)
# programs.verifier  -> str
# programs.decoder   -> str
```

## Architecture

XQCP uses a **record-then-compile** pattern:

1. **Record** — DSL calls (`input`, `define_model`, `range`, `stow`, `model.linear[i].add(w)`, etc.) append `Action` objects to an ordered list.
2. **Compile** — The compiler walks the action list and emits assembly for each of the three programs, with automatic register allocation and loop nesting.

### Module Structure

| Module | Purpose |
|--------|---------|
| `problem.py` | `Problem` container, action recording, register allocator |
| `expression.py` | Symbolic expression tree nodes that emit assembly |
| `symbols.py` | Symbolic references: `InputRef`, `LoopVar`, `ModelRef`, `SampleRef`, `OutputRef`, `CoefficientRef`, `LinearProxy`, `QuadraticProxy` |
| `compiler.py` | Three compiler functions: `compile_encoder`, `compile_verifier`, `compile_decoder` |
| `__init__.py` | Public API re-exports and `xq_*` helper functions |

## DSL Reference

### Inputs

Declare runtime inputs with a name and type:

```python
n = problem.input("n", type=Types.Int)           # integer scalar
edges = problem.input("edges", type=Types.Vec)    # integer vector
```

`InputRef` supports arithmetic (`+`, `-`, `*`, `//`, `%`, unary `-`) and vector operations:

```python
edges.get(i)        # VECGET: access element at index i
edges.veclen()      # VECLEN: get vector length
```

### Model

Define the XQMX model the encoder will build:

```python
# 1D model (flat variables)
problem.define_model(size=n, domain=XQMXDomain.BINARY)

# 2D model (grid with rows and columns)
problem.define_model(
    size=n * n, domain=XQMXDomain.BINARY,
    rows=n, cols=n,
)
```

> **Note:** Discrete domain (`XQMXDomain.DISCRETE`) is not yet supported in the CP layer.

### Model Coefficients

Access linear and quadratic coefficients via subscript proxies:

```python
# Linear coefficients
model.linear[i]              # get (returns Expr → GETLINE)
model.linear[i] = weight     # set (→ SETLINE)
model.linear[i].add(weight)  # add (→ ADDLINE)

# Quadratic coefficients
model.quadratic[i, j]              # get (returns Expr → GETQUAD)
model.quadratic[i, j] = weight     # set (→ SETQUAD)
model.quadratic[i, j].add(weight)  # add (→ ADDQUAD)

# 2D models accept tuple coordinates (auto-flattened via IDXGRID)
model.quadratic[(row_a, col_a), (row_b, col_b)].add(weight)
```

### Constraints

```python
# One-hot constraints (2D models)
model.apply_onehot_row(row, penalty=100)    # → ONEHOTR
model.apply_onehot_col(col, penalty=100)    # → ONEHOTC

# Mutual exclusion (variables i and j cannot both be 1)
model.apply_exclude(i, j, penalty=100)      # → EXCLUDE

# Implication (if variable i is 1, variable j must be 1)
model.apply_implies(i, j, penalty=100)      # → IMPLIES

# 2D models accept tuple coordinates for exclude/implies
model.apply_exclude((r1, c1), (r2, c2), penalty=100)
```

### Loops

`problem.range(start, end)` emits a `RANGE` loop:

```python
with problem.range(0, n) as i:
    with problem.range(i + 1, n) as j:
        # body executes for all (i, j) pairs where i < j
```

`problem.iter(vec, start, end)` emits an `ITER` loop over vec elements:

```python
# Always yields (idx, val) — unpack as needed
with problem.iter(distances, 0, n) as (idx, val):
    model.linear[idx].add(val)

with problem.iter(distances, 0, n) as (_, val):
    # discard index, use value only
```

The yielded `LoopVar`s support full arithmetic and can be used as indices, coordinates, or operands.

### Stow

Evaluate an expression and store it in a register for reuse:

```python
dist = problem.stow("dist", edges.get(xq_triu(i, j)))
w = problem.stow("w", edges.get(offset + 2))
```

Returns a `RegLoad` that can be used in subsequent expressions. Pass an existing `RegLoad` to overwrite:

```python
weight = problem.stow("weight", 0)       # allocate new register
problem.stow(weight, dist * 10)           # overwrite same register
```

### Branch

Conditional multi-arm branching with first-match semantics:

```python
problem.branch(
    dist > threshold, lambda: model.linear[i].add(dist * 10),  # if
    dist > 0,         lambda: model.linear[i].add(dist),       # elif
    None,                                                       # else (do nothing)
)
```

- Variadic `(condition, callable)` pairs followed by a mandatory default (callable or `None`)
- Minimum 3 arguments: `(cond, callable, default)`
- Conditions use comparison operators (`>`, `<`, `==`, `>=`, `<=`)

### Outputs

Declare decoder outputs:

```python
tour = problem.output("tour", type=Types.Vec)

with problem.range(0, n) as pos:
    tour.append(problem.sample.colfind(col=pos, value=1))   # VECPUSH
    # or
    tour.append(problem.sample.getline(pos))                 # VECPUSH

# Random-access write/read
tour[i] = value    # VECSET
val = tour[i]      # VECGET
```

### Sample

`problem.sample` provides read access to the solution sample in the decoder:

```python
problem.sample.colfind(col=pos, value=1)   # find row where column has value (2D)
problem.sample.rowfind(row=pos, value=1)   # find col where row has value (2D)
problem.sample.getline(index)               # read variable by index (1D)
problem.sample.rowsum(row)                  # sum all values in row
problem.sample.colsum(col)                  # sum all values in column
```

### Operators

All symbolic types (`InputRef`, `LoopVar`, `RegLoad`, `Literal`) support:

#### Arithmetic

| Operator | Assembly | Notes |
|----------|----------|-------|
| `a + b` | `ADD` | Optimizes to `INC` when adding 1 |
| `a - b` | `SUB` | Optimizes to `DEC` when subtracting 1 |
| `a * b` | `MUL` | |
| `a // b` | `DIV` | Integer division |
| `a % b` | `MOD` | |
| `-a` | `NEG` | Unary negation |

#### Comparison

| Operator | Assembly |
|----------|----------|
| `a == b` | `EQ` |
| `a < b` | `LT` |
| `a > b` | `GT` |
| `a <= b` | `LTE` |
| `a >= b` | `GTE` |

#### Bitwise

| Operator | Assembly |
|----------|----------|
| `a & b` | `BAND` |
| `a \| b` | `BOR` |
| `a ^ b` | `BXOR` |
| `~a` | `BNOT` |
| `a << b` | `SHL` |
| `a >> b` | `SHR` |

### Free Functions

```python
from xqcp import (
    xq_triu, xq_grid, xq_sqr, xq_abs, xq_min, xq_max,
    xq_not, xq_and, xq_or, xq_xor, xq_bnot,
)
```

#### Index Math

| Function | Assembly | Description |
|----------|----------|-------------|
| `xq_triu(i, j)` | `IDXTRIU` | Upper triangular index |
| `xq_grid(row, col, cols)` | `IDXGRID` | Grid flat index (row * cols + col) |

#### Arithmetic

| Function | Assembly | Description |
|----------|----------|-------------|
| `xq_sqr(x)` | `SQR` | Square |
| `xq_abs(x)` | `ABS` | Absolute value |
| `xq_min(a, b)` | `MIN` | Minimum |
| `xq_max(a, b)` | `MAX` | Maximum |

#### Logical (Python keywords `and`/`or`/`not` can't be overloaded)

| Function | Assembly | Description |
|----------|----------|-------------|
| `xq_not(x)` | `NOT` | Logical NOT |
| `xq_and(a, b)` | `AND` | Logical AND |
| `xq_or(a, b)` | `OR` | Logical OR |
| `xq_xor(a, b)` | `XOR` | Logical XOR |
| `xq_bnot(x)` | `BNOT` | Bitwise NOT |

## Compiled Output

`problem.compile()` returns a `CompiledPrograms` dataclass with three `.xqasm` source strings:

- **Encoder** — reads inputs, allocates the XQMX model, emits objective terms and constraints
- **Verifier** — reads model + sample + N, checks validity (onehot or binary domain), computes energy
- **Decoder** — reads sample + N, extracts the solution into output vectors

The verifier automatically selects the right validity check:
- **ROWSUM/COLSUM** loops when onehot constraints are present
- **Binary domain** check (GETLINE + 0-or-1 test) when no onehot constraints exist

## Specification

Authoritative specification: [`../spec/xqcp/SPEC.md`](../spec/xqcp/SPEC.md). The spec is the source of truth; any divergence in this reference implementation is a bug.

## Examples

See the working XQCP programs in top-level runnable examples:

- **TSP**: `examples/tsp/runner.py` — 2D grid model, onehot constraints, COLFIND decoder
- **Max-Cut**: `examples/maxcut/runner.py` — 1D model, edge iteration, GETLINE decoder

Each runner inlines the CP DSL, compiles to XQVM assembly, and runs
the encoder → SA → verifier → decoder pipeline on either the Python
reference VM or the Rust VM (via `--interpreter`).
