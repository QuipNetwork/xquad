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

//! Worklist-based data-flow analysis framework.
//!
//! # Architecture
//!
//! The framework follows the template method pattern.  Clients implement the
//! [`Analysis`] trait to supply the domain-specific parts; the [`solve`]
//! function drives the fixed-point worklist loop.
//!
//! | Piece | What you provide |
//! |---|---|
//! | [`Lattice`] | Value domain with `top()` and `meet()` |
//! | [`Analysis::boundary_value`] | Seed at the boundary node |
//! | [`Analysis::transfer`] | Per-node effect on the incoming value |
//! | [`Analysis::Dir`] | [`Forward`] or [`Backward`] direction marker |
//! | [`Cfg`] | Control-flow graph (built via [`Cfg::builder`]) |
//!
//! # Quick start
//!
//! ```rust
//! use xqvm::dataflow::{Analysis, Cfg, DataFlowResult, Forward, Lattice, solve};
//!
//! // --- Value domain ---------------------------------------------------
//!
//! /// Stack depth at a program point.  The lattice is the natural numbers
//! /// under min: meet(a, b) = min(a, b); top() = usize::MAX (unknown).
//! #[derive(Clone, PartialEq, Eq)]
//! struct Depth(usize);
//!
//! impl Lattice for Depth {
//!     fn top() -> Self { Depth(usize::MAX) }
//!     fn meet(&self, other: &Self) -> Self { Depth(self.0.min(other.0)) }
//! }
//!
//! // --- Analysis -------------------------------------------------------
//!
//! /// Forward stack-depth analysis: each block has a net delta.
//! struct DepthAnalysis {
//!     deltas: std::collections::HashMap<u32, isize>,
//! }
//!
//! impl Analysis for DepthAnalysis {
//!     type Value = Depth;
//!     type Node  = u32;
//!     type Dir   = Forward;
//!
//!     fn boundary_value(&self) -> Depth { Depth(0) }
//!
//!     fn transfer(&self, node: &u32, input: &Depth) -> Depth {
//!         let delta = self.deltas.get(node).copied().unwrap_or(0);
//!         Depth((input.0 as isize + delta).max(0) as usize)
//!     }
//! }
//!
//! // --- CFG ------------------------------------------------------------
//!
//! //   entry(0) -> body(1) -> exit(2)
//! //               body(1) ---------^   (loop back would be body -> entry)
//! let cfg = Cfg::builder(0u32, 2)
//!     .edge(0, 1)
//!     .edge(1, 2)
//!     .build();
//!
//! let analysis = DepthAnalysis {
//!     deltas: [(0, 1_isize), (1, -1_isize)].into_iter().collect(),
//! };
//!
//! let result = solve(&analysis, &cfg);
//!
//! assert_eq!(result.before(&0).unwrap().0, 0);  // entry: depth 0 before
//! assert_eq!(result.after(&0).unwrap().0,  1);  // entry block pushes 1
//! assert_eq!(result.before(&1).unwrap().0, 1);  // body receives depth 1
//! assert_eq!(result.after(&1).unwrap().0,  0);  // body pops 1
//! ```

mod analysis;
pub mod bytecode;
mod cfg;
mod direction;
mod lattice;
pub mod register;
mod solver;
pub mod uninit;

#[cfg(test)]
mod tests;

pub use analysis::Analysis;
pub use bytecode::{
    BlockEffect, BlockId, DepthValue, OutputKind, StackDepthAnalysis, check_stack_depth,
};
pub use cfg::{Cfg, CfgBuilder};
pub use direction::{Backward, Direction, Forward};
pub use lattice::Lattice;
pub use solver::{DataFlowResult, solve};
