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

//! W1-014 regression vectors (finding 014): the checker must skip dead code --
//! a block after an unconditional jump or after `HALT` -- in both directions.
//!
//! O3 already fixes this at the pinned base; these vectors pin both directions
//! so a regression is caught. No checker code change rides with them.

use xqvm::Program;
use xqvm::checker::{CheckerError, check};

/// Direction 1 (the auditor's 12-byte reproduction — former false accept): a live block
/// reads `r5`, a dead block that is skipped writes `r5`. The write must not
/// count, so the live read of the unset register is rejected.
#[test]
fn dead_block_write_does_not_initialize_a_live_read() {
    // 0x00 JUMP1 .0   (01 00)   -> TARGET at 0x05
    // 0x02 STOW r5    (0B 05)   dead: never runs
    // 0x04 HALT       (FF)      dead block ends here (no edge, no node)
    // 0x05 TARGET .0  (00)
    // 0x06 LOAD r5    (0A 05)   live read of the unset r5
    // 0x08 POP        (10)
    // 0x09 JUMP1 .0   (01 00)
    // 0x0B HALT       (FF)      exit
    let code = vec![
        0x01, 0x00, 0x0B, 0x05, 0xFF, 0x00, 0x0A, 0x05, 0x10, 0x01, 0x00, 0xFF,
    ];
    assert_eq!(code.len(), 12);
    let err = check(&Program::new(code)).unwrap_err();
    assert!(
        matches!(err, CheckerError::ReadUnsetRegister { offset: 6, reg: 5 }),
        "expected ReadUnsetRegister {{ offset: 6, reg: 5 }}, got {err:?}"
    );
}

/// Direction 2 (former false reject): a valid program whose only unset read is
/// inside a dead block after `HALT` must check clean. The dead block unsets the
/// register itself before reading it, so the read is unset only on the dead
/// path: the pre-O3 checker (3c68010) merged that block into the live
/// analysis and rejected this program with `ReadUnsetRegister { offset: 7,
/// reg: 0 }`; it is accepted now that dead blocks are skipped.
#[test]
fn unset_read_inside_a_skipped_dead_block_is_not_rejected() {
    // 0x00 PUSH1 1    (11 01)
    // 0x02 STOW r0    (0B 00)   r0 is set on the live path
    // 0x04 HALT       (FF)      exit; everything after is dead
    // 0x05 DROP r0    (0C 00)   dead: unsets r0
    // 0x07 LOAD r0    (0A 00)   dead: reads the unset r0
    // 0x09 HALT       (FF)
    let code = vec![0x11, 0x01, 0x0B, 0x00, 0xFF, 0x0C, 0x00, 0x0A, 0x00, 0xFF];
    assert!(
        check(&Program::new(code)).is_ok(),
        "a dead-code unset read must be skipped, not rejected"
    );
}
