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
//! The cross-body-jump rule is a single extra checker phase, `JumpSpanPhase`,
//! registered in `Checker::default()`. This compares a baseline checker (the
//! pinned 4a31c96 phases, no `JumpSpanPhase`) against the candidate -- the
//! production `xqvm::checker::check`, so the phase must really be registered
//! there -- over a seeded corpus of structured programs,
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
//! Two sources of truth sit beside the checker. The independent one is the
//! construction flag `is_cross` of the stack-neutral generator below: each
//! program's cross-body status is known because the generator placed the jump.
//! `has_cross_body_jump` is a second, differently formulated oracle (loop
//! bodies as byte-offset intervals, compared by containment; see its doc), not
//! a copy of `jump_span.rs`; the generator checks it against `is_cross`, and the
//! corpora use it where no construction truth exists.

use xqvm::Program;
use xqvm::bytecode::{Instruction, InstructionStream};
use xqvm::checker::{
    Checker, CheckerError, JumpTargetPhase, LoopNestingPhase, RegisterTypePhase, StackDepthPhase,
    StructuralPhase, UninitRegisterPhase,
};

/// The pinned 4a31c96 phase set, without the cross-body rule.
fn baseline(program: &Program) -> Result<(), CheckerError> {
    Checker::new()
        .with_phase(StructuralPhase)
        .with_phase(JumpTargetPhase)
        .with_phase(LoopNestingPhase)
        .with_phase(RegisterTypePhase)
        .with_phase(UninitRegisterPhase)
        .with_phase(StackDepthPhase)
        .run(program)
}

/// The production checker: `xqvm::checker::check`, i.e. `Checker::default()`.
/// Running the candidate through the default pipeline (rather than through a
/// hand-built list that includes `JumpSpanPhase`) is what proves the phase is
/// actually registered there: if it were dropped from `Checker::default()`,
/// every accept->reject expectation below would fail.
fn candidate(program: &Program) -> Result<(), CheckerError> {
    xqvm::checker::check(program)
}

/// Oracle: does the program contain a jump whose target lies in a different
/// loop body? Formulated independently of `jump_span.rs`, which mints a body id
/// per loop during one stack scan and compares ids. Here each loop is an
/// INTERVAL of byte offsets: its body runs from just after the `RANGE`/`ITER`
/// to the matching `NEXT` inclusive -- so the opening instruction belongs to the
/// enclosing body and the `NEXT` to the body it closes -- and an unclosed loop
/// runs to the end of the code. A position's loop context is the set of loop
/// intervals containing it, and a jump is cross-body exactly when its own
/// position and its target's position have different contexts. A malformed
/// stream (undecodable instruction) is not classified.
fn has_cross_body_jump(program: &Program) -> bool {
    let mut positions: Vec<usize> = Vec::new();
    let mut opens: Vec<usize> = Vec::new();
    let mut loops: Vec<(usize, usize)> = Vec::new();
    let mut jumps: Vec<(usize, u16)> = Vec::new();
    let mut stream = InstructionStream::new(program.code());
    while let Some(item) = stream.next_instruction() {
        let Ok((pos, _label, instr)) = item else {
            return false;
        };
        positions.push(pos);
        match instr {
            Instruction::Range {} | Instruction::Iter { .. } => opens.push(pos),
            // A NEXT with no open loop closes nothing and lies in no interval.
            Instruction::Next {} => {
                if let Some(start) = opens.pop() {
                    loops.push((start, pos));
                }
            }
            Instruction::Jump1 { label } | Instruction::JumpI1 { label } => {
                jumps.push((pos, u16::from(label)));
            }
            Instruction::Jump2 { label } | Instruction::JumpI2 { label } => {
                jumps.push((pos, label));
            }
            _ => {}
        }
    }
    let end = positions.last().copied().unwrap_or(0);
    loops.extend(opens.into_iter().map(|start| (start, end)));
    let context = |at: usize| -> Vec<(usize, usize)> {
        let mut inside: Vec<(usize, usize)> = loops
            .iter()
            .copied()
            .filter(|&(start, close)| start < at && at <= close)
            .collect();
        inside.sort_unstable();
        inside
    };
    let table = program.jump_table();
    jumps.into_iter().any(|(pos, label)| {
        table
            .get(label)
            .is_some_and(|target| context(pos) != context(target))
    })
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
            base.as_ref().map_err(CheckerError::variant_name),
            cand.as_ref().map_err(CheckerError::variant_name),
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
            cand.as_ref().err().map(CheckerError::variant_name),
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

/// The phase is registered in production (X R8 item 2): the auditor reproduction goes
/// through `xqvm::checker::check` -- the entry point the pallet and the
/// `xqffi` bindings call -- and must be rejected with `JumpAcrossLoopBoundary`.
/// Removing `.with_phase(JumpSpanPhase)` from `Checker::default()` turns this
/// red, as it does the differential tests that use the same entry point.
#[test]
fn auditor_poc_is_rejected_by_the_production_checker() {
    for count in [[0x23, 0x28], [0x00, 0x01], [0x00, 0x00]] {
        let program = Program::new(auditor_poc(count));
        assert!(
            matches!(
                xqvm::checker::check(&program),
                Err(CheckerError::JumpAcrossLoopBoundary { .. })
            ),
            "PoC with loop A count {count:02x?}: expected JumpAcrossLoopBoundary, got {:?}",
            xqvm::checker::check(&program)
        );
    }
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
            "auditor PoC (count {count:?}) should pass the baseline checker"
        );
        let changed = check_one(code, "auditor PoC");
        assert!(changed, "auditor PoC must be an accept->reject change");
    }
}

/// Accept->reject changes the 10k seeded corpus must produce. Measured: 331
/// changes among 966 baseline-accepted programs at this seed (claude's R8
/// count, reproduced here); the floor sits just under it so the corpus cannot
/// silently stop exercising the rule.
const SEEDED_CHANGE_FLOOR: usize = 300;

#[test]
#[expect(
    clippy::print_stdout,
    reason = "the differential prints its change list for review (run with --nocapture)"
)]
fn differential_seeded_structured_corpus() {
    // 10k seeded structured CFGs. Every program must satisfy the three
    // invariants, and the corpus must actually exercise the rule.
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
    let (mut accepted, mut changes) = (0usize, 0usize);
    // Every change is kept and printed (run with --nocapture), each with its
    // corpus index, so the whole change list can be reviewed, not a sample.
    let mut change_list: Vec<(usize, String)> = Vec::new();
    for index in 0..10_000usize {
        let code = gen_structured(&mut rng);
        if baseline(&Program::new(code.clone())).is_ok() {
            accepted += 1;
        }
        if check_one(code.clone(), "seeded") {
            changes += 1;
            change_list.push((index, hex(&code)));
        }
    }
    println!(
        "W1-011 seeded corpus: 10000 programs, {accepted} baseline-accepted, \
         {changes} accept->reject (JumpAcrossLoopBoundary); all of them:"
    );
    for (index, program) in &change_list {
        println!("  #{index:05} {program}");
    }
    assert!(
        changes >= SEEDED_CHANGE_FLOOR,
        "only {changes} accept->reject changes in the seeded corpus (floor {SEEDED_CHANGE_FLOOR})"
    );
}

#[test]
fn differential_malformed_streams() {
    // 2k random byte streams: neither checker may panic, and no reject->accept.
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
                base.as_ref().map_err(CheckerError::variant_name),
                cand.as_ref().map_err(CheckerError::variant_name),
                "malformed: candidate diverged without a cross-body jump"
            );
        }
    }
}

/// Real compiler output (X R8 item 5): `xqcp`-compiled encoder, verifier and
/// decoder programs for a conditional inside a loop, a loop inside a
/// conditional, and a conditional inside a loop whose arm itself loops (its
/// decoder jumps over a whole inner loop, within the outer body). Regenerate
/// with `tests/xqcp_fixtures/generate.py`. Every program must be accepted by
/// both the baseline and the production checker.
#[test]
fn xqcp_compiled_control_flow_still_checks_clean() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/xqcp_fixtures");
    let mut checked = 0usize;
    for entry in std::fs::read_dir(&dir)
        .expect("xqcp fixtures directory")
        .flatten()
    {
        let path = entry.path();
        if path.extension().is_none_or(|e| e != "xqasm") {
            continue;
        }
        let source = std::fs::read_to_string(&path).expect("read fixture");
        let program = xqasm::assemble_source(&source)
            .unwrap_or_else(|e| panic!("{} does not assemble: {e}", path.display()));
        assert!(
            baseline(&program).is_ok(),
            "baseline rejects compiler output {}",
            path.display()
        );
        assert!(
            candidate(&program).is_ok(),
            "the production checker rejects compiler output {}: {:?}",
            path.display(),
            candidate(&program)
        );
        checked += 1;
    }
    assert_eq!(
        checked, 9,
        "expected 3 shapes x (encoder, verifier, decoder)"
    );
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

// --- Deliberately stack-neutral jump generator (architect R8 pre-finding) ---
//
// Builds baseline-valid loop programs and inserts a single jump whose source and
// target sit at the same (depth-0) block boundary, so the jump is stack-neutral
// by construction and the baseline stack-depth check cannot reject it for a
// depth reason. Loop bodies all sit at base depth 0: `PUSH1 0; PUSH1 1; RANGE`
// pushes two and RANGE pops two (fixed(2,0)); NEXT/TARGET/JUMP are fixed(0,0);
// JUMPI pops its condition (fixed(1,0)) after a matching PUSH1. The jump's body
// and the target's body are recorded during construction, so `is_cross` is known
// truth, independent of the checker.

/// Builds depth-0-boundary loop programs with a recorded body id per TARGET.
struct Builder {
    code: Vec<u8>,
    targets: Vec<u32>, // label index -> body id of the TARGET
    open: Vec<u32>,
    next_body: u32,
    current: u32,
    /// Seeded choice of loop opener: RANGE or ITER.
    opener: Rng,
    /// How many loops this program opened with ITER.
    iter_loops: usize,
}

/// The register the ITER opener iterates. The generator uses no other
/// register (its filler is NOPs), so it never collides with one.
const ITER_REG: u8 = 200;

impl Builder {
    fn new(opener_seed: u64) -> Self {
        Self {
            code: Vec::new(),
            targets: Vec::new(),
            open: Vec::new(),
            next_body: 1,
            current: 0,
            // xorshift needs a non-zero state.
            opener: Rng(opener_seed | 1),
            iter_loops: 0,
        }
    }
    fn nop(&mut self) {
        self.code.push(0xF0);
    }
    /// Emit a TARGET in the current body; returns its label index.
    fn target(&mut self) -> u8 {
        let label = u8::try_from(self.targets.len()).unwrap_or(u8::MAX);
        self.targets.push(self.current);
        self.code.push(0x00);
        label
    }
    /// Open a one-iteration loop with RANGE or, on a seeded choice, with ITER
    /// over a one-element vec. Both are stack-neutral; `JumpSpanPhase` must
    /// treat both as loop openers.
    fn open_loop(&mut self) {
        if self.opener.next() & 1 == 1 {
            let k = ITER_REG;
            self.code.extend_from_slice(&[
                0x4B, k, // VECI rK
                0x11, 0x01, 0x50, k, // PUSH1 1; VECPUSH rK
                0x11, 0x00, 0x11, 0x01, 0x09, k, // PUSH1 0; PUSH1 1; ITER rK
            ]);
            self.iter_loops += 1;
        } else {
            self.code.extend_from_slice(&[0x11, 0x00, 0x11, 0x01, 0x08]); // PUSH1 0; PUSH1 1; RANGE
        }
        self.open.push(self.current);
        self.current = self.next_body;
        self.next_body += 1;
    }
    fn close_loop(&mut self) {
        self.code.push(0x07); // NEXT
        if let Some(parent) = self.open.pop() {
            self.current = parent;
        }
    }
    /// Emit a jump to `label` in the current body; returns the jump's body id.
    fn jump(&mut self, label: u8, cond: bool) -> u32 {
        let body = self.current;
        if cond {
            self.code.extend_from_slice(&[0x11, 0x01, 0x02, label]); // PUSH1 1; JUMPI1 label
        } else {
            self.code.extend_from_slice(&[0x01, label]); // JUMP1 label
        }
        body
    }
    fn finish(mut self) -> Vec<u8> {
        while !self.open.is_empty() {
            self.close_loop();
        }
        self.code.push(0xFF); // HALT
        self.code
    }
}

/// One generated jump program: its bytes, a category label, and the
/// construction-truth cross-body flag.
struct JumpProgram {
    /// Loops opened with ITER (read before `finish` consumes the builder).
    iter_loops: usize,
    code: Vec<u8>,
    category: &'static str,
    is_cross: bool,
}

/// Produce a stack-neutral jump program. `seed` selects the category and small
/// structural variations (nesting depth, filler, forward/backward, JUMP/JUMPI).
#[expect(
    clippy::too_many_lines,
    reason = "one match arm per jump category keeps the whole generator readable in one place"
)]
fn gen_jump_program(seed: u64) -> JumpProgram {
    let mut r = Rng(seed ^ 0xD1B5_4A32_D192_ED03);
    let cond = r.next() & 1 == 0;
    let depth = 1 + (r.next() % 4); // 1..4 nesting levels
    let fill = r.next() % 3;
    let mut b = Builder::new(r.next());
    let add_fill = |b: &mut Builder, r: &mut Rng| {
        for _ in 0..(r.next() % (fill + 1)) {
            b.nop();
        }
    };

    match r.next() % 9 {
        6 => {
            // over_loop_fwd: inside 1..3 outer loops (total nesting 2..4), a
            // jump forward over a whole inner loop to a TARGET in the same
            // outer body -- xqcp's "if inside a loop whose arm loops". Intra-
            // body: the inner loop's NEXT returns to the outer body.
            let outer = 1 + (r.next() % 3);
            for _ in 0..outer {
                b.open_loop();
            }
            let jb = b.jump(0, cond);
            b.open_loop();
            add_fill(&mut b, &mut r);
            b.close_loop();
            let _ = b.target();
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "over_loop_fwd",
                is_cross: jb != target_body,
            }
        }
        7 => {
            // over_loop_back: the same, jumping backward over the inner loop.
            let outer = 1 + (r.next() % 3);
            for _ in 0..outer {
                b.open_loop();
            }
            let _ = b.target();
            b.open_loop();
            add_fill(&mut b, &mut r);
            b.close_loop();
            let jb = b.jump(0, cond);
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "over_loop_back",
                is_cross: jb != target_body,
            }
        }
        8 => {
            // top_over_loop: a top-level jump forward over a 1..3-deep loop nest
            // to a top-level TARGET.
            let nest = 1 + (r.next() % 3);
            let jb = b.jump(0, cond);
            for _ in 0..nest {
                b.open_loop();
            }
            add_fill(&mut b, &mut r);
            for _ in 0..nest {
                b.close_loop();
            }
            let _ = b.target();
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "top_over_loop",
                is_cross: jb != target_body,
            }
        }
        0 => {
            // nested_inner (the auditor shape, generalized to `depth` levels):
            // a jump in the outermost loop body targets a TARGET in the
            // innermost loop body. Forward (jump before the target).
            b.open_loop();
            add_fill(&mut b, &mut r);
            let jb = b.jump(0, cond); // in outer body, label 0 (first target, below)
            for _ in 1..depth {
                b.open_loop();
            }
            add_fill(&mut b, &mut r);
            let _ = b.target(); // label 0, innermost body
            for _ in 0..depth {
                b.close_loop();
            }
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "nested_inner",
                is_cross: jb != target_body,
            }
        }
        1 => {
            // into_loop: a top-level jump targets a TARGET inside a loop body.
            let jb = b.jump(0, cond); // body 0 (top level)
            b.open_loop();
            add_fill(&mut b, &mut r);
            let _ = b.target(); // label 0, loop body
            b.close_loop();
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "into_loop",
                is_cross: jb != target_body,
            }
        }
        2 => {
            // out_of_loop: a jump in a loop body targets a TARGET at top level,
            // placed after the loop (forward).
            b.open_loop();
            add_fill(&mut b, &mut r);
            let jb = b.jump(0, cond); // in loop body, label 0 (below)
            b.close_loop();
            let _ = b.target(); // label 0, top level
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "out_of_loop",
                is_cross: jb != target_body,
            }
        }
        3 => {
            // sibling: a jump in loop A's body targets a TARGET in loop B's body.
            b.open_loop(); // A
            add_fill(&mut b, &mut r);
            let jb = b.jump(0, cond); // in A, label 0 (below)
            b.close_loop(); // close A
            b.open_loop(); // B
            let _ = b.target(); // label 0, B
            b.close_loop();
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "sibling",
                is_cross: jb != target_body,
            }
        }
        4 => {
            // to_next: a jump in the outer body targets a TARGET placed right
            // before the inner loop's NEXT (the finding's exact shape).
            b.open_loop(); // outer
            add_fill(&mut b, &mut r);
            let jb = b.jump(0, cond);
            b.open_loop(); // inner
            let _ = b.target(); // label 0, inner body, just before NEXT
            b.close_loop(); // inner NEXT
            b.close_loop(); // outer NEXT
            let target_body = b.targets.first().copied().unwrap_or(0);
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "to_next",
                is_cross: jb != target_body,
            }
        }
        _ => {
            // intra_body: a jump to a TARGET in the SAME body. Nest `depth`
            // loops; the jump and target share the innermost body. Backward
            // (target before the jump) when `fill` is even, else forward.
            for _ in 0..depth {
                b.open_loop();
            }
            let backward = fill.is_multiple_of(2);
            if backward {
                let _ = b.target(); // label 0, innermost body
                add_fill(&mut b, &mut r);
                let jb = b.jump(0, cond);
                let target_body = b.targets.first().copied().unwrap_or(0);
                for _ in 0..depth {
                    b.close_loop();
                }
                return JumpProgram {
                    iter_loops: b.iter_loops,
                    code: b.finish(),
                    category: "intra_body",
                    is_cross: jb != target_body,
                };
            }
            let jb = b.jump(0, cond);
            add_fill(&mut b, &mut r);
            let _ = b.target(); // label 0, innermost body
            let target_body = b.targets.first().copied().unwrap_or(0);
            for _ in 0..depth {
                b.close_loop();
            }
            JumpProgram {
                iter_loops: b.iter_loops,
                code: b.finish(),
                category: "intra_body",
                is_cross: jb != target_body,
            }
        }
    }
}

#[test]
#[expect(
    clippy::print_stdout,
    reason = "the differential prints its change-list summary for review (run with --nocapture)"
)]
#[expect(
    clippy::too_many_lines,
    reason = "one loop that tallies every hard minimum, then the summary and the asserts"
)]
fn differential_stack_neutral_jumps_hard_minimums() {
    use std::collections::BTreeMap;

    let mut rng = Rng(0xC0FF_EE12_3456_789A);
    // accepted-by-baseline counts, by (category, is_cross).
    let mut by_cat: BTreeMap<(&'static str, bool), usize> = BTreeMap::new();
    let mut cross_accepted = 0usize;
    let mut intra_accepted = 0usize;
    // Intra-body jumps with a whole loop between jump and target: the shape a
    // NEXT that resets to top level (instead of the enclosing body) would
    // wrongly reject.
    let mut over_loop_accepted = 0usize;
    // Baseline-accepted programs with at least one ITER-opened loop, by
    // cross/intra: without them a JumpSpanPhase that forgot ITER as an
    // opener would pass (claude's M4).
    let mut iter_cross = 0usize;
    let mut iter_intra = 0usize;
    let mut cross_examples: Vec<String> = Vec::new();
    let mut intra_examples: Vec<String> = Vec::new();

    // Generate until both hard minimums are comfortably met, bounded so a
    // regression that stops producing accepted cross-body jumps fails loudly.
    for i in 0..200_000u64 {
        if cross_accepted >= 1_000
            && intra_accepted >= 1_000
            && over_loop_accepted >= 500
            && iter_cross >= 300
            && iter_intra >= 300
        {
            break;
        }
        let jp = gen_jump_program(rng.next() ^ i);
        let program = Program::new(jp.code.clone());
        if baseline(&program).is_err() {
            continue; // only baseline-accepted programs exercise the accept->reject claim
        }
        // The construction flag is the independent truth; the interval oracle
        // must agree with it.
        assert_eq!(
            jp.is_cross,
            has_cross_body_jump(&program),
            "{}: construction cross-body flag disagrees with the oracle: {}",
            jp.category,
            hex(&jp.code)
        );
        let cand = candidate(&program);
        if jp.is_cross {
            assert_eq!(
                cand.as_ref().err().map(CheckerError::variant_name),
                Some("JumpAcrossLoopBoundary"),
                "{}: baseline-accepted cross-body jump not rejected: {}",
                jp.category,
                hex(&jp.code)
            );
            cross_accepted += 1;
            if cross_examples.len() < 5 {
                cross_examples.push(format!("{}: {}", jp.category, hex(&jp.code)));
            }
        } else {
            assert!(
                cand.is_ok(),
                "{}: baseline-accepted intra-body jump wrongly rejected: {} ({:?})",
                jp.category,
                hex(&jp.code),
                cand
            );
            intra_accepted += 1;
            if jp.category.contains("over_loop") {
                assert!(
                    !jp.is_cross,
                    "{}: over_loop shapes are intra-body",
                    jp.category
                );
                over_loop_accepted += 1;
            }
            if intra_examples.len() < 5 {
                intra_examples.push(format!("{}: {}", jp.category, hex(&jp.code)));
            }
        }
        if jp.iter_loops > 0 {
            if jp.is_cross {
                iter_cross += 1;
            } else {
                iter_intra += 1;
            }
        }
        *by_cat.entry((jp.category, jp.is_cross)).or_default() += 1;
    }

    println!("W1-011 differential change list (baseline-accepted jump programs):");
    for ((cat, cross), n) in &by_cat {
        println!("  {cat:<12} cross={cross:<5} count={n}");
    }
    println!("  with an ITER-opened loop: cross={iter_cross} intra={iter_intra}");
    println!("first cross-body examples:");
    for e in &cross_examples {
        println!("  {e}");
    }
    println!("first intra-body examples:");
    for e in &intra_examples {
        println!("  {e}");
    }

    assert!(
        cross_accepted >= 1_000,
        "only {cross_accepted} baseline-accepted depth-matched cross-body jumps (need >= 1000)"
    );
    assert!(
        intra_accepted >= 1_000,
        "only {intra_accepted} baseline-accepted intra-body jumps (need >= 1000)"
    );
    assert!(
        over_loop_accepted >= 500,
        "only {over_loop_accepted} baseline-accepted jumps over a loop (need >= 500)"
    );
    assert!(
        iter_cross >= 300 && iter_intra >= 300,
        "only {iter_cross} cross / {iter_intra} intra baseline-accepted jumps with an \
         ITER-opened loop (need >= 300 each)"
    );
}

use core::fmt::Write as _;
fn hex(code: &[u8]) -> String {
    code.iter()
        .fold(String::with_capacity(code.len() * 2), |mut s, byte| {
            let _ = write!(s, "{byte:02x}");
            s
        })
}
