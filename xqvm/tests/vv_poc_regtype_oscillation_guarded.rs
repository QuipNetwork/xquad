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

//! QUIP-02 guarded register-type convergence regressions.

#![expect(
    clippy::unwrap_used,
    reason = "test setup and expected rejection assertions"
)]

use std::{sync::mpsc, time::Duration};
use xqvm::{Instruction, InstructionBuilder, Program, Register, verifier};

fn cyclic_program(registers: u16) -> Program {
    let mut b = InstructionBuilder::new();
    let _ = b.emit(Instruction::Nop {});
    let cycle = b.label();
    let _ = b.place(cycle).unwrap();
    let _ = b.emit(Instruction::Target {});
    for slot in 0..registers {
        let _ = b.emit(Instruction::Bsmx {
            reg: Register(u8::try_from(slot).unwrap()),
        });
    }
    let _ = b.emit_jump(cycle).emit_halt();
    b.build().unwrap()
}

#[test]
fn one_unwritten_register_returns() {
    let result = verifier::verify(&cyclic_program(255));
    assert!(result.is_err());
}

#[test]
fn all_registers_written_returns() {
    let program = cyclic_program(256);
    let vector = xqasm::assemble_source(include_str!(
        "vectors/control-flow/audit_all_registers_cycle/program.xqasm"
    ))
    .unwrap();
    let (sender, receiver) = mpsc::channel();
    let _ = std::thread::spawn(move || {
        let _ = sender.send((verifier::verify(&program), verifier::verify(&vector)));
    });
    match receiver.recv_timeout(Duration::from_secs(2)) {
        Ok((original, vector)) => {
            assert!(original.is_err());
            assert!(vector.is_err());
        }
        Err(mpsc::RecvTimeoutError::Timeout) => {
            panic!("register-type solver did not terminate");
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => panic!("verifier worker disconnected"),
    }
}
