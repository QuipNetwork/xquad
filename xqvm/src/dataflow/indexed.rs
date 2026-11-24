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

//! Dense, deterministic indexing for built-in bytecode analyses.

#[cfg(not(feature = "std"))]
use alloc::{collections::BTreeMap, vec::Vec};
#[cfg(feature = "std")]
use std::collections::BTreeMap;

use super::{BlockId, Cfg, SolveError};

pub(super) struct IndexedCfg {
    pub(super) nodes: Vec<BlockId>,
    pub(super) successors: Vec<Vec<usize>>,
    pub(super) entry: usize,
    indices: BTreeMap<BlockId, usize>,
}

impl IndexedCfg {
    pub(super) fn new(cfg: &Cfg<BlockId>) -> Result<Self, SolveError> {
        let nodes = cfg.nodes().to_vec();
        let indices: BTreeMap<_, _> = nodes
            .iter()
            .copied()
            .enumerate()
            .map(|(index, node)| (node, index))
            .collect();
        let entry = *indices.get(cfg.entry()).ok_or(SolveError::BudgetExceeded)?;
        let successors = nodes
            .iter()
            .map(|node| {
                cfg.successors(node)
                    .iter()
                    .map(|next| indices.get(next).copied().ok_or(SolveError::BudgetExceeded))
                    .collect::<Result<Vec<_>, _>>()
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            nodes,
            successors,
            entry,
            indices,
        })
    }

    pub(super) fn index(&self, node: BlockId) -> Result<usize, SolveError> {
        self.indices
            .get(&node)
            .copied()
            .ok_or(SolveError::BudgetExceeded)
    }

    pub(super) fn edges(&self) -> Result<u64, SolveError> {
        self.successors.iter().try_fold(0u64, |sum, edges| {
            u64::try_from(edges.len())
                .ok()
                .and_then(|n| sum.checked_add(n))
                .ok_or(SolveError::BudgetExceeded)
        })
    }
}
