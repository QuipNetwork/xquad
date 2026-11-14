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

//! O7: every intra-block stack peak must respect the VM limit.

use xqvm::{Instruction, InstructionBuilder, checker};

#[test]
fn balanced_one_block_overflow_is_rejected_but_exact_limit_is_valid() {
    for peak in [8192, 8193] {
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(1);
        for _ in 1..peak {
            let _ = b.emit(Instruction::Copy {});
        }
        for _ in 0..peak {
            let _ = b.emit_pop();
        }
        let _ = b.emit_halt();
        let program = b.build().unwrap();
        if peak == 8193 {
            assert_eq!(program.code().len(), 16_388);
            assert_eq!(
                checker::check(&program).unwrap_err().variant_name(),
                "StackOverflowRisk"
            );
            assert!(matches!(
                xqvm::Vm::new().run(&program),
                Err(xqvm::Error::StackOverflow { .. })
            ));
        } else {
            assert!(checker::check(&program).is_ok());
            assert!(xqvm::Vm::new().run(&program).is_ok());
        }
    }
}

#[test]
fn peaks_before_and_after_resets_survive_later_clears() {
    let vector = xqasm::assemble_source(include_str!(
        "vectors/control-flow/audit_peak_before_sclr/program.xqasm"
    ))
    .unwrap();
    assert_eq!(
        checker::check(&vector).unwrap_err().variant_name(),
        "StackOverflowRisk"
    );
    for reset_first in [false, true] {
        let mut b = InstructionBuilder::new();
        if reset_first {
            let _ = b.emit_push(1).emit_sclr();
        }
        for _ in 0..8193 {
            let _ = b.emit_push(1);
        }
        let _ = b.emit_sclr().emit_halt();
        assert_eq!(
            checker::check(&b.build().unwrap())
                .unwrap_err()
                .variant_name(),
            "StackOverflowRisk"
        );
    }
}

#[test]
fn incoming_depth_is_added_to_the_relative_block_peak() {
    let mut b = InstructionBuilder::new();
    for _ in 0..4096 {
        let _ = b.emit_push(1);
    }
    let next = b.label();
    let _ = b.emit_jump(next).place(next).unwrap();
    for _ in 0..4097 {
        let _ = b.emit_push(1);
    }
    let _ = b.emit_sclr().emit_halt();
    assert_eq!(
        checker::check(&b.build().unwrap())
            .unwrap_err()
            .variant_name(),
        "StackOverflowRisk"
    );
}
