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

//! Per-byte cost of turning bytecode into a runnable [`Program`].
//!
//! [`Program::decode`] is what an embedder pays before every run: the XQBC
//! header and CRC-32 check, a copy of the code, and the jump-table walk in
//! [`Program::new`]. Both are timed over two 64 KiB programs:
//!
//! - **realistic** -- the TSP encoder, decoder and verifier from
//!   `examples/tsp/`, repeated.
//! - **dense** -- `TARGET RANGE JUMP1 NEXT` repeated, the densest mix of the
//!   instructions the walk records.
//!
//! Each figure is the median of 31 samples of 100 calls, after a warm-up.
//! Std-only, so it needs no benchmark framework:
//!
//! ```text
//! cargo bench -p xqvm --bench decode
//! ```

use std::error::Error;
use std::hint::black_box;
use std::time::{Duration, Instant};

use xqvm::{Instruction, Program, codec};

/// Size every corpus is repeated up to.
const CORPUS_BYTES: usize = 64 * 1024;
const WARMUP_CALLS: u32 = 200;
const SAMPLES: usize = 31;
const CALLS_PER_SAMPLE: u32 = 100;

fn main() -> Result<(), Box<dyn Error>> {
    for (name, code) in [("realistic", realistic()?), ("dense", dense())] {
        let encoded = Program::new(code.clone()).encode();
        report(name, "Program::new", code.len(), || {
            Program::new(black_box(code.clone()))
        });
        report(name, "Program::decode", code.len(), || {
            Program::decode(black_box(&encoded))
        });
    }
    Ok(())
}

/// Repeat `unit` as many whole times as fits in [`CORPUS_BYTES`].
fn repeat(unit: &[u8]) -> Vec<u8> {
    let copies = CORPUS_BYTES / unit.len().max(1);
    unit.repeat(copies)
}

fn realistic() -> Result<Vec<u8>, Box<dyn Error>> {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/examples/tsp");
    let mut unit = Vec::new();
    for name in ["encoder", "decoder", "verifier"] {
        let source = std::fs::read_to_string(format!("{dir}/{name}.xqasm"))?;
        let program = xqasm::assemble_source(&source).map_err(|e| format!("{name}: {e}"))?;
        unit.extend_from_slice(program.code());
    }
    Ok(repeat(&unit))
}

fn dense() -> Vec<u8> {
    let unit: Vec<u8> = [
        Instruction::Target {},
        Instruction::Range {},
        Instruction::Jump1 { label: 0 },
        Instruction::Next {},
    ]
    .iter()
    .flat_map(codec::encode)
    .collect();
    repeat(&unit)
}

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

#[expect(
    clippy::print_stdout,
    reason = "a harness = false bench reports on stdout"
)]
#[expect(
    clippy::cast_precision_loss,
    reason = "a 64 KiB corpus length is exact in f64"
)]
fn report<R>(corpus: &str, what: &str, bytes: usize, f: impl FnMut() -> R) {
    let call = median_call(f);
    let ns_per_byte = call.as_secs_f64() * 1e9 / bytes as f64;
    println!(
        "{corpus:<10} {what:<16} {bytes:>6} B {:>9.1} us {ns_per_byte:>6.2} ns/B",
        call.as_secs_f64() * 1e6,
    );
}
