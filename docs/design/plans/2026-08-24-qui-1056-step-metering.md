# QUI-1056 Step Metering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one XQVM step a bounded amount of work, so `pallet-xqvm`'s `WeightPerStep * steps` price is honest for programs that touch a model.

**Architecture:** The VM keeps two counters: `instructions` (the ordinal a tracer reports) and `steps` (metered cost units, what `step_limit` bounds and what the pallet prices). Every instruction charges a base cost of 1 before dispatch, exactly as today; the handlers whose work scales with program-controlled data charge additional units *before* doing that work, through a `charge_steps` helper that mirrors the existing memory `charge`. Cost constants and the pure cost functions live in a new `xqvm/src/metering.rs`, mirrored in `xqvm_py/metering.py`, kept in lockstep by a parity script, and specified normatively in `spec/xqvm/METERING.md`.

**Tech Stack:** Rust 2024 (`xqvm`, `xqffi`, `xquad-conformance`), Python 3.13+ (`xqvm_py`), cargo-nextest, pytest, mdbook.

**Spec:** [QUI-1056](https://linear.app/quip-network/issue/QUI-1056/fixxqvm-energy-and-the-other-omodel-opcodes-cost-one-step) plus the Design section below. Task 9 lands the normative specification at `spec/xqvm/METERING.md`; until then this document is the spec.

**Base:** the tree this plan was written against, before QUI-1056 landed. Every line number in this plan is from that tree and is not maintained as those files move; resolve a reference by the name it cites rather than by its line.

---

## Design

### The problem

`xqvm/src/vm.rs:515` charges one step per instruction:

```rust
if self.steps >= self.step_limit {
    return Err(Error::StepLimitExceeded { limit: self.step_limit });
}
self.steps += 1;
```

`ENERGY` (`vm.rs:2414`) clones the sample vector and walks every coefficient in the model, and pays one step. `ONEHOTR` writes `cols + cols*(cols-1)/2` coefficients in one step; `ONEHOTC` the same in `rows`; `EQUALITY`, `ATLEAST` and `ATLEASTW` the same in the term count. `ITER`, `LVAL`, `INPUT` and `OUTPUT` clone whole registers, and a register can hold a model. So the ratio between the cheapest step (`NOP`) and the dearest has no bound below the memory limit, and a flat per-step price is wrong by that factor.

The memory budget (`charge`, `charge_variables`, `charge_coefficient`, `charge_equality_expansion` at `vm.rs:670`ff, mirrored in `xqvm_py/executor.py:338`ff) already bounds the *space* those opcodes take, with the charge-before-you-allocate discipline and a Rust/Python-identical formula. This plan applies the same shape to *time*.

### The model

- **Two counters.** `instructions` counts dispatches and is what a `Tracer` sees. `steps` counts cost units, is what `set_step_limit` bounds, and is what an embedder prices. Today they are the same field; splitting them keeps traces readable once costs stop being 1.
- **Base cost 1.** Charged before dispatch, unchanged, so a straight-line arithmetic program's step count does not move.
- **Extra units charged before the work.** A handler computes the size of the work it is about to do, calls `charge_steps`, and only then does it. A program that cannot pay performs none of the work, exactly like an allocation it cannot afford.
- **Units are relative to `NOP`.** One unit is one `NOP` dispatch, rounded so that a unit is never cheaper than the operation it stands for. Task 2 measures the ratios; Task 3 freezes them as named constants.
- **Integer arithmetic, saturating throughout.** The counts are consensus-visible, so no floats and no platform-dependent rounding. `usize` widths are converted with `u64::try_from(..).unwrap_or(u64::MAX)`, matching `charge_variables`.

### Cost table

`B` = base cost 1, charged for every instruction. Everything below is *in addition* to `B`.

| Opcode(s) | Extra units | Constant(s) |
|---|---|---|
| `ENERGY` | `SAMPLE_COPY_STEPS * sample.len() + MODEL_TERM_STEPS * (linear_len + quadratic_len)` | both |
| `SETLINE`, `ADDLINE`, `SETQUAD`, `ADDQUAD` | `COEFF_WRITE_STEPS`, only when the register holds a model | `COEFF_WRITE_STEPS` |
| `ONEHOTR` | `equality_expansion_steps(cols)` | `COEFF_WRITE_STEPS` |
| `ONEHOTC` | `equality_expansion_steps(rows)` | `COEFF_WRITE_STEPS` |
| `EQUALITY` | `equality_expansion_steps(n)` where `n = idx_vec.len()` | `COEFF_WRITE_STEPS` |
| `ATLEAST`, `ATLEASTW` | `equality_expansion_steps(n + num_slacks)` | `COEFF_WRITE_STEPS` |
| `BSMX`, `SSMX`, `XSMX` | `SAMPLE_COPY_STEPS * size` (the buffer is filled) | `SAMPLE_COPY_STEPS` |
| `BQMX`, `SQMX`, `XQMX` | none: an empty model allocates no coefficients | -- |
| `RANGE` | `ELEMENT_COPY_STEPS * count` | `ELEMENT_COPY_STEPS` |
| `ITER` over `vec<int>` | `ELEMENT_COPY_STEPS * slice.len()` | `ELEMENT_COPY_STEPS` |
| `ITER` over `vec<xqmx>` | `value_copy_steps` summed over the sliced models | both |
| `LVAL`, `INPUT`, `OUTPUT` | `value_copy_steps(value)` | both |
| `SLACK` | `ELEMENT_COPY_STEPS * 2 * entries` | `ELEMENT_COPY_STEPS` |
| everything else | none | -- |

with

```
equality_expansion_steps(n) = (n + n*(n-1)/2) * COEFF_WRITE_STEPS

value_copy_steps(Unset | Int)   = 0
value_copy_steps(VecInt(v))     = ELEMENT_COPY_STEPS * v.len()
value_copy_steps(Sample(s))     = SAMPLE_COPY_STEPS * s.values.len()
value_copy_steps(Model(m))      = COEFF_WRITE_STEPS * (m.linear_len() + m.quadratic_len())
value_copy_steps(VecXqmx(v))    = sum of value_copy_steps over the models
```

`equality_expansion_steps` counts the same worst case as `equality_expansion_bytes` (`vm.rs:242`): `n` linear terms and one quadratic term per unordered pair. Repeated indices collide on one map key, so an expansion can write fewer entries than it is charged for -- the count bounds the work, it does not measure it.

### Consequences

- Step counts become observable and normative: `xqvm` and `xqvm_py` must produce identical counts for identical programs, and conformance asserts it.
- Existing programs that touch a model get larger step counts. This is a behavioural break; it lands in 0.4.0 with QUI-1019.
- `Error::StepLimitExceeded` gains the position and the refused charge, so a fault is diagnosable the way `MemoryLimitExceeded` already is.
- QUI-1054 can then calibrate a flat `WeightPerStep` against `NOP` plus a safety multiplier, because a step is now bounded work.

### Out of scope

- Changing the default step limit or `set_step_limit(0)` semantics (QUI-1053, already fixed).
- Pallet-side weight calibration (QUI-1054, QUI-1013) -- this plan only makes the unit meaningful.
- The stale `set_step_limit(0)` prose in `docs/book/src/xqvm/limits-and-errors.md:36` and `execution.md:53`, which predates QUI-1053.

## Global Constraints

- Every new source file starts with the AGPL header from `AGENTS.md` (`//` for Rust, `#` for Python).
- Conventional Commits, imperative, lowercase subject, max 72 chars, scope = crate/package name. Sign off with `git commit -s`. Never add AI co-author trailers.
- No emojis. No em-dashes in prose -- use `--`. Four spaces, no tabs.
- Rust: no `unwrap()`, no indexing/slicing (`.get()`/`.get_mut()`), handle or `let _ =` every result (`unused-results` is blocking), document every public item.
- After edits run `make fmt`; before declaring a task done run the task's stated checks.
- `xqvm` is `no_std`-capable: `metering.rs` must not use `std`.

---

## File Structure

**Created:**
- `xqvm/src/metering.rs` -- cost constants and the pure cost functions. Small and dependency-free so the parity script can read it and so `no_std` builds are unaffected.
- `xqvm/examples/metering_calibration.rs` -- one-off measurement harness that prints the ns/operation ratios the constants are derived from.
- `xqvm_py/metering.py` -- the Python mirror of `metering.rs`.
- `scripts/check-metering-parity.py` -- three-way guard: Rust constants, Python constants, and the `spec/xqvm/METERING.md` table must agree.
- `spec/xqvm/METERING.md` -- the normative cost table.
- `conformance/vectors/metering/*` -- vectors that assert exact step counts.

**Modified:**
- `xqvm/src/vm.rs` -- counter split, `charge_steps`, the charge call sites.
- `xqvm/src/error.rs:150` -- `StepLimitExceeded` fields, and the `pos()` arm at `:262`.
- `xqvm/src/lib.rs` -- re-export the metering module.
- `xqvm/src/tracer/*` -- `StepState.step` fed from `instructions`.
- `xqvm/tests/integration.rs` -- metering tests.
- `xqffi/src/vm.rs:288` -- add `instructions()` next to `steps()`.
- `xqvm_py/executor.py` -- counter split and charge call sites.
- `conformance/src/lib.rs` -- `steps` on `Expected`/`Outcome`/`PyOut`.
- `Makefile` -- `metering-parity` target wired into `check-parity`.
- `spec/xqvm/SPEC.md` (Runtime Limits), `spec/xqvm/ISA.md`, `docs/book/src/xqvm/execution.md`, `docs/book/src/xqvm/limits-and-errors.md`.

---

### Task 1: The metering primitive

Splits the counters and adds the charge helper. No opcode changes yet, so no step count moves.

**Files:**
- Modify: `xqvm/src/vm.rs:272` (field), `:461-471` (accessors), `:506-520` (run loop), `:670-711` (charge helpers)
- Modify: `xqvm/src/error.rs:149-151`, `:246-255`
- Test: `xqvm/tests/integration.rs`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Vm::steps(&self) -> u64` -- metered cost units (existing name, new meaning).
  - `Vm::instructions(&self) -> u64` -- dispatch ordinal (new).
  - `Vm::charge_steps(&mut self, pos: usize, units: u64) -> Result<(), Error>` -- private; every later task calls this.
  - `Error::StepLimitExceeded { pos: Option<usize>, requested: u64, used: u64, limit: u64 }`.

- [ ] **Step 1: Write the failing tests**

Append to `xqvm/tests/integration.rs`:

```rust
// ---------------------------------------------------------------------------
// Metering (QUI-1056)
// ---------------------------------------------------------------------------

#[test]
fn instructions_and_steps_agree_when_every_opcode_costs_base() {
    // 100 NOPs and a HALT: nothing here charges beyond the base cost, so the
    // metered count and the dispatch ordinal are the same number.
    let mut b = InstructionBuilder::new();
    for _ in 0..100 {
        b.emit_nop();
    }
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_unlimited_steps();
    vm.run(&bytecode).expect("runs to HALT");
    assert_eq!(vm.steps(), 101);
    assert_eq!(vm.instructions(), 101);
}

#[test]
fn step_limit_error_reports_the_refused_charge() {
    let mut b = InstructionBuilder::new();
    b.emit_push(1).emit_push(2).emit_add();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_step_limit(2);
    let err = vm.run(&bytecode).expect_err("two steps is one too few");
    let Error::StepLimitExceeded {
        requested,
        used,
        limit,
        ..
    } = err
    else {
        panic!("expected StepLimitExceeded, got {err:?}");
    };
    assert_eq!(requested, 1, "the base charge is what was refused");
    assert_eq!(used, 2);
    assert_eq!(limit, 2);
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cargo nextest run -p xqvm -E 'test(instructions_and_steps_agree_when_every_opcode_costs_base) + test(step_limit_error_reports_the_refused_charge)'
```

Expected: FAIL -- `no method named instructions`, and `StepLimitExceeded` has no `requested` field.

- [ ] **Step 3: Widen the error**

In `xqvm/src/error.rs`, replace the `StepLimitExceeded` variant:

```rust
    /// Execution exceeded the configured step budget.
    ///
    /// `requested` is the charge that could not be paid: `1` for the base
    /// cost every instruction pays before dispatch, or the extra units an
    /// opcode asked for before doing work whose size the program controls.
    /// `pos` is absent only for the base charge, which is levied before the
    /// instruction is decoded.
    #[error(
        "step charge of {requested} exceeds the step limit of {limit} \
         ({used} steps already charged)"
    )]
    StepLimitExceeded {
        pos: Option<usize>,
        requested: u64,
        used: u64,
        limit: u64,
    },
```

and move it out of the `None` arm of `pos()` into its own arm beside `ArithmeticOverflow`:

```rust
            Self::ArithmeticOverflow { pos } | Self::StepLimitExceeded { pos, .. } => *pos,
```

- [ ] **Step 4: Split the counters and add the helper**

In `xqvm/src/vm.rs`, add the field next to `steps` (`:272`) and reset it wherever `steps` is reset (`:297`, `:471`, `:506`):

```rust
    /// Instructions dispatched by the last run. Distinct from `steps`, which
    /// counts metered cost units: an opcode whose work scales with the
    /// program's own data charges more than one step for one dispatch.
    instructions: u64,
```

Add the accessor beside `steps()` (`:461`):

```rust
    /// Return the number of instructions dispatched by the last
    /// [`run`](Self::run) call.
    ///
    /// Distinct from [`steps`](Self::steps): a tracer numbers instructions,
    /// an embedder prices steps. See `spec/xqvm/METERING.md`.
    pub fn instructions(&self) -> u64 {
        self.instructions
    }
```

Replace the run-loop charge (`:515-520`) with a call to the helper, and count the dispatch:

```rust
            self.charge_steps_base()?;
            self.instructions += 1;
```

Add both helpers next to `charge` (`:670`):

```rust
    /// Charge `units` of execution against the step budget.
    ///
    /// Callers charge *before* they do the work, so an instruction that
    /// cannot pay does none of it -- the same discipline as [`charge`], and
    /// for the same reason: the budget is a bound on what a program can make
    /// the host do, which is worth nothing if the work happens first.
    fn charge_steps(&mut self, pos: usize, units: u64) -> Result<(), Error> {
        self.charge_steps_at(Some(pos), units)
    }

    /// Charge the base cost every instruction pays before dispatch. Levied
    /// before the instruction is decoded, so it carries no position.
    fn charge_steps_base(&mut self) -> Result<(), Error> {
        self.charge_steps_at(None, 1)
    }

    fn charge_steps_at(&mut self, pos: Option<usize>, units: u64) -> Result<(), Error> {
        let total = self.steps.saturating_add(units);
        if total > self.step_limit {
            return Err(Error::StepLimitExceeded {
                pos,
                requested: units,
                used: self.steps,
                limit: self.step_limit,
            });
        }
        self.steps = total;
        Ok(())
    }
```

- [ ] **Step 5: Feed the tracer from the ordinal**

In `xqvm/src/vm.rs`, the `StepState` construction at `:556` reads `step: self.steps`. Change it to `step: self.instructions` so a trace stays numbered `1, 2, 3, ...` once opcodes charge more than one unit.

- [ ] **Step 6: Run the tests**

```bash
cargo nextest run -p xqvm && cargo test --doc -p xqvm
```

Expected: PASS, including the pre-existing `zero_step_limit_executes_nothing`, `unlimited_steps_runs_to_completion`, `step_limit_equal_to_instruction_count_succeeds` and `step_limit_one_below_instruction_count_fails`, none of which change value.

- [ ] **Step 7: Check the rest of the workspace still builds**

```bash
make fmt && cargo clippy --workspace --all-targets --all-features -- -D warnings
```

Expected: clean. `conformance/src/lib.rs:377` matches `E::StepLimitExceeded { .. }` and is unaffected by the new fields.

- [ ] **Step 8: Commit**

```bash
git add xqvm/src/vm.rs xqvm/src/error.rs xqvm/tests/integration.rs && git commit -s -m "refactor(xqvm): meter steps through a charge helper"
```

---

### Task 2: Calibrate the unit against NOP

Measures what the constants in Task 3 have to be. Produces numbers, not behaviour.

**Files:**
- Create: `xqvm/examples/metering_calibration.rs`

**Interfaces:**
- Consumes: `Vm` from Task 1 (unchanged public API).
- Produces: measured ns/operation ratios, recorded in the commit message and reused by Task 3.

- [ ] **Step 1: Write the harness**

Create `xqvm/examples/metering_calibration.rs` with the AGPL header, then:

```rust
//! Measure the cost of the operations the step meter charges for, relative
//! to a `NOP` dispatch.
//!
//! Not a test and not run by CI: the constants in `xqvm::metering` are frozen
//! from a run of this harness and re-checked by hand when the interpreter
//! changes shape. Run it in release mode, on an otherwise idle machine:
//!
//! ```sh
//! cargo run --release --example metering_calibration
//! ```

#![expect(
    clippy::expect_used,
    clippy::print_stdout,
    reason = "a one-off measurement harness reports to stdout and may panic"
)]

use std::time::Instant;

use xqvm::bytecode::{InstructionBuilder, Register};
use xqvm::Vm;

/// Run `bytecode` `reps` times and return the mean nanoseconds per run.
fn time(bytecode: &xqvm::Program, reps: u32) -> f64 {
    let mut vm = Vm::new();
    vm.set_unlimited_steps();
    vm.set_memory_limit(u64::MAX);
    // Warm the instruction cache and the allocator before measuring.
    for _ in 0..16 {
        vm.run(bytecode).expect("warmup run");
    }
    let start = Instant::now();
    for _ in 0..reps {
        vm.run(bytecode).expect("measured run");
    }
    start.elapsed().as_nanos() as f64 / f64::from(reps)
}

fn main() {
    let reps = 2_000;

    // 1. NOP dispatch: the unit. 10_000 NOPs, minus the HALT, per run.
    let mut b = InstructionBuilder::new();
    for _ in 0..10_000 {
        b.emit_nop();
    }
    b.emit_halt();
    let nops = b.build().expect("build nops");
    let nop_ns = time(&nops, reps) / 10_000.0;

    // 2. A coefficient write into a model: PUSH i, PUSH j, PUSH v, SETQUAD.
    //    Subtract the three PUSHes, priced at the NOP rate.
    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i).emit_push((i + 1) % 1_000).emit_push(3).emit_set_quad(Register(0));
    }
    b.emit_halt();
    let writes = b.build().expect("build writes");
    let write_ns = time(&writes, reps) / 1_000.0 - 3.0 * nop_ns;

    // 3. An energy term: one model with 1_000 quadratic coefficients,
    //    evaluated once. Subtract the sample copy, measured in (4).
    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i).emit_push((i + 1) % 1_000).emit_push(3).emit_set_quad(Register(0));
    }
    b.emit_push(1_000).emit_bsmx(Register(1));
    b.emit_energy(Register(0), Register(1)).emit_halt();
    let with_energy = b.build().expect("build energy");

    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i).emit_push((i + 1) % 1_000).emit_push(3).emit_set_quad(Register(0));
    }
    b.emit_push(1_000).emit_bsmx(Register(1));
    b.emit_halt();
    let without_energy = b.build().expect("build baseline");
    let energy_ns = (time(&with_energy, reps) - time(&without_energy, reps)) / 1_000.0;

    // 4. A sample element: BSMX fills a buffer of `size`.
    let mut b = InstructionBuilder::new();
    b.emit_push(100_000).emit_bsmx(Register(0)).emit_halt();
    let big_sample = b.build().expect("build big sample");
    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_bsmx(Register(0)).emit_halt();
    let empty_sample = b.build().expect("build empty sample");
    let element_ns =
        (time(&big_sample, reps) - time(&empty_sample, reps)) / 100_000.0;

    println!("nop_ns              = {nop_ns:.3}");
    println!("coeff_write_ns      = {write_ns:.3}  ratio {:.2}", write_ns / nop_ns);
    println!("energy_term_ns      = {energy_ns:.3}  ratio {:.2}", energy_ns / nop_ns);
    println!("element_copy_ns     = {element_ns:.3}  ratio {:.2}", element_ns / nop_ns);
}
```

- [ ] **Step 2: Run it**

```bash
cargo run --release --example metering_calibration
```

Expected: four lines of output, `nop_ns` in the neighbourhood of the 6.73 ns recorded in QUI-1056. If `nop_ns` is an order of magnitude off, the machine is busy or the build is not release -- rerun before trusting the ratios.

- [ ] **Step 3: Record the ratios**

Round each ratio **up** to the next integer, with a floor of 1. Those integers are `COEFF_WRITE_STEPS`, `MODEL_TERM_STEPS` and `ELEMENT_COPY_STEPS` in Task 3. Take `SAMPLE_COPY_STEPS = ELEMENT_COPY_STEPS`: both are one `i64` written into a contiguous buffer. Write the four measured ns values and the four integers into the commit message body -- Task 9 copies them into `spec/xqvm/METERING.md`.

Rounding up is deliberate: a unit that is cheaper than the operation it stands for reintroduces exactly the underpricing this ticket is about.

- [ ] **Step 4: Commit**

```bash
git add xqvm/examples/metering_calibration.rs && git commit -s -m "test(xqvm): add the step-cost calibration harness"
```

---

### Task 3: The metering module

**Files:**
- Create: `xqvm/src/metering.rs`
- Modify: `xqvm/src/lib.rs`

**Interfaces:**
- Consumes: the four integers measured in Task 2.
- Produces (all `pub`, re-exported from the crate root as `xqvm::metering`):
  - `pub const BASE_STEPS: u64`, `COEFF_WRITE_STEPS: u64`, `MODEL_TERM_STEPS: u64`, `SAMPLE_COPY_STEPS: u64`, `ELEMENT_COPY_STEPS: u64`
  - `pub fn equality_expansion_steps(n: u64) -> u64`
  - `pub fn model_eval_steps(sample_len: u64, terms: u64) -> u64`
  - `pub fn value_copy_steps(value: &RegVal) -> u64`
  - `pub fn widen(n: usize) -> u64` -- the shared saturating `usize`-to-`u64` conversion

Later tasks assert against the *constants*, never against their numeric values, so a recalibration does not rewrite the test suite.

- [ ] **Step 1: Write the failing tests**

Create `xqvm/src/metering.rs` with the AGPL header and only its test module for now:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Domain, RegVal, XqmxModel, XqmxSample};

    #[test]
    fn expansion_charges_every_term_and_every_pair() {
        // n linear terms and one quadratic term per unordered pair, the same
        // worst case `equality_expansion_bytes` charges for.
        assert_eq!(equality_expansion_steps(0), 0);
        assert_eq!(equality_expansion_steps(1), COEFF_WRITE_STEPS);
        assert_eq!(equality_expansion_steps(4), 10 * COEFF_WRITE_STEPS);
    }

    #[test]
    fn expansion_saturates_rather_than_wrapping() {
        // An n large enough to overflow the pair count must price out, not
        // wrap into a charge the program can afford.
        assert_eq!(equality_expansion_steps(u64::MAX), u64::MAX);
    }

    #[test]
    fn model_eval_charges_the_copy_and_every_term() {
        assert_eq!(
            model_eval_steps(8, 3),
            8 * SAMPLE_COPY_STEPS + 3 * MODEL_TERM_STEPS
        );
    }

    #[test]
    fn copying_a_scalar_is_free_and_copying_a_model_is_not() {
        assert_eq!(value_copy_steps(&RegVal::Unset), 0);
        assert_eq!(value_copy_steps(&RegVal::Int(7)), 0);
        assert_eq!(
            value_copy_steps(&RegVal::VecInt(vec![1, 2, 3])),
            3 * ELEMENT_COPY_STEPS
        );
        assert_eq!(
            value_copy_steps(&RegVal::Sample(XqmxSample::new(Domain::Binary, vec![0; 5]))),
            5 * SAMPLE_COPY_STEPS
        );

        let mut m = XqmxModel::new(Domain::Binary, 4);
        m.set_linear(0, 1);
        m.set_quad(1, 2, 3);
        assert_eq!(value_copy_steps(&RegVal::Model(m)), 2 * COEFF_WRITE_STEPS);
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cargo nextest run -p xqvm -E 'test(metering::)'
```

Expected: FAIL -- `metering` is not a module of the crate.

- [ ] **Step 3: Write the module**

Above the test module in `xqvm/src/metering.rs`:

```rust
//! Execution cost units and the functions that count them.
//!
//! The VM meters execution in *steps*. A step is not an instruction: it is a
//! unit of work, calibrated so that one step is one `NOP` dispatch and no
//! step is cheaper than the operation it is charged for. Every instruction
//! pays [`BASE_STEPS`] before dispatch; the opcodes whose work scales with
//! data the program controls -- evaluating a model, expanding a constraint,
//! copying a register -- charge the extra units counted here before they do
//! that work.
//!
//! Without this, a single `ENERGY` over a model the program built itself
//! costs one step and an embedder pricing `WeightPerStep * steps` underprices
//! it without bound.
//!
//! The constants are consensus-visible: `xqvm_py/metering.py` mirrors them
//! value for value, `scripts/check-metering-parity.py` enforces it, and
//! `spec/xqvm/METERING.md` specifies them normatively. Changing one is a
//! breaking change to observable behaviour.

use crate::RegVal;

/// Cost of dispatching one instruction, charged before every dispatch.
///
/// This is the unit: one `NOP`, measured at 6.73 ns natively.
pub const BASE_STEPS: u64 = 1;

/// Cost of writing one coefficient into a model's sparse map.
pub const COEFF_WRITE_STEPS: u64 = 8;

/// Cost of accumulating one model term into a Hamiltonian energy.
pub const MODEL_TERM_STEPS: u64 = 2;

/// Cost of writing one element of a sample buffer.
pub const SAMPLE_COPY_STEPS: u64 = 1;

/// Cost of copying one `i64` out of a vec.
pub const ELEMENT_COPY_STEPS: u64 = 1;

/// Worst-case cost of an equality expansion over `n` terms: one linear term
/// per index and one quadratic term per unordered pair.
///
/// Saturating throughout, so an `n` large enough to overflow the pair count
/// yields `u64::MAX` and is refused by the budget rather than wrapping into
/// a small charge. Repeated indices collide on one map key, so an expansion
/// can write fewer entries than it is charged for: this bounds the work, it
/// does not measure it.
#[must_use]
pub fn equality_expansion_steps(n: u64) -> u64 {
    let pairs = n.saturating_mul(n.saturating_sub(1)) / 2;
    n.saturating_add(pairs).saturating_mul(COEFF_WRITE_STEPS)
}

/// Cost of evaluating a model with `terms` nonzero coefficients against a
/// sample of `sample_len` values, including the copy of the sample.
#[must_use]
pub fn model_eval_steps(sample_len: u64, terms: u64) -> u64 {
    sample_len
        .saturating_mul(SAMPLE_COPY_STEPS)
        .saturating_add(terms.saturating_mul(MODEL_TERM_STEPS))
}

/// Cost of copying a register value.
///
/// Scalars are free: the copy is a machine word. Everything else is charged
/// for what it actually holds, because cloning a model clones its
/// coefficient maps.
#[must_use]
pub fn value_copy_steps(value: &RegVal) -> u64 {
    match value {
        RegVal::Unset | RegVal::Int(_) => 0,
        RegVal::VecInt(v) => widen(v.len()).saturating_mul(ELEMENT_COPY_STEPS),
        RegVal::Sample(s) => widen(s.values.len()).saturating_mul(SAMPLE_COPY_STEPS),
        RegVal::Model(m) => widen(m.linear_len().saturating_add(m.quadratic_len()))
            .saturating_mul(COEFF_WRITE_STEPS),
        RegVal::VecXqmx(v) => v.iter().fold(0u64, |acc, m| {
            let terms = widen(m.linear_len().saturating_add(m.quadratic_len()));
            acc.saturating_add(terms.saturating_mul(COEFF_WRITE_STEPS))
        }),
    }
}

/// Widen a length to the charge width, saturating rather than truncating on
/// a 128-bit target. Matches `charge_variables`.
///
/// Public because `vm.rs` computes charge sizes from `usize` lengths at every
/// call site and must widen them the same way.
#[must_use]
pub fn widen(n: usize) -> u64 {
    u64::try_from(n).unwrap_or(u64::MAX)
}
```

Replace the three non-`BASE_STEPS` constant values with the integers measured in Task 2 if they differ from the ones above.

- [ ] **Step 4: Wire the module into the crate**

In `xqvm/src/lib.rs`, add `pub mod metering;` beside the other module declarations, in the same alphabetical position the existing list uses.

- [ ] **Step 5: Run the tests**

```bash
cargo nextest run -p xqvm -E 'test(metering::)' && cargo clippy -p xqvm --all-targets --all-features -- -D warnings
```

Expected: PASS, clean clippy.

- [ ] **Step 6: Check the no_std build still works**

```bash
cargo build -p xqvm --no-default-features
```

Expected: success. `metering.rs` uses only core and `crate::RegVal`.

- [ ] **Step 7: Commit**

```bash
git add xqvm/src/metering.rs xqvm/src/lib.rs && git commit -s -m "feat(xqvm): add calibrated execution cost units"
```

---

### Task 4: Charge `ENERGY`

The ticket's headline case: an unbounded model walk for one step.

**Files:**
- Modify: `xqvm/src/vm.rs:2414-2449` (`exec_energy`)
- Test: `xqvm/tests/integration.rs`

**Interfaces:**
- Consumes: `charge_steps` (Task 1), `metering::model_eval_steps`, `metering::MODEL_TERM_STEPS`, `metering::SAMPLE_COPY_STEPS` (Task 3).
- Produces: `ENERGY` charges `BASE_STEPS + model_eval_steps(sample.len(), linear_len + quadratic_len)`.

- [ ] **Step 1: Write the failing test**

Append to `xqvm/tests/integration.rs`:

```rust
#[test]
fn energy_charges_for_the_model_it_evaluates() {
    // Two models of different size, evaluated identically. If ENERGY still
    // cost one step the two runs would cost the same, which is the bug.
    fn steps_for(terms: i64) -> u64 {
        let mut b = InstructionBuilder::new();
        b.emit_push(terms).emit_bqmx(Register(0));
        for i in 0..terms {
            b.emit_push(i).emit_push(1).emit_set_line(Register(0));
        }
        b.emit_push(terms).emit_bsmx(Register(1));
        b.emit_energy(Register(0), Register(1)).emit_halt();
        let bytecode = b.build().expect("builder build");

        let mut vm = Vm::new();
        vm.set_unlimited_steps();
        vm.run(&bytecode).expect("vm run");
        vm.steps()
    }

    let small = steps_for(4);
    let large = steps_for(8);
    assert!(
        large > small,
        "a bigger model must cost more: {large} vs {small}"
    );

    // And by exactly the modelled amount: four more sample elements to copy
    // and four more linear terms to accumulate.
    assert_eq!(
        large - small,
        // four more instructions of setup (PUSH, PUSH, SETLINE) plus their
        // coefficient writes, four more sample elements, four more terms.
        4 * (3 * xqvm::metering::BASE_STEPS + xqvm::metering::COEFF_WRITE_STEPS)
            + 4 * xqvm::metering::SAMPLE_COPY_STEPS
            + 4 * xqvm::metering::MODEL_TERM_STEPS
    );
}
```

Note this test also depends on Task 5 charging `SETLINE`; run it again at the end of Task 5.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cargo nextest run -p xqvm -E 'test(energy_charges_for_the_model_it_evaluates)'
```

Expected: FAIL -- both runs cost the same, so `large > small` is false.

- [ ] **Step 3: Charge before evaluating**

In `exec_energy` (`vm.rs:2414`), the charge goes after both register types are established and before `m.energy(..)` is called -- and, importantly, before the sample is cloned. Restructure the head of the function so the sizes are read first:

```rust
        // Both halves of this are O(model): the sample is copied out of its
        // register and every coefficient is accumulated. Charge for both
        // before either happens, or a program can buy an arbitrarily large
        // model walk for one step (QUI-1056).
        let sample_len = match self.reg(sample) {
            RegVal::Sample(s) => s.values.len(),
            other => {
                return Err(Error::RegisterType {
                    reg: sample.slot(),
                    expected: "sample",
                    got: other.kind().kind_name(),
                });
            }
        };
        let terms = match self.reg(model) {
            RegVal::Model(m) => m.linear_len().saturating_add(m.quadratic_len()),
            other => {
                return Err(Error::RegisterType {
                    reg: model.slot(),
                    expected: "model",
                    got: other.kind().kind_name(),
                });
            }
        };
        self.charge_steps(pos, model_eval_steps(widen(sample_len), widen(terms)))?;
        let sample_values: Vec<i64> = match self.reg(sample) {
            RegVal::Sample(s) => s.values.clone(),
            _ => unreachable!("sample register type checked above"),
        };
        let RegVal::Model(m) = self.reg(model) else {
            unreachable!("model register type checked above")
        };
        let energy = m.energy(&sample_values)?;
```

Keep the existing doc comment about the model-as-sample shortcut: the type checks above preserve that behaviour, including the order in which the two register types are validated (sample first), which existing tests depend on.

Add `use crate::metering::{model_eval_steps, widen};` to the imports at the top of `vm.rs`; later tasks extend that same line.

- [ ] **Step 4: Run the tests**

```bash
cargo nextest run -p xqvm && cargo test --doc -p xqvm
```

Expected: the new assertion on the exact difference still fails (Task 5 charges `SETLINE`); the `large > small` half passes. If any *other* test fails, it is a real regression -- investigate before continuing.

- [ ] **Step 5: Commit**

```bash
git add xqvm/src/vm.rs xqvm/tests/integration.rs && git commit -s -m "fix(xqvm): charge ENERGY for the model it evaluates"
```

---

### Task 5: Charge the coefficient writes and constraint expansions

Every site that already charges bytes gets the matching step charge, computed from the same `n`.

**Files:**
- Modify: `xqvm/src/vm.rs` at `:1702`, `:1731`, `:1783`, `:1810` (coefficient writes), `:1998` (`ONEHOTR`), `:2051` (`ONEHOTC`), `:2199` (`EQUALITY`), `:2256` (`ATLEAST`), `:2344` (`ATLEASTW`), `:1593` (`SLACK`)
- Test: `xqvm/tests/integration.rs`

**Interfaces:**
- Consumes: `charge_steps`, `metering::{equality_expansion_steps, COEFF_WRITE_STEPS, ELEMENT_COPY_STEPS}`.
- Produces: the expansion opcodes charge `equality_expansion_steps(n)` for the same `n` their byte charge uses.

- [ ] **Step 1: Write the failing test**

Append to `xqvm/tests/integration.rs`:

```rust
#[test]
fn onehot_charges_for_the_expansion_it_writes() {
    // ONEHOTR writes one linear term per column and one quadratic term per
    // pair of columns. Doubling the columns roughly quadruples the work, and
    // the step count has to see it.
    fn steps_for(cols: i64) -> u64 {
        let mut b = InstructionBuilder::new();
        b.emit_push(cols).emit_bqmx(Register(0));
        b.emit_push(1).emit_push(cols).emit_resize(Register(0));
        b.emit_push(0).emit_push(5).emit_one_hot_r(Register(0));
        b.emit_halt();
        let bytecode = b.build().expect("builder build");

        let mut vm = Vm::new();
        vm.set_unlimited_steps();
        vm.run(&bytecode).expect("vm run");
        vm.steps()
    }

    use xqvm::metering::equality_expansion_steps;
    let four = steps_for(4);
    let eight = steps_for(8);
    assert_eq!(
        eight - four,
        equality_expansion_steps(8) - equality_expansion_steps(4),
        "the whole difference is the expansion: {eight} vs {four}"
    );
}

#[test]
fn a_coefficient_write_costs_more_than_a_nop() {
    // SETLINE inserts into a sparse map. Pricing it at a NOP is what let the
    // flat per-step weight be wrong (QUI-1056).
    let mut b = InstructionBuilder::new();
    b.emit_push(2).emit_bqmx(Register(0));
    b.emit_push(0).emit_push(3).emit_set_line(Register(0));
    b.emit_halt();
    let bytecode = b.build().expect("builder build");

    let mut vm = Vm::new();
    vm.set_unlimited_steps();
    vm.run(&bytecode).expect("vm run");

    // PUSH, BQMX, PUSH, PUSH, SETLINE, HALT = 6 dispatches.
    assert_eq!(vm.instructions(), 6);
    assert_eq!(
        vm.steps(),
        6 * xqvm::metering::BASE_STEPS + xqvm::metering::COEFF_WRITE_STEPS
    );
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cargo nextest run -p xqvm -E 'test(onehot_charges_for_the_expansion_it_writes) + test(a_coefficient_write_costs_more_than_a_nop)'
```

Expected: FAIL -- both differences come out at zero extra units.

- [ ] **Step 3: Charge each site**

Add `equality_expansion_steps`, `COEFF_WRITE_STEPS` and `ELEMENT_COPY_STEPS` to the `use crate::metering::{..}` line in `vm.rs`, then insert one line immediately **after** each existing byte charge, using the same `n` that charge uses:

| Existing line in `vm.rs` | Line to insert directly below |
|---|---|
| `:1702` `self.charge_coefficient(pos, reg, LINEAR_ENTRY_BYTES)?;` | `self.charge_coefficient_steps(pos, reg)?;` |
| `:1731` same | `self.charge_coefficient_steps(pos, reg)?;` |
| `:1783` `self.charge_coefficient(pos, reg, QUAD_ENTRY_BYTES)?;` | `self.charge_coefficient_steps(pos, reg)?;` |
| `:1810` same | `self.charge_coefficient_steps(pos, reg)?;` |
| `:1998` `self.charge_equality_expansion(pos, cols)?;` | `self.charge_steps(pos, equality_expansion_steps(widen(cols)))?;` |
| `:2051` `self.charge_equality_expansion(pos, rows)?;` | `self.charge_steps(pos, equality_expansion_steps(widen(rows)))?;` |
| `:2199` `self.charge_equality_expansion(pos, idx_vec.len())?;` | `self.charge_steps(pos, equality_expansion_steps(widen(idx_vec.len())))?;` |
| `:2256` `self.charge_equality_expansion(pos, n.saturating_add(num_slacks))?;` | `self.charge_steps(pos, equality_expansion_steps(widen(n.saturating_add(num_slacks))))?;` |
| `:2344` same as `:2256` | `self.charge_steps(pos, equality_expansion_steps(widen(n.saturating_add(num_slacks))))?;` |
| `:1593` `self.charge(pos, entries.saturating_mul(2 * VEC_ELEMENT_BYTES))?;` | `self.charge_steps(pos, entries.saturating_mul(2).saturating_mul(ELEMENT_COPY_STEPS))?;` |

Add the two private helpers next to `charge_coefficient` (`vm.rs:699`):

```rust
    /// Charge the step cost of one coefficient write into `reg`, but only
    /// when `reg` holds a model. The byte twin is [`charge_coefficient`];
    /// the reasoning about samples and repeated writes is identical.
    fn charge_coefficient_steps(&mut self, pos: usize, reg: Register) -> Result<(), Error> {
        if matches!(self.reg(reg), RegVal::Model(_)) {
            self.charge_steps(pos, COEFF_WRITE_STEPS)?;
        }
        Ok(())
    }
```

`widen` in the table above is `metering::widen` -- add it to the same `use crate::metering::{..}` line rather than writing a second copy in `vm.rs`.

`EQUALITY` and the `ATLEAST` family clone their index and coefficient vecs a few lines above the charge. That copy is O(n) and momentarily uncharged, but it is bounded by the same `n` that the expansion charge covers and it happens before the quadratic work, so the instruction still cannot escape its budget by more than a linear term. Leave the ordering as it is rather than restructuring three handlers; note it in `METERING.md` (Task 9).

- [ ] **Step 4: Run the tests**

```bash
cargo nextest run -p xqvm && cargo test --doc -p xqvm
```

Expected: PASS, including `energy_charges_for_the_model_it_evaluates` from Task 4, whose exact-difference assertion needs `SETLINE` charged.

- [ ] **Step 5: Commit**

```bash
git add xqvm/src/vm.rs xqvm/tests/integration.rs && git commit -s -m "fix(xqvm): charge constraint expansions for the terms they write"
```

---

### Task 6: Charge the allocators and the register copies

**Files:**
- Modify: `xqvm/src/vm.rs` at `:1438`, `:1447`, `:1458` (sample allocators), `:919` (`RANGE`/`ITER` int path), `:951` (`ITER` xqmx path), `:864` (`LVAL`), `:1082` (`INPUT`), `:1120` (`OUTPUT`)
- Test: `xqvm/tests/integration.rs`

**Interfaces:**
- Consumes: `charge_steps`, `metering::{value_copy_steps, SAMPLE_COPY_STEPS, ELEMENT_COPY_STEPS}`.
- Produces: no register-sized copy costs one step any more.

- [ ] **Step 1: Write the failing test**

Append to `xqvm/tests/integration.rs`:

```rust
#[test]
fn allocating_a_sample_costs_its_length() {
    // BSMX fills a buffer of `size`. One step for an arbitrarily long fill is
    // the same unbounded-work-per-step bug as ENERGY (QUI-1056).
    fn steps_for(size: i64) -> u64 {
        let mut b = InstructionBuilder::new();
        b.emit_push(size).emit_bsmx(Register(0)).emit_halt();
        let bytecode = b.build().expect("builder build");

        let mut vm = Vm::new();
        vm.set_unlimited_steps();
        vm.run(&bytecode).expect("vm run");
        vm.steps()
    }

    assert_eq!(
        steps_for(1_000) - steps_for(0),
        1_000 * xqvm::metering::SAMPLE_COPY_STEPS
    );
}

#[test]
fn reading_a_model_out_of_a_register_costs_its_coefficients() {
    // OUTPUT clones the register wholesale, and a register can hold a model.
    fn steps_for(terms: i64) -> u64 {
        let mut b = InstructionBuilder::new();
        b.emit_push(terms.max(1)).emit_bqmx(Register(0));
        for i in 0..terms {
            b.emit_push(i).emit_push(1).emit_set_line(Register(0));
        }
        b.emit_push(0).emit_output(Register(0)).emit_halt();
        let bytecode = b.build().expect("builder build");

        let mut vm = Vm::new();
        vm.set_unlimited_steps();
        vm.set_output_slots(1);
        vm.run(&bytecode).expect("vm run");
        vm.steps()
    }

    let extra_terms = 8;
    let per_term_setup = 3 * xqvm::metering::BASE_STEPS + xqvm::metering::COEFF_WRITE_STEPS;
    assert_eq!(
        steps_for(extra_terms) - steps_for(0),
        u64::try_from(extra_terms).expect("small") * per_term_setup
            // the OUTPUT clone now carries the model's coefficients too
            + u64::try_from(extra_terms).expect("small") * xqvm::metering::COEFF_WRITE_STEPS
    );
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cargo nextest run -p xqvm -E 'test(allocating_a_sample_costs_its_length) + test(reading_a_model_out_of_a_register_costs_its_coefficients)'
```

Expected: FAIL -- both differences are short by the copy cost.

- [ ] **Step 3: Charge each site**

Add `value_copy_steps` and `SAMPLE_COPY_STEPS` to the `use crate::metering::{..}` line, then:

- `exec_bsmx` (`:1438`), `exec_ssmx` (`:1447`), `exec_xsmx` (`:1458`): after each `self.charge_variables(pos, size)?;` add

  ```rust
        self.charge_steps(pos, widen(size).saturating_mul(SAMPLE_COPY_STEPS))?;
  ```

  Leave `exec_bqmx`, `exec_sqmx` and `exec_xqmx` alone: an empty model allocates no coefficients, so the base cost is honest.

- `exec_iter` int path (`:919`): after `self.charge_variables(pos, range.len())?;` add

  ```rust
                self.charge_steps(pos, widen(range.len()).saturating_mul(ELEMENT_COPY_STEPS))?;
  ```

- `exec_iter` xqmx path (`:951`): the handler already folds the slice to compute `cost` in bytes. Add a second fold in the same shape, immediately after `self.charge(pos, cost)?;`:

  ```rust
                let step_cost = v
                    .get(range.clone())
                    .unwrap_or_else(|| unreachable!("resolve_iter_slice already validated"))
                    .iter()
                    .fold(0u64, |acc, m| {
                        let terms = widen(m.linear_len().saturating_add(m.quadratic_len()));
                        acc.saturating_add(terms.saturating_mul(COEFF_WRITE_STEPS))
                    });
                self.charge(pos, cost)?;
                self.charge_steps(pos, step_cost)?;
  ```

  Compute `step_cost` from the same borrow that computes `cost`, before the `charge` call, so the two folds share one lookup.

- `exec_l_val` (`:864`), `exec_input` (`:1082`), `exec_output` (`:1120`): each clones a value. Charge for what is about to be cloned, before the clone:

  ```rust
        let copy_cost = value_copy_steps(&value_to_clone);
        self.charge_steps(pos, copy_cost)?;
  ```

  where `value_to_clone` is the borrow the handler already resolves. In `exec_output` (`:1120`) that means replacing

  ```rust
        let val = self.reg(reg).clone();
  ```

  with

  ```rust
        // A register can hold a model, and cloning one clones its
        // coefficient maps -- O(model) work for one dispatch (QUI-1056).
        let copy_cost = value_copy_steps(self.reg(reg));
        self.charge_steps(pos, copy_cost)?;
        let val = self.reg(reg).clone();
  ```

  Apply the same two-line prologue at `:864` and `:1082`, adjusting the expression that names the value.

- `RANGE` (`exec_range`, whose `charge_variables(pos, range.len())` is the call at `:919`'s sibling site): charge `widen(count).saturating_mul(ELEMENT_COPY_STEPS)` for the generated values, next to the existing byte charge.

- [ ] **Step 4: Run the whole suite**

```bash
cargo nextest run -p xqvm && cargo test --doc -p xqvm && cargo clippy --workspace --all-targets --all-features -- -D warnings
```

Expected: PASS, clean clippy. Doc tests that assert a step count -- notably the `set_memory_limit` example at `vm.rs:378` -- must still pass; if one now trips a step limit, that is a real finding: report it rather than raising the limit in the example.

- [ ] **Step 5: Commit**

```bash
git add xqvm/src/vm.rs xqvm/tests/integration.rs && git commit -s -m "fix(xqvm): charge allocators and register copies for their size"
```

---

### Task 7: Mirror the metering in the Python VM

Step counts are now observable, so `xqvm_py` must produce identical ones.

**Files:**
- Create: `xqvm_py/metering.py`
- Modify: `xqvm_py/executor.py` (`:314-324` run loop, `:338-368` charge helpers, and every `self._charge*` call site), `xqvm_py/state.py:268`, `xqvm_py/errors.py:158`
- Test: `xqvm_py/tests/test_executor.py`

**Interfaces:**
- Consumes: the constants and formulas frozen in Task 3.
- Produces: `xqvm_py.metering` with `BASE_STEPS`, `COEFF_WRITE_STEPS`, `MODEL_TERM_STEPS`, `SAMPLE_COPY_STEPS`, `ELEMENT_COPY_STEPS`, `equality_expansion_steps(n)`, `model_eval_steps(sample_len, terms)`, `value_copy_steps(value)`; `Executor.instructions` property; `StepLimitExceeded(limit, requested, used)`.

- [ ] **Step 1: Write the failing tests**

Append to `xqvm_py/tests/test_executor.py`, inside the class that holds the existing step-limit tests (`test_step_limit_raises_an_xqvm_error` at `:2980`):

```python
    def test_energy_charges_for_the_model_it_evaluates(self):
        """ENERGY walks every coefficient; one step for that is QUI-1056."""
        from xqvm_py.metering import (
            BASE_STEPS,
            COEFF_WRITE_STEPS,
            MODEL_TERM_STEPS,
            SAMPLE_COPY_STEPS,
        )

        def steps_for(terms: int) -> int:
            source = [f"PUSH {terms}", "BQMX r0"]
            for i in range(terms):
                source += [f"PUSH {i}", "PUSH 1", "SETLINE r0"]
            source += [f"PUSH {terms}", "BSMX r1", "ENERGY r0 r1", "HALT"]
            ex = Executor()
            ex.execute(assemble("\n".join(source)))
            return ex.steps

        delta = steps_for(8) - steps_for(4)
        assert delta == 4 * (3 * BASE_STEPS + COEFF_WRITE_STEPS) + 4 * (
            SAMPLE_COPY_STEPS + MODEL_TERM_STEPS
        )

    def test_instructions_and_steps_are_separate_counters(self):
        ex = Executor()
        ex.execute(assemble("PUSH 2\nBQMX r0\nPUSH 0\nPUSH 3\nSETLINE r0\nHALT"))
        assert ex.instructions == 6
        assert ex.steps > ex.instructions
```

Use whatever assembler helper the surrounding tests already use to turn source into a `Program`; do not introduce a second one.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest xqvm_py/tests/test_executor.py -k "charges_for_the_model or separate_counters" -v
```

Expected: FAIL -- `xqvm_py.metering` does not exist.

- [ ] **Step 3: Write the mirror module**

Create `xqvm_py/metering.py` with the AGPL header and a module docstring that says it mirrors `xqvm/src/metering.rs` value for value and points at `spec/xqvm/METERING.md`, then the same five constants and three functions. Python integers are unbounded, so the saturating arithmetic has to be written out:

```python
U64_MAX = (2**64) - 1


def _saturating(value: int) -> int:
    """Clamp to the u64 range the Rust VM's counters live in.

    Python integers do not overflow, so a charge that Rust saturates to
    ``u64::MAX`` would otherwise grow past it here and the two VMs would
    disagree about which programs are affordable.
    """
    return min(max(value, 0), U64_MAX)


def equality_expansion_steps(n: int) -> int:
    """Worst-case cost of an equality expansion over ``n`` terms."""
    n = _saturating(n)
    pairs = _saturating(n * (n - 1)) // 2
    return _saturating(_saturating(n + pairs) * COEFF_WRITE_STEPS)
```

`model_eval_steps` and `value_copy_steps` follow the Rust definitions in the Design section. `value_copy_steps` dispatches on the Python register value types the executor uses (`int`, `list[int]`, `XQMX` in model vs sample mode, `list[XQMX]`); read `_get_register_as_xqmx` and the `XQMX` class in `xqvm_py/xqmx.py` for the accessors that correspond to `linear_len()` and `quadratic_len()`.

- [ ] **Step 4: Split the counters and add the charge helper**

In `xqvm_py/state.py:268`, add `self.instructions = 0` beside `self.steps = 0` and reset it in the same place.

In `xqvm_py/executor.py`, replace the run loop at `:314-317`:

```python
        while not self.state.halted and self.state.pc < len(program):
            self._charge_steps(BASE_STEPS, step_limit)
            self.state.instructions += 1
            self.step()
```

and add, beside `_charge` (`:338`):

```python
    def _charge_steps(self, units: int, limit: int | None = None) -> None:
        """Charge ``units`` of execution against the step budget.

        Callers charge *before* they do the work, so an instruction that
        cannot pay does none of it -- the same discipline as ``_charge``.
        """
        limit = self._step_limit if limit is None else limit
        if limit is not None and self.state.steps + units > limit:
            raise StepLimitExceeded(limit, requested=units, used=self.state.steps)
        self.state.steps += units
```

Store the limit on the executor (`self._step_limit = step_limit` at the top of `execute`, beside `self._memory_limit`) so handlers can charge without threading it through, and add the `instructions` property next to `steps` (`:322`).

Extend `StepLimitExceeded` in `xqvm_py/errors.py:158` with the two new keyword fields, defaulted so existing raise sites keep working, and mirror the Rust message text.

- [ ] **Step 5: Charge the same sites the Rust VM charges**

Work through the Rust diff from Tasks 4-6 and add the twin call at each Python site. Every one of them is directly below an existing `self._charge*` call except `ENERGY`, `LVAL`, `INPUT` and `OUTPUT`:

| Python site | Charge |
|---|---|
| `_runner_ENERGY` (`:1145`) | `model_eval_steps` over the sample length and the model's term count, before `compute_energy` |
| `_runner_SETLINE`/`ADDLINE`/`SETQUAD`/`ADDQUAD` | `COEFF_WRITE_STEPS`, only when the register holds a model |
| `_runner_ONEHOTR` / `ONEHOTC` | `equality_expansion_steps(cols)` / `(rows)` |
| `_runner_EQUALITY` / `ATLEAST` / `ATLEASTW` | `equality_expansion_steps(n)` / `(n + num_slacks)` |
| `_runner_BSMX` / `SSMX` / `XSMX` | `size * SAMPLE_COPY_STEPS` |
| `_runner_RANGE` / `ITER` | `ELEMENT_COPY_STEPS` per element, `value_copy_steps` per model for the xqmx path |
| `_runner_LVAL` / `INPUT` / `OUTPUT` | `value_copy_steps(value)` |
| `_runner_SLACK` | `2 * entries * ELEMENT_COPY_STEPS` |

- [ ] **Step 6: Run the Python suite**

```bash
uv run --no-sync pytest xqvm_py/tests -q && make lint-py && make fmt-check-py
```

Expected: PASS. `xqvm_py/tests/test_cli_run.py:77` exercises the `--step-limit` flag; if it now trips, the fixture's program got more expensive -- adjust the fixture's limit, not the metering.

- [ ] **Step 7: Commit**

```bash
git add xqvm_py/metering.py xqvm_py/executor.py xqvm_py/state.py xqvm_py/errors.py xqvm_py/tests/test_executor.py && git commit -s -m "fix(xqvm-py): mirror the Rust step metering"
```

---

### Task 8: Lock the two implementations together

A number both VMs must agree on needs a guard that fails when they drift.

**Files:**
- Create: `scripts/check-metering-parity.py`, `conformance/vectors/metering/energy_scales_with_model/{program.xqasm,inputs.json,expected.json}`, `conformance/vectors/metering/onehot_expansion/{program.xqasm,inputs.json,expected.json}`
- Modify: `conformance/src/lib.rs:55-60` (`PyOut`), `:160-205` (`Expected`), `:215-235` (`Outcome`), `:344` and `:527` (the two runners), `:556` (`check`); `Makefile:91`

**Interfaces:**
- Consumes: `xqvm::metering` (Task 3), `xqvm_py.metering` (Task 7), `Vm::steps()`, `executor.steps` (already emitted by `xqvm_py/cli/run.py:159`).
- Produces: `make metering-parity`; an optional `steps` field on `expected.json` that both runners assert.

- [ ] **Step 1: Write the parity script's failing state**

Create `scripts/check-metering-parity.py` modelled on `scripts/check-opcode-parity.py` (read it first for the repo's conventions on argument handling, exit codes and diff output). It compares three sources and exits non-zero on any disagreement:

1. `xqvm/src/metering.rs` -- regex `^pub const (\w+): u64 = (\d+);` over the file.
2. `xqvm_py/metering.py` -- import the module and read the same names.
3. `spec/xqvm/METERING.md` -- the constants table, parsed as `| \`NAME\` | value | ...`.

Report every mismatched name with all three values, not just the first.

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --no-sync python scripts/check-metering-parity.py
```

Expected: FAIL -- `spec/xqvm/METERING.md` does not exist yet. That file arrives in Task 9; this script is what makes Task 9 non-optional.

- [ ] **Step 3: Wire it into the Makefile**

In `Makefile`, add the target beside `opcode-parity-py` and extend `check-parity` (`:91`):

```make
check-parity: opcode-parity conformance example-smoke metering-parity

metering-parity: deps-py
	uv run --no-sync python scripts/check-metering-parity.py
```

- [ ] **Step 4: Teach the conformance harness about steps**

In `conformance/src/lib.rs`:

- Add `steps: Option<u64>` to `RawExpected` and to `Expected::Success`; a vector that omits it asserts nothing about cost, so the existing 100-odd vectors keep passing unchanged.
- Add `steps: u64` to `Outcome::Success`, filled from `vm.steps()` in `run_rust_program` (`:344`) and from a new `steps: Option<u64>` field on `PyOut` in `run_python_file` (`:527`). The Python CLI already emits it.
- In `check` (`:556`), compare steps only when the vector supplies them:

```rust
            if let Some(exp_steps) = exp_steps {
                if steps != exp_steps {
                    let _ = writeln!(
                        detail,
                        "  steps:\n    expected: {exp_steps}\n    actual:   {steps}"
                    );
                }
            }
```

  Fold it into the existing mismatch-reporting shape rather than adding a second failure path.
- Update the module doc comment at `:25` to describe the new field.

- [ ] **Step 5: Add the vectors**

`conformance/vectors/metering/energy_scales_with_model/program.xqasm`: build a 4-variable binary model with four linear coefficients, allocate a matching sample, `ENERGY`, `STOW`, `OUTPUT`, `HALT`. Head it with a comment naming QUI-1056 and stating what the vector pins: that both implementations charge the same for a model walk.

`conformance/vectors/metering/onehot_expansion/program.xqasm`: a 4-column model, `RESIZE`, `ONEHOTR` with a penalty, `HALT`. Same comment discipline.

`inputs.json` for both: `{"calldata": [], "output_slots": 1}` (or `0` for the ONEHOTR vector, which produces no output).

`expected.json`: run each vector under both implementations, confirm the two agree, hand-check the number against the cost table in the Design section, and record it:

```json
{"outputs": [0], "final_stack": [], "steps": 42}
```

If the two implementations disagree, that is the guard doing its job -- fix the implementation, never the expectation.

- [ ] **Step 6: Run the conformance suite both ways**

```bash
make conformance
```

Expected: PASS for both `conformance-rs` and `conformance-py`, including the two new vectors.

- [ ] **Step 7: Commit**

```bash
git add scripts/check-metering-parity.py Makefile conformance && git commit -s -m "test(conformance): assert step counts agree across implementations"
```

---

### Task 9: Specify it

A count the chain prices is consensus-visible and belongs in the spec. `spec/xqvm/` currently says nothing about step budgets at all.

**Files:**
- Create: `spec/xqvm/METERING.md`
- Modify: `spec/xqvm/SPEC.md:91-102` (Runtime Limits) and `:127` (Related Specifications), `spec/xqvm/ISA.md` (notation section), `spec/xqvm/VERIFIER.md`, `docs/book/src/xqvm/execution.md:45-56`, `docs/book/src/xqvm/limits-and-errors.md:26`, `xqffi/src/vm.rs:288`

**Interfaces:**
- Consumes: the constants from Task 3 and the measured ns values recorded in Task 2's commit message.
- Produces: `spec/xqvm/METERING.md`, the third source `scripts/check-metering-parity.py` reads; `XqVm.instructions()` on the FFI surface.

- [ ] **Step 1: Write the specification**

Create `spec/xqvm/METERING.md` following the house style of `spec/xqvm/HLF.md` (normative language, tables, no emojis, `--` not em-dash). It must contain:

- **What a step is.** A unit of work, not an instruction. One step is one `NOP` dispatch. Every instruction charges `BASE_STEPS` before dispatch; opcodes whose work scales with program-controlled data charge more, before doing the work.
- **The constants table**, in the exact shape `scripts/check-metering-parity.py` parses:

```markdown
| Constant | Value | Meaning |
|----------|-------|---------|
| `BASE_STEPS` | 1 | One instruction dispatch. |
| `COEFF_WRITE_STEPS` | 8 | One coefficient written into a model's sparse map. |
| `MODEL_TERM_STEPS` | 2 | One model term accumulated into an energy. |
| `SAMPLE_COPY_STEPS` | 1 | One sample element written. |
| `ELEMENT_COPY_STEPS` | 1 | One `i64` copied out of a vec. |
```

  with the values from Task 3 and a note recording the measured nanoseconds each was derived from.
- **The per-opcode cost table** from the Design section above, verbatim.
- **The formulas** for `equality_expansion_steps`, `model_eval_steps` and `value_copy_steps`, including the statement that all arithmetic is integer and saturating, and that an expansion count is a worst case that bounds the work rather than measuring it.
- **The known slack**: `EQUALITY` and the `ATLEAST` family clone their index and coefficient vecs before charging, so up to `O(n)` element copies precede the charge for the `O(n^2)` expansion that follows.
- **Conformance**: step counts are observable; two implementations executing the same program must report the same count.

- [ ] **Step 2: Link it from the spec**

In `spec/xqvm/SPEC.md`, replace the `Loop nesting` and `XQMX size` notes' silence about time with a metering row in the Runtime Limits table:

```markdown
| Step budget | Implementation-defined | A step is a unit of work, not an instruction. Implementations meter execution as specified in [METERING.md](METERING.md) and raise `StepLimitExceeded` when the budget is exhausted. |
```

Add `METERING.md` to the Related Specifications list at `:127`.

In `spec/xqvm/ISA.md`, add one line to the Notation section: opcode cost is specified in `METERING.md`, not per-row here.

In `spec/xqvm/VERIFIER.md`, state that a step count is data-dependent and cannot be predicted statically: the verifier bounds execution, it does not predict its cost. This mirrors what is already true of the memory budget.

- [ ] **Step 3: Update the book**

- `docs/book/src/xqvm/execution.md:45-56`: the paragraph claiming the counter "increments after every instruction dispatch" is now wrong. Rewrite it around the two counters, and link `spec/xqvm/METERING.md`.
- `docs/book/src/xqvm/limits-and-errors.md:26`: the `Step count` row needs the same correction, plus the new `StepLimitExceeded` message shape at `:114`.

Leave the stale `set_step_limit(0)` prose at `execution.md:53` and `limits-and-errors.md:36` alone -- it predates QUI-1053 and is a separate fix.

- [ ] **Step 4: Expose the ordinal on the FFI**

In `xqffi/src/vm.rs`, beside `steps()` (`:288`):

```rust
    /// Total instructions dispatched by the last run.
    ///
    /// Distinct from `steps()`, which counts metered cost units: an opcode
    /// whose work scales with the program's own data charges more than one
    /// step for one dispatch. See `spec/xqvm/METERING.md`.
    fn instructions(&self) -> u64 {
        self.inner.instructions()
    }
```

and correct the `set_step_limit` doc comment at `:233`, which currently calls it "the instruction-step limit".

- [ ] **Step 5: Run every guard**

```bash
make preflight
```

Expected: PASS. In particular `metering-parity` now finds all three sources, `check-docs-drift` accepts the new page links, and `check-mermaid-render` is unaffected.

- [ ] **Step 6: Commit**

```bash
git add spec docs xqffi/src/vm.rs && git commit -s -m "docs(xqvm): specify step metering"
```

---

## Verification

Run before declaring the branch done:

```bash
make preflight
```

That covers `preflight-rs` (fmt, taplo, clippy, rustdoc, deny, unit/integration/doc tests), `preflight-py`, `preflight-parity` (opcode parity, metering parity, conformance both ways, example smoke), `preflight-docs` and `preflight-policy`.

Then confirm the ticket's own claim by hand:

```bash
cargo run --release --example metering_calibration
```

The ratio between the dearest charged operation and `NOP` is the factor by which a flat per-step price was previously wrong. That number belongs in the QUI-1056 close-out comment and is the input QUI-1054 needs for its safety multiplier.

## Follow-ups, not this branch

- QUI-1054 / QUI-1013: recalibrate `WeightPerStep` against `NOP` plus a safety multiplier now that a step is bounded work.
- QUI-1019: this is a behavioural break for any program that touches a model; it ships in 0.4.0.
- The stale `set_step_limit(0)` prose in the book (`execution.md:53`, `limits-and-errors.md:36`), left over from QUI-1053.
