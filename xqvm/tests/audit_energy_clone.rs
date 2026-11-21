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

//! W1-013: `ENERGY` must not clone the sample vector.
//!
//! A counting global allocator measures the real heap high-water across an
//! `ENERGY` run. The VM's `memory_used` meter only charges for allocations the
//! opcodes declare; a clone of the sample inside `ENERGY` is an extra `N * 8`
//! bytes the step/memory budget never saw, so it shows up in the real
//! high-water but not in `memory_used`. Sizing the sample large makes that
//! clone the dominant allocation, so the test fails on the base (clone present)
//! and passes once the clone is removed.
//!
//! This file must stay a single-test binary. The counting allocator is
//! process-wide, so a second test running on another thread would add its own
//! allocations to the measured high-water and make the assertion flaky. Put
//! further allocator-sensitive tests in their own `tests/*.rs` file.

#![expect(
    unsafe_code,
    reason = "a counting global allocator is the only way to measure the real heap \
              high-water this test asserts against (W1-013)"
)]

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};

use xqvm::Vm;

static CURRENT: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);

struct Counting;

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: forwarding the same layout to the system allocator.
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            let now = CURRENT.fetch_add(layout.size(), Ordering::Relaxed) + layout.size();
            let _ = PEAK.fetch_max(now, Ordering::Relaxed);
        }
        ptr
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: `ptr`/`layout` come straight from a matching `alloc` call.
        unsafe { System.dealloc(ptr, layout) };
        let _ = CURRENT.fetch_sub(layout.size(), Ordering::Relaxed);
    }
}

#[global_allocator]
static ALLOC: Counting = Counting;

#[test]
fn energy_does_not_clone_the_sample() {
    const SAMPLE_VARS: usize = 1 << 18; // 262_144 vars -> 2 MiB of i64 values
    let sample_bytes = SAMPLE_VARS * size_of::<i64>();

    // PUSH N / BQMX r0 : empty binary model of N vars (sparse BTreeMaps, ~0 real
    //                    bytes; its declared-size memory charge is a phantom).
    // PUSH N / BSMX r1 : binary sample of N vars (N*8 real bytes).
    // ENERGY r0 r1     : energy 0 (no terms). The fix must read the sample in
    //                    place, not clone it.
    let source =
        format!("PUSH {SAMPLE_VARS}\nBQMX r0\nPUSH {SAMPLE_VARS}\nBSMX r1\nENERGY r0 r1\nHALT\n");
    let program = xqasm::assemble_source(&source).expect("program assembles");

    let mut vm = Vm::new();
    let _ = vm.set_memory_limit(1 << 30).set_step_limit(1 << 32);

    // Real heap high-water across the run, relative to the state just before it
    // (which already holds the assembled program and the source string).
    let baseline = CURRENT.load(Ordering::Relaxed);
    PEAK.store(baseline, Ordering::Relaxed);
    let run = vm.run(&program);
    let peak = PEAK.load(Ordering::Relaxed);
    assert!(run.is_ok(), "energy program must run: {run:?}");

    let high_water = peak - baseline;
    // The only large allocation the run needs is the sample itself (BSMX: N*8
    // real bytes); the empty model is sparse (~0). A clone of the sample inside
    // ENERGY would add another N*8. `memory_used` charges N*8 twice -- BQMX's
    // declared size is a sparse phantom, plus BSMX -- so it is not a sound
    // ceiling here; the sample's real footprint is. Epsilon covers the model,
    // the stack and allocator bookkeeping and stays well below one clone.
    let epsilon = sample_bytes / 2;
    assert!(
        high_water <= sample_bytes + epsilon,
        "ENERGY heap high-water {high_water} B exceeds one sample ({sample_bytes} B) + epsilon \
         ({epsilon} B) by ~{} B -- the sample is cloned uncharged (W1-013); \
         vm.memory_used()={} B",
        high_water.saturating_sub(sample_bytes + epsilon),
        vm.memory_used(),
    );
}
