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

#![expect(
    clippy::print_stdout,
    reason = "example binary: stdout output is intentional"
)]

//! Travelling Salesman Problem showcase using the XQVM interpreter.
//!
//! This example demonstrates a three-program XQVM pipeline for formulating,
//! verifying, and decoding a TSP as a QUBO (Quadratic Unconstrained Binary
//! Optimisation) problem.  All inter-program data (models, vectors, integers)
//! flows through calldata slots and output slots via the `INPUT`/`OUTPUT`
//! instructions.
//!
//! # Problem encoding
//!
//! For N cities, the QUBO uses N*N binary variables x[city*N + position].
//! The Hamiltonian is:
//!
//! H = `H_dist` + `H_row` + `H_col`
//!
//! Where:
//! - `H_dist`: sum d[ci][cj]*x[ci*N+p]*x[cj*N+(p+1)%N] for adjacent cities
//! - `H_row`:  penalty*(`sum_p` x[ci*N+p] - 1)^2 for each city ci
//! - `H_col`:  penalty*(`sum_c` x[c*N+p]  - 1)^2 for each position p
//!
//! # Pipeline
//!
//! 1. **Encoder** -- reads `N` and `distances` from input slots, builds the
//!    QUBO model, and writes it to output slot 0.
//! 2. **Verifier** -- reads the model, sample, and `N` from input slots;
//!    checks row/column one-hot constraints; writes energy and validity flag
//!    to output slots.
//! 3. **Decoder** -- reads the sample and `N` from input slots; extracts the
//!    ordered tour; writes it to output slot 0.

use miette::{IntoDiagnostic, Result, WrapErr, ensure, miette};
use xqasm::assemble_source;
use xqvm::{Domain, RegVal, Vm, XqmxModel, XqmxSample};

const ENCODER_ASM: &str = include_str!("encoder.xqasm");
const VERIFIER_ASM: &str = include_str!("verifier.xqasm");
const DECODER_ASM: &str = include_str!("decoder.xqasm");

// ---------------------------------------------------------------------------
// Example driver
// ---------------------------------------------------------------------------

fn main() -> Result<()> {
    // 4-city ring: distance i<->j = min(|i-j|, 4-|i-j|).
    // Row-major flat distance matrix (symmetric, zeros on diagonal).
    let n: usize = 4;
    let n_i64 = i64::try_from(n).into_diagnostic()?;
    #[rustfmt::skip]
    let distances: Vec<i64> = vec![
        0, 1, 2, 3,
        1, 0, 1, 2,
        2, 1, 0, 1,
        3, 2, 1, 0,
    ];

    // -- Step 1: encode the TSP as a QUBO model ---------------------------------

    let encoder_bc = assemble_source(ENCODER_ASM)
        .into_diagnostic()
        .wrap_err("encoder assembly failed")?;

    let mut vm = Vm::new();
    let _ = vm.set_calldata(vec![RegVal::Int(n_i64), RegVal::VecInt(distances)]);
    let _ = vm.set_output_slots(1);
    vm.run(&encoder_bc)
        .into_diagnostic()
        .wrap_err("encoder run failed")?;

    let qubo = match vm.outputs().first() {
        Some(v) => v.clone(),
        None => return Err(miette!("encoder produced no output")),
    };

    if let RegVal::Model(ref m) = qubo {
        println!(
            "QUBO: {} variables, {} linear terms, {} quadratic terms",
            m.size,
            m.linear_len(),
            m.quadratic_len()
        );
    }

    // -- Step 2: build the identity sample (city i visits position i) -----------
    //
    // The verifier needs the sample in two shapes:
    //
    //   * `sample_grid` is an `XqmxModel` whose linear table stores
    //     `linear[city*N + position] = 1`. The grid instructions
    //     (`ROWSUM`, `COLSUM`, `ROWFIND`, `COLFIND`) only operate on
    //     models, so the one-hot row/column scans need this shape.
    //   * `sample_values` is an `XqmxSample` carrying the same dense
    //     vector. `ENERGY` requires its sample operand to be an
    //     `XqmxSample`, matching the xq-py spec; xq-rs used to allow a
    //     model in the sample slot, but that shortcut was removed in
    //     QUI-410.
    //
    // Both are derived from the same dense values so they cannot drift.

    let mut dense = vec![0i64; n * n];
    for i in 0..n {
        if let Some(slot) = dense.get_mut(i * n + i) {
            *slot = 1;
        }
    }

    let mut sample_grid = XqmxModel::new(Domain::Binary, n * n);
    sample_grid.rows = n;
    sample_grid.cols = n;
    for (idx, &val) in dense.iter().enumerate() {
        sample_grid.set_linear(idx, val);
    }
    let sample_grid_val = RegVal::Model(sample_grid);
    let sample_values_val = RegVal::Sample(XqmxSample::new(Domain::Binary, dense));

    // -- Step 3: verify the sample ----------------------------------------------

    let verifier_bc = assemble_source(VERIFIER_ASM)
        .into_diagnostic()
        .wrap_err("verifier assembly failed")?;

    let mut vm = Vm::new();
    let _ = vm.set_calldata(vec![
        qubo,
        sample_grid_val.clone(),
        sample_values_val,
        RegVal::Int(n_i64),
    ]);
    let _ = vm.set_output_slots(2);
    vm.run(&verifier_bc)
        .into_diagnostic()
        .wrap_err("verifier run failed")?;

    let energy = match vm.outputs().first() {
        Some(RegVal::Int(v)) => *v,
        Some(other) => return Err(miette!("expected Int for energy output, got {other:?}")),
        None => return Err(miette!("verifier produced no output at slot 0")),
    };
    let is_valid = match vm.outputs().get(1) {
        Some(RegVal::Int(v)) => *v != 0,
        Some(other) => return Err(miette!("expected Int for valid flag output, got {other:?}")),
        None => return Err(miette!("verifier produced no output at slot 1")),
    };

    // -- Step 4: decode the sample into an ordered tour -------------------------

    let decoder_bc = assemble_source(DECODER_ASM)
        .into_diagnostic()
        .wrap_err("decoder assembly failed")?;

    let mut vm = Vm::new();
    let _ = vm.set_calldata(vec![sample_grid_val, RegVal::Int(n_i64)]);
    let _ = vm.set_output_slots(1);
    vm.run(&decoder_bc)
        .into_diagnostic()
        .wrap_err("decoder run failed")?;

    let tour = match vm.outputs().first() {
        Some(RegVal::VecInt(v)) => v.clone(),
        Some(other) => return Err(miette!("expected VecInt for tour output, got {other:?}")),
        None => return Err(miette!("decoder produced no output")),
    };

    // -- Print results ----------------------------------------------------------

    println!("TSP ({n} cities, ring distances)");
    println!("Tour:   {tour:?}");
    println!("Energy: {energy}");
    println!("Valid:  {is_valid}");

    // Sanity: identity tour is valid and has the expected energy.
    // H_dist = d(0,1)+d(1,2)+d(2,3)+d(3,0) = 1+1+1+3 = 6
    // H_one_hot (linear) = 4 cities * 2 constraints * (-100) = -800
    // H_one_hot (quadratic) = 0 (one variable selected per row and column)
    // Total = 6 - 800 = -794
    ensure!(
        is_valid,
        "identity tour must satisfy all one-hot constraints"
    );
    ensure!(
        energy == -794,
        "energy mismatch for identity tour: expected -794, got {energy}"
    );
    ensure!(
        tour == vec![0, 1, 2, 3],
        "identity tour should decode to [0,1,2,3], got {tour:?}"
    );

    Ok(())
}
