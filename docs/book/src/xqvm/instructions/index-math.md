# Index Math

`IDXGRID` and `IDXTRIU` compute the two flat-indexing schemes the rest of
the VM relies on, so that bytecode which builds indices by hand does not
have to reimplement either formula. Byte values, operand layouts and stack
effects are in the [Index Math](../opcodes.md#index-math) section of the
opcode reference. Neither instruction touches a register, and both use the
same wrapping `i64` arithmetic as [Arithmetic](arithmetic.md); a
sufficiently large `IDXGRID` product wraps rather than trapping, the same
as `MUL` would.

## IDXGRID: Row-Major Flat Index

`IDXGRID` pops `C` (columns), then `c` (column), then `r` (row), and
pushes

$$\text{index} = r \cdot C + c$$

the same row-major convention [`RESIZE`](grid.md) attaches to a model.
Computing this by hand and computing it with `IDXGRID` produce identical
results, since `RESIZE` does not change how flat indices are interpreted
internally; `IDXGRID` exists so bytecode that needs the flat index as an
ordinary stack value, for example to pass to
[`SETLINE`/`SETQUAD`](coefficient-access.md) without going through a grid
instruction, does not have to spell out `r * C + c` as separate `MUL` and
`ADD` instructions.

A worked example: a TSP over 4 cities and 4 positions models
\\(x[\text{city}][\text{position}]\\) as a \\(4 \times 4\\) grid, so city 2
at position 1 is variable

```asm
PUSH 2        ; row = city = 2
PUSH 1        ; col = position = 1
PUSH 4        ; cols = 4
IDXGRID       ; index = 2 * 4 + 1 = 9
```

This program's result, read back with `STOW`/`OUTPUT`, is `9`.

## IDXTRIU: Upper-Triangular Packed Index

`IDXTRIU` pops `j`, then `i`, and pushes

$$\text{index} = \frac{j \cdot (j - 1)}{2} + i \qquad (i < j)$$

the packed index for the pair \\((i, j)\\) in the **strictly** upper
triangle of a symmetric matrix, useful for iterating over quadratic
coefficient pairs without visiting \\((i, j)\\) and \\((j, i)\\) as two
different positions. If `i > j`, `IDXTRIU` swaps them before computing
the index, so the result is order-independent: \\((i, j)\\) and \\((j,
i)\\) pack to the same value, the same guarantee
[`SETQUAD`/`GETQUAD`](coefficient-access.md) give by normalising the pair
internally.

The enumeration is strictly upper-triangular, so it has no slot for a
diagonal cell even though the quadratic table has one. `SETQUAD` and
`GETQUAD` accept \\(i = j\\) and store a self-coupling; `IDXTRIU` called
with \\(i = j\\) yields the index of some off-diagonal pair rather than
of the diagonal one. Do not use it to address a diagonal coefficient.

`IDXTRIU` and `IDXGRID` do not range-check their operands, but every
intermediate of the computation is checked, so a product or sum that
leaves the `i64` range raises `ArithmeticOverflow` even when the final
index would land back inside it.

```asm
PUSH 1        ; i = 1
PUSH 3        ; j = 3
IDXTRIU       ; index = 3 * 2 / 2 + 1 = 4
```

This program's result is `4`.
