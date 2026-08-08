# Index Math

Utilities for mapping 2-D coordinates to flat array indices. All arithmetic is
wrapping on `i64`.

| Code | Mnemonic | Stack Effect | Description |
|------|----------|--------------|-------------|
| `0x5A` | `IDXGRID` | \\([\ldots, r, c, C] \to [\ldots, r \cdot C + c]\\) | Row-major flat index. Pops \\(C\\) (cols), then \\(c\\) (col), then \\(r\\) (row). |
| `0x5B` | `IDXTRIU` | \\([\ldots, i, j] \to [\ldots, j(j{-}1)/2 + i]\\) | Upper-triangular index for the pair \\((i, j)\\) with \\(i \le j\\). |

None of these instructions have register effects.

## Use Cases

### `IDXGRID`

Computes the row-major flat index:

$$\text{index} = \text{row} \cdot \text{cols} + \text{col}$$

Used to convert 2-D grid coordinates to a flat index for models with grid
dimensions set by `RESIZE`. For example, in a TSP with \\(N\\) cities and \\(N\\) positions,
variable \\(x[\text{city}][\text{position}]\\) maps to flat index \\(\text{city} \cdot N + \text{position}\\).

### `IDXTRIU`

Computes the upper-triangular packed index:

$$\text{index} = \frac{j \cdot (j - 1)}{2} + i \qquad (i \le j)$$

Used to index into the upper triangle of a symmetric matrix. For a pair of
variables \\((i, j)\\) with \\(i \le j\\), the upper-triangular index gives a unique
position in a packed representation. This is useful for iterating over quadratic
coefficient pairs without double-counting.
