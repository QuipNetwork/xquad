// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Opcode coverage ratchet.
//!
//! # The recorded decision (QUI-1034)
//!
//! Coverage **reports**; it does not gate on completeness. Requiring every
//! opcode to have a vector would fail today, on 28 opcodes, and could not
//! land on its own.
//!
//! What it does gate on is **regression**. The two floors below are the
//! measured coverage at the time this landed. Adding a vector may raise
//! them; nothing may lower them. That turns "someone should write more
//! vectors" from a wish into a number that only moves one way, without
//! blocking the work that exists.
//!
//! Raise a floor in the same commit that raises the coverage. Lowering one
//! means deleting vector coverage, which wants a reason in the commit
//! message.

#![cfg(feature = "rust")]

use xquad_conformance::Coverage;

/// Opcodes appearing in at least one vector's program. See
/// `xquad_conformance::coverage` for why this is measured separately from
/// the opcodes actually executed.
///
/// Raising this also dates the prose in
/// `docs/book/src/embedding/conformance.md`; check what that page still
/// claims about coverage before you push.
const PRESENT_FLOOR: usize = 65;

/// Opcodes executed to completion by at least one vector. Lower than
/// `PRESENT_FLOOR` because several opcodes appear only in vectors that
/// assert the fault they raise.
const REACHED_FLOOR: usize = 56;

#[test]
fn coverage_does_not_regress() {
    let coverage = Coverage::collect();

    assert!(
        coverage.present_count() >= PRESENT_FLOOR,
        "opcode coverage regressed: {} of {} opcodes appear in a vector, floor is {PRESENT_FLOOR}.\n\
         Restore the missing vector, or lower PRESENT_FLOOR with a reason.\n\n{}",
        coverage.present_count(),
        Coverage::total(),
        coverage.render(),
    );

    assert!(
        coverage.reached_count() >= REACHED_FLOOR,
        "opcode coverage regressed: {} of {} opcodes execute in a vector, floor is {REACHED_FLOOR}.\n\
         Restore the missing vector, or lower REACHED_FLOOR with a reason.\n\n{}",
        coverage.reached_count(),
        Coverage::total(),
        coverage.render(),
    );
}

/// A floor that has fallen behind the real number is a floor nobody is
/// ratcheting. Fail once coverage has grown past it so the constant is
/// raised deliberately rather than silently drifting into irrelevance.
///
/// Only the grew direction is asserted here. A drop is a regression, which
/// [`coverage_does_not_regress`] reports in the words that fit it; an
/// equality would fail here too and print a message telling the reader to
/// raise a floor above a number coverage can no longer reach.
#[test]
fn floors_do_not_fall_behind_coverage() {
    let coverage = Coverage::collect();

    assert!(
        coverage.present_count() <= PRESENT_FLOOR,
        "coverage grew to {} opcodes present; raise PRESENT_FLOOR to match.\n\n{}",
        coverage.present_count(),
        coverage.render(),
    );
    assert!(
        coverage.reached_count() <= REACHED_FLOOR,
        "coverage grew to {} opcodes reached; raise REACHED_FLOOR to match.\n\n{}",
        coverage.reached_count(),
        coverage.render(),
    );
}
