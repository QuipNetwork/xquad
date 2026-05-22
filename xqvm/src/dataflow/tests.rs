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

//! Unit tests for the data-flow framework using simple synthetic lattices.

#[cfg(not(feature = "std"))]
use alloc::collections::HashMap;
#[cfg(feature = "std")]
use std::collections::HashMap;

use super::analysis::Analysis;
use super::cfg::Cfg;
use super::direction::{Backward, Forward};
use super::lattice::Lattice;
use super::solver::solve;

// ---------------------------------------------------------------------------
// Shared lattice: stack depth under min-meet.
//   top() = usize::MAX  (no information yet)
//   meet(a, b) = min(a, b)  (conservative: take the smaller known depth)
// ---------------------------------------------------------------------------

#[derive(Clone, PartialEq, Eq, Debug)]
struct Depth(usize);

impl Lattice for Depth {
    fn top() -> Self {
        Self(usize::MAX)
    }
    fn meet(&self, other: &Self) -> Self {
        Self(self.0.min(other.0))
    }
}

// ---------------------------------------------------------------------------
// Forward depth-tracking analysis.
// ---------------------------------------------------------------------------

struct ForwardDepth {
    deltas: HashMap<u32, isize>,
}

impl Analysis for ForwardDepth {
    type Value = Depth;
    type Node = u32;
    type Dir = Forward;

    fn boundary_value(&self) -> Depth {
        Depth(0)
    }

    fn transfer(&self, node: &u32, input: &Depth) -> Depth {
        if input.0 == usize::MAX {
            return Depth(usize::MAX);
        }
        let delta = self.deltas.get(node).copied().unwrap_or(0);
        Depth(input.0.saturating_add_signed(delta))
    }
}

// ---------------------------------------------------------------------------
// Backward depth-tracking analysis (reverse propagation for illustration).
// Same deltas, applied from exits upward.
// ---------------------------------------------------------------------------

struct BackwardDepth {
    deltas: HashMap<u32, isize>,
}

impl Analysis for BackwardDepth {
    type Value = Depth;
    type Node = u32;
    type Dir = Backward;

    fn boundary_value(&self) -> Depth {
        Depth(0)
    }

    fn transfer(&self, node: &u32, input: &Depth) -> Depth {
        if input.0 == usize::MAX {
            return Depth(usize::MAX);
        }
        let delta = self.deltas.get(node).copied().unwrap_or(0);
        Depth(input.0.saturating_add_signed(delta))
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[test]
fn forward_linear_chain() {
    // entry(0) --+1--> body(1) --(-1)--> exit(2)
    //
    // before: 0=0, 1=1, 2=0
    //  after: 0=1, 1=0, 2=0
    let cfg = Cfg::builder(0u32, 2).edge(0, 1).edge(1, 2).build();
    let analysis = ForwardDepth {
        deltas: [(0, 1_isize), (1, -1_isize)].into_iter().collect(),
    };
    let r = solve(&analysis, &cfg);

    assert_eq!(r.before(&0).unwrap().0, 0, "entry: boundary seeds 0");
    assert_eq!(r.after(&0).unwrap().0, 1, "entry: +1 delta");
    assert_eq!(r.before(&1).unwrap().0, 1, "body: receives from entry");
    assert_eq!(r.after(&1).unwrap().0, 0, "body: -1 delta");
    assert_eq!(r.before(&2).unwrap().0, 0, "exit: receives from body");
    assert_eq!(r.after(&2).unwrap().0, 0, "exit: no delta");
}

#[test]
fn forward_diamond_join() {
    // entry(0) --> left(1) --> exit(3)
    //          --> right(2) --> exit(3)
    //
    // left pushes 2, right pushes 5; meet at exit = min(2,5) = 2.
    let cfg = Cfg::builder(0u32, 3)
        .edge(0, 1)
        .edge(0, 2)
        .edge(1, 3)
        .edge(2, 3)
        .build();
    let analysis = ForwardDepth {
        deltas: [(1, 2_isize), (2, 5_isize)].into_iter().collect(),
    };
    let r = solve(&analysis, &cfg);

    assert_eq!(r.after(&1).unwrap().0, 2);
    assert_eq!(r.after(&2).unwrap().0, 5);
    // meet(2, 5) = min(2, 5) = 2
    assert_eq!(
        r.before(&3).unwrap().0,
        2,
        "exit: conservative meet of branches"
    );
}

#[test]
fn forward_loop_converges() {
    // entry(0) --> header(1) --> body(2) --> header(1)  [back edge]
    //                        --> exit(3)
    //
    // header has no delta; body adds 0 (neutral).
    // The back edge must not inflate the depth -- meet(1, 1) = 1 throughout.
    let cfg = Cfg::builder(0u32, 3)
        .edge(0, 1)
        .edge(1, 2)
        .edge(1, 3)
        .edge(2, 1) // back edge
        .build();
    let analysis = ForwardDepth {
        deltas: [(0, 1_isize)].into_iter().collect(),
    };
    let r = solve(&analysis, &cfg);

    // Entry pushes 1; header and body are neutral.
    assert_eq!(r.after(&0).unwrap().0, 1);
    assert_eq!(r.before(&1).unwrap().0, 1);
    assert_eq!(r.before(&3).unwrap().0, 1, "exit receives stable depth");
}

#[test]
fn backward_linear_chain() {
    // exit(2) seeds depth 0 backward; body(1) has delta +1 (going backward).
    // After backward propagation: exit before=0, body before=1, entry before=0.
    let cfg = Cfg::builder(0u32, 2).edge(0, 1).edge(1, 2).build();
    let analysis = BackwardDepth {
        deltas: [(1, 1_isize)].into_iter().collect(),
    };
    let r = solve(&analysis, &cfg);

    // Exit is the boundary node; its "before" (in backward = the output) = 0.
    assert_eq!(r.after(&2).unwrap().0, 0, "exit boundary seeds 0");
    // Body receives 0 from exit, applies +1: before=1.
    assert_eq!(r.before(&1).unwrap().0, 1);
    // Entry receives 1 from body: before=1.
    assert_eq!(r.before(&0).unwrap().0, 1);
}

#[test]
fn cfg_no_edges_single_node() {
    // A CFG with one node (entry == exit).
    let cfg = Cfg::builder(0u32, 0).build();
    let analysis = ForwardDepth {
        deltas: HashMap::new(),
    };
    let r = solve(&analysis, &cfg);
    assert_eq!(r.before(&0).unwrap().0, 0);
    assert_eq!(r.after(&0).unwrap().0, 0);
}

#[test]
fn lattice_meet_identity() {
    // top.meet(x) == x  (identity law)
    let x = Depth(42);
    let t = Depth::top();
    assert_eq!(t.meet(&x), x);
    assert_eq!(x.meet(&t), x);
}

#[test]
fn cfg_builder_deduplicates_nodes_and_edges() {
    let cfg = Cfg::builder(0u32, 2)
        .edge(0, 1)
        .edge(0, 1) // duplicate -- must be silently ignored
        .edge(1, 2)
        .build();
    // Each node appears exactly once in the node list.
    assert_eq!(cfg.nodes().len(), 3);
    // Duplicate edges are deduplicated: only one 0→1 edge.
    assert_eq!(cfg.successors(&0).len(), 1);
    assert_eq!(cfg.predecessors(&1).len(), 1);
}
