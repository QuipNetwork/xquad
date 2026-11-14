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

//! QUIP-16 dead-write and live-read must-init regressions.

use xqvm::bytecode::codec;
use xqvm::dataflow::uninit::check_uninit_registers;
use xqvm::{Instruction, Program, Register, verifier};

fn program(instructions: &[Instruction]) -> Program {
    Program::new(instructions.iter().flat_map(codec::encode).collect())
}

#[test]
fn vv_poc_dead_drop_must_not_reject_live_halt() {
    // The sole live path stops at the first HALT. The DROP and LOAD are in
    // separate CFG nodes after it and cannot execute.
    let dead = program(&[
        Instruction::Halt {},
        Instruction::Target {},
        Instruction::Drop { reg: Register(0) },
        Instruction::Jump2 { label: 1 },
        Instruction::Target {},
        Instruction::Load { reg: Register(0) },
        Instruction::Halt {},
    ]);

    // The same DROP/LOAD sequence on a live path must remain rejected.
    let live = program(&[
        Instruction::Target {},
        Instruction::Drop { reg: Register(0) },
        Instruction::Jump2 { label: 1 },
        Instruction::Target {},
        Instruction::Load { reg: Register(0) },
        Instruction::Halt {},
    ]);
    assert_eq!(
        check_uninit_registers(&live).unwrap_err().variant_name(),
        "ReadUnsetRegister"
    );
    assert_eq!(
        verifier::verify(&live).unwrap_err().variant_name(),
        "ReadUnsetRegister"
    );

    let narrow = check_uninit_registers(&dead);
    let full = verifier::verify(&dead);
    assert!(narrow.is_ok(), "must-init rejected dead code: {narrow:?}");
    assert!(full.is_ok(), "full verifier rejected dead code: {full:?}");
}

#[test]
fn reachability_vectors_pin_must_init_and_full_verifier() {
    for (source, expected) in [
        (
            include_str!("vectors/control-flow/audit_dead_drop_load/program.xqasm"),
            None,
        ),
        (
            include_str!("vectors/control-flow/audit_live_drop_load/program.xqasm"),
            Some("ReadUnsetRegister"),
        ),
        (
            include_str!("vectors/control-flow/audit_all_initialized_drop/program.xqasm"),
            Some("ReadUnsetRegister"),
        ),
    ] {
        let program = xqasm::assemble_source(source).unwrap();
        assert_eq!(
            check_uninit_registers(&program)
                .err()
                .map(|e| e.variant_name()),
            expected
        );
        assert_eq!(
            verifier::verify(&program).err().map(|e| e.variant_name()),
            expected
        );
    }
}
