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

//! Sparse coordinate propagation for built-in forward register analyses.
//!
//! Transfers overwrite independent coordinates with constants. Only a
//! coordinate whose output descends is sent over its outgoing edges.

#[cfg(not(feature = "std"))]
use alloc::{collections::VecDeque, vec, vec::Vec};
#[cfg(not(feature = "std"))]
use hashbrown::HashMap;
#[cfg(feature = "std")]
use std::collections::{HashMap, VecDeque};

use super::indexed::IndexedCfg;
use super::solver::VisitBudget;
use super::{BlockId, Cfg, SolveError};
use crate::verifier::RegType;

pub(super) trait Coordinate: Copy + Eq {
    const TOP: Self;
    const ENTRY: Self;
    const DESCENTS: u64;
    fn meet(self, other: Self) -> Self;
}

impl Coordinate for RegType {
    const TOP: Self = Self::Any;
    const ENTRY: Self = Self::Unset;
    const DESCENTS: u64 = 4;
    fn meet(self, other: Self) -> Self {
        super::register::meet_reg(self, other)
    }
}

impl Coordinate for bool {
    const TOP: Self = true;
    const ENTRY: Self = false;
    const DESCENTS: u64 = 1;
    fn meet(self, other: Self) -> Self {
        self && other
    }
}

pub(super) struct SparseFacts<C> {
    graph: IndexedCfg,
    before: Vec<[C; 256]>,
    #[cfg(test)]
    pub(super) after: Vec<[C; 256]>,
    #[cfg(test)]
    pub(super) events: u64,
    #[cfg(test)]
    pub(super) updates: u64,
    #[cfg(test)]
    pub(super) node_events: Vec<u64>,
}

impl<C> SparseFacts<C> {
    pub(super) fn before(&self, node: BlockId) -> Option<&[C; 256]> {
        self.graph
            .index(node)
            .ok()
            .and_then(|index| self.before.get(index))
    }
}

fn enqueue(
    pending: &mut VecDeque<(usize, usize)>,
    queued: &mut [[bool; 256]],
    node: usize,
    slot: usize,
) -> Result<(), SolveError> {
    let flag = queued
        .get_mut(node)
        .and_then(|row| row.get_mut(slot))
        .ok_or(SolveError::BudgetExceeded)?;
    if !*flag {
        *flag = true;
        pending.push_back((node, slot));
    }
    Ok(())
}

/// Final writes are constant, so non-written coordinates are identity
/// transfers and written coordinates need no re-evaluation on input change.
#[expect(
    clippy::too_many_lines,
    reason = "initialisation and sparse propagation share the same checked dense state"
)]
pub(super) fn solve<C: Coordinate>(
    cfg: &Cfg<BlockId>,
    writes: &HashMap<BlockId, Vec<(u8, C)>>,
) -> Result<SparseFacts<C>, SolveError> {
    let graph = IndexedCfg::new(cfg)?;
    let n = graph.nodes.len();
    let n64 = u64::try_from(n).map_err(|_| SolveError::BudgetExceeded)?;
    let limit = |size: u64| {
        size.checked_mul(256)
            .and_then(|v| v.checked_mul(C::DESCENTS))
            .ok_or(SolveError::BudgetExceeded)
    };
    let mut event_budget = VisitBudget::new(limit(n64)?);
    let mut update_budget = VisitBudget::new(limit(graph.edges()?)?);
    let mut before = vec![[C::TOP; 256]; n];
    let mut after = before.clone();
    let mut overrides = vec![[None; 256]; n];
    let mut reachable = vec![false; n];
    *reachable
        .get_mut(graph.entry)
        .ok_or(SolveError::BudgetExceeded)? = true;
    let mut discovery = VecDeque::from([graph.entry]);
    while let Some(node) = discovery.pop_front() {
        for &next in graph
            .successors
            .get(node)
            .ok_or(SolveError::BudgetExceeded)?
        {
            let seen = reachable.get_mut(next).ok_or(SolveError::BudgetExceeded)?;
            if !*seen {
                *seen = true;
                discovery.push_back(next);
            }
        }
    }
    let mut pending = VecDeque::new();
    let mut queued = vec![[false; 256]; n];
    for (index, &node) in graph.nodes.iter().enumerate() {
        if !reachable.get(index).copied().unwrap_or(false) {
            continue;
        }
        let row = overrides.get_mut(index).ok_or(SolveError::BudgetExceeded)?;
        if let Some(writes) = writes.get(&node) {
            for &(slot, value) in writes {
                *row.get_mut(usize::from(slot))
                    .ok_or(SolveError::BudgetExceeded)? = Some(value);
            }
        }
        let input = if index == graph.entry {
            C::ENTRY
        } else {
            C::TOP
        };
        *before.get_mut(index).ok_or(SolveError::BudgetExceeded)? = [input; 256];
        let output = after.get_mut(index).ok_or(SolveError::BudgetExceeded)?;
        for (slot, (out, write)) in output.iter_mut().zip(row.iter()).enumerate() {
            *out = write.unwrap_or(input);
            if *out != C::TOP {
                enqueue(&mut pending, &mut queued, index, slot)?;
            }
        }
    }
    #[cfg(test)]
    let (mut events, mut updates) = (0u64, 0u64);
    #[cfg(test)]
    let mut node_events = vec![0u64; n];
    while let Some((node, slot)) = pending.pop_front() {
        event_budget.visit()?;
        #[cfg(test)]
        {
            events = events.saturating_add(1);
            if let Some(count) = node_events.get_mut(node) {
                *count = count.saturating_add(1);
            }
            super::solver::test_work::visit();
        }
        *queued
            .get_mut(node)
            .and_then(|row| row.get_mut(slot))
            .ok_or(SolveError::BudgetExceeded)? = false;
        let value = *after
            .get(node)
            .and_then(|row| row.get(slot))
            .ok_or(SolveError::BudgetExceeded)?;
        for &next in graph
            .successors
            .get(node)
            .ok_or(SolveError::BudgetExceeded)?
        {
            // The boundary input is constant, as in the public generic solver.
            if next == graph.entry {
                continue;
            }
            update_budget.visit()?;
            #[cfg(test)]
            {
                updates = updates.saturating_add(1);
                super::solver::test_work::visit();
            }
            let input = before
                .get_mut(next)
                .and_then(|row| row.get_mut(slot))
                .ok_or(SolveError::BudgetExceeded)?;
            let merged = input.meet(value);
            if *input == merged {
                continue;
            }
            *input = merged;
            if overrides
                .get(next)
                .and_then(|row| row.get(slot))
                .ok_or(SolveError::BudgetExceeded)?
                .is_none()
            {
                *after
                    .get_mut(next)
                    .and_then(|row| row.get_mut(slot))
                    .ok_or(SolveError::BudgetExceeded)? = merged;
                enqueue(&mut pending, &mut queued, next, slot)?;
            }
        }
    }
    Ok(SparseFacts {
        graph,
        before,
        #[cfg(test)]
        after,
        #[cfg(test)]
        events,
        #[cfg(test)]
        updates,
        #[cfg(test)]
        node_events,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dataflow::{Analysis, Forward, Lattice};
    use std::collections::HashSet;

    #[derive(Clone, PartialEq, Eq)]
    struct Value<C>([C; 256]);
    impl<C: Coordinate> Lattice for Value<C> {
        fn top() -> Self {
            Self([C::TOP; 256])
        }
        fn meet(&self, other: &Self) -> Self {
            let mut value = self.clone();
            for (out, &input) in value.0.iter_mut().zip(other.0.iter()) {
                *out = out.meet(input);
            }
            value
        }
    }
    struct Oracle<'a, C> {
        writes: &'a HashMap<BlockId, Vec<(u8, C)>>,
        reachable: HashSet<BlockId>,
    }
    impl<C: Coordinate> Analysis for Oracle<'_, C> {
        type Value = Value<C>;
        type Node = BlockId;
        type Dir = Forward;
        fn boundary_value(&self) -> Self::Value {
            Value([C::ENTRY; 256])
        }
        fn transfer(&self, node: &BlockId, input: &Self::Value) -> Self::Value {
            if !self.reachable.contains(node) {
                return Value::top();
            }
            let mut value = input.clone();
            if let Some(writes) = self.writes.get(node) {
                for &(slot, output) in writes {
                    *value.0.get_mut(usize::from(slot)).unwrap() = output;
                }
            }
            value
        }
    }

    fn compare<C: Coordinate>(cfg: &Cfg<BlockId>, writes: &HashMap<BlockId, Vec<(u8, C)>>) {
        let oracle = Oracle {
            writes,
            reachable: cfg.reachable_nodes(),
        };
        let limit =
            super::super::solver::visit_limit(cfg, C::DESCENTS.saturating_mul(256)).unwrap();
        let generic = super::super::solver::solve(&oracle, cfg, limit).unwrap();
        let sparse = solve(cfg, writes).unwrap();
        for &node in cfg.nodes() {
            assert!(generic.before(&node).unwrap().0 == *sparse.before(node).unwrap());
            assert!(
                generic.after(&node).unwrap().0
                    == *sparse.after.get(sparse.graph.index(node).unwrap()).unwrap()
            );
        }
        let n = u64::try_from(cfg.nodes().len()).unwrap();
        let edges = sparse.graph.edges().unwrap();
        assert!(sparse.events <= C::DESCENTS.saturating_mul(256).saturating_mul(n));
        assert!(sparse.updates <= C::DESCENTS.saturating_mul(256).saturating_mul(edges));
    }

    #[test]
    fn sparse_facts_match_generic_solver_including_dead_and_top_valued_blocks() {
        let mut seed = 0x18a7_2026u64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        let types = [
            RegType::Any,
            RegType::Unset,
            RegType::Int,
            RegType::Model,
            RegType::Sample,
            RegType::Grid,
            RegType::VecInt,
            RegType::VecXqmx,
            RegType::AnyVec,
            RegType::Conflict,
        ];
        for _ in 0..1000 {
            let n = usize::try_from(next() % 20).unwrap().saturating_add(2);
            let mut cfg = Cfg::builder(0, n.saturating_sub(1));
            let mut writes = HashMap::new();
            let mut init = HashMap::new();
            for node in 0..n {
                let a = usize::try_from(next()).unwrap() % n;
                let b = usize::try_from(next()).unwrap() % n;
                cfg = cfg.edge(node, a).edge(node, b);
                let mut row = Vec::new();
                let mut bits = Vec::new();
                for _ in 0..next() % 10 {
                    let slot = u8::try_from(next() % 256).unwrap();
                    let value = *types.get(usize::try_from(next() % 10).unwrap()).unwrap();
                    row.push((slot, value));
                    bits.push((slot, value != RegType::Unset));
                }
                let _ = writes.insert(node, row);
                let _ = init.insert(node, bits);
            }
            let cfg = cfg.build();
            compare(&cfg, &writes);
            compare(&cfg, &init);
        }
    }

    #[test]
    #[expect(
        clippy::print_stdout,
        reason = "emit measured sparse work counts for cap-cost review"
    )]
    fn every_coordinate_reaches_all_four_descents_on_shared_edges() {
        // Four ordered arrivals: Any -> Model -> Grid -> Conflict -> Unset.
        // Delay each next arrival until the previous wave has crossed the tail.
        let tail = 280usize;
        let mut cfg = Cfg::builder(0, 7);
        for (a, b) in [(0, 1), (0, 2), (1, 3), (1, 4), (2, 5), (2, 6)] {
            cfg = cfg.edge(a, b);
        }
        let common = 7usize;
        for i in common..common.saturating_add(tail).saturating_sub(1) {
            cfg = cfg.edge(i, i.saturating_add(1));
        }
        let mut fresh = common.saturating_add(tail);
        for (source, delay) in [
            (3, 0),
            (4, tail.saturating_add(2)),
            (5, tail.saturating_mul(2).saturating_add(4)),
            (6, tail.saturating_mul(3).saturating_add(6)),
        ] {
            let mut previous = source;
            for _ in 0..delay {
                cfg = cfg.edge(previous, fresh);
                previous = fresh;
                fresh = fresh.saturating_add(1);
            }
            cfg = cfg.edge(previous, common);
        }
        let cfg = cfg.build();
        assert!(cfg.nodes().len() <= 2048);
        let mut writes = HashMap::new();
        let mut init = HashMap::new();
        for (node, value) in [
            (3, RegType::Model),
            (4, RegType::Sample),
            (5, RegType::Int),
            (6, RegType::Unset),
        ] {
            let _ = writes.insert(node, (0..=255).map(|slot| (slot, value)).collect());
            let _ = init.insert(
                node,
                (0..=255)
                    .map(|slot| (slot, value != RegType::Unset))
                    .collect(),
            );
        }
        let facts = solve(&cfg, &writes).unwrap();
        let bits = solve(&cfg, &init).unwrap();
        for node in common..common.saturating_add(tail) {
            let index = facts.graph.index(node).unwrap();
            assert_eq!(facts.node_events.get(index), Some(&1024));
            assert_eq!(bits.node_events.get(index), Some(&256));
        }
        let edges = facts.graph.edges().unwrap();
        assert!(facts.updates <= 1024u64.saturating_mul(edges));
        assert!(bits.updates <= 256u64.saturating_mul(edges));
        println!(
            "sparse register control N={} E={edges} register_events={} register_updates={} must_init_events={} must_init_updates={}",
            cfg.nodes().len(),
            facts.events,
            facts.updates,
            bits.events,
            bits.updates
        );
        compare(&cfg, &writes);
        compare(&cfg, &init);
    }
}
