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

//! The specification vectors under `tests/vectors/`, run on the VM.
//!
//! Each vector is a small program with its inputs and its expected result,
//! derived from `spec/xqvm/`. The vectors are data files rather than Rust
//! tests so they stay reviewable as spec artefacts and replayable by other
//! harnesses, such as the chain's metering check.
//!
//! This is a `harness = false` test target driven by `libtest-mimic`, so
//! every vector is still its own named, filterable test without a build
//! script generating one function per directory:
//!
//! ```sh
//! cargo test -p xqvm --test vectors                   # everything
//! cargo test -p xqvm --test vectors -- arithmetic     # one category
//! cargo test -p xqvm --test vectors -- --ignored coverage::report --nocapture
//! ```
//!
//! The target assembles each `program.xqasm` with `xqasm`, a path-only
//! dev-dependency that the published tarball drops, so it is excluded from
//! the package and runs from the workspace only.

mod coverage;
mod vector;

use libtest_mimic::{Arguments, Failed, Trial};

fn main() {
    let args = Arguments::from_args();

    let vectors = vector::discover().into_iter().map(|(category, name)| {
        Trial::test(format!("vector::{category}::{name}"), move || {
            run_vector(&category, &name)
        })
    });
    let format_tests = vector::self_tests::TESTS
        .iter()
        .map(|&(name, test)| plain_trial("format", name, test));
    let coverage_tests = coverage::TESTS
        .iter()
        .map(|&(name, test)| plain_trial("coverage", name, test));
    let report = Trial::test("coverage::report", print_coverage_report).with_ignored_flag(true);

    let trials = vectors
        .chain(format_tests)
        .chain(coverage_tests)
        .chain(std::iter::once(report))
        .collect();

    libtest_mimic::run(&args, trials).exit();
}

/// Load a vector, run it, and compare the result against `expected.json`.
fn run_vector(category: &str, name: &str) -> Result<(), Failed> {
    let vector =
        vector::load(category, name).map_err(|e| format!("cannot load {category}/{name}: {e}"))?;
    let outcome = vector::run(&vector)?;
    vector::check(&outcome, &vector.expected)?;
    Ok(())
}

/// Wrap a test that signals failure by panicking. `libtest-mimic` catches
/// the panic and reports it as the trial's failure.
fn plain_trial(module: &str, name: &str, test: fn()) -> Trial {
    Trial::test(format!("{module}::{name}"), move || {
        test();
        Ok(())
    })
}

/// Print which opcodes no vector covers. Ignored by default: it reports
/// and never fails, and the ratchet is what gates on coverage.
#[expect(
    clippy::print_stdout,
    clippy::unnecessary_wraps,
    reason = "a report trial: printing is its purpose, and a trial returns a Result"
)]
fn print_coverage_report() -> Result<(), Failed> {
    print!("{}", coverage::Coverage::collect().render());
    Ok(())
}
