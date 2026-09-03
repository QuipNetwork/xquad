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

- Constraint action kinds (`_CONSTRAINT_KINDS`): `{onehot_row, onehot_col, exclude, implies, equality, atleast, atleastw, inequality}`
- If a top-level block (a contiguous range of actions at loop depth 0) contains any constraint action, it is classified as a constraint block
- The constraint check descends into `branch` actions: a constraint nested inside any arm of a `problem.branch()` call classifies the whole top-level block as a constraint block, even though `branch` itself is not a constraint action kind
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

The verifier replays the encoder's action stream. Constraint operands are `VecRef` register handles, not data: the index and coefficient vectors exist only once the encoder's `VECPUSH` instructions have run at VM runtime, frequently inside loops and branches. A separate program cannot inherit that state, so the verifier re-executes the structural half of the encoder and substitutes a sample check for each constraint.

### Replay Rules

Actions fall into three groups.

**Replayed verbatim.** `input`, `range_start`/`range_end`, `iter_start`/`iter_end`, `stow`, `vec_alloc`, `vec_push`, `branch`. These reconstruct the register and vector state the encoder had.

**Skipped.** `add_linear`, `add_quadratic`, `set_linear`, `set_quadratic`, `slack`. The verifier is handed a finished model and must not mutate it. Skipping `slack` is what leaves the index and coefficient vectors holding only the user's own variables.

**Replaced by a check.** `onehot_row`, `onehot_col`, `equality`, `inequality`, `atleast`, `atleastw`, `exclude`, `implies`, `reduce`. Each is emitted at the same point in the stream, so a constraint declared inside a loop is checked once per iteration.

The body is replayed in encoder order: `_partition_body` splits top-level blocks into an objective section and a constraint section, and both compilers emit objective first. The invariant is that the verifier's register and vector state at each constraint site equals the encoder's.

### Calldata Contract

| Slot | Contents |
|------|----------|
| `0 .. k-1` | One per `problem.input()`, in declaration order; slot index equals register index |
| `k` | The model, `INPUT` into the encoder's own model register |
| `k+1` | The sample |

Appending rather than prepending keeps the replayed `PUSH {reg}; INPUT r{reg}` preamble identical to the encoder's. `Problem.verifier_calldata()` returns the slot names.

### Register Layout

The verifier keeps the encoder's register numbers and claims eight registers above the encoder's high-water mark (`Problem._alloc._next`, read without mutating -- `compile()` runs all three compilers over one `Problem`, so allocating through the shared counter would make compilation non-idempotent). `base + 7 > 255` raises at compile time.

| Register | Purpose |
|----------|---------|
| `base+0` | Sample (XQMX, SAMPLE mode), from calldata slot `k+1` |
| `base+1` | Valid flag (int, output slot 1) |
| `base+2` | Energy (int, output slot 0) |
| `base+3` | Declared model size: domain-check bound and REDUCE counter seed |
| `base+4` | Weighted-sum accumulator |
| `base+5` | `LIDX` position, and the domain check's loop variable |
| `base+6` | `LVAL` element |
| `base+7` | REDUCE shadow counter |

A constraint action is a leaf, so no two checks ever nest and the accumulator and loop registers are safely single-instance. Each is written before it is read inside its own block, so neither the bytecode verifier's type-state phase nor its must-init phase sees a read-before-write, even for a check inside a branch arm.

### Model Shape

`BQMX`/`SQMX`/`RESIZE` are skipped. The `size_expr` is replayed and stowed into `base+3`, and for a 2D model the `cols_expr` is replayed and stowed into the encoder's own `cols_reg` so that `IDXGRID` resolves for 2D `exclude` and `implies` checks.

### Check Semantics

Checks are over the variables the user declared. Slack and `ATLEAST`/`ATLEASTW` auxiliaries are encoding artefacts and stay unconstrained: checking the expanded penalty form would report `valid = 0` for a feasible sample whose slack bits a solver left inconsistent.

| Constraint | Check |
|------------|-------|
| `equality(idx, coef, b)` | `sum(coef[k] * sample[idx[k]]) == b` |
| `equality` preceded by `slack` on the same vecs | `sum(coef[k] * sample[idx[k]]) <= b` |
| `inequality` | `sum(coef[k] * sample[idx[k]]) <= capacity`. The bound is `capacity`; `target` is the slack start index |
| `atleast(idx, k)` | `sum(sample[idx[j]]) >= k` |
| `atleastw(idx, coef, k)` | `sum(coef[j] * sample[idx[j]]) >= k` |
| `exclude(a, b)` | `sample[a] * sample[b] == 0` |
| `implies(a, b)` | `sample[a] <= sample[b]` |
| `onehot_row` / `onehot_col` | `ROWSUM`/`COLSUM` on the sample `== 1`, at the constraint site |
| `reduce(a, b) -> w` | `sample[w] == sample[a] * sample[b]` |

Every check emitter is net-zero on the stack and initialises its accumulator before the loop or branch that feeds it. The weighted-sum core, shared by `equality`, `inequality` and `atleastw`:

```
PUSH 0
STOW r{acc}
PUSH 0
VECLEN r{indices}
ITER r{indices}
  LIDX r{pos}
  LVAL r{elem}
  LOAD r{pos}
  VECGET r{coeffs}
  LOAD r{elem}
  GETLINE r{sample}
  MUL
  LOAD r{acc}
  ADD
  STOW r{acc}
NEXT
LOAD r{acc}
{bound}
EQ                      ; or LTE / GTE
LOAD r{valid}
AND
STOW r{valid}
```

`atleast` drops the `LIDX`/`VECGET` pair. An empty index vector is safe: `ITER` skips a body whose slice is empty, leaving the accumulator at zero.

### Domain Check

Always emitted, gated on the model's domain, and bounded by the model's *declared* size rather than a caller-supplied value. Slack and auxiliary variables past that size are not domain-checked.

```
PUSH 1
STOW r{valid}
PUSH 0
LOAD r{size}
RANGE
  LVAL r{loop}
  LOAD r{loop}
  GETLINE r{sample}
  COPY
  PUSH {low}            ; 0 for BINARY, -1 for SPIN
  EQ
  SWAP
  PUSH {high}           ; 1 for both
  EQ
  OR
  LOAD r{valid}
  AND
  STOW r{valid}
NEXT
```

### Slack Detection

A prepass walks the same flattened stream the emitter walks, descending into loop bodies and every branch arm while carrying a scope path, and keys a map on `(indices.reg, coeffs.reg)`:

- `vec_alloc` for register R clears every key containing R. This is the only reset, and it is what makes per-iteration vectors work.
- `slack` sets the key.
- `equality` consults the key; if present it emits `LTE` instead of `EQ`.

The key is never cleared on consume: two equalities over one slack-extended vector pair are both inequalities in the encoder. A `slack` and the `equality` consuming it in different loop or branch scopes raises at compile time.

### REDUCE Shadow Counter

`reduce` is in `_actions` but deliberately not in `_constraints`. The VM allocates its auxiliary at `model.size` when `REDUCE` executes; the verifier must not run `REDUCE`, so it tracks the same index in a counter seeded from the declared size before any loop. At each `reduce` site it reads the counter as the auxiliary index, stows it into the encoder's own `stow_reg` so a chained `reduce()` resolves, then increments. Because the replay follows the encoder's control flow instruction for instruction, the counter tracks `model.size` through loops, skipped empty loops and branch arms with no static trip-count analysis.

The counter is correct only while nothing else grows the model first. Growing actions are `atleast`, `atleastw`, every `inequality` and every slack-extended `equality`. Compilation raises unless every growing action appears after every `reduce` in flattened order and shares no enclosing loop or branch arm with one.

### Rejected Programs

Three cases raise at compile time rather than emit a check that would be wrong:

- **Model-coefficient reads.** `model.linear[i]` and `model.quadratic[i, j]` as reads emit `GETLINE`/`GETQUAD` against the model register. In the encoder those read a partially built model; in the verifier the register holds the finished one from calldata. Anything derived from such a read differs between the two programs with no error.
- **Binary-only constraints on a non-binary model.** `onehot_row`, `onehot_col`, `exclude` and `implies` have no meaning over `SPIN` variables.
- **Register exhaustion.** The eight verifier registers would pass `r255`.

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

The decoder uses its own register layout:

| Register | Purpose |
|----------|---------|
| `r0` | Sample (XQMX, SAMPLE mode) |
| `r1` | The one scalar the caller passes on calldata slot 1 |
| `r2` upwards | One output vector per declared output, in declaration order |
| `r10` upwards | One loop variable per open loop, by nesting depth |

Loop registers sit above the outputs and no lower than `r10`, so the common single-loop program emits the `r10` the hand-written decoder fixtures document. A program with more than eight outputs pushes the loop base up rather than colliding with them.

### Output Block Collection

The decoder compiler collects output blocks defined by `output_decl` markers. Between one `output_decl` and the next (or end of actions), all actions belong to that output's decoder block.

### Supported Action Kinds

The decoder emits three action kinds: `range_start`, `range_end` and `output_append`. Every other kind is rejected with a `RuntimeError` at `compile()`.

The rejection is load-bearing rather than defensive. Program partitioning drops everything after the first `output_decl` from the encoder and the verifier, so an action recorded after `problem.output()` reaches the decoder alone. A kind the decoder cannot emit would therefore be dropped by all three programs, and the failure would surface as an empty output vector rather than as an error -- `branch()` inside an output block silently lost its arms' `.append()` calls this way. Conditional append is not supported; declare every other operation before the first `output()` call.

`iter_start` and `iter_end` are rejected with the rest. `ITER` reads a vector register, and the decoder's calldata is the sample and one scalar, so there is no vector for it to iterate -- remapping its index and value variables would still leave the loop reading a register the decoder never wrote. Walk the indices with `range()` and read each one from `problem.sample`.

An `output_append` whose recorded target is not the block's own output is rejected too. The decoder writes each output to its slot as soon as that output's block ends, so an append recorded after a later `output()` would land after its target had already shipped. Finish filling one output before declaring the next.

An `output()` declared inside a `range()` block is rejected: its decoder block opens with the loop's `range_end`, closing a loop the block never opened.

### Assembly Structure

For each output, in order: `VECI`, the decode block, then `PUSH slot` / `OUTPUT r{reg}` -- there is no non-`Vec` branch, since `Types.Vec` is the only output type `problem.output()` accepts.

```
VECI r{out}                 ; allocate output vector
{output block actions}      ; loops, VECPUSH, GETLINE, COLFIND, etc.
PUSH {slot}
OUTPUT r{out}
```

Final `HALT` after all outputs.

### Register Remapping

Every expression the decoder emits -- a loop bound and an appended value alike -- is rebuilt against the layout above before emission. Bounds and values share one register file, so they share one rewrite; two rewrites disagreeing about what a reference means is how an input read came to address the sample.

The mapping is total. Every node either resolves onto a decoder register or raises, at any depth: a read nested inside arithmetic is remapped the same as a bare one. A node left to fall through emits the register the DSL allocator assigned at recording time, which the decoder never wrote, so the program reads whatever happens to sit there instead of failing.

| Node | Resolves to |
|------|-------------|
| Sample read (`GETLINE`, `ROWSUM`, `COLSUM`, `ROWFIND`, `COLFIND`) | The decoder's sample register, `r0` |
| Loop variable | The register of its own open loop |
| `InputRef` (Int), `RegLoad` | The scalar register, `r1` |

Everything else is refused with a message naming the construct: a model coefficient (`CoefficientRef`, `GETQUAD`), a vector (a `Types.Vec` input, `.get()`, `.veclen()`, a `problem.vec()` allocation), and a loop variable read outside its own loop.

The rebuild shares its traversal with the verifier's sentinel rewrite. Both supply a per-node mapping to one structural walker.

### The Single Scalar

The decoder is handed exactly one scalar, on calldata slot 1. Every `InputRef` and `RegLoad` the decoder reaches resolves to `r1`, and referencing a second, distinct one is rejected at `compile()` -- the decoder could not tell the caller which of the two to pass, so it would silently read the other.

Which scalar that is, is the program's choice rather than a fixed "N". `examples/graph_coloring` and `examples/bin_packing` both stow a total variable count before the first `output()` and pass that on slot 1. The emitted header names the resolved scalar so the caller can see what slot 1 must hold:

```
PUSH 1
INPUT r1  ; total_vars
```

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
