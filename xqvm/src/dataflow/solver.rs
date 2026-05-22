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

#[cfg(not(feature = "std"))]
use alloc::collections::VecDeque;
#[cfg(feature = "std")]
use std::collections::VecDeque;

use core::hash::Hash;

#[cfg(feature = "std")]
use std::collections::{HashMap, HashSet};

#[cfg(not(feature = "std"))]
use hashbrown::{HashMap, HashSet};

use crate::dataflow::analysis::Analysis;
use crate::dataflow::cfg::Cfg;
use crate::dataflow::direction::Direction;
use crate::dataflow::lattice::Lattice;

/// The converged data-flow facts for every node in the CFG.
///
/// `before[n]` is the value just before the transfer function of `n` is
/// applied; `after[n]` is the value just after.  For a forward analysis,
/// `before` flows in from predecessors and `after` flows out to successors;
/// for a backward analysis the roles are reversed.
pub struct DataFlowResult<N, V> {
    /// Value at the input of each node's transfer function.
    pub before: HashMap<N, V>,
    /// Value at the output of each node's transfer function.
    pub after: HashMap<N, V>,
}

impl<N: Eq + Hash, V> DataFlowResult<N, V> {
    /// Value flowing into `node` (before its transfer function).
    pub fn before(&self, node: &N) -> Option<&V> {
        self.before.get(node)
    }

    /// Value flowing out of `node` (after its transfer function).
    pub fn after(&self, node: &N) -> Option<&V> {
        self.after.get(node)
    }
}

/// Run a worklist-based fixed-point data-flow analysis.
///
/// Initialises every node to [`Lattice::top`], seeds the boundary node with
/// [`Analysis::boundary_value`], then iterates until no node's state changes.
///
/// The worklist is a FIFO queue backed by a [`HashSet`] for O(1) duplicate
/// suppression.  Nodes are enqueued in CFG insertion order initially and re-
/// enqueued whenever their state changes.
///
/// # Convergence
///
/// The algorithm converges if and only if:
/// - The lattice has finite height (no infinite ascending chains).
/// - The transfer function is monotone.
///
/// # Errors
///
/// This function is infallible; analysis-level errors (e.g. type mismatches
/// found during transfer) should be encoded in `Value` or collected via a
/// side-channel on the `Analysis` implementor.
///
/// # Examples
///
/// See [`crate::dataflow`] module docs for a complete worked example.
pub fn solve<A>(analysis: &A, cfg: &Cfg<A::Node>) -> DataFlowResult<A::Node, A::Value>
where
    A: Analysis,
    A::Node: Eq + Hash + Clone,
    A::Dir: Direction,
{
    let boundary = A::Dir::boundary_node(cfg);

    // ------------------------------------------------------------------
    // Initialise: boundary node → boundary_value; all others → top.
    // ------------------------------------------------------------------
    let mut state: HashMap<A::Node, A::Value> = cfg
        .nodes()
        .iter()
        .map(|n| {
            let v = if n == boundary {
                analysis.boundary_value()
            } else {
                A::Value::top()
            };
            (n.clone(), v)
        })
        .collect();

    // ------------------------------------------------------------------
    // Worklist: FIFO queue + membership set.
    // ------------------------------------------------------------------
    let mut worklist: VecDeque<A::Node> = cfg.nodes().iter().cloned().collect();
    let mut in_worklist: HashSet<A::Node> = worklist.iter().cloned().collect();

    // ------------------------------------------------------------------
    // Fixed-point iteration.
    // ------------------------------------------------------------------
    while let Some(node) = worklist.pop_front() {
        let _ = in_worklist.remove(&node);

        // Compute the input: meet of all incoming states.
        // The boundary node always uses boundary_value as its input so that
        // back-edges (e.g. loop header) cannot override the seed.
        // Boundary node always uses the seed value so back-edges cannot
        // override it.  All others fold over incoming state with meet;
        // top() is the identity so an empty predecessor set gives top().
        let input = if &node == A::Dir::boundary_node(cfg) {
            analysis.boundary_value()
        } else {
            A::Dir::input_edges(cfg, &node)
                .iter()
                .map(|p| state.get(p).cloned().unwrap_or_else(A::Value::top))
                .fold(A::Value::top(), |acc, v| acc.meet(&v))
        };

        let new_out = analysis.transfer(&node, &input);

        if &new_out != state.get(&node).unwrap_or(&A::Value::top()) {
            let _ = state.insert(node.clone(), new_out);
            for next in A::Dir::output_edges(cfg, &node) {
                if in_worklist.insert(next.clone()) {
                    worklist.push_back(next.clone());
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Materialise before/after maps from the converged state.
    //
    // `state` holds the *output* of each node's transfer function:
    //   Forward  → state = after; before = meet of preds' after values.
    //   Backward → state = before; after = meet of succs' before values.
    // ------------------------------------------------------------------
    let boundary_val = analysis.boundary_value();

    let mut before: HashMap<A::Node, A::Value> = HashMap::new();
    let mut after: HashMap<A::Node, A::Value> = HashMap::new();

    for node in cfg.nodes() {
        let out = state.get(node).cloned().unwrap_or_else(A::Value::top);

        let input = if node == A::Dir::boundary_node(cfg) {
            boundary_val.clone()
        } else {
            A::Dir::input_edges(cfg, node)
                .iter()
                .map(|p| state.get(p).cloned().unwrap_or_else(A::Value::top))
                .fold(A::Value::top(), |acc, v| acc.meet(&v))
        };

        // For Forward: input = before, out = after.
        // For Backward: input = after, out = before.
        // We use the direction to decide which slot each value belongs to.
        // Since `Direction` doesn't expose a const, we check by comparing
        // which node is the boundary to identify direction at runtime.
        //
        // Simpler: Forward → before=input/after=out; Backward → before=out/after=input.
        // We detect direction by asking: is the boundary node the entry?
        // If boundary == entry, it's forward; if boundary == exit, backward.
        let is_forward = A::Dir::boundary_node(cfg) == cfg.entry();
        if is_forward {
            let _ = before.insert(node.clone(), input);
            let _ = after.insert(node.clone(), out);
        } else {
            let _ = before.insert(node.clone(), out);
            let _ = after.insert(node.clone(), input);
        }
    }

    DataFlowResult { before, after }
}
