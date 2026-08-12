# Constraint Taxonomy

## Overview

XQCP constraints are applied to the XQMX model via methods on `ModelRef`. Each method records an `Action` and emits a corresponding XQVM high-level function opcode. The opcode expands into linear and quadratic coefficient deltas that inject QUBO penalty terms automatically.

All constraint methods take a `penalty` parameter (positive integer) that controls the strength of the penalty term. Higher penalties make constraint violations more costly to the solver.

## Constraint Methods

### `model.apply_onehot_row(row, penalty)`

Enforce exactly one variable in a grid row to be 1.

| Property | Value |
|----------|-------|
| Opcode | `ONEHOTR` |
| Stack inputs | `row`, `penalty` |
| Requires | 2D model (`rows` and `cols` set) |
| Action kind | `onehot_row` |
| Tracked for verifier | Yes |

**Verifier implication:** when any `onehot_row` constraint is present, the verifier emits a `ROWSUM` loop checking that every row sums to 1.

### `model.apply_onehot_col(col, penalty)`

Enforce exactly one variable in a grid column to be 1.

| Property | Value |
|----------|-------|
| Opcode | `ONEHOTC` |
| Stack inputs | `col`, `penalty` |
| Requires | 2D model |
| Action kind | `onehot_col` |
| Tracked for verifier | Yes |

**Verifier implication:** when any `onehot_col` constraint is present, the verifier emits a `COLSUM` loop checking that every column sums to 1.

### `model.apply_exclude(coord_a, coord_b, penalty)`

Mutual exclusion: variables at `coord_a` and `coord_b` cannot both be 1.

| Property | Value |
|----------|-------|
| Opcode | `EXCLUDE` |
| Stack inputs | `i`, `j`, `penalty` |
| Requires | -- |
| Action kind | `exclude` |
| Tracked for verifier | Yes |

For 2D models, coordinates can be tuples `(row, col)` which are auto-flattened via `IDXGRID`.

### `model.apply_implies(coord_a, coord_b, penalty)`

Implication: if variable at `coord_a` is 1, variable at `coord_b` must be 1.

| Property | Value |
|----------|-------|
| Opcode | `IMPLIES` |
| Stack inputs | `i`, `j`, `penalty` |
| Requires | -- |
| Action kind | `implies` |
| Tracked for verifier | Yes |

For 2D models, coordinates can be tuples `(row, col)` which are auto-flattened via `IDXGRID`.

### `model.apply_equality(indices_vec, coeffs_vec, target, penalty)`

Weighted equality: `P * (sum(a_k * x_k) - b)^2` where `a_k` are coefficients, `x_k` are variables at the given indices, and `b` is the target.

| Property | Value |
|----------|-------|
| Opcode | `EQUALITY` |
| Stack inputs | `target`, `penalty` |
| Register inputs | `indices_vec`, `coeffs_vec` |
| Requires | -- |
| Action kind | `equality` |
| Tracked for verifier | Yes |

### `model.apply_atleast(indices_vec, k, penalty)`

At-least-k constraint with unit weights: at least `k` of the indexed variables must be 1. Allocates slack variables, growing `model.size`.

| Property | Value |
|----------|-------|
| Opcode | `ATLEAST` |
| Stack inputs | `penalty`, `k` |
| Register inputs | `indices_vec` |
| Side effects | Allocates auxiliary variables |
| Action kind | `atleast` |
| Tracked for verifier | Yes |

### `model.apply_atleastw(indices_vec, coeffs_vec, k, penalty)`

At-least-k constraint with weighted variables. Allocates slack variables, growing `model.size`.

| Property | Value |
|----------|-------|
| Opcode | `ATLEASTW` |
| Stack inputs | `penalty`, `k` |
| Register inputs | `indices_vec`, `coeffs_vec` |
| Side effects | Allocates auxiliary variables |
| Action kind | `atleastw` |
| Tracked for verifier | Yes |

### `model.apply_inequality(indices_vec, coeffs_vec, target, capacity, penalty)`

Inequality constraint via composition: generates slack variables with `SLACK`, then applies `EQUALITY`. Equivalent to `sum(a_k * x_k) <= capacity`. `target` is the slack start index (normally the count of real variables), not a bound; `capacity` is both the slack bound and the `EQUALITY` target.

| Property | Value |
|----------|-------|
| Opcodes | `SLACK` + `EQUALITY` |
| Stack inputs | `target`, `capacity`, `penalty` |
| Register inputs | `indices_vec`, `coeffs_vec` |
| Side effects | Allocates slack variables |
| Action kind | composite |

### `model.reduce(var_a, var_b, p_aux)`

Rosenberg degree reduction: replace the product `x_a * x_b` with an auxiliary variable `w`, adding penalty terms to enforce `w = x_a * x_b`. Grows `model.size` by 1.

| Property | Value |
|----------|-------|
| Opcode | `REDUCE` |
| Stack inputs | `var_a`, `var_b`, `p_aux` |
| Side effects | Allocates one auxiliary variable |
| Returns | Expression for the auxiliary variable index |

## Coefficient Operations

Direct coefficient manipulation is used to build the objective function:

| Operation | Opcode | Description |
|-----------|--------|-------------|
| `model.linear[i].add(weight)` | `ADDLINE` | Add to linear coefficient |
| `model.linear[i] = weight` | `SETLINE` | Set linear coefficient |
| `model.linear[i]` (read) | `GETLINE` | Read linear coefficient (returns expression) |
| `model.quadratic[i, j].add(weight)` | `ADDQUAD` | Add to quadratic coefficient |
| `model.quadratic[i, j] = weight` | `SETQUAD` | Set quadratic coefficient |
| `model.quadratic[i, j]` (read) | `GETQUAD` | Read quadratic coefficient (returns expression) |

## HLF Expansion Cross-References

The XQVM opcodes emitted by XQCP constraints expand into linear and quadratic coefficient deltas. The expansion formulas are specified in the XQVM high-level functions document:

- **[../xqvm/HLF.md](../xqvm/HLF.md)** -- complete expansion formulas for ONEHOTR, ONEHOTC, EXCLUDE, IMPLIES, EQUALITY, ATLEAST, ATLEASTW, REDUCE, and ENERGY

XQCP does not perform these expansions itself -- it emits the high-level opcodes and the XQVM runtime applies the expansions during execution.

## Constraint Tracking

All constraint methods append to two lists:
1. `Problem._actions` -- the main action sequence (used by all three compiler passes)
2. `Problem._constraints` -- constraint-only subset (used by the verifier compiler to select the appropriate validity check)

The verifier compiler inspects `_constraints` to decide which validity check to emit:
- If any `onehot_row` constraint exists: emit `ROWSUM` loop
- If any `onehot_col` constraint exists: emit `COLSUM` loop
- If no onehot constraints exist: emit binary domain check (each variable is 0 or 1)

See [COMPILER.md](COMPILER.md) for the full verifier generation algorithm.
