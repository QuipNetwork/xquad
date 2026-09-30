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

//! Timing shared by the std-only benches in this directory.
//!
//! Each figure is the median of 31 samples of 100 calls, after 200 warm-up
//! calls, so no benchmark framework is needed.

use std::hint::black_box;
use std::time::{Duration, Instant};

const WARMUP_CALLS: u32 = 200;
const SAMPLES: usize = 31;
const CALLS_PER_SAMPLE: u32 = 100;

/// Median wall time of one call to `f`.
fn median_call<R>(mut f: impl FnMut() -> R) -> Duration {
    for _ in 0..WARMUP_CALLS {
        let _ = black_box(f());
    }
    let mut samples: Vec<Duration> = (0..SAMPLES)
        .map(|_| {
            let start = Instant::now();
            for _ in 0..CALLS_PER_SAMPLE {
                let _ = black_box(f());
            }
            start.elapsed() / CALLS_PER_SAMPLE
        })
        .collect();
    samples.sort_unstable();
    samples.get(SAMPLES / 2).copied().unwrap_or_default()
}

/// Time `f` and print one row: the median call, and that call divided over
/// `units` of work, each named `unit` (a byte, a step).
#[expect(
    clippy::print_stdout,
    reason = "a harness = false bench reports on stdout"
)]
#[expect(
    clippy::cast_precision_loss,
    reason = "the unit counts a bench divides by are far below 2^52"
)]
pub(crate) fn report<R>(case: &str, what: &str, units: u64, unit: &str, f: impl FnMut() -> R) {
    let call = median_call(f);
    let per_unit = call.as_secs_f64() * 1e9 / units.max(1) as f64;
    println!(
        "{case:<12} {what:<16} {units:>8} {unit:<4} {:>10.1} us {per_unit:>7.2} ns/{unit}",
        call.as_secs_f64() * 1e6,
    );
}
