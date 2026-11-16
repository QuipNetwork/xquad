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
/// `before[n]` and `after[n]` refer to program order. For a forward
/// analysis they hold transfer input and output respectively; for a
/// backward analysis they hold transfer output and input respectively.
#[derive(Debug)]
pub struct DataFlowResult<N, V> {
    /// Facts immediately before each node in program order.
    pub before: HashMap<N, V>,
    /// Facts immediately after each node in program order.
    pub after: HashMap<N, V>,
}

impl<N: Eq + Hash, V> DataFlowResult<N, V> {
    /// Facts before `node` in program order.
    pub fn before(&self, node: &N) -> Option<&V> {
        self.before.get(node)
    }

    /// Facts after `node` in program order.
    pub fn after(&self, node: &N) -> Option<&V> {
        self.after.get(node)
    }
}

/// Failure to converge within the caller's deterministic scheduling bound.
#[derive(Clone, Copy, Debug, PartialEq, Eq, thiserror::Error)]
pub enum SolveError {
    /// The worklist needs more visits than its supplied bound permits.
    #[error("data-flow work budget exhausted")]
    BudgetExceeded,
}

impl From<SolveError> for crate::VerifierError {
    fn from(_: SolveError) -> Self {
        Self::AnalysisBudgetExceeded
    }
}

pub(super) struct VisitBudget(u64);

impl VisitBudget {
    pub(super) fn new(limit: u64) -> Self {
        Self(limit)
    }

    pub(super) fn visit(&mut self) -> Result<(), SolveError> {
        #[cfg(test)]
        if test_work::budget_disabled() {
            return Ok(());
        }
        self.0 = self.0.checked_sub(1).ok_or(SolveError::BudgetExceeded)?;
        Ok(())
    }
}

/// Initial N visits plus at most H enqueue attempts per outgoing edge.
/// Checked arithmetic fails closed if the bound cannot be represented.
pub(super) fn visit_limit<N: Eq + Hash + Clone>(
    cfg: &Cfg<N>,
    max_descents: u64,
) -> Result<u64, SolveError> {
    let nodes = u64::try_from(cfg.nodes().len()).map_err(|_| SolveError::BudgetExceeded)?;
    let edges = cfg.nodes().iter().try_fold(0u64, |sum, node| {
        let degree = u64::try_from(cfg.successors(node).len()).ok()?;
        sum.checked_add(degree)
    });
    edges
        .and_then(|e| e.checked_mul(max_descents))
        .and_then(|changes| nodes.checked_add(changes))
        .ok_or(SolveError::BudgetExceeded)
}

/// Run a bounded worklist-based fixed-point data-flow analysis.
///
/// Every stored output starts at [`Lattice::top`]. The boundary value is
/// supplied only as the boundary node's input, before its transfer runs.
/// The FIFO worklist suppresses duplicate queued nodes.
///
/// # Convergence and budget
///
/// Meet must be a greatest lower bound with top as identity, and transfers
/// must be monotone. If a node's output can descend strictly at most H times,
/// N nodes and E edges require at most N + H * E visits. Supply that bound as
/// `max_visits`, using checked arithmetic. The bound covers scheduling, not
/// the cost or termination of a caller-provided transfer function.
///
/// # Errors
///
/// Returns [`SolveError::BudgetExceeded`] before exceeding `max_visits`.
/// With a valid derived bound, exhaustion indicates a broken analysis
/// contract, not a bytecode validation error. No partial result is returned.
///
/// # Examples
///
/// See [`crate::dataflow`] for a complete worked example.
pub fn solve<A>(
    analysis: &A,
    cfg: &Cfg<A::Node>,
    max_visits: u64,
) -> Result<DataFlowResult<A::Node, A::Value>, SolveError>
where
    A: Analysis,
    A::Node: Eq + Hash + Clone,
    A::Dir: Direction,
{
    let mut budget = VisitBudget::new(max_visits);

    // These are transfer OUTPUTS: even the boundary starts at top.
    let mut state: HashMap<A::Node, A::Value> = cfg
        .nodes()
        .iter()
        .map(|n| (n.clone(), A::Value::top()))
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
        budget.visit()?;
        #[cfg(test)]
        test_work::visit();
        let _ = in_worklist.remove(&node);

        // Compute the input: meet of all incoming states.
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

        // Direction is explicit even when entry and exit are the same node.
        let is_forward = <A::Dir as super::direction::sealed::Sealed>::FORWARD;
        if is_forward {
            let _ = before.insert(node.clone(), input);
            let _ = after.insert(node.clone(), out);
        } else {
            let _ = before.insert(node.clone(), out);
            let _ = after.insert(node.clone(), input);
        }
    }

    Ok(DataFlowResult { before, after })
}

/// Scoped, thread-local instrumentation of the actual production worklists.
/// Absent from non-test builds, including the public solver signature.
#[cfg(test)]
pub(super) mod test_work {
    use core::cell::Cell;

    std::thread_local! {
        static BUDGET: Cell<Option<(usize, usize)>> = const { Cell::new(None) };
        static DISABLED: Cell<bool> = const { Cell::new(false) };
    }

    pub(in crate::dataflow) fn budget_disabled() -> bool {
        DISABLED.get()
    }

    pub(in crate::dataflow) fn without_budget<R>(run: impl FnOnce() -> R) -> R {
        struct Restore(bool);
        impl Drop for Restore {
            fn drop(&mut self) {
                DISABLED.set(self.0);
            }
        }
        let _restore = Restore(DISABLED.replace(true));
        run()
    }

    struct Reset;

    impl Drop for Reset {
        fn drop(&mut self) {
            BUDGET.set(None);
        }
    }

    pub(in crate::dataflow) fn visit() {
        if let Some((limit, visits)) = BUDGET.get() {
            assert!(visits < limit, "worklist visit budget exceeded: {limit}");
            BUDGET.set(Some((limit, visits.saturating_add(1))));
        }
    }

    pub(in crate::dataflow) fn measure<R>(limit: usize, run: impl FnOnce() -> R) -> (R, usize) {
        assert!(BUDGET.get().is_none(), "worklist budgets must not nest");
        BUDGET.set(Some((limit, 0)));
        let _reset = Reset;
        let result = run();
        let visits = BUDGET
            .get()
            .expect("budget stays installed until scope exit")
            .1;
        (result, visits)
    }
}
