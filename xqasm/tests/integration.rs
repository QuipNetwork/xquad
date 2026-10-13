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

//! Integration tests for the XQVM assembler.
//!
//! These tests assemble complete programs and verify the decoded instruction
//! stream, exercising the full parse -> assemble -> decode pipeline.

#![expect(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "test helpers - panics on failure are intentional"
)]

use xqasm::assemble_source;
use xqvm::{Instruction, InstructionStream, Register};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn decode_all(buf: &[u8]) -> Vec<Instruction> {
    InstructionStream::new(buf).map(|r| r.unwrap().2).collect()
}

fn asm(src: &str) -> Vec<Instruction> {
    let program = assemble_source(src).expect("assemble_source failed");
    decode_all(program.code())
}

// ---------------------------------------------------------------------------
// Minimal programs
// ---------------------------------------------------------------------------

#[test]
fn empty_program_produces_empty_buffer() {
    let prog = assemble_source("").unwrap();
    assert_eq!(prog.code(), []);
}

#[test]
fn comment_only_produces_empty_buffer() {
    let prog = assemble_source("; nothing here\n; more comments").unwrap();
    assert_eq!(prog.code(), []);
}

#[test]
fn single_halt() {
    let instrs = asm("HALT");
    assert_eq!(instrs, [Instruction::Halt {}]);
}

#[test]
fn push_and_halt() {
    let instrs = asm("PUSH 7\nHALT");
    assert_eq!(instrs[0], Instruction::Push1 { val: [7] });
    assert_eq!(instrs[1], Instruction::Halt {});
}

// ---------------------------------------------------------------------------
// Arithmetic
// ---------------------------------------------------------------------------

#[test]
fn arithmetic_sequence() {
    let instrs = asm("PUSH 10\nPUSH 3\nSUB\nNEG\nHALT");
    assert_eq!(instrs[0], Instruction::Push1 { val: [10] });
    assert_eq!(instrs[1], Instruction::Push1 { val: [3] });
    assert_eq!(instrs[2], Instruction::Sub {});
    assert_eq!(instrs[3], Instruction::Neg {});
    assert_eq!(instrs[4], Instruction::Halt {});
}

#[test]
fn bitwise_instructions() {
    let instrs = asm("PUSH 0xFF\nPUSH 0x0F\nBAND\nHALT");
    assert_eq!(instrs[0], Instruction::Push2 { val: [0x00, 0xFF] });
    assert_eq!(instrs[1], Instruction::Push1 { val: [0x0F] });
    assert_eq!(instrs[2], Instruction::BAnd {});
}

// ---------------------------------------------------------------------------
// Registers
// ---------------------------------------------------------------------------

#[test]
fn stow_and_load_roundtrip() {
    let instrs = asm("PUSH 99\nSTOW r5\nLOAD r5\nHALT");
    assert_eq!(instrs[0], Instruction::Push1 { val: [99] });
    assert_eq!(instrs[1], Instruction::Stow { reg: Register(5) });
    assert_eq!(instrs[2], Instruction::Load { reg: Register(5) });
}

#[test]
fn max_register_r255() {
    let instrs = asm("PUSH 1\nSTOW r255\nHALT");
    assert_eq!(instrs[1], Instruction::Stow { reg: Register(255) });
}

#[test]
fn energy_instruction() {
    let instrs = asm("ENERGY r0 r1");
    assert_eq!(
        instrs[0],
        Instruction::Energy {
            model: Register(0),
            sample: Register(1),
        }
    );
}

// ---------------------------------------------------------------------------
// Labels and jumps
// ---------------------------------------------------------------------------

#[test]
fn forward_jump_resolves() {
    // After QUI-404 + QUI-437 narrowing:
    //   JUMP1 .0  (2 bytes at 0)
    //   NOP       (1 byte  at 2)
    //   TARGET    (1 byte  at 3, emitted by place())
    //   HALT      (1 byte  at 4)
    let instrs = asm("JUMP .0\nNOP\n.0:\nHALT");
    assert_eq!(instrs[0], Instruction::Jump1 { label: 0 });
    assert_eq!(instrs[1], Instruction::Nop {});
    assert_eq!(instrs[2], Instruction::Target {});
    assert_eq!(instrs[3], Instruction::Halt {});
}

#[test]
fn backward_jumpi_resolves() {
    // .0:
    // PUSH -1  (2 bytes, site=0: Push1 0xFF)
    // ADD      (1 byte,  site=2)
    // COPY     (1 byte,  site=3)
    // JUMPI .0 (2 bytes, site=4: JumpI1 + u8, narrowed from JumpI2)
    let instrs = asm(".0:\nPUSH -1\nADD\nCOPY\nJUMPI .0");
    assert_eq!(instrs.last().unwrap(), &Instruction::JumpI1 { label: 0 });
}

#[test]
fn label_on_same_line_as_instruction() {
    // After QUI-404, place() emits an inline TARGET before the trailing NOP.
    let instrs = asm(".0: NOP\nJUMP .0");
    assert_eq!(instrs[0], Instruction::Target {});
    assert_eq!(instrs[1], Instruction::Nop {});
    assert_eq!(instrs[2], Instruction::Jump1 { label: 0 });
}

#[test]
fn multiple_labels_and_jumps() {
    let src = "
        PUSH 0
        JUMPI .0
        PUSH 42
    .0:
        HALT
    ";
    let instrs = asm(src);
    assert_eq!(instrs[0], Instruction::Push1 { val: [0x00] });
    // Label .0 has id 0, fits in u8 -> JumpI1 narrow form.
    assert!(matches!(instrs[1], Instruction::JumpI1 { .. }));
    assert_eq!(instrs[2], Instruction::Push1 { val: [42] });
    // place() inserts an inline TARGET before HALT.
    assert_eq!(instrs[3], Instruction::Target {});
    assert_eq!(instrs[4], Instruction::Halt {});
}

#[test]
fn jump_raw_integer_is_error() {
    assert!(assemble_source("JUMP 3").is_err());
}

#[test]
fn jump_negative_integer_is_error() {
    assert!(assemble_source("JUMP -4").is_err());
}

// ---------------------------------------------------------------------------
// QUBO / quantum-specific instructions
// ---------------------------------------------------------------------------

#[test]
fn bqmx_allocate() {
    let instrs = asm("PUSH 4\nBQMX r0");
    assert_eq!(instrs[0], Instruction::Push1 { val: [4] });
    assert_eq!(instrs[1], Instruction::Bqmx { reg: Register(0) });
}

#[test]
fn setquad_instruction() {
    let instrs = asm("PUSH -1\nPUSH 2\nPUSH 2\nSETQUAD r0");
    assert_eq!(instrs[0], Instruction::Push1 { val: [0xFF] });
    assert_eq!(instrs[3], Instruction::SetQuad { reg: Register(0) });
}

#[test]
fn vec_operations() {
    let instrs = asm("VECI r2\nPUSH 7\nVECPUSH r2\nPUSH 0\nVECGET r2");
    assert_eq!(instrs[0], Instruction::VecI { reg: Register(2) });
    assert_eq!(instrs[2], Instruction::VecPush { reg: Register(2) });
    assert_eq!(instrs[4], Instruction::VecGet { reg: Register(2) });
}

// ---------------------------------------------------------------------------
// Whitespace and comment tolerance
// ---------------------------------------------------------------------------

#[test]
fn inline_comments_ignored() {
    let instrs = asm("PUSH 1 ; push one\nHALT ; stop");
    assert_eq!(instrs.len(), 2);
    assert_eq!(instrs[0], Instruction::Push1 { val: [1] });
}

#[test]
fn blank_lines_and_indentation_ignored() {
    let src = "\n\n    PUSH 1\n\n    HALT\n\n";
    let instrs = asm(src);
    assert_eq!(instrs.len(), 2);
}

#[test]
fn hex_and_decimal_literals() {
    let instrs = asm("PUSH 0xFF\nPUSH 255");
    assert_eq!(instrs[0], Instruction::Push2 { val: [0x00, 0xFF] });
    assert_eq!(instrs[1], Instruction::Push2 { val: [0x00, 0xFF] });
}

// ---------------------------------------------------------------------------
// Case-insensitive mnemonics
// ---------------------------------------------------------------------------

#[test]
fn lowercase_mnemonics_assemble() {
    let instrs = asm("push 5\npush 3\nadd\nhalt");
    assert_eq!(instrs[0], Instruction::Push1 { val: [5] });
    assert_eq!(instrs[1], Instruction::Push1 { val: [3] });
    assert_eq!(instrs[2], Instruction::Add {});
    assert_eq!(instrs[3], Instruction::Halt {});
}

#[test]
fn mixed_case_mnemonics_assemble() {
    let instrs = asm("pUsH 7\nHaLt");
    assert_eq!(instrs[0], Instruction::Push1 { val: [7] });
    assert_eq!(instrs[1], Instruction::Halt {});
}

#[test]
fn mixed_case_produces_same_bytecode_as_uppercase() {
    let upper = assemble_source("PUSH 1\nADD\nHALT").unwrap();
    let lower = assemble_source("push 1\nadd\nhalt").unwrap();
    let mixed = assemble_source("Push 1\nAdd\nHalt").unwrap();
    assert_eq!(upper.code(), lower.code());
    assert_eq!(upper.code(), mixed.code());
}

#[test]
fn case_insensitive_jump_mnemonics() {
    let instrs = asm("jump .0\nnop\n.0:\nhalt");
    assert_eq!(instrs[0], Instruction::Jump1 { label: 0 });
    assert_eq!(instrs[1], Instruction::Nop {});
    assert_eq!(instrs[2], Instruction::Target {});
    assert_eq!(instrs[3], Instruction::Halt {});
}

// ---------------------------------------------------------------------------
// PUSH auto-promotion
// ---------------------------------------------------------------------------

#[test]
fn push_large_value_encodes_inline() {
    // 100000 = 0x0001_86A0 encodes inline as PUSH3 -- no constant pool needed.
    let program = assemble_source("PUSH 100000\nHALT").expect("assemble failed");
    let instrs = decode_all(program.code());
    assert_eq!(
        instrs[0],
        Instruction::Push3 {
            val: [0x01, 0x86, 0xA0]
        }
    );
    assert_eq!(instrs[1], Instruction::Halt {});
}

// ---------------------------------------------------------------------------
// Error cases
// ---------------------------------------------------------------------------

#[test]
fn unknown_mnemonic_is_error() {
    assert!(assemble_source("INVALID_OPCODE").is_err());
}

#[test]
fn undefined_label_is_error() {
    assert!(assemble_source("JUMP .99").is_err());
}

#[test]
fn duplicate_label_is_error() {
    assert!(assemble_source(".0:\nNOP\n.0:\nHALT").is_err());
}

#[test]
fn wrong_operand_count_is_error() {
    assert!(assemble_source("ADD r0").is_err());
}

#[test]
fn register_where_integer_expected_is_error() {
    assert!(assemble_source("PUSH r0").is_err());
}

#[test]
fn integer_where_register_expected_is_error() {
    assert!(assemble_source("LOAD 42").is_err());
}

#[test]
fn register_out_of_range_parse_error() {
    assert!(assemble_source("LOAD r256").is_err());
}

#[test]
fn raw_integer_for_jump_is_error() {
    assert!(assemble_source("JUMP 40000").is_err());
}
