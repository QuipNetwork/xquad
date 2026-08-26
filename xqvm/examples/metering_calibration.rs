// Copyright (C) 2026 Postquant Labs Incorporated
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.
//
// SPDX-License-Identifier: AGPL-3.0-or-later

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
//!
//! Each measurement is a paired baseline (the program under test minus an
//! otherwise-identical program without the operation under test), repeated
//! [`TRIALS`] times; the reported number is the *median* trial, so one
//! descheduled run cannot drag the result. Min and max are printed alongside
//! it so the spread is visible without re-running by hand.

#![expect(
    clippy::expect_used,
    clippy::print_stdout,
    unused_results,
    reason = "a one-off measurement harness reports to stdout, may panic, \
              and discards the builder-style setters' `&mut Self` returns"
)]
#![expect(
    clippy::cast_precision_loss,
    reason = "converting nanosecond and repetition counts to f64 for a mean \
              is exact at the magnitudes this harness runs, and any residual \
              error is far below what matters for rounding a ratio up"
)]
#![expect(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "the derived per-operation step count is a small ratio (well \
              under u32::MAX) rounded up and floored at 1 before the cast"
)]

use std::time::Instant;

use xqvm::Vm;
use xqvm::bytecode::{InstructionBuilder, Register};

/// Number of independent trials per measurement; the reported value is the
/// median across these, with min/max printed alongside it. Odd, so the
/// median is a single observed sample rather than an average of two.
const TRIALS: u32 = 11;

/// Run `bytecode` `reps` times and return the mean nanoseconds per run.
///
/// `reset()`s the VM before every run: `Vm::run` does not clear the stack on
/// its own (see `Vm::reset`), and some of the programs measured here
/// deliberately leave values on the stack at `HALT` (see the coefficient
/// write baseline in `main`), which would otherwise accumulate across
/// repeated runs and overflow the 8_192-deep stack limit.
fn time(bytecode: &xqvm::Program, reps: u32) -> f64 {
    let mut vm = Vm::new();
    vm.set_unlimited_steps();
    vm.set_memory_limit(u64::MAX);
    // Warm the instruction cache and the allocator before measuring.
    for _ in 0..16 {
        vm.reset();
        vm.run(bytecode).expect("warmup run");
    }
    let start = Instant::now();
    for _ in 0..reps {
        vm.reset();
        vm.run(bytecode).expect("measured run");
    }
    start.elapsed().as_nanos() as f64 / f64::from(reps)
}

/// Sorted (median, min, max) of `samples`. Panics on an empty slice: every
/// call site here passes exactly `TRIALS` samples.
fn stats(samples: &mut [f64]) -> (f64, f64, f64) {
    samples.sort_by(f64::total_cmp);
    let min = *samples.first().expect("at least one trial");
    let max = *samples.last().expect("at least one trial");
    let median = *samples.get(samples.len() / 2).expect("at least one trial");
    (median, min, max)
}

/// Time `program` for `TRIALS` independent trials, each the mean of `reps`
/// runs divided by `unit_count`, and return (median, min, max) nanoseconds.
fn measure(program: &xqvm::Program, reps: u32, unit_count: f64) -> (f64, f64, f64) {
    let mut samples: Vec<f64> = (0..TRIALS)
        .map(|_| time(program, reps) / unit_count)
        .collect();
    stats(&mut samples)
}

/// Time the paired difference `with` minus `without` for `TRIALS`
/// independent trials (each pair measured back to back, so the two halves of
/// a trial share whatever system noise is present at that moment), each
/// divided by `unit_count`, and return (median, min, max) nanoseconds.
fn measure_diff(
    with: &xqvm::Program,
    without: &xqvm::Program,
    reps: u32,
    unit_count: f64,
) -> (f64, f64, f64) {
    let mut samples: Vec<f64> = (0..TRIALS)
        .map(|_| (time(with, reps) - time(without, reps)) / unit_count)
        .collect();
    stats(&mut samples)
}

/// Round `ratio` up to the next integer, floored at 1 -- a unit cheaper than
/// the operation it prices reintroduces the underpricing this harness exists
/// to catch.
fn round_up_steps(ratio: f64) -> u32 {
    ratio.ceil().max(1.0) as u32
}

/// Print one derived measurement: median/min/max in nanoseconds, the ratio
/// to the NOP rate, and the step count that ratio implies. Returns that step
/// count, so a constant derived from more than one measurement can be taken
/// as the maximum over them.
fn report(label: &str, constant: &str, stats: (f64, f64, f64), nop_ns: f64) -> u32 {
    let (median_ns, min_ns, max_ns) = stats;
    let ratio = median_ns / nop_ns;
    let steps = round_up_steps(ratio);
    println!(
        "{label:<16}= median {median_ns:7.3}  min {min_ns:7.3}  max {max_ns:7.3}  \
         ratio {ratio:5.2}  => {constant} = {steps}"
    );
    steps
}

/// Measure one grid cell read: `ROWSUM` walks `cols` cells of the linear
/// surface, one sparse-map lookup each. This is the per-cell cost `ROWSUM`,
/// `COLSUM`, `ROWFIND` and `COLFIND` are charged for. Measured over a model
/// rather than a sample because the model's sparse lookup is the dearer of
/// the two surfaces and the constant must not be cheaper than the operation
/// it prices. The row is fully populated, so every lookup hits rather than
/// missing -- a miss can short-circuit and would measure low. Paired against
/// the identical setup without the `ROWSUM`.
fn grid_cell_stats(reps: u32) -> (f64, f64, f64) {
    let grid_cols = 1_000i64;
    let setup = |b: &mut InstructionBuilder| {
        b.emit_push(grid_cols).emit_bqmx(Register(0));
        for i in 0..grid_cols {
            b.emit_push(i).emit_push(3).emit_set_line(Register(0));
        }
        b.emit_push(1).emit_push(grid_cols).emit_resize(Register(0));
    };

    let mut b = InstructionBuilder::new();
    setup(&mut b);
    b.emit_push(0).emit_row_sum(Register(0)).emit_halt();
    let with_scan = b.build().expect("build grid scan");

    let mut b = InstructionBuilder::new();
    setup(&mut b);
    b.emit_push(0).emit_halt();
    let without_scan = b.build().expect("build grid scan baseline");

    #[expect(
        clippy::cast_precision_loss,
        reason = "grid_cols is 1_000, exactly representable as f64"
    )]
    let cells = grid_cols as f64;
    measure_diff(&with_scan, &without_scan, reps, cells)
}

fn main() {
    let reps = 2_000;

    // 1. NOP dispatch: the unit. Time 10_000 NOPs plus one HALT and divide
    //    by 10_000; the HALT's one-time cost is amortized away by the large
    //    NOP count and not measured or subtracted separately.
    let mut b = InstructionBuilder::new();
    for _ in 0..10_000 {
        b.emit_nop();
    }
    b.emit_halt();
    let nops = b.build().expect("build nops");
    let (nop_ns, nop_min, nop_max) = measure(&nops, reps, 10_000.0);

    // 2. A coefficient write into a model: PUSH i, PUSH j, PUSH v, SETQUAD.
    //    Paired against a baseline that emits the identical three PUSHes per
    //    iteration but not the SETQUAD, so the two programs differ only in
    //    the operation under test. The baseline's PUSHes are never popped
    //    (1_000 iterations x 3 = 3_000 values, well under the 8_192-deep
    //    stack limit), which is fine: nothing after HALT reads the stack.
    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i)
            .emit_push((i + 1) % 1_000)
            .emit_push(3)
            .emit_set_quad(Register(0));
    }
    b.emit_halt();
    let writes = b.build().expect("build writes");

    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i).emit_push((i + 1) % 1_000).emit_push(3);
    }
    b.emit_halt();
    let writes_baseline = b.build().expect("build writes baseline");
    let write_stats = measure_diff(&writes, &writes_baseline, reps, 1_000.0);

    // 2b. The same coefficient write, but read-modify-write: ADDQUAD looks
    //     the entry up before it inserts. `COEFF_WRITE_STEPS` prices every
    //     coefficient write in the VM, and the constraint expansions
    //     (`ONEHOTR`, `ONEHOTC`, `EQUALITY`, `ATLEAST`, `ATLEASTW`, `REDUCE`)
    //     all go through `add_linear`/`add_quad`, not the insert-only
    //     `set_*`. The constant is the maximum of the two ratios, so it is
    //     never cheaper than the operation it stands for. Same keys, same
    //     paired baseline, so the difference is only SETQUAD versus ADDQUAD.
    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i)
            .emit_push((i + 1) % 1_000)
            .emit_push(3)
            .emit_add_quad(Register(0));
    }
    b.emit_halt();
    let rmw_writes = b.build().expect("build rmw writes");
    let rmw_stats = measure_diff(&rmw_writes, &writes_baseline, reps, 1_000.0);

    // 3. An energy term: one model with 1_000 quadratic coefficients,
    //    evaluated once. Paired against the identical setup without the
    //    ENERGY call.
    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i)
            .emit_push((i + 1) % 1_000)
            .emit_push(3)
            .emit_set_quad(Register(0));
    }
    b.emit_push(1_000).emit_bsmx(Register(1));
    b.emit_energy(Register(0), Register(1)).emit_halt();
    let with_energy = b.build().expect("build energy");

    let mut b = InstructionBuilder::new();
    b.emit_push(1_000).emit_bqmx(Register(0));
    for i in 0..1_000i64 {
        b.emit_push(i)
            .emit_push((i + 1) % 1_000)
            .emit_push(3)
            .emit_set_quad(Register(0));
    }
    b.emit_push(1_000).emit_bsmx(Register(1));
    b.emit_halt();
    let without_energy = b.build().expect("build baseline");
    let energy_stats = measure_diff(&with_energy, &without_energy, reps, 1_000.0);

    let grid_stats = grid_cell_stats(reps);

    // 4. A sample element: BSMX fills a buffer of `size`. Paired against an
    //    empty (size 0) BSMX so the two programs differ only in element
    //    count, not in the allocation itself.
    let mut b = InstructionBuilder::new();
    b.emit_push(100_000).emit_bsmx(Register(0)).emit_halt();
    let big_sample = b.build().expect("build big sample");
    let mut b = InstructionBuilder::new();
    b.emit_push(0).emit_bsmx(Register(0)).emit_halt();
    let empty_sample = b.build().expect("build empty sample");
    let element_stats = measure_diff(&big_sample, &empty_sample, reps, 100_000.0);

    println!(
        "nop_ns          = median {nop_ns:7.3}  min {nop_min:7.3}  max {nop_max:7.3}  (the unit)"
    );
    let set_steps = report(
        "coeff_write_ns",
        "COEFF_WRITE_STEPS(set)",
        write_stats,
        nop_ns,
    );
    let add_steps = report("coeff_rmw_ns", "COEFF_WRITE_STEPS(add)", rmw_stats, nop_ns);
    println!(
        "COEFF_WRITE_STEPS = max(set {set_steps}, add {add_steps}) = {}",
        set_steps.max(add_steps)
    );
    report("energy_term_ns", "MODEL_TERM_STEPS", energy_stats, nop_ns);
    report("grid_cell_ns", "GRID_CELL_STEPS", grid_stats, nop_ns);
    report(
        "element_copy_ns",
        "ELEMENT_COPY_STEPS",
        element_stats,
        nop_ns,
    );
}
