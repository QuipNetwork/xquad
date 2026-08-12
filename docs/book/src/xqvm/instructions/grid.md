# Grid Operations

A model's linear surface is stored flat, indexed by a single `usize`. Grid
operations let it additionally be addressed as a 2-D matrix,
\\((\text{row}, \text{col})\\), by attaching row and column counts as
metadata and reinterpreting the flat index as
\\(\text{row} \cdot \text{cols} + \text{col}\\), the same convention
[`IDXGRID`](index-math.md) computes by hand. Byte values, operand layouts
and stack effects are in the [XQMX Grid](../opcodes.md#xqmx-grid) section of
the opcode reference.

Every instruction here accepts `reg` holding either a `Model` or a
`Sample`, not only a `Model`: `ROWFIND`, `COLFIND`, `ROWSUM` and `COLSUM`
read a "linear surface" that is the model's sparse coefficient map for a
`Model` register, and the sample's dense assignment vector for a `Sample`
register, so the same instruction reads either a model's biases or a
solved sample's variable values depending on what is in the register.
`ROWSUM` on a freshly allocated `BSMX` sample (no coefficients to speak
of, only assignments) runs without a `RegisterType` error and returns the
row's summed values.

## RESIZE Attaches, It Does Not Reshape

`RESIZE` pops `cols` then `rows` and stores them on the model or sample; it
does not touch the coefficient map or assignment vector at all. A model
with 16 variables and no grid set, and the same model after `RESIZE r0`
with rows = 4, cols = 4, have byte-for-byte identical `linear` and
`quadratic` maps; what changes is how `ROWFIND`, `COLFIND`, `ROWSUM`,
`COLSUM` and, outside this page, [`ONEHOTR`/`ONEHOTC`](constraints.md)
interpret a flat index. Both `rows` and `cols` must be strictly positive:
`RESIZE r0` with either argument \\(\le 0\\) errors at runtime with
`invalid grid dimensions`, for example with `rows = 0, cols = 4`. This is
a runtime check, not one the verifier catches statically, since grid
dimensions are ordinary popped stack values rather than something the
verifier's dataflow passes track.

The canonical use is encoding a two-index variable directly instead of
computing flat indices by hand at every access site. A 4-city TSP, for
example, models \\(x[\text{city}][\text{position}]\\) as a
\\(4 \times 4\\) grid:

```asm
PUSH 16        ; size = 4 * 4
BQMX r0        ; allocate binary model
PUSH 4         ; rows = 4
PUSH 4         ; cols = 4
RESIZE r0      ; set grid dimensions
```

after which row 2 is every variable for city 2 across all four positions,
and `ONEHOTR r0` over row 2 is exactly the constraint "city 2 occupies
exactly one position".

## Row and Column Bounds Are Not Enforced

None of `ROWFIND`, `COLFIND`, `ROWSUM` or `COLSUM` checks the row or column
index it pops against the `rows`/`cols` set by `RESIZE`. Each converts the
popped value to a `usize` and errors `IndexOutOfBounds` only if that
conversion fails, meaning the index is negative; a row or column number at
or past what `RESIZE` declared is scanned anyway, since the underlying flat
index simply lands on coefficients or assignments beyond the intended grid
rather than being rejected. `ROWSUM r0` for `row = 99` on a model
`RESIZE`d to \\(2 \times 2\\) returns `0` rather than erroring, because the
flat range it scans falls on absent (implicitly zero) sparse entries.
Treat `RESIZE`'s dimensions as documentation for
correctly-written bytecode, not as a bound this family of instructions
enforces for you.

## ROWFIND and COLFIND

`ROWFIND` pops `v` then `r`, scans row `r` left to right, and pushes the
column of the first entry whose value equals `v`, or \\(-1\\) if none
matches. `COLFIND` pops `v` then `c` and is the column-major mirror. Since
a model's storage is sparse, an unset coefficient reads as `0` (see
[Coefficient Access](coefficient-access.md)), so searching for `v = 0`
against a model can match either an explicit zero or nothing at all,
depending on write history; either way, an unmatched search returns
\\(-1\\) rather than erroring. On a \\(2 \times 2\\) model with
`linear[0] = 9` and nothing else set, `ROWFIND` for `v = 9` in row 0
returns `0` (the match), and the identical search in row 1 returns `-1`.

## ROWSUM and COLSUM

`ROWSUM` pops `r` and pushes
\\(s = \sum_{c=0}^{C-1} \text{linear}[r \cdot C + c]\\); `COLSUM` pops `c`
and pushes the column-major equivalent. Both sum over the full declared
row or column width, treating absent model entries as `0` rather than
skipping them. On a `Sample` register the same sum runs over the sample's
assignment values instead of coefficients, which is how bytecode checks a
solved one-hot row without decoding each variable index by hand: after a
solver's result is loaded back into a `Sample` register, `ROWSUM` over a
row that carried a `ONEHOTR` constraint should read exactly `1` if the
constraint holds in that sample.
