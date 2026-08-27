# XQVM High-Level Function Expansions

These expansions describe the QUBO penalty terms injected by the [XQMX High-Level Functions](ISA.md#xqmx-high-level-functions) opcodes.

---

## Expansion Rules

These rules hold for every expansion in this file. They are normative: each one decides whether a program faults, so an implementation that reorders or defers any of them accepts a different set of programs than one that does not.

### Scale factors are validated before the terms that use them

An expansion computes each penalty scale factor once, range-checks it, and does so before the loop that emits the terms scaled by it -- unconditionally, whether or not that loop goes on to emit anything. `ONEHOTR` and `ONEHOTC` check `-penalty` and `2 × penalty` before the linear loop; `EQUALITY` (and `ATLEAST` and `ATLEASTW`, which route through it) checks `2 × target` before the linear loop and `2 × penalty` before the pair loop.

The consequence is the reason for writing it down: a one-column `ONEHOTR` raises for a `2 × penalty` that no emitted term uses, and a one-element `EQUALITY` raises for the same factor over a pair loop that emits nothing. A factor outside the i64 range is a defective constraint whether or not this particular row or index list is wide enough to make the defect visible, and evaluating it lazily would make the same constraint accepted at one width and rejected at another.

### Deltas are applied as they are computed

An expansion is not atomic and does not stage its deltas. Each delta is computed, range-checked, and applied to the model before the next one is computed, in the order the expansion enumerates its terms: the whole linear pass first, in ascending position order over the index list, then the whole quadratic pass, in ascending `(k, m)` order over the pairs with `k < m`. An expansion that raises part-way through leaves the deltas it has already applied standing in the model.

The order is observable whenever an index appears twice in the same `indices` vector, because a repeated index accumulates: the second delta is added on top of the first, and the coefficient that is range-checked is the accumulated result rather than the delta. A size-1 model holding `linear[0] = 7e18`, expanded by `EQUALITY` with `indices = [0, 0]`, `coeffs = [3e9, 1e9]`, `target = 1e9` and `penalty = 1`, raises in the order above and would complete in the reverse one. Both current implementations apply deltas in the order stated here.

### An expansion that would write nothing

This section settles one half of the grid precondition [ISA.md](ISA.md#xqmx-grid) states normatively -- the no-grid half. The other half, an index outside the axis extent, is not a question about writing nothing: it raises `IndexOutOfBounds` because the variables it would write to are not the ones named, and ISA.md is the statement of record for it.

`ONEHOTR` and `ONEHOTC` raise `InvalidGridDimensions` on a register with no grid, rather than expanding to nothing and continuing, while `EQUALITY` accepts an empty `indices` vector and writes nothing. That is not an inconsistency: an absent grid is a precondition the program never established, so there is no row to constrain and the instruction cannot mean anything, whereas an empty `indices` vector is a caller-supplied set that is legitimately empty, and a constraint over no variables is vacuously satisfied.

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

Both indices are range-checked against `model.size` before either delta is applied, in the order the operands were supplied, and one outside `[0, model.size)` raises `IndexOutOfBounds`. The rule is the one [ISA.md](ISA.md#xqmx-coefficient-access) states for the coefficient opcodes, and it applies here for the same reason: this expansion writes a coefficient, and a coefficient over a variable the allocator never declared is a constraint that constrains nothing.

Penalise `x_i = 1` and `x_j = 1` simultaneously:

```
quad[i, j] += penalty
```

## `IMPLIES` Expansion

Both indices are range-checked exactly as `EXCLUDE`'s are, before either delta is applied. `IMPLIES` writes to the linear surface as well as the quadratic one, so an unbounded `i` would reach both.

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

The weights are program-controlled, so `Σ(w_i)` is accumulated in index order with every partial sum checked against the i64 range, and the subtraction of `k` is checked the same way; a value outside the range raises `ArithmeticOverflow` (SPEC.md overflow rule) rather than deriving the slack count from a wrapped excess.

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
  + Σ_{i<=j} quad_model[i,j] × x_sample[i] × x_sample[j]
```

Where `x_sample[i] = sample.values[i]` (the variable assignment). Error: `SizeMismatch` if `model.size != sample.size`.

### Term grouping

Each quadratic term is evaluated as `(coeff × x_i) × x_j`, not as `coeff × (x_i × x_j)`. The two group differently under checked arithmetic: on the spin domain with `coeff = -2^63` and `x_i = x_j = -1`, the first raises at `coeff × x_i` and the second never leaves the range. The stated grouping is what both implementations do, and it is normative for the same reason the accumulation order is -- the grouping decides whether the program errors at all.

### The diagonal

The quadratic table's keys satisfy `i <= j`, not `i < j`: **the diagonal is legal**. `SETQUAD`, `ADDQUAD` and `GETQUAD` normalise a pair by swapping when `i > j` and impose no further restriction, so `PUSH 2 / BQMX r0 / PUSH 1 / PUSH 1 / PUSH 7 / SETQUAD r0` stores `quadratic[(1,1)] = 7`, and `ENERGY` evaluates that entry as `7 × x_1 × x_1` like any other. Both implementations store and evaluate self-couplings.

What a self-coupling means is domain-dependent, and the VM does not interpret it. On binary variables `x² = x`, so a diagonal term acts as a linear bias written through the quadratic table; on spin variables `x² = 1`, so it acts as a constant energy offset; on the discrete domain it is neither. A program that writes one is doing something the VM permits and gives no meaning to.

`IDXTRIU` is unaffected. It enumerates the strictly upper-triangular pairs `i < j`, so no diagonal cell has an index of its own in that enumeration: `IDXTRIU` with `i == j` yields the index of some off-diagonal pair rather than of a diagonal one.

### Accumulation order

Both sums are accumulated in **sorted key order**: linear terms by ascending `i`, quadratic terms by ascending `(i, j)` with `i <= j`. The linear sum is accumulated in full before the quadratic sum begins.

This is normative, not an implementation detail. An implementation whose sparse tables preserve insertion order -- the order the program's `SETLINE`/`ADDLINE`/`SETQUAD`/`ADDQUAD` instructions happened to run -- must sort before accumulating, because insertion order is a property of the program's history rather than of the model, and two programs that build the same model by different routes must produce the same result.

The order is observable because overflow raises: a partial sum can leave the `i64` range in one order and stay inside it in the other, so two orders would disagree about whether the program errors at all. Every partial sum is checked, not just the total.

The same rule applies to any reduction over a model's sparse tables.

`ROWSUM` and `COLSUM` are not reductions over the sparse tables and need no sorting rule. They fold the grid extent `RESIZE` established, in ascending flat-index order along their axis -- column order for `ROWSUM`, row order for `COLSUM` -- reading the `linear` surface at every cell of the extent whether or not an entry exists there. The reduction that does fold an index list the program supplied is `ATLEASTW`, whose weight sum is accumulated in the order of the `coeffs` vector; that order is the program's own and likewise needs no sorting rule. Both are still subject to the per-step check: every partial sum is range-checked, so the order in which the program built the list is observable through whether the reduction raises.

### Declined: order-free `ENERGY`

An alternative was considered and declined: range-check each term product, accumulate the total in a type wider than the value type (`i128` on Rust, native unbounded integers on Python), and range-check only the final total. It would make the accumulation order unobservable, and `ENERGY` would then return every total that is representable rather than raising on some of them.

It is declined because per-step checking is the arithmetic-safety paradigm the rest of the VM is built on, and because it keeps the implementation contract to checked 64-bit arithmetic: a third implementation written from this specification needs no intermediate type wider than the value type, and the failure mode of getting a wide accumulator subtly wrong is a silently wrong energy rather than a spurious fault. The cost is stated here rather than left implicit -- the accumulation order above is normative because of this decision, and `ENERGY` raises for some totals that are exactly representable.
