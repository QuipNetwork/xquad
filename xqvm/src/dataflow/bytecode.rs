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

//! CFG construction and forward stack-depth analysis for XQVM bytecode.
//!
//! The main entry point is [`check_stack_depth`], which:
//!
//! 1. Builds a [`Cfg`] of basic blocks (`BlockId = byte offset of first instruction`).
//! 2. Derives a [`BlockEffect`] for every block (abstract stack effect).
//! 3. Runs a forward worklist analysis with a [`DepthValue`] lattice.
//! 4. Reports the first [`VerifierError`] found.
//!
//! Unlike a linear scan, this analysis follows conditional branches so
//! underflows only reachable on non-fall-through paths are caught.
//!
//! # Correctness note
//!
//! Block effects are computed using the net stack delta from the opcode table.
//! This deliberately does not model the internal pop/push split for instructions
//! like `ADD` (pop 2, push 1, delta -1).  Any false negatives are the same as
//! those already present in a linear net-delta scan.

#[cfg(not(feature = "std"))]
use alloc::{
    collections::{BTreeSet, VecDeque},
    vec::Vec,
};

#[cfg(feature = "std")]
use std::collections::{BTreeSet, HashMap, VecDeque};

#[cfg(not(feature = "std"))]
use hashbrown::HashMap;

use crate::Program;
use crate::VerifierError;
use crate::bytecode::{Instruction, InstructionStream, JumpTable, StackEffect};
use crate::dataflow::analysis::Analysis;
use crate::dataflow::cfg::Cfg;
use crate::dataflow::direction::Forward;
use crate::dataflow::lattice::Lattice;
use crate::dataflow::solver::solve;

/// Maximum integer stack depth permitted by the VM spec.
const STACK_LIMIT: usize = 8192;

// ---------------------------------------------------------------------------
// BlockId
// ---------------------------------------------------------------------------

/// Byte offset of a basic block's first instruction.
pub type BlockId = usize;

// ---------------------------------------------------------------------------
// BlockEffect
// ---------------------------------------------------------------------------

/// Abstract stack effect of a basic block.
///
/// `min_input` is the minimum entry depth required to avoid underflow inside
/// the block (in the pre-`SCLR` phase).  `output` describes the depth at exit.
#[derive(Clone, Debug)]
pub struct BlockEffect {
    /// Minimum entry depth required to execute the block without underflow.
    pub min_input: usize,
    /// Stack depth at block exit.
    pub output: OutputKind,
}

/// How the exit depth relates to the entry depth.
#[derive(Clone, Debug)]
pub enum OutputKind {
    /// Exit depth = entry depth + `delta`.
    Delta(i32),
    /// Exit depth is fixed regardless of entry (block contains `SCLR`).
    Fixed(usize),
    /// Block always causes stack underflow regardless of entry depth.
    ///
    /// Occurs when instructions after a `SCLR` drive the absolute depth below 0.
    Underflow,
}

// ---------------------------------------------------------------------------
// DepthValue lattice
// ---------------------------------------------------------------------------

/// Abstract stack depth at a program point.
///
/// The meet-semilattice order is `Top` ≥ `Known(n)` (for all n) ≥ `Underflow`.
/// `Known` values are additionally ordered by magnitude so that
/// `meet(Known(a), Known(b)) = Known(min(a, b))` (conservative join).
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum DepthValue {
    /// No information yet (initial state for unvisited nodes).
    Top,
    /// The stack has exactly `n` items at this program point.
    Known(usize),
    /// A stack underflow was detected on at least one path to this point.
    Underflow,
}

impl Lattice for DepthValue {
    fn top() -> Self {
        Self::Top
    }

    fn meet(&self, other: &Self) -> Self {
        match (self, other) {
            (Self::Top, x) | (x, Self::Top) => x.clone(),
            (Self::Underflow, _) | (_, Self::Underflow) => Self::Underflow,
            (Self::Known(a), Self::Known(b)) => Self::Known(*a.min(b)),
        }
    }
}

// ---------------------------------------------------------------------------
// StackDepthAnalysis
// ---------------------------------------------------------------------------

/// Forward stack-depth analysis over XQVM basic blocks.
pub struct StackDepthAnalysis {
    effects: HashMap<BlockId, BlockEffect>,
}

impl Analysis for StackDepthAnalysis {
    type Value = DepthValue;
    type Node = BlockId;
    type Dir = Forward;

    fn boundary_value(&self) -> DepthValue {
        DepthValue::Known(0)
    }

    fn transfer(&self, node: &BlockId, input: &DepthValue) -> DepthValue {
        let effect = self.effects.get(node);
        debug_assert!(effect.is_some(), "every CFG node must have a BlockEffect");
        apply_effect(input.clone(), effect)
    }
}

// ---------------------------------------------------------------------------
// apply_effect  (internal)
// ---------------------------------------------------------------------------

/// Apply a [`BlockEffect`] to an entry [`DepthValue`], returning the exit value.
fn apply_effect(input: DepthValue, effect: Option<&BlockEffect>) -> DepthValue {
    let Some(effect) = effect else {
        return input;
    };
    match input {
        DepthValue::Top => DepthValue::Top,
        DepthValue::Underflow => DepthValue::Underflow,
        DepthValue::Known(depth) => {
            if depth < effect.min_input {
                return DepthValue::Underflow;
            }
            match &effect.output {
                OutputKind::Underflow => DepthValue::Underflow,
                OutputKind::Delta(d) => {
                    let out = depth.saturating_add_signed(isize::try_from(*d).unwrap_or(0));
                    DepthValue::Known(out)
                }
                OutputKind::Fixed(k) => DepthValue::Known(*k),
            }
        }
    }
}

// ---------------------------------------------------------------------------
// BlockTerminatorKind  (internal)
// ---------------------------------------------------------------------------

/// Kind of the last control-flow instruction in a basic block.
#[derive(Debug)]
enum BlockTerminatorKind {
    FallThrough,
    Jump {
        label: u16,
    },
    CondJump {
        label: u16,
    },
    /// Block ends with `RANGE` or `ITER`.
    LoopOpener,
    /// Block ends with `NEXT`.
    LoopCloser,
    Halt,
}

// ---------------------------------------------------------------------------
// LoopRegion  (internal)
// ---------------------------------------------------------------------------

/// Pre-built CFG context shared across the three CFG-based verifier phases.
///
/// Constructed once by [`build_cfg`] so that the register, uninit, and stack-depth
/// analyses can all operate on the same graph without repeating the two-pass construction.
pub(crate) struct CfgContext {
    /// The control-flow graph of basic blocks.
    pub(crate) cfg: Cfg<BlockId>,
    /// Abstract stack effect for each basic block.
    pub(crate) effects: HashMap<BlockId, BlockEffect>,
    /// Loop regions identified during CFG construction.
    pub(crate) loop_regions: Vec<LoopRegion>,
}

/// Pairing of a loop opener with the blocks it governs.
pub(crate) struct LoopRegion {
    /// Byte offset of the `RANGE`/`ITER` instruction (for error reporting).
    opener_pos: usize,
    /// Block that contains the `RANGE`/`ITER` terminator.
    opener_block: BlockId,
    /// First block of the loop body (successor of opener via fall-through).
    body_start: BlockId,
    /// First block after the loop (skip-edge target of opener).
    after_next: BlockId,
}

// ---------------------------------------------------------------------------
// compute_block_effect  (internal)
// ---------------------------------------------------------------------------

/// Simulate `code[block_start..block_end]` to derive a [`BlockEffect`] and the
/// [`BlockTerminatorKind`] with the absolute byte offset of the terminator.
///
/// Returns `(effect, kind, terminator_abs_offset)`.
fn compute_block_effect(
    code: &[u8],
    block_start: usize,
    block_end: usize,
) -> (BlockEffect, BlockTerminatorKind, usize) {
    let default = (
        BlockEffect {
            min_input: 0,
            output: OutputKind::Delta(0),
        },
        BlockTerminatorKind::FallThrough,
        block_start,
    );

    let block_code = match code.get(block_start..block_end) {
        Some(s) if !s.is_empty() => s,
        _ => return default,
    };

    // `depth` is the running stack depth relative to entry (starts at 0).
    // After `SCLR`, `depth` is absolute (reset to 0) and `fixed` is set.
    let mut depth: i32 = 0;
    let mut min_input: usize = 0;
    let mut fixed: bool = false;
    let mut static_underflow: bool = false;

    let mut kind = BlockTerminatorKind::FallThrough;
    let mut term_pos: usize = block_start;

    let mut stream = InstructionStream::new(block_code);
    while let Some(item) = stream.next_instruction() {
        let Ok((rel_pos, _, instr)) = item else { break };
        let abs_pos = block_start + rel_pos;

        match instr.stack_effect() {
            StackEffect::Reset => {
                if !fixed && depth < 0 {
                    min_input = min_input.max(depth.unsigned_abs() as usize);
                }
                fixed = true;
                static_underflow = false;
                depth = 0;
            }
            StackEffect::Delta(d) => {
                let d = i32::from(d);
                depth += d;
                if !fixed {
                    if depth < 0 {
                        min_input = min_input.max(depth.unsigned_abs() as usize);
                    }
                } else if depth < 0 {
                    static_underflow = true;
                }
            }
        }

        let new_kind = match &instr {
            Instruction::Jump1 { label } => Some(BlockTerminatorKind::Jump {
                label: u16::from(*label),
            }),
            Instruction::Jump2 { label } => Some(BlockTerminatorKind::Jump { label: *label }),
            Instruction::JumpI1 { label } => Some(BlockTerminatorKind::CondJump {
                label: u16::from(*label),
            }),
            Instruction::JumpI2 { label } => Some(BlockTerminatorKind::CondJump { label: *label }),
            Instruction::Halt {} => Some(BlockTerminatorKind::Halt),
            Instruction::Range {} | Instruction::Iter { .. } => {
                Some(BlockTerminatorKind::LoopOpener)
            }
            Instruction::Next {} => Some(BlockTerminatorKind::LoopCloser),
            _ => None,
        };

        if let Some(k) = new_kind {
            kind = k;
            term_pos = abs_pos;
        }
    }

    let output = if static_underflow {
        OutputKind::Underflow
    } else if fixed {
        OutputKind::Fixed(usize::try_from(depth).unwrap_or(0))
    } else {
        OutputKind::Delta(depth)
    };

    (BlockEffect { min_input, output }, kind, term_pos)
}

// ---------------------------------------------------------------------------
// simulate_body  (internal)
// ---------------------------------------------------------------------------

/// One-pass BFS simulation of a loop body for [`LoopStackImbalance`] detection.
///
/// Starting from `body_start` with `entry` depth, propagates through the body
/// blocks (skipping the back-edge to `body_start` and the skip-edge to
/// `after_next`).  Returns the depth at the `NEXT` block's output.
///
/// Because back-edges are skipped, each block is visited at most once per
/// depth-change, so the simulation always terminates.
///
/// [`LoopStackImbalance`]: VerifierError::LoopStackImbalance
fn simulate_body(
    body_start: BlockId,
    entry: DepthValue,
    after_next: BlockId,
    effects: &HashMap<BlockId, BlockEffect>,
    cfg: &Cfg<BlockId>,
) -> DepthValue {
    let mut depth_in: HashMap<BlockId, DepthValue> = HashMap::new();
    let mut worklist: VecDeque<BlockId> = VecDeque::new();
    let _ = depth_in.insert(body_start, entry);
    worklist.push_back(body_start);

    let mut exit_depth = DepthValue::Top;

    while let Some(block) = worklist.pop_front() {
        let d_in = depth_in.get(&block).cloned().unwrap_or(DepthValue::Top);
        let d_out = apply_effect(d_in, effects.get(&block));

        // A block with a successor == body_start is the NEXT block.
        if cfg.successors(&block).contains(&body_start) {
            exit_depth = exit_depth.meet(&d_out);
        }

        for &succ in cfg.successors(&block) {
            if succ == body_start || succ == after_next {
                continue;
            }
            let current = depth_in.get(&succ).cloned().unwrap_or(DepthValue::Top);
            let merged = current.meet(&d_out);
            if merged != current {
                let _ = depth_in.insert(succ, merged);
                worklist.push_back(succ);
            }
        }
    }

    exit_depth
}

// ---------------------------------------------------------------------------
// build_cfg
// ---------------------------------------------------------------------------

/// Build a [`CfgContext`] from bytecode.
///
/// Two-pass construction:
///
/// 1. Collect *leaders* (block-entry byte offsets) and pair every `RANGE`/`ITER`
///    with its matching `NEXT` to get loop skip-edges and back-edges.
/// 2. For each block `[leader_i, leader_{i+1})`, compute the [`BlockEffect`]
///    and emit outgoing CFG edges based on the terminator instruction.
///
/// Returns `None` for empty bytecode.  Call after structural verification to
/// guarantee well-formed input (balanced loops, valid jump targets).
#[expect(
    clippy::too_many_lines,
    reason = "two-pass CFG construction; splitting the passes into separate functions would obscure the algorithm"
)]
pub(crate) fn build_cfg(code: &[u8], jump_table: &JumpTable) -> Option<CfgContext> {
    if code.is_empty() {
        return None;
    }

    // ------------------------------------------------------------------
    // Pass 1 -- collect leaders and loop pairing.
    //
    // opener_info : RANGE/ITER abs_pos → (body_start, after_next)
    // next_back   : NEXT abs_pos       → body_start (back-edge target)
    // ------------------------------------------------------------------
    let mut leaders: BTreeSet<usize> = BTreeSet::new();
    let _ = leaders.insert(0);

    let mut opener_info: HashMap<usize, (usize, usize)> = HashMap::new();
    let mut next_back: HashMap<usize, usize> = HashMap::new();
    let mut loop_stack: Vec<(usize, usize)> = Vec::new(); // (opener_pos, body_start)
    let mut halt_block: Option<usize> = None;
    let mut cur_block: usize = 0;

    let mut stream = InstructionStream::new(code);
    while let Some(item) = stream.next_instruction() {
        let Ok((pos, _, instr)) = item else { break };
        let after = stream.pos();

        if leaders.contains(&pos) {
            cur_block = pos;
        }

        match &instr {
            Instruction::Target {} => {
                let _ = leaders.insert(pos);
            }
            Instruction::Jump1 { .. }
            | Instruction::Jump2 { .. }
            | Instruction::JumpI1 { .. }
            | Instruction::JumpI2 { .. } => {
                let _ = leaders.insert(after);
            }
            Instruction::Range {} | Instruction::Iter { .. } => {
                let _ = leaders.insert(after);
                loop_stack.push((pos, after));
            }
            Instruction::Next {} => {
                let _ = leaders.insert(after);
                if let Some((opener_pos, body_start)) = loop_stack.pop() {
                    let _ = next_back.insert(pos, body_start);
                    let _ = opener_info.insert(opener_pos, (body_start, after));
                }
            }
            Instruction::Halt {} => {
                halt_block = Some(cur_block);
            }
            _ => {}
        }
    }

    let leaders_sorted: Vec<usize> = leaders.into_iter().collect();
    let exit_block = halt_block.unwrap_or_else(|| *leaders_sorted.last().unwrap_or(&0));

    // ------------------------------------------------------------------
    // Pass 2 -- compute effects, build edges, collect loop regions.
    // ------------------------------------------------------------------
    let mut effects: HashMap<BlockId, BlockEffect> = HashMap::new();
    let mut cfg_builder = Cfg::builder(0usize, exit_block);
    let mut loop_regions: Vec<LoopRegion> = Vec::new();

    for (idx, &block_start) in leaders_sorted.iter().enumerate() {
        let block_end = leaders_sorted.get(idx + 1).copied().unwrap_or(code.len());

        let (effect, term_kind, term_pos) = compute_block_effect(code, block_start, block_end);
        let _ = effects.insert(block_start, effect);

        match term_kind {
            BlockTerminatorKind::FallThrough => {
                if block_end < code.len() {
                    cfg_builder = cfg_builder.edge(block_start, block_end);
                }
            }
            BlockTerminatorKind::Jump { label } => {
                if let Some(target) = jump_table.get(label) {
                    cfg_builder = cfg_builder.edge(block_start, target);
                }
            }
            BlockTerminatorKind::CondJump { label } => {
                if let Some(target) = jump_table.get(label) {
                    cfg_builder = cfg_builder.edge(block_start, target);
                }
                if block_end < code.len() {
                    cfg_builder = cfg_builder.edge(block_start, block_end);
                }
            }
            BlockTerminatorKind::LoopOpener => {
                if let Some(&(body_start, after_next)) = opener_info.get(&term_pos) {
                    cfg_builder = cfg_builder.edge(block_start, body_start);
                    cfg_builder = cfg_builder.edge(block_start, after_next);
                    loop_regions.push(LoopRegion {
                        opener_pos: term_pos,
                        opener_block: block_start,
                        body_start,
                        after_next,
                    });
                } else {
                    // Unmatched loop opener (shouldn't happen after Phase 1).
                    cfg_builder = cfg_builder.edge(block_start, block_end);
                }
            }
            BlockTerminatorKind::LoopCloser => {
                if let Some(&body_start) = next_back.get(&term_pos) {
                    cfg_builder = cfg_builder.edge(block_start, body_start);
                }
                if block_end < code.len() {
                    cfg_builder = cfg_builder.edge(block_start, block_end);
                }
            }
            BlockTerminatorKind::Halt => {}
        }
    }

    Some(CfgContext {
        cfg: cfg_builder.build(),
        effects,
        loop_regions,
    })
}

// ---------------------------------------------------------------------------
// check_stack_depth
// ---------------------------------------------------------------------------

/// Run the CFG-based forward stack-depth analysis using a pre-built [`CfgContext`].
///
/// Consumes `ctx` so that the caller can construct it once and pass it in
/// without cloning.  The check order is:
///
/// 1. [`VerifierError::LoopStackImbalance`] -- a loop body has non-zero net
///    stack effect (detected via a one-pass BFS that ignores back-edges).
/// 2. [`VerifierError::StackDepthMismatch`] -- two predecessors of a join
///    point carry different stack depths.
/// 3. [`VerifierError::StackUnderflow`] -- a reachable block has insufficient
///    entry depth or a static post-`SCLR` underflow.
/// 4. [`VerifierError::StackOverflowRisk`] -- depth exceeds 8 192.
///
/// # Errors
///
/// Returns the first [`VerifierError`] found, in the order described above.
pub(crate) fn check_stack_depth_with_context(ctx: CfgContext) -> Result<(), VerifierError> {
    let CfgContext {
        cfg,
        effects,
        loop_regions,
    } = ctx;
    let analysis = StackDepthAnalysis { effects };
    let result = solve(&analysis, &cfg);

    // --- LoopStackImbalance check (before underflow scan) ---
    //
    // The worklist analysis uses min-meet on back-edges, which causes
    // loop-body imbalances to converge rather than diverge.  A separate
    // one-pass BFS (simulate_body) detects them correctly.
    for region in &loop_regions {
        let entry = result
            .after(&region.opener_block)
            .cloned()
            .unwrap_or(DepthValue::Top);
        let exit = simulate_body(
            region.body_start,
            entry.clone(),
            region.after_next,
            &analysis.effects,
            &cfg,
        );
        if let (DepthValue::Known(e), DepthValue::Known(x)) = (&entry, &exit)
            && e != x
        {
            return Err(VerifierError::LoopStackImbalance {
                opener: region.opener_pos,
                entry: *e,
                exit: *x,
            });
        }
    }

    // --- StackDepthMismatch check ---
    //
    // For every block with two or more reachable predecessors, collect the
    // exit depth of each predecessor and verify they are all equal.  A
    // discrepancy means the stack is in an undefined state at the join point.
    // Blocks are visited in program order (ascending byte offset) so the
    // error reported is deterministic.
    //
    // `Top` predecessors are unreachable paths; they are excluded from the
    // comparison because they contribute no real stack frame.
    let mut sorted_join_blocks: Vec<BlockId> = cfg.nodes().to_vec();
    sorted_join_blocks.sort_unstable();

    for &block in &sorted_join_blocks {
        // Skip single-predecessor and entry blocks (no join to check).
        let preds = cfg.predecessors(&block);
        if preds.len() < 2 {
            continue;
        }
        // Skip unreachable blocks (their before-state is Top or missing).
        match result.before(&block) {
            None | Some(DepthValue::Top) => continue,
            _ => {}
        }
        // Collect known exit depths from reachable predecessors.
        let mut known = preds.iter().filter_map(|p| {
            if let Some(DepthValue::Known(d)) = result.after(p) {
                Some(*d)
            } else {
                None
            }
        });
        let Some(first) = known.next() else { continue };
        for other in known {
            if other != first {
                return Err(VerifierError::StackDepthMismatch {
                    target_offset: block,
                    depth_a: first,
                    depth_b: other,
                });
            }
        }
    }

    // --- Underflow / overflow scan ---
    for &node in cfg.nodes() {
        let before = result.before(&node).cloned().unwrap_or(DepthValue::Top);
        let after = result.after(&node).cloned().unwrap_or(DepthValue::Top);

        if before == DepthValue::Top {
            continue;
        }

        match &before {
            DepthValue::Underflow => {
                return Err(VerifierError::StackUnderflow { offset: node });
            }
            DepthValue::Known(d) if *d > STACK_LIMIT => {
                return Err(VerifierError::StackOverflowRisk {
                    offset: node,
                    depth: *d,
                });
            }
            _ => {}
        }

        match &after {
            DepthValue::Underflow => {
                return Err(VerifierError::StackUnderflow { offset: node });
            }
            DepthValue::Known(d) if *d > STACK_LIMIT => {
                return Err(VerifierError::StackOverflowRisk {
                    offset: node,
                    depth: *d,
                });
            }
            _ => {}
        }
    }

    Ok(())
}

/// Run the CFG-based forward stack-depth analysis on `program`.
///
/// Builds a `CfgContext` from `program` and delegates to
/// `check_stack_depth_with_context`.  Returns `Ok(())` for empty programs.
///
/// Callers that run multiple CFG-based analyses should prefer building the
/// context once via `build_cfg` and calling
/// `check_stack_depth_with_context` directly.
///
/// # Errors
///
/// Returns the first [`VerifierError`] found.
pub fn check_stack_depth(program: &Program) -> Result<(), VerifierError> {
    let code = program.code();
    if code.is_empty() {
        return Ok(());
    }
    let Some(ctx) = build_cfg(code, program.jump_table()) else {
        return Ok(());
    };
    check_stack_depth_with_context(ctx)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::InstructionBuilder;

    fn prog(build: impl FnOnce(&mut InstructionBuilder)) -> Program {
        let mut b = InstructionBuilder::new();
        build(&mut b);
        b.build().unwrap()
    }

    // --- basic valid programs ---

    #[test]
    fn empty_program_ok() {
        assert!(check_stack_depth(&Program::new(vec![])).is_ok());
    }

    #[test]
    fn halt_only_ok() {
        let p = prog(|b| {
            let _ = b.emit_halt();
        });
        assert!(check_stack_depth(&p).is_ok());
    }

    #[test]
    fn push_halt_ok() {
        let p = prog(|b| {
            let _ = b.emit_push(42).emit_halt();
        });
        assert!(check_stack_depth(&p).is_ok());
    }

    #[test]
    fn push_pop_halt_ok() {
        let p = prog(|b| {
            let _ = b.emit_push(1).emit_pop().emit_halt();
        });
        assert!(check_stack_depth(&p).is_ok());
    }

    // --- underflow detection ---

    #[test]
    fn pop_on_empty_stack_underflow() {
        let p = prog(|b| {
            let _ = b.emit_pop().emit_halt();
        });
        let err = check_stack_depth(&p).unwrap_err();
        assert_eq!(err.variant_name(), "StackUnderflow");
    }

    #[test]
    fn static_underflow_after_sclr() {
        // SCLR resets depth to 0; subsequent POP underflows unconditionally.
        let p = prog(|b| {
            let _ = b.emit_sclr().emit_pop().emit_halt();
        });
        let err = check_stack_depth(&p).unwrap_err();
        assert_eq!(err.variant_name(), "StackUnderflow");
    }

    #[test]
    fn sclr_then_push_ok() {
        // SCLR followed by a push is fine.
        let p = prog(|b| {
            let _ = b.emit_sclr().emit_push(1).emit_halt();
        });
        assert!(check_stack_depth(&p).is_ok());
    }

    // --- CFG path coverage: catches what a linear scan misses ---
    //
    // Program structure:
    //   PUSH1(1)          ; depth 1
    //   JUMPI1(target)    ; pops condition → depth 0; taken path jumps to TARGET
    //   PUSH1(99)         ; fall-through: depth 1
    //   TARGET            ; join point: taken arrives with depth 0, fall-through with depth 1
    //   POP               ; ...
    //   HALT
    //
    // The linear scan walks: 1 → JUMPI1 (depth 0) → PUSH1 (depth 1) → TARGET (1)
    // → POP (0) → HALT.  It misses that the taken path arrives with depth 0 != 1.
    // The CFG analysis detects the mismatch (StackDepthMismatch), which is the
    // root cause of what the linear scan would have eventually reported as
    // StackUnderflow on the POP instruction.

    #[test]
    fn cond_branch_depth_mismatch_caught() {
        let mut b = InstructionBuilder::new();
        let target = b.label();
        let _ = b
            .emit_push(1)
            .emit_jump_if(target) // taken: depth 0; fall-through: depth 0 → +1 below
            .emit_push(99) // fall-through only: depth 1 at TARGET
            .place(target)
            .unwrap()
            .emit_pop()
            .emit_halt();
        let p = b.build().unwrap();
        let err = check_stack_depth(&p).unwrap_err();
        // StackDepthMismatch fires before StackUnderflow: depth 0 vs 1 at TARGET.
        assert_eq!(err.variant_name(), "StackDepthMismatch");
    }

    // Both paths must arrive at the join with exactly the same depth.
    // Taken path: PUSH1(1)[+1], JUMPI1[-1] → depth 0 at TARGET.
    // Fall-through: same block as taken, then PUSH1(2)[+1], POP[-1] → depth 0 at TARGET.
    // meet(0, 0) = 0 → no mismatch; HALT at depth 0 → ok.
    #[test]
    fn cond_branch_both_paths_same_depth_ok() {
        let mut b = InstructionBuilder::new();
        let target = b.label();
        let _ = b
            .emit_push(1)
            .emit_jump_if(target) // taken: depth 0 → TARGET
            .emit_push(2) // fall-through: depth 1
            .emit_pop() // depth 0 → TARGET
            .place(target)
            .unwrap()
            .emit_halt();
        let p = b.build().unwrap();
        assert!(check_stack_depth(&p).is_ok());
    }

    #[test]
    fn stack_depth_mismatch_at_join() {
        // Minimal StackDepthMismatch case: JUMPI1 taken brings depth 0,
        // but the fall-through block pushes one extra item before TARGET.
        // PUSH1(5); JUMPI1(0); PUSH1(7); TARGET(0); HALT
        //   Taken:        depth 0 at TARGET.
        //   Fall-through: depth 1 at TARGET.
        //   → StackDepthMismatch.
        let mut b = InstructionBuilder::new();
        let target = b.label();
        let _ = b
            .emit_push(5)
            .emit_jump_if(target) // taken → TARGET with depth 0
            .emit_push(7) // fall-through: depth 1 → TARGET
            .place(target)
            .unwrap()
            .emit_halt();
        let p = b.build().unwrap();
        let err = check_stack_depth(&p).unwrap_err();
        assert_eq!(err.variant_name(), "StackDepthMismatch");
    }

    // --- loop ---

    #[test]
    fn stack_neutral_loop_ok() {
        // PUSH start=0, count=3; loop body pushes and pops (net 0); HALT.
        let p = prog(|b| {
            let _ = b
                .emit_push(0) // start
                .emit_push(3) // count
                .emit_range() // pops 2; depth 0 entering body
                .emit_push(1) // push
                .emit_pop() // pop (net 0 body)
                .emit_next()
                .emit_halt();
        });
        assert!(check_stack_depth(&p).is_ok());
    }

    #[test]
    fn loop_body_push_causes_imbalance() {
        let p = prog(|b| {
            let _ = b
                .emit_push(0)
                .emit_push(3)
                .emit_range()
                .emit_push(99) // unmatched push
                .emit_next()
                .emit_halt();
        });
        let err = check_stack_depth(&p).unwrap_err();
        assert_eq!(err.variant_name(), "LoopStackImbalance");
    }

    #[test]
    fn sclr_inside_loop_body_causes_imbalance() {
        // Entry depth after RANGE = 1; SCLR resets to 0; NEXT sees 0 != 1.
        let p = prog(|b| {
            let _ = b
                .emit_push(1) // extra item -- depth 1 after RANGE's -2
                .emit_push(0)
                .emit_push(3)
                .emit_range()
                .emit_sclr() // resets absolute depth to 0
                .emit_next()
                .emit_halt();
        });
        let err = check_stack_depth(&p).unwrap_err();
        assert_eq!(err.variant_name(), "LoopStackImbalance");
    }

    #[test]
    fn loop_body_pop_causes_imbalance() {
        // Entry depth after RANGE = 1; POP gives 0; NEXT sees 0 != 1.
        let p = prog(|b| {
            let _ = b
                .emit_push(1) // extra item
                .emit_push(0)
                .emit_push(3)
                .emit_range() // pops 2, depth = 1
                .emit_pop() // depth = 0
                .emit_next() // expects 1, got 0
                .emit_halt();
        });
        let err = check_stack_depth(&p).unwrap_err();
        assert_eq!(err.variant_name(), "LoopStackImbalance");
    }

    // --- depth_value lattice ---

    #[test]
    fn depth_value_meet_identity() {
        let x = DepthValue::Known(5);
        assert_eq!(DepthValue::Top.meet(&x), x);
        assert_eq!(x.meet(&DepthValue::Top), x);
    }

    #[test]
    fn depth_value_meet_known_takes_min() {
        let a = DepthValue::Known(3);
        let b = DepthValue::Known(7);
        assert_eq!(a.meet(&b), DepthValue::Known(3));
    }

    #[test]
    fn depth_value_underflow_absorbs() {
        let k = DepthValue::Known(10);
        assert_eq!(DepthValue::Underflow.meet(&k), DepthValue::Underflow);
        assert_eq!(k.meet(&DepthValue::Underflow), DepthValue::Underflow);
    }
}
