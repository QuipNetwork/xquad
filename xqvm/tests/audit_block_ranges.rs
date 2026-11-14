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

//! O3: checker block ranges stop at their first terminator.

#[test]
fn dead_writes_cannot_initialize_a_live_read() {
    let program = xqasm::assemble_source(include_str!(
        "vectors/control-flow/audit_dead_write_after_jump/program.xqasm"
    ))
    .unwrap();
    assert_eq!(
        xqvm::checker::check(&program).unwrap_err().variant_name(),
        "ReadUnsetRegister"
    );
    assert!(matches!(
        xqvm::Vm::new().run(&program),
        Err(xqvm::Error::UnsetRegister { .. })
    ));
}

#[test]
fn dead_reads_after_halt_do_not_reject_a_live_program() {
    let program = xqasm::assemble_source(include_str!(
        "vectors/control-flow/audit_dead_read_after_halt/program.xqasm"
    ))
    .unwrap();
    assert!(xqvm::checker::check(&program).is_ok());
    assert!(xqvm::Vm::new().run(&program).is_ok());
}
