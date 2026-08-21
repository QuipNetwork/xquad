# XQVM High-Level Function Expansions

These expansions describe the QUBO penalty terms injected by the [XQMX High-Level Functions](ISA.md#xqmx-high-level-functions) opcodes.

---

## `ONEHOTR` / `ONEHOTC` Expansion

Apply the one-hot constraint over a set of variable indices (all variables in a row or column):

```
H += penalty × (Σ x_i - 1)²
```

Expanding for binary variables (`x² = x`):

```
linear[i]    += -penalty          for each i in indices
quad[i, j]   += 2 × penalty      for each pair i < j in indices
```

`ONEHOTR` uses indices `[row*cols, row*cols+1, ..., row*cols+cols-1]`.
`ONEHOTC` uses indices `[col, col+cols, col+2*cols, ..., col+(rows-1)*cols]`.

## `EXCLUDE` Expansion

Penalise `x_i = 1` and `x_j = 1` simultaneously:

```
quad[i, j] += penalty
```

## `IMPLIES` Expansion

Penalise `x_i = 1` with `x_j = 0` (implication `x_i → x_j`):

```
H += penalty × x_i × (1 - x_j) = penalty × x_i - penalty × x_i × x_j

linear[i]    += penalty
quad[i, j]   += -penalty
```

## Model Growth

`ATLEAST`, `ATLEASTW`, and `REDUCE` allocate new variables during execution: slack variables for constraints, auxiliary variables for degree reduction. New variables are always allocated at the current `model.size` (append semantics). Existing variable indices remain valid after growth. `ATLEAST` and `ATLEASTW` validate the indices in `indices` against the model's existing size before allocating slack variables, and raise on an out-of-range one; they grow `model.size` only to hold the slack variables they allocate themselves.

`EQUALITY` also modifies `model.size`, on the opposite rule: an index in `indices` at or past the model's current size grows `model.size` to fit it rather than raising. `ATLEAST`, `ATLEASTW`, `REDUCE`, and `EQUALITY` are together the opcodes that modify `model.size`.

## `EQUALITY` Expansion

Expand the weighted equality constraint `H = P × (Σ_k(a_k × x_k) − b)²` into QUBO coefficients.

Given:

- penalty P (popped from stack)
- target b (popped from stack)
- indices `[idx_0, idx_1, ..., idx_{N-1}]` (from `indices` register)
- coefficients `[a_0, a_1, ..., a_{N-1}]` (from `coeffs` register)

The expansion adds:

```
linear[idx_k]           += P × a_k × (a_k − 2×b)      for each k
quad[idx_k, idx_m]      += P × 2 × a_k × a_m           for each pair k < m
```

The constant term `P × b²` is dropped (it shifts all energies equally and does not affect which assignment is optimal).

`ONEHOTR`/`ONEHOTC` are the special case where `a_k = 1` for all k and `b = 1`.

## `ATLEAST` Derivation

Enforce `Σ(x_i) ≥ k` over a set of binary variables (unit weights).

Given:

- penalty P (popped from stack)
- target k (popped from stack)
- variable indices `[idx_0, ..., idx_{N-1}]` (from `indices` register)

Steps:

1. Compute `max_excess = N − k`
2. Allocate `S = floor(log2(max_excess)) + 1` slack variables at `model.size` (model size grows by S)
3. Build combined coefficient vector: `[+1, +1, ..., +1, −1, −2, −4, ..., −2^(S-1)]` — original variables get `+1`, slack variables get negative powers of two
4. Apply `EQUALITY` expansion with combined indices/coefficients and target = k

The negative slack coefficients absorb excess selections above k:

```
Σ(x_i) − 1×s_0 − 2×s_1 − ... = k
```

## `ATLEASTW` Derivation

Enforce `Σ(w_i × x_i) ≥ k` over a set of binary variables with arbitrary weights.

Same logic as `ATLEAST`, but the original variables use the provided weights from the `coeffs` register instead of unit weights. The combined coefficient vector becomes `[w_0, w_1, ..., w_{N-1}, −1, −2, −4, ..., −2^(S-1)]`, and `max_excess` is computed as `Σ(w_i) − k`.

## `REDUCE` Derivation

Replace the product `x_a × x_b` with an auxiliary variable w, adding penalty terms that enforce `w = x_a × x_b` at the energy minimum. This is the Rosenberg reduction (1975) — the standard HOBO-to-QUBO method.

Given:

- P_aux (popped from stack) — penalty strength for the enforcement constraint
- var_b (popped from stack)
- var_a (popped from stack)

Steps:

1. Allocate auxiliary variable w at `model.size` (model size grows by 1)
2. Add enforcement terms:

```
quad[var_a, var_b]  += P_aux
quad[var_a, w]      += −2 × P_aux
quad[var_b, w]      += −2 × P_aux
linear[w]           += 3 × P_aux
```

3. Push w (the auxiliary index) onto the stack

The penalty is 0 when `w = x_a × x_b` and ≥ 1 otherwise, so the solver always prefers the correct w. The stack push enables natural chaining for higher-order terms (e.g. quartic `x_i × x_j × x_k × x_l` via two successive `REDUCE` calls followed by `ADDQUAD`).

## `ENERGY` Computation

```
E = Σ_i linear_model[i] × x_sample[i]
  + Σ_{i<j} quad_model[i,j] × x_sample[i] × x_sample[j]
```

Where `x_sample[i] = sample.values[i]` (the variable assignment). Error if `model.size != sample.size`: `xqvm_py` raises `ValueError`, the Rust `xqvm` VM raises `SizeMismatch`.

### Accumulation order

Both sums are accumulated in **sorted key order**: linear terms by ascending `i`, quadratic terms by ascending `(i, j)` with `i <= j`. The linear sum is accumulated in full before the quadratic sum begins.

This is normative, not an implementation detail. An implementation whose sparse tables preserve insertion order -- the order the program's `SETLINE`/`ADDLINE`/`SETQUAD`/`ADDQUAD` instructions happened to run -- must sort before accumulating, because insertion order is a property of the program's history rather than of the model, and two programs that build the same model by different routes must produce the same result.

The order is unobservable while addition wraps, since wrapping addition is associative and commutative and every order reaches the same total. It becomes observable the moment an operation outside the `i64` range raises: a partial sum can leave the range in one order and stay inside it in the other, so the two orders would disagree about whether the program errors at all.

The same rule applies to any reduction over a model's sparse tables. Reductions over an index list computed by the program (`ROWSUM`, `COLSUM`) are already ordered by that list and are unaffected.
