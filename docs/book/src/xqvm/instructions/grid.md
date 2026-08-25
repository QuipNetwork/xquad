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
`ROWSUM` on a `BSMX` sample (no coefficients to speak of, only
assignments) raises no `RegisterType` error and returns the row's summed
values. A sample has to be gridded first, though: a freshly allocated one
carries `rows = cols = 0`, and all four opcodes raise
`InvalidGridDimensions` on a register with no grid.

## RESIZE Attaches, It Does Not Reshape

`RESIZE` pops `cols` then `rows` and stores them on the model or sample; it
does not touch the coefficient map or assignment vector at all. A model
with 16 variables and no grid set, and the same model after `RESIZE r0`
with rows = 4, cols = 4, have byte-for-byte identical `linear` and
`quadratic` maps; what changes is how `ROWFIND`, `COLFIND`, `ROWSUM`,
`COLSUM` and, outside this page, [`ONEHOTR`/`ONEHOTC`](constraints.md)
interpret a flat index. A grid has to satisfy two conditions. Both `rows`
and `cols` must be strictly positive, so `RESIZE r0` with either argument
\\(\le 0\\) errors at runtime with `invalid grid dimensions`, for example
with `rows = 0, cols = 4`. And \\(\text{rows} \cdot \text{cols}\\) must
not exceed the register's declared size: a grid is a reinterpretation of
variables the program already declared, so it cannot describe cells that
do not exist. `RESIZE r0` to \\(3 \times 3\\) on a 4-variable model
raises `InvalidGridDimensions` for the same reason a negative row count
does.

The extent bound is \\(\le\\), not \\(=\\). `EQUALITY`, `ATLEAST`,
`ATLEASTW` and `REDUCE` append slack and auxiliary variables past the
grid and nothing ever shrinks a register's size, so a model whose size
exceeds its extent is the normal state after any of them; a later
`RESIZE` over a strict subset of the variables is still accepted.

Both checks are runtime checks, not ones the verifier catches statically,
since grid dimensions are ordinary popped stack values rather than
something the verifier's dataflow passes track.

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

## Row and Column Bounds Are Enforced

`ROWFIND`, `COLFIND`, `ROWSUM` and `COLSUM` each check the index they pop
against the grid axis they address. A negative index and an index at or
past the declared extent both raise `IndexOutOfBounds`, naming the index
and the extent it exceeded. `ROWSUM r0` for `row = 99` on a model
`RESIZE`d to \\(2 \times 2\\) raises rather than returning `0`:

```asm
PUSH 4
BQMX r0
PUSH 2         ; rows
PUSH 2         ; cols
RESIZE r0
PUSH 99        ; row -- past the two rows the grid declares
ROWSUM r0
HALT
```

```
Error: xqvm::runtime_error

  × index 99 out of bounds (len 2) at byte 0x000c
```

A register with no grid at all is a separate fault: `rows = cols = 0`
addresses no line, so all four opcodes raise `InvalidGridDimensions`
rather than reducing over nothing. That is the same identity `ONEHOTR`
and `ONEHOTC` raise without a grid.

`RESIZE`'s dimensions are a bound the VM enforces, not a convention
correct bytecode is trusted to honour. Both implementations agree on all
three cases, and `conformance/vectors/xqmx-grid/` pins them.

## ROWFIND and COLFIND

`ROWFIND` pops `v` then `r`, scans row `r` left to right, and pushes the
column of the first entry whose value equals `v`, or \\(-1\\) if none
matches. `COLFIND` pops `v` then `c` and is the column-major mirror. Since
a model's storage is sparse, an unset coefficient reads as `0` (see
[Coefficient Access](coefficient-access.md)), so searching for `v = 0`
against a model can match either an explicit zero or nothing at all,
depending on write history. An unmatched search returns \\(-1\\) rather
than erroring, but only once the search has run: the register still has
to carry a grid, and `r` still has to name a row that grid declares.
\\(-1\\) means "scanned, no match", never "no such row". On a \\(2
\times 2\\) model with `linear[0] = 9` and nothing else set, `ROWFIND`
for `v = 9` in row 0 returns `0` (the match), and the identical search
in row 1 returns \\(-1\\); the same search in row 2 raises
`IndexOutOfBounds`.

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
