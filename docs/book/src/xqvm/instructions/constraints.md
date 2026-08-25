# High-Level Constraints

These instructions inject QUBO penalty terms for common combinatorial
constraints, expanding into linear and quadratic coefficient deltas
automatically. The model register must hold a `Model` in model mode.
Grid-based opcodes (`ONEHOTR`, `ONEHOTC`) read the grid dimensions set by
`RESIZE` and require them. On a model with no grid set (`rows` and `cols`
both `0`) both raise `InvalidGridDimensions` rather than expanding over
nothing, so forgetting `RESIZE` fails at the instruction that needed it
rather than producing a model missing a constraint. Both implementations
agree. `xquad verify` still cannot catch it, because grid dimensions are
popped stack values rather than something the verifier's dataflow passes
track. Vec-based opcodes (`EQUALITY`,
`ATLEAST`, `ATLEASTW`, `REDUCE`) operate on arbitrary variable sets. All
coefficients are `i64`. For each opcode's byte value and operand layout,
see the [Opcode Reference](../opcodes.md).

## `ONEHOTR reg`

**Register effect:** `mutate`

Pop `penalty`, then `row`. Apply the one-hot constraint over all variables in
grid row `row`:

$$H \mathrel{+}= \text{penalty} \cdot \left(\sum_c x_{\text{row},c} - 1\right)^2$$

Expanding (binary variables: \\(x^2 = x\\)):

$$\text{linear}[\text{row} \cdot \text{cols} + c] \mathrel{+}= -\text{penalty} \qquad \forall\; c \in [0, \text{cols})$$

$$\text{quad}[\text{row} \cdot \text{cols} + c_i,\; \text{row} \cdot \text{cols} + c_j] \mathrel{+}= 2 \cdot \text{penalty} \qquad \forall\; c_i < c_j$$

## `ONEHOTC reg`

**Register effect:** `mutate`

Pop `penalty`, then `col`. One-hot over all variables in grid column `col`:

$$\text{linear}[r_i \cdot \text{cols} + \text{col}] \mathrel{+}= -\text{penalty} \qquad \forall\; r_i \in [0, \text{rows})$$

$$\text{quad}[r_i \cdot \text{cols} + \text{col},\; r_j \cdot \text{cols} + \text{col}] \mathrel{+}= 2 \cdot \text{penalty} \qquad \forall\; r_i < r_j$$

## `EXCLUDE reg`

**Register effect:** `mutate`

Pop `penalty`, then \\(j\\), then \\(i\\). Add mutual-exclusion: penalise
\\(x_i = 1\\) and \\(x_j = 1\\) simultaneously.

$$\text{quad}[i, j] \mathrel{+}= \text{penalty}$$

## `IMPLIES reg`

**Register effect:** `mutate`

Pop `penalty`, then \\(j\\), then \\(i\\). Add implication \\(i \Rightarrow j\\):
penalise \\(x_i = 1\\) with \\(x_j = 0\\).

$$H \mathrel{+}= \text{penalty} \cdot x_i \cdot (1 - x_j) = \text{penalty} \cdot x_i - \text{penalty} \cdot x_i \cdot x_j$$

$$\text{linear}[i] \mathrel{+}= \text{penalty}$$

$$\text{quad}[i, j] \mathrel{+}= -\text{penalty}$$

## `EQUALITY model indices coeffs`

**Register effect:** `read` indices, coeffs; `mutate` model

Pop `penalty`, then `target`. Read variable indices from `indices` (`VecInt`)
and coefficients from `coeffs` (`VecInt`). Expand the weighted equality
constraint into QUBO terms on `model`:

$$H \mathrel{+}= P \cdot \left(\sum_k a_k \cdot x_{\text{idx}_k} - b\right)^2$$

Expanding:

$$\text{linear}[\text{idx}_k] \mathrel{+}= P \cdot a_k \cdot (a_k - 2b) \qquad \forall\; k$$

$$\text{quad}[\text{idx}_k, \text{idx}_m] \mathrel{+}= 2P \cdot a_k \cdot a_m \qquad \forall\; k < m$$

The constant term \\(P \cdot b^2\\) is dropped. `EQUALITY` is the general form of
`ONEHOTR`/`ONEHOTC` -- setting all \\(a_k = 1\\) and \\(b = 1\\) produces the same
expansion.

If an index in `indices` is at or past the model's current size, `EQUALITY`
grows `model.size` to fit it rather than erroring -- unlike `ATLEAST` and
`ATLEASTW` below, which validate incoming indices against the model's
existing size and raise `IndexOutOfBounds` on an out-of-range one. Otherwise,
`indices` and `coeffs` must have equal length or the instruction raises
`VecLengthMismatch`.

## `ATLEAST model indices`

**Register effect:** `read` indices; `mutate` model (grows size)

Pop `penalty`, then \\(k\\). Read variable indices from `indices`. Enforce
\\(\sum x_i \ge k\\) by allocating \\(S = \lfloor\log_2(N - k)\rfloor + 1\\)
slack variables at `model.size` and applying an `EQUALITY` expansion with
target \\(k\\), where \\(N\\) is the number of indices:

$$\sum_i x_{\text{idx}_i} - \sum_{j=0}^{S-1} 2^j \cdot s_j = k$$

This formula covers \\(N - k > 0\\). When \\(N - k \le 0\\) -- only
possible at \\(k = N\\), since `IndexOutOfBounds` below already rejects
\\(k > N\\) -- the constraint is already an equality with nothing left to
slacken, so `ATLEAST` allocates zero slack variables and applies the
`EQUALITY` expansion directly, with no \\(S\\) term at all.

Raises `IndexOutOfBounds` if \\(k \le 0\\) or \\(k > N\\), and if any index in
`indices` is at or past the model's existing size -- `ATLEAST` does not grow
the model to fit an out-of-range input index, only to hold the slack
variables it allocates itself.

## `ATLEASTW model indices coeffs`

**Register effect:** `read` indices, coeffs; `mutate` model (grows size)

Pop `penalty`, then \\(k\\). Same as `ATLEAST` but with arbitrary weights from
`coeffs`. Enforces \\(\sum w_i \cdot x_i \ge k\\). The slack count is computed
from \\(\text{max\_excess} = \sum w_i - k\\).

Raises `VecLengthMismatch` if `indices` and `coeffs` have different lengths,
or `IndexOutOfBounds` if \\(k \le 0\\).

## `REDUCE model`

**Register effect:** `mutate` model (grows size)

Pop \\(P_{\text{aux}}\\), then \\(\text{var\_b}\\), then \\(\text{var\_a}\\).
Allocate auxiliary variable \\(w\\) at `model.size`. Add Rosenberg enforcement
terms constraining \\(w = x_a \cdot x_b\\):

$$\text{quad}[\text{var\_a}, \text{var\_b}] \mathrel{+}= P_{\text{aux}}$$

$$\text{quad}[\text{var\_a}, w] \mathrel{+}= -2 P_{\text{aux}}$$

$$\text{quad}[\text{var\_b}, w] \mathrel{+}= -2 P_{\text{aux}}$$

$$\text{linear}[w] \mathrel{+}= 3 P_{\text{aux}}$$

Push \\(w\\) (the auxiliary index). Enables chaining for higher-order terms:
reduce a quartic \\(x_i x_j x_k x_l\\) by calling `REDUCE` twice to get
\\(w_1 = x_i x_j\\) then \\(w_2 = w_1 x_k\\), and finish with `ADDQUAD` on
\\((w_2, x_l)\\).

## Usage Pattern

Constraint instructions are designed to work with grid models. A typical
pattern for a TSP-style assignment grid:

```asm
; Allocate model and set grid
PUSH 16
BQMX r0
PUSH 4
PUSH 4
RESIZE r0

; Apply one-hot constraints on each row and column
PUSH 0
PUSH 4
RANGE
  LVAL r1
  LOAD r1
  PUSH 100       ; penalty weight
  ONEHOTR r0     ; each city visits exactly one position
NEXT

PUSH 0
PUSH 4
RANGE
  LVAL r1
  LOAD r1
  PUSH 100
  ONEHOTC r0     ; each position has exactly one city
NEXT

HALT
```
