# Expressions

Every symbolic value XQCP hands you -- an `InputRef` from `problem.input()`,
a `LoopVar` from `problem.range()`/`problem.iter()`, a `RegLoad` from
`problem.stow()`, a coefficient read back from `model.linear[i]` -- builds
the same kind of expression tree when you combine it with an operator or
an `xq_*` function. A plain Python `int` used anywhere one of these is
expected gets coerced to a literal automatically; anything else raises
`TypeError`. This page groups the resulting vocabulary by what you are
trying to compute, not by class hierarchy.

Every `xq_*` function below imports from the same module as `Problem` and
`Types`:

```python
from xquad.cp import (
    xq_not, xq_and, xq_or, xq_xor, xq_bnot,
    xq_sqr, xq_abs, xq_min, xq_max, xq_bitlen,
    xq_triu, xq_grid,
)
```

## Arithmetic: Computing a Value

`+ - * // %` and unary `-` work exactly as Python's operators do on
integers, and compile to `ADD SUB MUL DIV MOD NEG`. Knapsack's objective
loop uses three of them in three lines:

```python
with problem.range(0, num_items) as i:
    vi = problem.stow("vi", values_in.get(i))
    problem.model.linear[i].add(-vi)
```

`-vi` is unary negation on a `RegLoad`; `problem.range` and `problem.iter`
yield `LoopVar`s (`i` here) with the same arithmetic, used as loop-bound
math in Max-Cut's `offset = e * 3`. `//` is integer division, matching
XQVM's `DIV`, not Python's floating-point `/` -- XQCP has no `/`, since
every value in this system is an `i64`.

Adding or subtracting exactly the literal `1` is special-cased: `a + 1`
compiles to `LOAD`, `INC` and `a - 1` to `LOAD`, `DEC`, skipping the
`PUSH 1` a general add or subtract would need. Nothing about how you write
it changes -- `a + 1` and `a - 1` are the natural way to write these, and
the compiler does the substitution for you.

## Comparisons: Producing 0 or 1

`== < > <= >=` compile to `EQ LT GT LTE GTE`, each pushing `1` for true
and `0` for false rather than a Python `bool`. That makes a comparison's
result usable anywhere an integer is: as a coefficient directly, as the
condition argument to `problem.branch()` (see
[Control Flow](control-flow.md)), or combined further with arithmetic.

**Python has no `!=` here.** There is no XQVM opcode for it, so `!=` on
an XQCP expression raises immediately, at problem-definition time:

```text
TypeError: '!=' is not supported on XQCP expressions; use xq_not(a == b) instead
```

`_ExprOps` overloads `__ne__` purely to raise that. Left to Python's
default, `__ne__` would call `__eq__` and invert the result, and
inverting a `CompareOp` object with `not` only asks whether the object
itself is falsy -- which it never is -- so `a != b` would evaluate to the
constant `False` regardless of `a` and `b`. Write `xq_not(a == b)`
instead:

```python
model.linear[0] = xq_not(a == b)
```

compiles to `LOAD`, `LOAD`, `EQ`, `NOT`, and gives `1` when `a` and `b`
differ, `0` when they match.

## Bitwise: Bit-Level Values

`& | ^ ~ << >>` compile to `BAND BOR BXOR BNOT SHL SHR`, useful wherever a
problem packs several small values into one integer, or -- as in
knapsack's `slack` step -- builds a binary-weighted sum out of individual
bits. These are ordinary Python operators, not `xq_*` functions, because
Python lets you overload them.

## Index Math: One Number From Several

Two functions turn a pair (or triple) of coordinates into a single flat
index:

```python
xq_triu(i, j)            # IDXTRIU: upper-triangular packed index for (i, j)
xq_grid(row, col, cols)  # IDXGRID: row * cols + col, grid flat index
```

`xq_grid` is what a 2D model's tuple-coordinate coefficient access uses
internally -- see [Inputs and Model Shape](inputs-and-model.md#1d-and-2d-models)
for that path; call it directly when you need the flat index as a value
in its own right rather than as a coordinate to `model.linear[...]`.
`xq_triu(i, j)` swaps `i` and `j` when `i > j`, then computes
`j * (j - 1) // 2 + i` on the swapped pair, so the packed index does not
depend on argument order, per the swap rule
[`spec/xqvm/ISA.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ISA.md)
gives for `IDXTRIU`. `xq_triu(2, 5)` returns `12`
(`5 * 4 // 2 + 2`; no swap needed, since `2 <= 5`).

## Logical and Other Free Functions

Python's `and`, `or`, and `not` keywords cannot be overloaded. XQCP gives
you functions in their place. The first four work on the 0/1 convention
comparisons use, not on Python truthiness:

```python
xq_not(x)       # NOT:  0 -> 1, non-zero -> 0
xq_and(a, b)    # AND
xq_or(a, b)     # OR
xq_xor(a, b)    # XOR
xq_bnot(x)      # BNOT: bitwise complement, the same opcode as ~ above,
                # kept as a function for symmetry -- not the same as xq_not
```

Writing `a and b`, `a or b`, `not a`, or using an expression as an `if`
condition instead of calling one of these functions raises immediately,
the same way `!=` does above:

```text
TypeError: XQCP expressions cannot be used in a boolean context ('and', 'or', 'not', 'if'); use xq_and(a, b), xq_or(a, b) or xq_not(a) instead
```

Python's `and`/`or`/`not`/`if` all decide on the truthiness of the
*object*, not on the value it represents, so XQCP has no way to give them
the right answer -- only to refuse before a Python habit produces the
wrong operand or the constant `False` in place of a real expression.

Five more free functions round out the arithmetic vocabulary that has no
Python operator:

```python
xq_sqr(x)       # SQR:  x * x
xq_abs(x)       # ABS:  absolute value
xq_min(a, b)    # MIN
xq_max(a, b)    # MAX
xq_bitlen(x)    # BITLEN: floor(log2(x)) + 1, and 0 for x <= 0
```

## Naming an Expression: `stow`

`problem.stow(name, expr)` evaluates an expression once and stores it in a
register, returning a `RegLoad` you can reuse without re-emitting the
computation. Knapsack's `vi = problem.stow("vi", values_in.get(i))` is
this: `values_in.get(i)` is a `VECGET`, emitted once per loop iteration
and bound to `vi`, and every later use of `vi` in that iteration is a
cheap `LOAD` instead of repeating the `VECGET`. Passing an existing
`RegLoad` back into `stow` overwrites that register instead of allocating
a new one -- useful for an accumulator threaded through a loop.

The operators and free functions above are the DSL's whole vocabulary;
`spec/xqcp/TYPES.md` lists nothing this page omits. With that in hand,
the next question is what you do with a value once you have one:
[Objectives](objectives.md) covers turning an expression into a term the
solver minimises.
