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

//! W1-014 regression vectors (finding 014): the verifier must skip a dead block
//! that ends in `HALT` after an unconditional jump, in both directions.
//!
//! O3 already fixes this at the pinned base; these vectors pin both directions
//! so a regression is caught. No verifier code change rides with them.

use xqvm::Program;
use xqvm::verifier::{VerifierError, verify};

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
    let err = verify(&Program::new(code)).unwrap_err();
    assert!(
        matches!(err, VerifierError::ReadUnsetRegister { offset: 6, reg: 5 }),
        "expected ReadUnsetRegister {{ offset: 6, reg: 5 }}, got {err:?}"
    );
}

/// Direction 2 (former false reject): a valid program whose only unset read is
/// inside a dead block skipped after an unconditional jump must verify.
#[test]
fn unset_read_inside_a_skipped_dead_block_is_not_rejected() {
    // 0x00 JUMP1 .0   (01 00)   -> TARGET at 0x05, skipping the dead block
    // 0x02 LOAD r5    (0A 05)   dead: unset read, but unreachable
    // 0x04 HALT       (FF)      dead block ends here
    // 0x05 TARGET .0  (00)
    // 0x06 HALT       (FF)      reachable, valid
    let code = vec![0x01, 0x00, 0x0A, 0x05, 0xFF, 0x00, 0xFF];
    assert!(
        verify(&Program::new(code)).is_ok(),
        "a dead-code unset read must be skipped, not rejected"
    );
}
