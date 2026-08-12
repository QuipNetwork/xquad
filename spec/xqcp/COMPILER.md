# Compilation Pipeline

## Overview

`problem.compile()` runs three independent compiler passes over the recorded action list, generating `.xqasm` source for each program. Each pass reads the same `Problem._actions` list but extracts different subsets of actions.

## Register Allocation

A simple incrementing counter allocates registers from `r0` to `r255`. Inputs, the cols register (2D models only) and the model register claim the first registers, in this fixed order:

1. Inputs (`r0`, `r1`, ...) -- one per `problem.input()` call
2. Cols register -- if the model is 2D, a register holds the column count, allocated by `define_model()` before the model register
3. Model register -- allocated by `define_model()`

Every other register-allocating construct claims a register in call order, not in a fixed precedence relative to the others:

- Loop variables -- allocated on demand by `range()` and `iter()`
- Stowed values -- one register per `stow()` call (or reuse if an existing `RegLoad` is passed as target)
- Vector registers -- allocated by `problem.vec()`
- Output registers -- allocated by `problem.output()`

Exceeding 256 registers raises `RuntimeError`.

## Action Recording

Every DSL call appends an `Action(kind, data)` to `Problem._actions`. The complete set of action kinds:

| Kind | Data | Source |
|------|------|--------|
| `input` | `{ref: InputRef}` | `problem.input()` |
| `define_model` | `{model_reg, domain, size_expr, is_2d, cols_reg, rows_expr, cols_expr}` | `problem.define_model()` |
| `range_start` | `{var: LoopVar, start_expr, end_expr}` | `problem.range()` enter |
| `range_end` | `{}` | `problem.range()` exit |
| `iter_start` | `{vec_ref, idx_var, val_var, start_expr, end_expr}` | `problem.iter()` enter |
| `iter_end` | `{}` | `problem.iter()` exit |
| `stow` | `{reg, name, expr}` | `problem.stow()` |
| `add_linear` | `{model, coord, weight}` | `model.linear[i].add(w)` |
| `add_quadratic` | `{model, coord_a, coord_b, weight}` | `model.quadratic[i,j].add(w)` |
| `set_linear` | `{model, coord, weight}` | `model.linear[i] = w` |
| `set_quadratic` | `{model, coord_a, coord_b, weight}` | `model.quadratic[i,j] = w` |
| `onehot_row` | `{model, row, penalty}` | `model.apply_onehot_row()` |
| `onehot_col` | `{model, col, penalty}` | `model.apply_onehot_col()` |
| `exclude` | `{model, coord_a, coord_b, penalty}` | `model.apply_exclude()` |
| `implies` | `{model, coord_a, coord_b, penalty}` | `model.apply_implies()` |
| `equality` | `{model, indices_vec, coeffs_vec, target, penalty}` | `model.apply_equality()` |
| `atleast` | `{model, indices_vec, k, penalty}` | `model.apply_atleast()` |
| `atleastw` | `{model, indices_vec, coeffs_vec, k, penalty}` | `model.apply_atleastw()` |
| `slack` | `{model, indices_vec, coeffs_vec, start_idx, capacity}` | `model.slack()` |
| `reduce` | `{model, var_a, var_b, p_aux}` | `model.reduce()` |
| `branch` | `{arms: [{condition, actions}]}` | `problem.branch()` |
| `output_decl` | `{ref: OutputRef}` | `problem.output()` |
| `output_append` | `{output, value_expr}` | `output.append()` |
| `output_setitem` | `{output, index_expr, value_expr}` | `output[i] = value` |
| `vec_init` | `{reg}` | `problem.vec()` |
| `vec_push` | `{vec_ref, value_expr}` | `vec.push(value)` |

---

## Encoder Compilation

### Partitioning

The encoder compiler partitions the action list into:

1. **Inputs** -- `input` actions
2. **Model action** -- `define_model` action
3. **Body actions** -- everything between `define_model` and the first `output_decl`
4. **Output actions** -- `output_decl` and subsequent actions (decoder territory, not emitted)

Body actions are further partitioned into **objective** and **constraint** blocks at depth-0 boundaries:

- Constraint action kinds: `{onehot_row, onehot_col, exclude, implies, equality, atleast, atleastw, slack, reduce}`
- If a top-level block (a contiguous range of actions at loop depth 0) contains any constraint action, it is classified as a constraint block
- Otherwise it is an objective block

### Assembly Structure

```
; === Inputs ===
PUSH 0
INPUT r0
PUSH 1
INPUT r1
; ...

; === Allocations ===
{size_expr}
BQMX r{model}              ; or SQMX for spin domain
; [optional 2D: {rows_expr} {cols_expr} RESIZE r{model}]

; === Objective ===
{objective block actions}

; === Constraints ===
{constraint block actions}

; === Output ===
PUSH 0
OUTPUT r{model}
HALT
```

### Size Expression Optimization

When `size = N * N` (detected as `BinOp("MUL", RegLoad(r), RegLoad(r))` where both sides reference the same register), the compiler emits `LOAD r{N}; SQR` instead of `LOAD r{N}; LOAD r{N}; MUL`.

---

## Verifier Compilation

The verifier uses a fixed register layout:

| Register | Purpose |
|----------|---------|
| `r0` | Model (XQMX, MODEL mode) |
| `r1` | Sample (XQMX, SAMPLE mode) |
| `r2` | N (problem size parameter) |
| `r3` | Valid flag (int, output) |
| `r4` | Energy (int, output) |

### Validity Check Selection

The verifier compiler inspects `Problem._constraints` to decide which validity check to emit:

**Case 1: onehot_row constraints present**

Emit a `ROWSUM` loop:
```
PUSH 1
STOW r{valid}
PUSH 0
LOAD r{N}
RANGE
  LVAL r{loop}
  LOAD r{loop}
  ROWSUM r{sample}
  PUSH 1
  EQ
  LOAD r{valid}
  AND
  STOW r{valid}
NEXT
```

**Case 2: onehot_col constraints present**

Emit a `COLSUM` loop (same structure as ROWSUM but using `COLSUM`).

Both ROWSUM and COLSUM loops may be emitted if both constraint types are present.

**Case 3: no onehot constraints**

Emit a binary domain check:
```
PUSH 1
STOW r{valid}
PUSH 0
{size_expr}
RANGE
  LVAL r{loop}
  LOAD r{loop}
  GETLINE r{sample}
  DUP
  PUSH 0
  EQ
  SWAP
  PUSH 1
  EQ
  OR
  LOAD r{valid}
  AND
  STOW r{valid}
NEXT
```

### Energy and Output

After validity checks:
```
ENERGY r{model} r{sample}
STOW r{energy}
PUSH 0
OUTPUT r{energy}
PUSH 1
OUTPUT r{valid}
HALT
```

---

## Decoder Compilation

The decoder uses a fixed register layout:

| Register | Purpose |
|----------|---------|
| `r0` | Sample (XQMX, SAMPLE mode) |
| `r1` | N (problem size parameter) |

### Output Block Collection

The decoder compiler collects output blocks defined by `output_decl` markers. Between one `output_decl` and the next (or end of actions), all actions belong to that output's decoder block.

### Assembly Structure

For each output:
```
VECI r{out}                 ; allocate output vector
{output block actions}      ; loops, VECPUSH, VECSET, GETLINE, COLFIND, etc.
PUSH {slot}
OUTPUT r{out}
```

Final `HALT` after all outputs.

### InputRef Remapping

In the decoder context, `InputRef` references are remapped. The only scalar input available to the decoder is N (on `r1`), so `InputRef` loads resolve to `LOAD r1`.

---

## Branch Compilation

Multi-arm branches compile to a chain of conditional jumps with first-match semantics:

```
; arm 1
{condition}
NOT
JUMPI .skip_1
  {body at indent+1}
  JUMP .end
TARGET .skip_1

; arm 2
{condition}
NOT
JUMPI .skip_2
  {body at indent+1}
  JUMP .end
TARGET .skip_2

; default arm (no skip logic)
{body at indent+1}
TARGET .end
```

The default arm (condition `None`) is emitted without condition check or skip logic.

---

## Post-Compilation Verification

If the `xqffi` package is installed, each generated program is run through the Rust bytecode verifier (`xqffi.verifier.verify_source()`) before `compile()` returns. Verification failures raise `ValueError`. If `xqffi` is not installed, verification is silently skipped.

## Validation Anchors

The spec is validated against:
1. `xqcp/tests/test_xqcp.py` -- expression emission, compilation, section checks, end-to-end pipelines
2. `examples/tsp/runner.py` -- 2D grid model, onehot constraints, COLFIND decoder
3. `examples/maxcut/runner.py` -- 1D flat model, edge iteration, GETLINE decoder
4. `xqcp/tests/fixtures/` -- hand-written `.xqasm` fixtures for regression testing
