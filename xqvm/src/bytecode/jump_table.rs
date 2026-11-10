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

//! Jump table: maps sequential `TARGET` ids to their byte positions.
//!
//! After QUI-405 the jump table is no longer part of the wire format. It is
//! computed at load time by scanning the instruction stream for `TARGET`
//! opcodes; each `TARGET` is assigned a sequential id (`0, 1, 2, ...`) in
//! program order, and the table records the byte offset where it starts.
//!
//! `JUMP1`/`JUMP2`/`JUMPI1`/`JUMPI2` operands are these sequential ids: the
//! VM looks up `jump_table.get(label)` to recover the target byte offset.
//! `spec/xqvm/SPEC.md` specifies this sequential assignment (Target IDs).
//!
//! # Examples
//!
//! ```rust
//! use xqvm::{Instruction, JumpTable, codec};
//!
//! let bytes: Vec<u8> = [
//!     Instruction::Target {},
//!     Instruction::Halt {},
//! ]
//! .iter()
//! .flat_map(codec::encode)
//! .collect();
//!
//! let table = JumpTable::scan(&bytes);
//! assert_eq!(table.len(), 1);
//! assert_eq!(table.get(0), Some(0));
//! ```

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

// ---------------------------------------------------------------------------
// JumpTable
// ---------------------------------------------------------------------------

/// Maps sequential `TARGET` ids to their byte positions in the instruction
/// stream.
///
/// Built by [`scan`](Self::scan) -- a single linear pass that records the
/// byte offset of every `TARGET` opcode in the order they appear. Lookups
/// are O(1) via direct indexing.
///
/// # Examples
///
/// ```rust
/// use xqvm::{Instruction, JumpTable, codec};
///
/// // Two TARGETs followed by HALT.
/// let bytes: Vec<u8> = [
///     Instruction::Target {},
///     Instruction::Target {},
///     Instruction::Halt {},
/// ]
/// .iter()
/// .flat_map(codec::encode)
/// .collect();
///
/// let table = JumpTable::scan(&bytes);
/// assert_eq!(table.len(), 2);
/// assert_eq!(table.get(0), Some(0));
/// assert_eq!(table.get(1), Some(1));
/// assert_eq!(table.get(2), None);
/// ```
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct JumpTable {
    /// `targets[seq_id]` is the byte offset of the `TARGET` opcode whose
    /// sequential id is `seq_id`.
    targets: Vec<usize>,
}

impl JumpTable {
    /// Create a jump table from an explicit list of target byte offsets,
    /// indexed by sequential id.
    pub fn new(targets: Vec<usize>) -> Self {
        Self { targets }
    }

    /// Scan an instruction-byte buffer and record the position of every
    /// `TARGET` opcode in stream order.
    ///
    /// Delegates to the shared `verifier::scan` kernel so that
    /// TARGET collection and Phase 1 error detection share a single pass.
    /// Any verification error found during the scan is discarded here --
    /// callers that need it should call `verifier::scan` directly.
    pub fn scan(code: &[u8]) -> Self {
        crate::verifier::scan(code).0
    }

    /// Look up the byte offset of the `TARGET` with sequential id `label`.
    ///
    /// Returns `None` if `label` is out of range (i.e. the program contains
    /// fewer than `label + 1` `TARGET` opcodes).
    pub fn get(&self, label: u16) -> Option<usize> {
        self.targets.get(usize::from(label)).copied()
    }

    /// Number of `TARGET` opcodes recorded.
    pub fn len(&self) -> usize {
        self.targets.len()
    }

    /// Whether the table is empty (the program has no `TARGET` opcodes).
    pub fn is_empty(&self) -> bool {
        self.targets.is_empty()
    }

    /// Borrow the raw `targets` slice (sequential id -> byte offset).
    pub fn targets(&self) -> &[usize] {
        &self.targets
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bytecode::codec;
    use crate::bytecode::types::Instruction;

    fn assemble(instrs: &[Instruction]) -> Vec<u8> {
        instrs.iter().flat_map(codec::encode).collect()
    }

    #[test]
    fn empty_buffer_has_no_targets() {
        let table = JumpTable::scan(&[]);
        assert!(table.is_empty());
        assert_eq!(table.len(), 0);
        assert_eq!(table.get(0), None);
    }

    #[test]
    fn buffer_with_no_targets_has_empty_table() {
        let buf = assemble(&[Instruction::Push1 { val: [42] }, Instruction::Halt {}]);
        let table = JumpTable::scan(&buf);
        assert!(table.is_empty());
    }

    #[test]
    fn single_target_at_offset_zero() {
        let buf = assemble(&[Instruction::Target {}, Instruction::Halt {}]);
        let table = JumpTable::scan(&buf);
        assert_eq!(table.len(), 1);
        assert_eq!(table.get(0), Some(0));
    }

    #[test]
    fn target_after_other_instructions() {
        // PUSH1 (2 bytes) + TARGET (1 byte) + HALT
        let buf = assemble(&[
            Instruction::Push1 { val: [7] },
            Instruction::Target {},
            Instruction::Halt {},
        ]);
        let table = JumpTable::scan(&buf);
        assert_eq!(table.len(), 1);
        assert_eq!(table.get(0), Some(2));
    }

    #[test]
    fn multiple_targets_get_sequential_ids() {
        // TARGET (0) + NOP (1) + TARGET (2) + NOP (3) + TARGET (4) + HALT
        let buf = assemble(&[
            Instruction::Target {},
            Instruction::Nop {},
            Instruction::Target {},
            Instruction::Nop {},
            Instruction::Target {},
            Instruction::Halt {},
        ]);
        let table = JumpTable::scan(&buf);
        assert_eq!(table.len(), 3);
        assert_eq!(table.get(0), Some(0));
        assert_eq!(table.get(1), Some(2));
        assert_eq!(table.get(2), Some(4));
        assert_eq!(table.get(3), None);
    }

    #[test]
    fn explicit_constructor_round_trips_through_get() {
        let table = JumpTable::new(vec![10, 20, 30]);
        assert_eq!(table.len(), 3);
        assert_eq!(table.get(0), Some(10));
        assert_eq!(table.get(1), Some(20));
        assert_eq!(table.get(2), Some(30));
        assert_eq!(table.get(3), None);
        assert_eq!(table.targets(), &[10, 20, 30]);
    }
}
