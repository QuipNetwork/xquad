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
use alloc::vec::Vec;

use core::hash::Hash;

#[cfg(feature = "std")]
use std::collections::{HashMap, HashSet};

#[cfg(not(feature = "std"))]
use hashbrown::{HashMap, HashSet};

/// A directed control-flow graph over nodes of type `N`.
///
/// Stores explicit successor and predecessor edge lists for every node.  The
/// graph has a designated [`entry`](Self::entry) and [`exit`](Self::exit)
/// node; these may be the same node (single-block programs).
///
/// Build via [`CfgBuilder`].
///
/// # Examples
///
/// ```rust
/// use xqvm::dataflow::Cfg;
///
/// // Three-block CFG: entry --[true]--> body ---> exit
/// //                         --[false]-----------^
/// let cfg = Cfg::builder(0u32, 2)
///     .edge(0, 1)
///     .edge(0, 2)
///     .edge(1, 2)
///     .build();
///
/// assert_eq!(cfg.successors(&0), &[1, 2]);
/// assert_eq!(cfg.predecessors(&2), &[0, 1]);
/// ```
pub struct Cfg<N> {
    entry: N,
    exit: N,
    nodes: Vec<N>,
    succs: HashMap<N, Vec<N>>,
    preds: HashMap<N, Vec<N>>,
}

impl<N: Eq + Hash + Clone> Cfg<N> {
    /// Create a [`CfgBuilder`] seeded with `entry` and `exit` nodes.
    pub fn builder(entry: N, exit: N) -> CfgBuilder<N> {
        CfgBuilder::new(entry, exit)
    }

    /// The entry node (source of all forward analyses).
    pub fn entry(&self) -> &N {
        &self.entry
    }

    /// The exit node (source of all backward analyses).
    pub fn exit(&self) -> &N {
        &self.exit
    }

    /// All nodes in the graph, in insertion order.
    pub fn nodes(&self) -> &[N] {
        &self.nodes
    }

    /// Direct successors of `node`.  Returns `&[]` if `node` is not in the graph.
    pub fn successors(&self, node: &N) -> &[N] {
        self.succs.get(node).map(Vec::as_slice).unwrap_or_default()
    }

    /// Direct predecessors of `node`.  Returns `&[]` if `node` is not in the graph.
    ///
    /// This list only holds edges derived from the instruction stream. The
    /// entry block has one more incoming edge that is not in it: program entry
    /// itself. Any caller reasoning about join points must add that edge, or
    /// the entry block is silently exempted.
    pub fn predecessors(&self, node: &N) -> &[N] {
        self.preds.get(node).map(Vec::as_slice).unwrap_or_default()
    }
}

// ---------------------------------------------------------------------------
// CfgBuilder
// ---------------------------------------------------------------------------

/// Fluent builder for [`Cfg`].
///
/// Nodes are registered automatically when they appear in an edge.  The
/// entry and exit nodes passed to [`Cfg::builder`] are always present even if
/// they have no edges.
pub struct CfgBuilder<N> {
    entry: N,
    exit: N,
    nodes: Vec<N>,
    seen: HashSet<N>,
    succs: HashMap<N, Vec<N>>,
    preds: HashMap<N, Vec<N>>,
}

impl<N: Eq + Hash + Clone> CfgBuilder<N> {
    fn new(entry: N, exit: N) -> Self {
        let mut builder = Self {
            entry: entry.clone(),
            exit: exit.clone(),
            nodes: Vec::new(),
            seen: HashSet::new(),
            succs: HashMap::new(),
            preds: HashMap::new(),
        };
        builder.register(entry);
        builder.register(exit);
        builder
    }

    /// Add a directed edge from `from` to `to`, registering both nodes.
    ///
    /// Duplicate edges are silently ignored so that `preds.len()` and
    /// `succs.len()` reflect the true number of distinct edges.
    #[must_use]
    pub fn edge(mut self, from: N, to: N) -> Self {
        self.register(from.clone());
        self.register(to.clone());
        let succs = self.succs.entry(from.clone()).or_default();
        if !succs.contains(&to) {
            succs.push(to.clone());
        }
        let preds = self.preds.entry(to).or_default();
        if !preds.contains(&from) {
            preds.push(from);
        }
        self
    }

    /// Finalise and return the [`Cfg`].
    pub fn build(self) -> Cfg<N> {
        Cfg {
            entry: self.entry,
            exit: self.exit,
            nodes: self.nodes,
            succs: self.succs,
            preds: self.preds,
        }
    }

    fn register(&mut self, node: N) {
        if self.seen.insert(node.clone()) {
            self.nodes.push(node);
        }
    }
}
