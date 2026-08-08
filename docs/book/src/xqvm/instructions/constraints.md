# High-Level Constraints

These instructions inject QUBO penalty terms for common combinatorial
constraints, expanding into linear and quadratic coefficient deltas
automatically. The model register must hold a `Model` in model mode.
Grid-based opcodes (`ONEHOTR`, `ONEHOTC`) require grid dimensions pre-set by
`RESIZE`. Vec-based opcodes (`EQUALITY`, `ATLEAST`, `ATLEASTW`, `REDUCE`)
operate on arbitrary variable sets. All coefficients are `i64`.

## `0x70` -- `ONEHOTR reg`

**Stack:** \\([\ldots, \text{row}, \text{penalty}] \to [\ldots]\\)
**Register effect:** `mutate`

Pop `penalty`, then `row`. Apply the one-hot constraint over all variables in
grid row `row`:

$$H \mathrel{+}= \text{penalty} \cdot \left(\sum_c x_{\text{row},c} - 1\right)^2$$

Expanding (binary variables: \\(x^2 = x\\)):

$$\text{linear}[\text{row} \cdot \text{cols} + c] \mathrel{+}= -\text{penalty} \qquad \forall\; c \in [0, \text{cols})$$

$$\text{quad}[\text{row} \cdot \text{cols} + c_i,\; \text{row} \cdot \text{cols} + c_j] \mathrel{+}= 2 \cdot \text{penalty} \qquad \forall\; c_i < c_j$$

## `0x71` -- `ONEHOTC reg`

**Stack:** \\([\ldots, \text{col}, \text{penalty}] \to [\ldots]\\)
**Register effect:** `mutate`

Pop `penalty`, then `col`. One-hot over all variables in grid column `col`:

$$\text{linear}[r_i \cdot \text{cols} + \text{col}] \mathrel{+}= -\text{penalty} \qquad \forall\; r_i \in [0, \text{rows})$$

$$\text{quad}[r_i \cdot \text{cols} + \text{col},\; r_j \cdot \text{cols} + \text{col}] \mathrel{+}= 2 \cdot \text{penalty} \qquad \forall\; r_i < r_j$$

## `0x72` -- `EXCLUDE reg`

**Stack:** \\([\ldots, i, j, \text{penalty}] \to [\ldots]\\)
**Register effect:** `mutate`

Pop `penalty`, then \\(j\\), then \\(i\\). Add mutual-exclusion: penalise
\\(x_i = 1\\) and \\(x_j = 1\\) simultaneously.

$$\text{quad}[i, j] \mathrel{+}= \text{penalty}$$

## `0x73` -- `IMPLIES reg`

**Stack:** \\([\ldots, i, j, \text{penalty}] \to [\ldots]\\)
**Register effect:** `mutate`

Pop `penalty`, then \\(j\\), then \\(i\\). Add implication \\(i \Rightarrow j\\):
penalise \\(x_i = 1\\) with \\(x_j = 0\\).

$$H \mathrel{+}= \text{penalty} \cdot x_i \cdot (1 - x_j) = \text{penalty} \cdot x_i - \text{penalty} \cdot x_i \cdot x_j$$

$$\text{linear}[i] \mathrel{+}= \text{penalty}$$

$$\text{quad}[i, j] \mathrel{+}= -\text{penalty}$$

## `0x74` -- `EQUALITY model indices coeffs`

**Stack:** \\([\ldots, \text{target}, \text{penalty}] \to [\ldots]\\)
**Register effect:** `read` indices, coeffs; `mutate` model

Pop `penalty`, then `target`. Read variable indices from `indices` (`VecInt`)
and coefficients from `coeffs` (`VecInt`). Expand the weighted equality
constraint into QUBO terms on `model`:

$$H \mathrel{+}= P \cdot \left(\sum_k a_k \cdot x_{\text{idx}_k} - b\right)^2$$

Expanding:

$$\text{linear}[\text{idx}_k] \mathrel{+}= P \cdot a_k \cdot (a_k - 2b) \qquad \forall\; k$$

$$\text{quad}[\text{idx}_k, \text{idx}_m] \mathrel{+}= 2P \cdot a_k \cdot a_m \qquad \forall\; k < m$$

The constant term \\(P \cdot b^2\\) is dropped. `EQUALITY` is the general form of
`ONEHOTR`/`ONEHOTC` — setting all \\(a_k = 1\\) and \\(b = 1\\) produces the same
expansion.

## `0x75` -- `ATLEAST model indices`

**Stack:** \\([\ldots, k, \text{penalty}] \to [\ldots]\\)
**Register effect:** `read` indices; `mutate` model (grows size)

Pop `penalty`, then \\(k\\). Read variable indices from `indices`. Enforce
\\(\sum x_i \ge k\\) by allocating \\(S = \lfloor\log_2(N - k)\rfloor + 1\\)
slack variables at `model.size` and applying an `EQUALITY` expansion with
target \\(k\\):

$$\sum_i x_{\text{idx}_i} - \sum_{j=0}^{S-1} 2^j \cdot s_j = k$$

Error `ValueError` if \\(k \le 0\\) or \\(k > N\\).

## `0x76` -- `ATLEASTW model indices coeffs`

**Stack:** \\([\ldots, k, \text{penalty}] \to [\ldots]\\)
**Register effect:** `read` indices, coeffs; `mutate` model (grows size)

Pop `penalty`, then \\(k\\). Same as `ATLEAST` but with arbitrary weights from
`coeffs`. Enforces \\(\sum w_i \cdot x_i \ge k\\). The slack count is computed
from \\(\text{max\_excess} = \sum w_i - k\\).

Error `ValueError` if \\(k \le 0\\) or lengths of indices and coefficients differ.

## `0x77` -- `REDUCE model`

**Stack:** \\([\ldots, \text{var\_a}, \text{var\_b}, P_{\text{aux}}] \to [\ldots, w]\\)
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
pattern for a TSP:

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
```
