# Type System and Expressions

## Core Value Types

XQCP exposes two value types to the user:

| Type | Enum | Description |
|------|------|-------------|
| `Types.Int` | `"int"` | Scalar integer |
| `Types.Vec` | `"vec"` | Vector of integers |

These correspond to the XQVM register types `int` and `vec`. The XQMX type is implicit -- created by `define_model()`, not declared by the user.

## Symbolic Reference Types

DSL calls return symbolic references that build expression trees when combined with operators. Each reference type is bound to a register and emits assembly when compiled.

| Type | Created by | Holds | Register type |
|------|-----------|-------|---------------|
| `InputRef` | `problem.input(name, type)` | Runtime input | `int` or `vec` |
| `LoopVar` | `problem.range()` / `problem.iter()` | Loop iteration value | `int` |
| `RegLoad` | `problem.stow(target, expr)` | Evaluated expression | `int` |
| `ModelRef` | `problem.define_model()` | XQMX model register | `xqmx` |
| `SampleRef` | (implicit, from `define_model`) | XQMX sample register | `xqmx` |
| `OutputRef` | `problem.output(name, type)` | Decoder output | `vec` |
| `CoefficientRef` | `model.linear[i]` / `model.quadratic[i,j]` | Proxy for a single coefficient | (proxy, no register) |
| `VecRef` | `problem.vec()` | Allocated vector register | `vec` |

### InputRef

Supports arithmetic operators and, for `Vec` type:
- `.get(index)` -- emits `VECGET`, returns expression for element at index
- `.veclen()` -- emits `VECLEN`, returns expression for vector length

Raises `TypeError` if `.get()` or `.veclen()` is called on an `Int` input.

### LoopVar

Supports full arithmetic and all operators. Can be used as an index, coordinate, or operand anywhere an expression is expected.

### RegLoad

Supports full arithmetic. Emits `LOAD r{reg}` when used in an expression. Can be passed back to `problem.stow()` to overwrite the same register.

### ModelRef

Not an expression operand. Provides coefficient access and constraint methods:
- `model.linear[i]` -- returns `CoefficientRef` for linear coefficient `i`
- `model.quadratic[i, j]` -- returns `CoefficientRef` for quadratic coefficient `(i, j)`
- Constraint methods: see [CONSTRAINTS.md](CONSTRAINTS.md)

For 2D models, `exclude` and `implies` accept tuple coordinates `(row, col)` which are auto-flattened via `IDXGRID`.

### SampleRef

Available as `problem.sample` after `define_model()`. Provides read access in the decoder context:

| Method | Opcode | Description |
|--------|--------|-------------|
| `.colfind(col, value)` | `COLFIND` | Find row where column has given value (2D models) |
| `.rowfind(row, value)` | `ROWFIND` | Find column where row has given value (2D models) |
| `.getline(index)` | `GETLINE` | Read variable assignment by index |
| `.rowsum(row)` | `ROWSUM` | Sum all values in a row |
| `.colsum(col)` | `COLSUM` | Sum all values in a column |

### CoefficientRef

Proxy for a single model coefficient. Not a register -- emits operations on the model register:

| Method | Opcode | Description |
|--------|--------|-------------|
| `.add(weight)` | `ADDLINE` / `ADDQUAD` | Add to coefficient |
| `.get()` | `GETLINE` / `GETQUAD` | Read coefficient (returns expression) |
| `= weight` | `SETLINE` / `SETQUAD` | Set coefficient |

### OutputRef

Provides write access in the decoder context:

| Method | Opcode | Description |
|--------|--------|-------------|
| `.append(value)` | `VECPUSH` | Append value to output vector |
| `[idx] = value` | `VECSET` | Set element at index |
| `[idx]` | `VECGET` | Get element at index (returns expression) |

### VecRef

General-purpose vector register:

| Method | Opcode | Description |
|--------|--------|-------------|
| `.push(value)` | `VECPUSH` | Append value |
| `.get(idx)` | `VECGET` | Get element (returns expression) |
| `.veclen()` | `VECLEN` | Get length (returns expression) |

---

## Expression Tree

All symbolic types build expression trees when combined with operators. Each expression node has an `emit(lines, indent)` method that appends assembly lines.

| Node | Assembly | Operands |
|------|----------|----------|
| `Literal(value)` | `PUSH value` | Integer constant |
| `RegLoad(reg)` | `LOAD r{reg}` | Register number |
| `BinOp(op, left, right)` | `{left} {right} {op}` | Two expression children |
| `UnaryOp(op, inner)` | `{inner} {op}` | One expression child |
| `CompareOp(op, left, right)` | `{left} {right} {op}` | Two expression children, pushes 0 or 1 |
| `SqrExpr(inner)` | `{inner} SQR` | One expression child |
| `GridExpr(row, col, cols)` | `{row} {col} {cols} IDXGRID` | Three expression children |
| `TriuExpr(i, j)` | `{i} {j} IDXTRIU` | Two expression children |
| `VecGetExpr(vec_reg, index)` | `{index} VECGET r{vec_reg}` | Register + one expression child |
| `VecLenExpr(vec_reg)` | `VECLEN r{vec_reg}` | Register |
| `GetLineExpr(sample_reg, index)` | `{index} GETLINE r{sample_reg}` | Register + one expression child |
| `GetQuadExpr(model_reg, i, j)` | `{i} {j} GETQUAD r{model_reg}` | Register + two expression children |
| `RowSumExpr(sample_reg, row)` | `{row} ROWSUM r{sample_reg}` | Register + one expression child |
| `ColSumExpr(sample_reg, col)` | `{col} COLSUM r{sample_reg}` | Register + one expression child |
| `RowFindExpr(sample_reg, row, value)` | `{row} {value} ROWFIND r{sample_reg}` | Register + two expression children |
| `ColFindExpr(sample_reg, col, value)` | `{col} {value} COLFIND r{sample_reg}` | Register + two expression children |
| `BitLenExpr(inner)` | `{inner} BITLEN` | One expression child |

### Coercion

Raw `int` values appearing in expression contexts are automatically coerced to `Literal(value)`. Non-int, non-expression values raise `TypeError`.

### Emission Optimizations

The expression tree applies the following optimizations during emission:

- `BinOp("ADD", x, Literal(1))` emits `INC` instead of `PUSH 1; ADD`
- `BinOp("SUB", x, Literal(1))` emits `DEC` instead of `PUSH 1; SUB`
- `Literal(value)` uses hexadecimal formatting for common penalty values (e.g. 100 -> `0x64`, 200 -> `0xC8`)

---

## Operator Algebra

All symbolic types that inherit `_ExprOps` support the following Python operators:

### Arithmetic

| Operator | Assembly | Notes |
|----------|----------|-------|
| `a + b` | `ADD` | Optimizes to `INC` for `+1` |
| `a - b` | `SUB` | Optimizes to `DEC` for `-1` |
| `a * b` | `MUL` | |
| `a // b` | `DIV` | Integer division |
| `a % b` | `MOD` | |
| `-a` | `NEG` | Unary negation |

### Comparison

| Operator | Assembly | Result |
|----------|----------|--------|
| `a == b` | `EQ` | Pushes 0 or 1 |
| `a < b` | `LT` | Pushes 0 or 1 |
| `a > b` | `GT` | Pushes 0 or 1 |
| `a <= b` | `LTE` | Pushes 0 or 1 |
| `a >= b` | `GTE` | Pushes 0 or 1 |

### Bitwise

| Operator | Assembly |
|----------|----------|
| `a & b` | `BAND` |
| `a \| b` | `BOR` |
| `a ^ b` | `BXOR` |
| `~a` | `BNOT` |
| `a << b` | `SHL` |
| `a >> b` | `SHR` |

---

## Free Functions

Functions in the `xq_*` namespace handle operations that Python operators cannot express (logical operators, index math, special arithmetic):

### Index Math

| Function | Assembly | Description |
|----------|----------|-------------|
| `xq_triu(i, j)` | `IDXTRIU` | Upper triangular index: `j * (j - 1) / 2 + i` |
| `xq_grid(row, col, cols)` | `IDXGRID` | Grid flat index: `row * cols + col` |

### Arithmetic

| Function | Assembly | Description |
|----------|----------|-------------|
| `xq_sqr(x)` | `SQR` | Square: `x * x` |
| `xq_abs(x)` | `ABS` | Absolute value |
| `xq_min(a, b)` | `MIN` | Minimum of two values |
| `xq_max(a, b)` | `MAX` | Maximum of two values |

### Logical

Python keywords `and` / `or` / `not` cannot be overloaded, so XQCP provides function equivalents:

| Function | Assembly | Description |
|----------|----------|-------------|
| `xq_not(x)` | `NOT` | Logical NOT (0 -> 1, non-zero -> 0) |
| `xq_and(a, b)` | `AND` | Logical AND |
| `xq_or(a, b)` | `OR` | Logical OR |
| `xq_xor(a, b)` | `XOR` | Logical XOR |
| `xq_bnot(x)` | `BNOT` | Bitwise NOT |
| `xq_bitlen(x)` | `BITLEN` | Bit length: `floor(log2(x)) + 1` |
