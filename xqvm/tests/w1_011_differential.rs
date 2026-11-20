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

//! W1-011 differential (finding 011).
//!
//! The cross-body-jump rule is a single extra verifier phase, `JumpSpanPhase`,
//! inserted after the loop-nesting check. This compares a baseline verifier (the
//! pinned 4a31c96 phases, no `JumpSpanPhase`) against the candidate (the same
//! phases plus `JumpSpanPhase`) over a seeded corpus of structured programs,
//! malformed byte streams, the constructed auditor reproduction, and the
//! checked-in compiler-output vectors.
//!
//! Because the candidate is the baseline plus one reject-only phase that emits
//! only `JumpAcrossLoopBoundary`, the invariants are:
//!   - no reject -> accept (the candidate can only add rejections);
//!   - a program with no cross-body jump gets the exact baseline verdict;
//!   - a baseline-accepted program with a cross-body jump is rejected, with
//!     `JumpAcrossLoopBoundary` (no false negatives, no false positives).
//!
//! `has_cross_body_jump` is an independent reimplementation of the body-pairing
//! definition (not a call into the phase); the constructed auditor cases pin the
//! definition itself against a program whose cross-body status is known by
//! construction.

use std::collections::BTreeMap;

use xqvm::Program;
use xqvm::bytecode::{Instruction, InstructionStream};
use xqvm::verifier::{
    JumpSpanPhase, JumpTargetPhase, LoopNestingPhase, RegisterTypePhase, StackDepthPhase,
    StructuralPhase, UninitRegisterPhase, Verifier, VerifierError,
};

/// The pinned 4a31c96 phase set, without the cross-body rule.
fn baseline(program: &Program) -> Result<(), VerifierError> {
    Verifier::new()
        .with_phase(StructuralPhase)
        .with_phase(JumpTargetPhase)
        .with_phase(LoopNestingPhase)
        .with_phase(RegisterTypePhase)
        .with_phase(UninitRegisterPhase)
        .with_phase(StackDepthPhase)
        .run(program)
}

/// The same phases, plus `JumpSpanPhase` after the loop-nesting check.
fn candidate(program: &Program) -> Result<(), VerifierError> {
    Verifier::new()
        .with_phase(StructuralPhase)
        .with_phase(JumpTargetPhase)
        .with_phase(LoopNestingPhase)
        .with_phase(JumpSpanPhase)
        .with_phase(RegisterTypePhase)
        .with_phase(UninitRegisterPhase)
        .with_phase(StackDepthPhase)
        .run(program)
}

/// Independent oracle: does the program contain a jump whose target lies in a
/// different lexical loop body? A `RANGE`/`ITER` belongs to the enclosing body
/// and opens a fresh inner body; a `NEXT` belongs to the inner body it closes.
fn has_cross_body_jump(program: &Program) -> bool {
    let table = program.jump_table();
    let mut body_of: BTreeMap<usize, u32> = BTreeMap::new();
    let mut jumps: Vec<(usize, u16)> = Vec::new();
    let mut current: u32 = 0;
    let mut next_id: u32 = 1;
    let mut stack: Vec<u32> = Vec::new();

    let mut stream = InstructionStream::new(program.code());
    while let Some(item) = stream.next_instruction() {
        let Ok((pos, _label, instr)) = item else {
            // Malformed stream: nothing meaningful to classify.
            return false;
        };
        match instr {
            Instruction::Range {} | Instruction::Iter { .. } => {
                let _ = body_of.insert(pos, current);
                stack.push(current);
                current = next_id;
                next_id += 1;
            }
            Instruction::Next {} => {
                let _ = body_of.insert(pos, current);
                current = stack.pop().unwrap_or(0);
            }
            Instruction::Jump1 { label } | Instruction::JumpI1 { label } => {
                let _ = body_of.insert(pos, current);
                jumps.push((pos, u16::from(label)));
            }
            Instruction::Jump2 { label } | Instruction::JumpI2 { label } => {
                let _ = body_of.insert(pos, current);
                jumps.push((pos, label));
            }
            _ => {
                let _ = body_of.insert(pos, current);
            }
        }
    }
    for (pos, label) in jumps {
        let Some(target) = table.get(label) else {
            continue;
        };
        let jump_body = body_of.get(&pos).copied().unwrap_or(0);
        let target_body = body_of.get(&target).copied().unwrap_or(0);
        if jump_body != target_body {
            return true;
        }
    }
    false
}

/// Check the three differential invariants for one program. Returns true if the
/// candidate rejected an otherwise-baseline-accepted program (an accept->reject
/// change), so the caller can confirm the corpus exercises the rule.
fn check_one(code: Vec<u8>, note: &str) -> bool {
    let program = Program::new(code);
    let base = baseline(&program);
    let cand = candidate(&program);
    let cross = has_cross_body_jump(&program);

    // (3) No reject -> accept: the candidate only ever adds rejections.
    if base.is_err() {
        assert!(
            cand.is_err(),
            "{note}: reject -> accept (baseline {base:?}, candidate Ok)"
        );
    }

    if !cross {
        // (1) No cross-body jump: the candidate verdict equals the baseline's.
        assert_eq!(
            base.as_ref().map_err(VerifierError::variant_name),
            cand.as_ref().map_err(VerifierError::variant_name),
            "{note}: candidate diverged from baseline without a cross-body jump"
        );
        return false;
    }

    // Cross-body jump present. If the baseline accepted it, the candidate must
    // reject it, precisely with JumpAcrossLoopBoundary -- no false negatives.
    // (The baseline-Err case already had `cand.is_err()` asserted above.)
    if base.is_ok() {
        assert!(
            cand.is_err(),
            "{note}: cross-body jump present but candidate accepted (false negative)"
        );
        assert_eq!(
            cand.as_ref().err().map(VerifierError::variant_name),
            Some("JumpAcrossLoopBoundary"),
            "{note}: cross-body program rejected with the wrong error"
        );
        return true;
    }
    false
}

/// The auditor's 22-byte reproduction (finding 011): a conditional jump in loop
/// A's body targets a TARGET in loop B's body; both loops stay reachable with
/// equal known depths, so the baseline accepts it.
fn auditor_poc(loop_a_count: [u8; 2]) -> Vec<u8> {
    vec![
        0x11,
        0x00, // PUSH1 0
        0x12,
        loop_a_count[0],
        loop_a_count[1], // PUSH2 count
        0x08,            // RANGE (loop A)
        0x11,
        0x01, // PUSH1 1
        0x11,
        0x01, // PUSH1 1
        0x02,
        0x00, // JUMPI1 .0  (loop A body -> loop B body)
        0x11,
        0x00, // PUSH1 0
        0x11,
        0x01, // PUSH1 1
        0x08, // RANGE (loop B)
        0x00, // TARGET .0
        0x07, // NEXT
        0x10, // POP
        0x07, // NEXT
        0xFF, // HALT
    ]
}

/// Seeded xorshift64 PRNG; deterministic so the corpus is reproducible.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
}

/// A seeded structured program: loop-balanced, with a small alphabet of
/// control-flow instructions and jumps to a bounded set of labels. Many are
/// invalid under the baseline (stack/register/undefined-target); that is fine —
/// the invariants hold for every program, valid or not.
fn gen_structured(rng: &mut Rng) -> Vec<u8> {
    let mut code: Vec<u8> = Vec::new();
    let mut open: u32 = 0;
    let labels = 1 + (rng.next() % 4);
    let steps = 4 + (rng.next() % 24);
    for _ in 0..steps {
        match rng.next() % 9 {
            0 => {
                code.push(0x11);
                code.push(u8::try_from(rng.next() % 4).unwrap_or(0));
            } // PUSH1 small
            1 => code.push(0x10), // POP
            2 => {
                // PUSH1 0; PUSH1 1; RANGE  (open a loop)
                code.extend_from_slice(&[0x11, 0x00, 0x11, 0x01, 0x08]);
                open += 1;
            }
            3 => {
                if open > 0 {
                    code.push(0x07); // NEXT
                    open -= 1;
                }
            }
            4 => code.push(0x00), // TARGET
            5 => {
                code.push(0x01); // JUMP1 .k
                code.push(u8::try_from(rng.next() % labels).unwrap_or(0));
            }
            6 => {
                code.push(0x02); // JUMPI1 .k
                code.push(u8::try_from(rng.next() % labels).unwrap_or(0));
            }
            7 => code.push(0xF0), // NOP
            _ => {
                code.push(0x11);
                code.push(1); // PUSH1 1
            }
        }
    }
    while open > 0 {
        code.push(0x07);
        open -= 1;
    }
    code.push(0xFF); // HALT
    code
}

#[test]
fn differential_constructed_auditor_cases_are_rejected() {
    // Construction truth: both carry the cross-body jump, and the baseline
    // accepts them, so the candidate must reject with JumpAcrossLoopBoundary.
    for count in [[0x23, 0x28], [0x00, 0x01]] {
        let code = auditor_poc(count);
        let program = Program::new(code.clone());
        assert!(
            baseline(&program).is_ok(),
            "auditor PoC (count {count:?}) should pass the baseline verifier"
        );
        let changed = check_one(code, "auditor PoC");
        assert!(changed, "auditor PoC must be an accept->reject change");
    }
}

#[test]
fn differential_seeded_structured_corpus() {
    // 10k seeded structured CFGs. Every program must satisfy the three
    // invariants; the auditor family guarantees the accept->reject case is
    // exercised, so here we only require the invariants to hold.
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
    let mut changes = 0usize;
    for _ in 0..10_000 {
        let code = gen_structured(&mut rng);
        if check_one(code, "seeded") {
            changes += 1;
        }
    }
    // Not asserted to be non-zero (depth-matched cross-body jumps are rare in
    // random structured code); the auditor cases cover the positive direction.
    let _ = changes;
}

#[test]
fn differential_malformed_streams() {
    // 2k random byte streams: neither verifier may panic, and no reject->accept.
    let mut rng = Rng(0x0123_4567_89AB_CDEF);
    for _ in 0..2_000 {
        let len = usize::try_from(rng.next() % 40).unwrap_or(0);
        let mut code = Vec::with_capacity(len);
        for _ in 0..len {
            code.push(u8::try_from(rng.next() & 0xFF).unwrap_or(0));
        }
        let program = Program::new(code);
        let base = baseline(&program);
        let cand = candidate(&program);
        if base.is_err() {
            assert!(cand.is_err(), "malformed: reject -> accept");
        } else if !has_cross_body_jump(&program) {
            assert_eq!(
                base.as_ref().map_err(VerifierError::variant_name),
                cand.as_ref().map_err(VerifierError::variant_name),
                "malformed: candidate diverged without a cross-body jump"
            );
        }
    }
}

#[test]
fn differential_zero_new_rejections_of_compiler_output() {
    // Every assembled compiler-output vector the baseline accepts, the candidate
    // must also accept: the rule rejects none of the toolchain's real output.
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors");
    let mut checked = 0usize;
    let mut stack = vec![root];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.extension().is_some_and(|e| e == "xqasm") {
                let source = std::fs::read_to_string(&path).expect("read vector");
                let Ok(program) = xqasm::assemble_source(&source) else {
                    continue; // negative assembler fixtures are not programs
                };
                if baseline(&program).is_ok() {
                    assert!(
                        candidate(&program).is_ok(),
                        "candidate rejected compiler output the baseline accepts: {}",
                        path.display()
                    );
                }
                checked += 1;
            }
        }
    }
    assert!(checked > 0, "no compiler-output vectors were checked");
}
