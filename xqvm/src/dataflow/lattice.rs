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

/// A bounded join-semilattice over the data-flow value domain.
///
/// Every data-flow value type must implement this trait.  The solver uses
/// `top()` to initialise all nodes and `meet` to combine values arriving
/// from multiple predecessors (or successors for backward analyses).
///
/// # Contract
///
/// The following laws must hold for all values `a`, `b`, `c`:
///
/// - **Commutativity**: `a.meet(b) == b.meet(a)`
/// - **Associativity**: `a.meet(b.meet(c)) == (a.meet(b)).meet(c)`
/// - **Idempotence**: `a.meet(a) == a`
/// - **Identity**: `top().meet(a) == a`
///
/// Violation of these laws will cause the worklist solver to diverge or
/// produce incorrect results.
///
/// # Examples
///
/// ```rust
/// use xqvm::dataflow::Lattice;
///
/// /// A simple "may" lattice over a boolean flag.
/// #[derive(Clone, PartialEq, Eq)]
/// enum MayReach { Unreachable, Reachable }
///
/// impl Lattice for MayReach {
///     fn top() -> Self { Self::Unreachable }
///     fn meet(&self, other: &Self) -> Self {
///         match (self, other) {
///             (Self::Reachable, _) | (_, Self::Reachable) => Self::Reachable,
///             _ => Self::Unreachable,
///         }
///     }
/// }
/// ```
pub trait Lattice: Eq + Clone {
    /// The top element: the identity for `meet`.
    ///
    /// For a may-analysis (union) this is the empty set / `false`.
    /// For a must-analysis (intersection) this is the full set / `true`.
    fn top() -> Self;

    /// The meet (confluence) operator.
    ///
    /// For a may-analysis this is the union (join / least upper bound).
    /// For a must-analysis this is the intersection (greatest lower bound).
    #[must_use]
    fn meet(&self, other: &Self) -> Self;
}
