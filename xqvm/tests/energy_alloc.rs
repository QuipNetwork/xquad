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

//! `ENERGY` reads its sample in place and allocates nothing that scales with
//! it.
//!
//! `ENERGY` charges steps for every sample element and no memory
//! (`spec/xqvm/METERING.md`, `model_eval_steps`). A copy of the sample would
//! be heap the memory budget never sees. No conformance vector can observe
//! heap use, so this target counts it: a global allocator tallies the bytes
//! the current thread allocates, and the test compares one run with `ENERGY`
//! against the same run without it.
//!
//! Its own target, because the counting allocator is process-wide.

#![expect(
    unsafe_code,
    reason = "test - a GlobalAlloc impl forwards to System and is unsafe by signature"
)]
#![expect(
    unused_results,
    clippy::expect_used,
    reason = "test helpers - builder results and panics on failure are intentional"
)]

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

use xqvm::Vm;
use xqvm::bytecode::{InstructionBuilder, Register};

/// Forwards to `System` and adds every allocation's size to a per-thread
/// tally. The tally is thread-local so the test harness's own threads do not
/// count.
struct Counting;

thread_local! {
    static ALLOCATED: Cell<u64> = const { Cell::new(0) };
}

fn tally(bytes: usize) {
    // `try_with`: allocations during thread teardown find the slot gone.
    let _ = ALLOCATED.try_with(|c| {
        c.set(
            c.get()
                .saturating_add(u64::try_from(bytes).unwrap_or(u64::MAX)),
        );
    });
}

// SAFETY: every method forwards to `System` with the caller's arguments.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        tally(layout.size());
        // SAFETY: the caller upholds `GlobalAlloc::alloc`'s contract.
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        tally(layout.size());
        // SAFETY: the caller upholds `GlobalAlloc::alloc_zeroed`'s contract.
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: the caller upholds `GlobalAlloc::dealloc`'s contract.
        unsafe { System.dealloc(ptr, layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        tally(new_size);
        // SAFETY: the caller upholds `GlobalAlloc::realloc`'s contract.
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

/// Large enough that a copy of the sample (8 bytes a value) dwarfs every
/// fixed-size allocation a run makes.
const N: i64 = 200_000;

/// Bytes the current thread allocates while a fresh VM runs `BQMX`, `BSMX`
/// of size `N`, optionally `ENERGY` over them, then `HALT`.
fn allocated_by_run(with_energy: bool) -> u64 {
    let mut b = InstructionBuilder::new();
    b.emit_push(N).emit_bqmx(Register(0));
    b.emit_push(N).emit_bsmx(Register(1));
    if with_energy {
        b.emit_energy(Register(0), Register(1));
    }
    b.emit_halt();
    let program = b.build().expect("builder build");
    let mut vm = Vm::new();

    let before = ALLOCATED.with(Cell::get);
    vm.run(&program).expect("vm run");
    let after = ALLOCATED.with(Cell::get);
    if with_energy {
        assert_eq!(vm.stack(), &[0], "an empty model has energy 0");
    }
    after - before
}

#[test]
fn energy_does_not_copy_the_sample() {
    let without = allocated_by_run(false);
    let with = allocated_by_run(true);
    let extra = with.saturating_sub(without);
    let sample_bytes = u64::try_from(N).expect("N is positive") * 8;
    // A copy costs `sample_bytes`. Allow a small fixed amount for the
    // result pushed onto the stack.
    assert!(
        extra < sample_bytes / 16,
        "ENERGY allocated {extra} bytes over a {sample_bytes}-byte sample"
    );
}
