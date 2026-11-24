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

use std::collections::BTreeSet;

use crate::bytecode::codec;
use crate::{Instruction, InstructionBuilder, InstructionStream, JumpTable, Program, Register};

use super::check;
use super::error::CheckerError;
use super::jump_target::JumpTargetPhase;
use super::loop_nesting::LoopNestingPhase;
use super::phase::{Checker, Phase};
use super::scan;
use super::scan::stream_err;
use super::structural::StructuralPhase;

fn bytes(instrs: &[Instruction]) -> Vec<u8> {
    instrs.iter().flat_map(codec::encode).collect()
}

// --- StructuralPhase ---

#[test]
fn structural_accepts_valid_stream() {
    let mut b = InstructionBuilder::new();
    let _ = b.emit_push(1).emit_push(2).emit_add().emit_halt();
    assert!(StructuralPhase.run(&b.build().unwrap()).is_ok());
}

#[test]
fn structural_rejects_truncated_push2() {
    // PUSH2 (0x12) needs 2 operand bytes; supply only 1.
    let prog = Program::new(vec![0x12, 0x00]);
    let err = StructuralPhase.run(&prog).unwrap_err();
    assert_eq!(err.variant_name(), "TruncatedInstruction");
}

#[test]
fn structural_rejects_reserved_opcode() {
    // 0x0D is the reserved gap in the opcode table.
    let err = StructuralPhase.run(&Program::new(vec![0x0D])).unwrap_err();
    assert_eq!(err.variant_name(), "BadOpcode");
}

// --- JumpTargetPhase ---

#[test]
fn jump_target_accepts_valid_label() {
    // TARGET(.0) then JUMP1 0.
    let code = bytes(&[
        Instruction::Target {},
        Instruction::Jump1 { label: 0 },
        Instruction::Halt {},
    ]);
    assert!(JumpTargetPhase.run(&Program::new(code)).is_ok());
}

#[test]
fn jump1_to_nonexistent_label() {
    // No TARGETs; label 0 is undefined.
    let code = bytes(&[Instruction::Jump1 { label: 0 }, Instruction::Halt {}]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

#[test]
fn jumpi1_to_nonexistent_label() {
    let code = bytes(&[Instruction::JumpI1 { label: 0 }, Instruction::Halt {}]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

#[test]
fn jump2_to_nonexistent_label() {
    let code = bytes(&[Instruction::Jump2 { label: 0 }, Instruction::Halt {}]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

#[test]
fn jump2_label_out_of_range_with_one_target() {
    // One TARGET (id 0); JUMP2 label=1 is out of range.
    let code = bytes(&[
        Instruction::Target {},
        Instruction::Jump2 { label: 1 },
        Instruction::Halt {},
    ]);
    let err = JumpTargetPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UndefinedJumpTarget");
}

// --- LoopNestingPhase ---

#[test]
fn loop_nesting_accepts_balanced_range() {
    let mut b = InstructionBuilder::new();
    let _ = b
        .emit_push(0)
        .emit_push(3)
        .emit_range()
        .emit_next()
        .emit_halt();
    assert!(LoopNestingPhase.run(&b.build().unwrap()).is_ok());
}

#[test]
fn unmatched_range_at_eof() {
    let code = bytes(&[Instruction::Range {}, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UnmatchedLoop");
}

#[test]
fn next_outside_any_loop() {
    let code = bytes(&[Instruction::Next {}, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "NoActiveLoop");
}

#[test]
fn lval_outside_loop() {
    let code = bytes(&[Instruction::LVal { reg: Register(0) }, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "NoActiveLoop");
}

#[test]
fn lidx_outside_loop() {
    let code = bytes(&[Instruction::Lidx { reg: Register(0) }, Instruction::Halt {}]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "NoActiveLoop");
}

#[test]
fn nested_loops_balanced() {
    // RANGE { RANGE { NEXT } NEXT } HALT
    let code = bytes(&[
        Instruction::Range {},
        Instruction::Range {},
        Instruction::Next {},
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    assert!(LoopNestingPhase.run(&Program::new(code)).is_ok());
}

#[test]
fn nested_loops_outer_unmatched() {
    // RANGE { RANGE { NEXT } HALT -- outer RANGE has no NEXT
    let code = bytes(&[
        Instruction::Range {},
        Instruction::Range {},
        Instruction::Next {},
        Instruction::Halt {},
    ]);
    let err = LoopNestingPhase.run(&Program::new(code)).unwrap_err();
    assert_eq!(err.variant_name(), "UnmatchedLoop");
    // Outermost opener is at offset 0.
    assert!(matches!(
        err,
        CheckerError::UnmatchedLoop {
            offset: 0,
            depth: 1
        }
    ));
}

// --- Checker composition ---

#[test]
fn default_checker_passes_valid_program() {
    let mut b = InstructionBuilder::new();
    let _ = b.emit_push(10).emit_push(32).emit_add().emit_halt();
    assert!(check(&b.build().unwrap()).is_ok());
}

#[test]
fn custom_checker_only_jump_phase() {
    // An unmatched RANGE is not caught if LoopNestingPhase is not included.
    let code = bytes(&[Instruction::Range {}, Instruction::Halt {}]);
    let result = Checker::new()
        .with_phase(JumpTargetPhase)
        .run(&Program::new(code));
    assert!(
        result.is_ok(),
        "jump-only checker should not catch loop errors"
    );
}

#[test]
fn custom_checker_structural_then_loop() {
    // A bad opcode is caught by StructuralPhase before LoopNestingPhase runs.
    let prog = Program::new(vec![0x0D]);
    let err = Checker::new()
        .with_phase(StructuralPhase)
        .with_phase(LoopNestingPhase)
        .run(&prog)
        .unwrap_err();
    assert_eq!(err.variant_name(), "BadOpcode");
}

// --- StackEffect ---

#[test]
fn stack_effect_on_all_variants_does_not_panic() {
    // Smoke test: stack_effect() must not panic for any instruction variant.
    use crate::bytecode::types::Register;
    let instrs: &[Instruction] = &[
        Instruction::Target {},
        Instruction::Jump1 { label: 0 },
        Instruction::JumpI1 { label: 0 },
        Instruction::Nop {},
        Instruction::Halt {},
        Instruction::Sclr {},
        Instruction::Add {},
        Instruction::Energy {
            model: Register(0),
            sample: Register(1),
        },
    ];
    for instr in instrs {
        let _ = instr.stack_effect();
    }
}

// --- scan ---

/// `scan` as a walk over an [`InstructionStream`], kept verbatim as the
/// oracle for `scan_matches_the_stream_walk`.
fn scan_via_stream(code: &[u8]) -> (JumpTable, (u8, u8), Option<CheckerError>) {
    let mut targets: Vec<usize> = Vec::new();
    let mut loop_offsets: Vec<usize> = Vec::new();
    let mut jump_refs: Vec<(usize, u16)> = Vec::new();
    let mut first_error: Option<CheckerError> = None;
    let mut input_slots: u8 = 0;
    let mut output_slots: u8 = 0;

    let mut stream = InstructionStream::new(code);
    'scan: while let Some(item) = stream.next_instruction() {
        let (pos, _label, instr) = match item {
            Ok(v) => v,
            Err(e) => {
                let _ = first_error.get_or_insert_with(|| stream_err(&e));
                break 'scan;
            }
        };

        match instr {
            Instruction::Target {} => targets.push(pos),
            Instruction::Input { .. } => input_slots = input_slots.saturating_add(1),
            Instruction::Output { .. } => output_slots = output_slots.saturating_add(1),
            Instruction::Range {} | Instruction::Iter { .. } => loop_offsets.push(pos),
            Instruction::Next {} if first_error.is_none() && loop_offsets.pop().is_none() => {
                first_error = Some(CheckerError::NoActiveLoop { offset: pos });
            }
            Instruction::Lidx { .. } | Instruction::LVal { .. } if loop_offsets.is_empty() => {
                let _ = first_error.get_or_insert(CheckerError::NoActiveLoop { offset: pos });
            }
            Instruction::Jump1 { label } | Instruction::JumpI1 { label } => {
                jump_refs.push((pos, u16::from(label)));
            }
            Instruction::Jump2 { label } | Instruction::JumpI2 { label } => {
                jump_refs.push((pos, label));
            }
            _ => {}
        }
    }

    // Unmatched loop openers: report the outermost (first-pushed) one.
    if first_error.is_none()
        && let Some(&outermost) = loop_offsets.first()
    {
        first_error = Some(CheckerError::UnmatchedLoop {
            offset: outermost,
            depth: loop_offsets.len(),
        });
    }

    let jump_table = JumpTable::new(targets);

    // Deferred jump-target check: needs the final TARGET count.
    if first_error.is_none() {
        let target_count = jump_table.len();
        for (offset, label) in jump_refs {
            if usize::from(label) >= target_count {
                first_error = Some(CheckerError::UndefinedJumpTarget {
                    offset,
                    label,
                    target_count,
                });
                break;
            }
        }
    }

    (jump_table, (input_slots, output_slots), first_error)
}

/// One xorshift64 step: deterministic and dependency-free.
fn next_rand(state: &mut u64) -> u64 {
    *state ^= *state << 13;
    *state ^= *state >> 7;
    *state ^= *state << 17;
    *state
}

/// Encode one instruction from the set `scan` treats specially, or a byte
/// the decoder rejects, or a `PUSH2` that truncates when it ends the buffer.
fn random_instruction(state: &mut u64) -> Vec<u8> {
    let [reg, label8, label16, pick, ..] = next_rand(state).to_le_bytes();
    let reg = Register(reg);
    let label8 = label8 % 8;
    let label16 = u16::from(label16 % 8);
    let instr = match pick % 16 {
        0 | 1 => Instruction::Target {},
        2 => Instruction::Jump1 { label: label8 },
        3 => Instruction::JumpI1 { label: label8 },
        4 => Instruction::Jump2 { label: label16 },
        5 => Instruction::JumpI2 { label: label16 },
        6 => Instruction::Range {},
        7 => Instruction::Iter { reg },
        8 | 9 => Instruction::Next {},
        10 => Instruction::Lidx { reg },
        11 => Instruction::LVal { reg },
        12 => Instruction::Input { reg },
        13 => Instruction::Output { reg },
        14 => return vec![0x0D],
        _ => return vec![0x12],
    };
    codec::encode(&instr)
}

#[test]
fn scan_matches_the_stream_walk() {
    let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
    let mut seen: BTreeSet<&'static str> = BTreeSet::new();
    let mut clean = 0_u32;
    for _ in 0..20_000 {
        let len = next_rand(&mut state) % 24;
        let code: Vec<u8> = (0..len)
            .flat_map(|_| random_instruction(&mut state))
            .collect();
        let (table, slots, err) = scan(&code);
        let (want_table, want_slots, want_err) = scan_via_stream(&code);
        assert_eq!((&table, slots), (&want_table, want_slots), "{code:02X?}");
        assert_eq!(format!("{err:?}"), format!("{want_err:?}"), "{code:02X?}");
        match err {
            Some(e) => {
                let _ = seen.insert(e.variant_name());
            }
            None => clean += 1,
        }
    }
    // The generator has to reach every outcome `scan` can report, or the
    // comparison above proves less than it appears to.
    assert!(clean > 100, "only {clean} generated programs checked clean");
    for variant in [
        "BadOpcode",
        "TruncatedInstruction",
        "NoActiveLoop",
        "UnmatchedLoop",
        "UndefinedJumpTarget",
    ] {
        assert!(
            seen.contains(variant),
            "{variant} never generated; saw {seen:?}"
        );
    }
}

#[test]
fn scan_reports_a_truncated_last_instruction_at_its_opcode() {
    // NOP, then PUSH2 (0x12) with one of its two operand bytes.
    let (_, _, err) = scan(&[0xF0, 0x12, 0x00]);
    assert!(
        matches!(err, Some(CheckerError::TruncatedInstruction { offset: 1 })),
        "got {err:?}"
    );
}
