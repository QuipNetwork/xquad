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

use crate::dataflow::direction::Direction;
use crate::dataflow::lattice::Lattice;

/// Template for a data-flow analysis.
///
/// Implement this trait to define a new analysis.  The worklist solver in
/// [`crate::dataflow::solve`] drives the fixed-point computation; you supply
/// only the domain-specific parts:
///
/// - **`type Value`** -- the lattice element representing facts at a point.
/// - **`type Node`** -- the unit of analysis (typically a basic block index or
///   byte offset).
/// - **`type Dir`** -- [`Forward`](crate::dataflow::Forward) or
///   [`Backward`](crate::dataflow::Backward).
/// - **`boundary_value`** -- the seed value injected at the boundary node
///   (CFG entry for forward, CFG exit for backward).
/// - **`transfer`** -- apply one node's effect to an incoming value, producing
///   the outgoing value.
///
/// # Examples
///
/// A forward constant-zero check (toy example):
///
/// ```rust
/// use xqvm::dataflow::{Analysis, Forward, Lattice};
///
/// #[derive(Clone, PartialEq, Eq)]
/// enum Zeroness { MaybeNonZero, DefinitelyZero }
///
/// impl Lattice for Zeroness {
///     fn top() -> Self { Self::MaybeNonZero }
///     fn meet(&self, other: &Self) -> Self {
///         match (self, other) {
///             (Self::DefinitelyZero, Self::DefinitelyZero) => Self::DefinitelyZero,
///             _ => Self::MaybeNonZero,
///         }
///     }
/// }
///
/// struct ZeronessAnalysis;
///
/// impl Analysis for ZeronessAnalysis {
///     type Value = Zeroness;
///     type Node  = u32;
///     type Dir   = Forward;
///
///     fn boundary_value(&self) -> Zeroness { Zeroness::DefinitelyZero }
///
///     fn transfer(&self, _node: &u32, input: &Zeroness) -> Zeroness {
///         input.clone()
///     }
/// }
/// ```
pub trait Analysis {
    /// The lattice element used as the data-flow value.
    type Value: Lattice;

    /// The type identifying a node (basic block) in the CFG.
    type Node: Eq + Hash + Clone;

    /// The flow direction: [`Forward`](crate::dataflow::Forward) or
    /// [`Backward`](crate::dataflow::Backward).
    type Dir: Direction;

    /// The value injected at the boundary node before the fixed-point iteration.
    ///
    /// For forward analyses this seeds the CFG entry; for backward analyses the
    /// CFG exit.
    fn boundary_value(&self) -> Self::Value;

    /// Apply the effect of `node` to `input`, producing the outgoing value.
    ///
    /// `input` is the meet of all values arriving at `node` from its
    /// predecessors (forward) or successors (backward).  The transfer function
    /// must be monotone: if `a <= b` in the lattice then
    /// `transfer(node, a) <= transfer(node, b)`.
    fn transfer(&self, node: &Self::Node, input: &Self::Value) -> Self::Value;
}
