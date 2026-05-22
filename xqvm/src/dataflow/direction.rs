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

use core::hash::Hash;

use crate::dataflow::cfg::Cfg;

mod sealed {
    pub trait Sealed {}
}

/// Zero-sized marker for a forward data-flow analysis.
///
/// The boundary is the CFG entry; facts flow from predecessors into each node.
pub struct Forward;

/// Zero-sized marker for a backward data-flow analysis.
///
/// The boundary is the CFG exit; facts flow from successors into each node.
pub struct Backward;

impl sealed::Sealed for Forward {}
impl sealed::Sealed for Backward {}

/// Sealed trait that parameterises the solver over flow direction.
///
/// Implementors are [`Forward`] and [`Backward`].  All methods are generic
/// over the node type `N` so that a single `impl` handles any `Cfg<N>`.
///
/// This trait is sealed: no external implementations are allowed.
pub trait Direction: sealed::Sealed {
    /// The node that receives the analysis boundary value.
    ///
    /// For [`Forward`] this is the CFG entry; for [`Backward`] the CFG exit.
    fn boundary_node<N: Eq + Hash + Clone>(cfg: &Cfg<N>) -> &N;

    /// Nodes whose outgoing state feeds into `node` under this direction.
    ///
    /// For [`Forward`] these are the predecessors; for [`Backward`] the successors.
    fn input_edges<'g, N: Eq + Hash + Clone>(cfg: &'g Cfg<N>, node: &N) -> &'g [N];

    /// Nodes that must be re-evaluated when `node`'s state changes.
    ///
    /// For [`Forward`] these are the successors; for [`Backward`] the predecessors.
    fn output_edges<'g, N: Eq + Hash + Clone>(cfg: &'g Cfg<N>, node: &N) -> &'g [N];
}

impl Direction for Forward {
    fn boundary_node<N: Eq + Hash + Clone>(cfg: &Cfg<N>) -> &N {
        cfg.entry()
    }

    fn input_edges<'g, N: Eq + Hash + Clone>(cfg: &'g Cfg<N>, node: &N) -> &'g [N] {
        cfg.predecessors(node)
    }

    fn output_edges<'g, N: Eq + Hash + Clone>(cfg: &'g Cfg<N>, node: &N) -> &'g [N] {
        cfg.successors(node)
    }
}

impl Direction for Backward {
    fn boundary_node<N: Eq + Hash + Clone>(cfg: &Cfg<N>) -> &N {
        cfg.exit()
    }

    fn input_edges<'g, N: Eq + Hash + Clone>(cfg: &'g Cfg<N>, node: &N) -> &'g [N] {
        cfg.successors(node)
    }

    fn output_edges<'g, N: Eq + Hash + Clone>(cfg: &'g Cfg<N>, node: &N) -> &'g [N] {
        cfg.predecessors(node)
    }
}
