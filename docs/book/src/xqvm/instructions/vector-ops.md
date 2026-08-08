# Vector Operations

Instructions for reading, writing, and querying register-held vectors.

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x50` | `VECPUSH` | `reg: Register` | \\([\ldots, v] \to [\ldots]\\) | `mutate` | Pop \\(v\\). Append \\(v\\) to `reg`'s `VecInt`. |
| `0x51` | `VECGET` | `reg: Register` | \\([\ldots, i] \to [\ldots, v]\\) | `read` | Pop \\(i\\). Bounds-check: \\(0 \le i < \text{len}\\). Push \\(\text{vec}[i]\\). |
| `0x52` | `VECSET` | `reg: Register` | \\([\ldots, i, v] \to [\ldots]\\) | `mutate` | Pop \\(v\\), then \\(i\\). Bounds-check: \\(0 \le i < \text{len}\\). Set \\(\text{vec}[i] \leftarrow v\\). |
| `0x53` | `VECLEN` | `reg: Register` | \\([\ldots] \to [\ldots, n]\\) | `read` | `reg` must hold `VecInt` or `VecXqmx`. Push \\(\lvert\text{vec}\rvert\\) as `i64`. |
| `0x54` | `SLACK` | `indices: Register, coeffs: Register` | \\([\ldots, \text{start}, \text{cap}] \to [\ldots]\\) | `mutate` | Pop `cap` and `start`. Append \\(S = \lfloor\log_2(\text{cap})\rfloor + 1\\) slack entries to both vecs. |

## Type Requirements

- `VECPUSH`, `VECGET`, and `VECSET` require the register to hold `VecInt`.
- `VECLEN` accepts both `VecInt` and `VecXqmx`.
- `SLACK` requires both registers to hold `VecInt`. It appends (does not
  overwrite) so that item variables and slack variables coexist in one vec pair.
  If `cap <= 0`, no elements are appended.
- All indexing operations perform bounds checking and error with
  `IndexOutOfBounds` on violation.

## Example

```asm
VEC r0          ; r0 = empty VecInt
PUSH 10
VECPUSH r0      ; r0 = [10]
PUSH 20
VECPUSH r0      ; r0 = [10, 20]
PUSH 0
VECGET r0       ; stack = [..., 10]
```

## SLACK Details

`SLACK indices coeffs` pops `capacity` (top) then `start_index` from the stack.
It computes \\(S = \lfloor\log_2(\text{capacity})\rfloor + 1\\) and appends:

- To `indices`: \\([\text{start}, \text{start}+1, \ldots, \text{start}+S-1]\\)
- To `coeffs`: \\([1, 2, 4, \ldots, 2^{S-1}]\\)

This generates binary-weighted slack variables for inequality-to-equality
conversion. Combined with `EQUALITY`, it enforces knapsack-style capacity
constraints without manual coefficient loops.

```asm
VEC r5            ; indices
VEC r6            ; coeffs
; ... populate with item indices and weights ...
PUSH 3            ; start_index (first slack var index)
PUSH 10           ; capacity
SLACK r5 r6       ; appends 4 slack entries (floor(log2(10))+1 = 4)
```
