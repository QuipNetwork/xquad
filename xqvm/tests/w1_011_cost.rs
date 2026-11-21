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

//! W1-011 cost: the jump-span phase must not scale its memory with the number
//! of instructions.
//!
//! The first version of `JumpSpanPhase` kept a `BTreeMap` entry per
//! instruction, so on a 64 KiB all-`NOP` program it was the whole verify heap
//! peak. The phase now records only loop-boundary transitions and jumps, so a
//! program with neither costs it nothing. This measures the real heap
//! high-water of the phase alone (and of the full default verifier, for the
//! record) with a counting global allocator.
//!
//! This file must stay a single-test binary: the counting allocator is
//! process-wide, so a second test running on another thread would add its own
//! allocations to the measured high-water.

#![expect(
    unsafe_code,
    reason = "a counting global allocator is the only way to measure the real heap \
              high-water this test asserts against (W1-011 cost)"
)]

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use xqvm::Program;
use xqvm::verifier::{JumpSpanPhase, Phase};

static CURRENT: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);

struct Counting;

// SAFETY: forwards every call to `System` unchanged and only updates two
// atomic counters on the side.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: same contract as the caller's.
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            let now = CURRENT.fetch_add(layout.size(), Ordering::SeqCst) + layout.size();
            let _ = PEAK.fetch_max(now, Ordering::SeqCst);
        }
        ptr
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: same contract as the caller's.
        unsafe { System.dealloc(ptr, layout) };
        let _ = CURRENT.fetch_sub(layout.size(), Ordering::SeqCst);
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

/// Heap high-water above the starting level while `f` runs, and its duration.
fn measure<T>(f: impl FnOnce() -> T) -> (usize, f64, T) {
    let base = CURRENT.load(Ordering::SeqCst);
    PEAK.store(base, Ordering::SeqCst);
    let start = Instant::now();
    let out = f();
    let millis = start.elapsed().as_secs_f64() * 1000.0;
    (PEAK.load(Ordering::SeqCst) - base, millis, out)
}

/// The phase's own allocations on a 64 KiB all-`NOP` program stay small and do
/// not grow with the instruction count.
#[test]
#[expect(
    clippy::print_stdout,
    reason = "reports the measured peak and time for the review record"
)]
fn jump_span_phase_memory_does_not_scale_with_instructions() {
    let mut code = vec![0xF0_u8; 64 * 1024 - 1]; // NOP
    code.push(0xFF); // HALT
    let program = Program::new(code);

    let (phase_peak, phase_ms, phase) = measure(|| JumpSpanPhase.run(&program));
    assert!(phase.is_ok());
    let (verify_peak, verify_ms, verdict) = measure(|| xqvm::verifier::verify(&program));
    assert!(verdict.is_ok());
    println!(
        "64 KiB all-NOP: JumpSpanPhase peak {phase_peak} B in {phase_ms:.2} ms; \
         full verify peak {verify_peak} B in {verify_ms:.2} ms"
    );

    // No loops and no jumps: nothing for the phase to record. A per-instruction
    // structure would be ~65k entries (over a megabyte); allow a few KiB for
    // incidental allocations.
    assert!(
        phase_peak <= 4 * 1024,
        "JumpSpanPhase allocated {phase_peak} B on a loop-free, jump-free program"
    );
}
