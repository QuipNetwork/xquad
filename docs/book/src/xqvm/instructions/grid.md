# Grid Operations

A model can optionally be given 2-D grid dimensions so that variables are
addressed as \\((\text{row}, \text{col})\\) with flat index \\(\text{row} \cdot \text{cols} + \text{col}\\). `reg` must hold
`Model`.

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x66` | `RESIZE` | `reg: Register` | \\([\ldots, R, C] \to [\ldots]\\) | `mutate` | Pop \\(C\\) (cols), then \\(R\\) (rows). Set grid dimensions. Both must be \\(> 0\\). |
| `0x67` | `ROWFIND` | `reg: Register` | \\([\ldots, r, v] \to [\ldots, c]\\) | `read` | Pop \\(v\\), then \\(r\\). Scan row \\(r\\) for the first column where \\(\text{linear} = v\\). Push column index or \\(-1\\). |
| `0x68` | `COLFIND` | `reg: Register` | \\([\ldots, c, v] \to [\ldots, r]\\) | `read` | Pop \\(v\\), then \\(c\\). Scan column \\(c\\) for the first row where \\(\text{linear} = v\\). Push row index or \\(-1\\). |
| `0x69` | `ROWSUM` | `reg: Register` | \\([\ldots, r] \to [\ldots, s]\\) | `read` | Pop \\(r\\). Push \\(s = \sum_{c=0}^{C-1} \text{linear}[r \cdot C + c]\\). |
| `0x6A` | `COLSUM` | `reg: Register` | \\([\ldots, c] \to [\ldots, s]\\) | `read` | Pop \\(c\\). Push \\(s = \sum_{r=0}^{R-1} \text{linear}[r \cdot C + c]\\). |

## Grid Model

Grid dimensions are metadata attached to a model; they do not change the
underlying coefficient storage. After calling `RESIZE`, the grid instructions
(`ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM`) and constraint instructions
(`ONEHOTR`, `ONEHOTC`) interpret linear coefficients as a 2-D matrix.

For example, a TSP with 4 cities uses a \\(4 \times 4\\) grid where \\(x[\text{city}][\text{pos}]\\)
maps to flat index \\(\text{city} \cdot 4 + \text{pos}\\):

```asm
PUSH 16        ; size = 4 * 4
BQMX r0        ; allocate binary model
PUSH 4         ; rows = 4
PUSH 4         ; cols = 4
RESIZE r0      ; set grid dimensions
```

## Search and Aggregation

`ROWFIND` and `COLFIND` perform linear scans over sparse coefficient entries
in the specified row or column. They return the first match or \\(-1\\) if no entry
matches the search value.

`ROWSUM` and `COLSUM` sum all linear coefficients in a row or column. These are
useful for verifying constraint satisfaction (e.g. checking that exactly one
variable is set in a one-hot row).
