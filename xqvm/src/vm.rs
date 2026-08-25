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

//! XQVM bytecode interpreter.
//!
//! [`Vm`] executes XQVM bytecode programs. It maintains an integer stack,
//! a 256-slot register file, a loop stack, and optional calldata / output slots.
//! Constants are encoded inline in `PUSH1`..`PUSH8` instructions; no
//! separate constant pool is required.
//!
//! # Examples
//!
//! ```rust
//! use xqvm::Vm;
//! use xqvm::InstructionBuilder;
//!
//! // Build: PUSH 3 + PUSH 4 = 7; HALT.
//! let mut b = InstructionBuilder::new();
//! b.emit_push(3).emit_push(4).emit_add().emit_halt();
//! let program = b.build().unwrap();
//!
//! let mut vm = Vm::new();
//! vm.run(&program).unwrap();
//! assert_eq!(vm.stack(), &[7]);
//! ```

#[cfg(not(feature = "std"))]
use alloc::{format, vec, vec::Vec};

use crate::bytecode::{Instruction, InstructionStream, Program, Register};
use crate::opcodes;

use crate::error::Error;
use crate::model::{Domain, XqmxModel, XqmxSample};
use crate::tracer::{NoopTracer, StepState, Tracer};
use crate::value::RegVal;

// ---------------------------------------------------------------------------
// Loop support
// ---------------------------------------------------------------------------

/// The kind of the active loop.
#[derive(Debug)]
pub(crate) enum LoopKind {
    /// A range loop started by `RANGE`. Iterates current..end.
    Range { current: i64, end: i64 },
    /// A vec-iteration loop started by `ITER`. The frame owns a copy of the
    /// slice `vec[start_offset..start_offset + elements.len()]` taken at the
    /// time `ITER` ran, so subsequent mutations to the source register do
    /// not affect the iteration. `start_offset` is reported by `LIDX` so
    /// loop bodies can recover the original vec position.
    Iter {
        elements: IterElements,
        start_offset: usize,
        index: usize,
    },
}

/// Storage for the slice copied by `ITER`.
///
/// Vecs in xq-rs hold either `Int` or `Model` elements, so the loop frame
/// carries one of two parallel buffers rather than a `Vec<RegVal>` (which
/// would force `RegVal::default()` placeholders into every slot).
#[derive(Debug)]
pub(crate) enum IterElements {
    Int(Vec<i64>),
    Xqmx(Vec<XqmxModel>),
}

impl IterElements {
    fn len(&self) -> usize {
        match self {
            Self::Int(v) => v.len(),
            Self::Xqmx(v) => v.len(),
        }
    }
}

/// A single frame on the loop stack.
#[derive(Debug)]
pub(crate) struct LoopFrame {
    /// The kind of loop.
    pub kind: LoopKind,
    /// Byte offset of the first instruction inside the loop body
    /// (the instruction immediately after `RANGE` or `ITER`).
    pub body_start: usize,
}

// ---------------------------------------------------------------------------
// Step result
// ---------------------------------------------------------------------------

/// Control-flow signal returned by each instruction handler.
#[derive(Debug)]
pub(crate) enum StepResult {
    /// Advance to the next sequential instruction.
    Continue,
    /// Jump to the basic block identified by this label index.
    Jump(u16),
    /// Seek the instruction stream to the given byte offset (loop back-edge).
    Seek(usize),
    /// Stop execution.
    Halt,
    /// Push a new loop frame; the run loop sets `body_start` to `stream.pos()`.
    StartLoop { kind: LoopKind },
    /// Skip the loop body: scan forward to the matching NEXT without pushing a frame.
    SkipLoop,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Validate `start`/`end` against `len` and return `(start_usize, range)`.
///
/// `ITER` accepts the indices via the value stack as `i64`. The accepted
/// range is `0 <= start <= end` and `0 <= end <= len`; anything outside
/// produces an `IndexOutOfBounds` error pointing at the offending value.
/// `start == end` is permitted and produces an empty range.
fn resolve_iter_slice(
    pos: usize,
    start: i64,
    end: i64,
    len: usize,
) -> Result<(usize, core::ops::Range<usize>), Error> {
    let start_us = usize::try_from(start).map_err(|_| Error::IndexOutOfBounds {
        pos,
        index: start,
        len,
    })?;
    let end_us = usize::try_from(end).map_err(|_| Error::IndexOutOfBounds {
        pos,
        index: end,
        len,
    })?;
    if start_us > len {
        return Err(Error::IndexOutOfBounds {
            pos,
            index: start,
            len,
        });
    }
    if end_us > len {
        return Err(Error::IndexOutOfBounds {
            pos,
            index: end,
            len,
        });
    }
    if start_us > end_us {
        return Err(Error::IndexOutOfBounds {
            pos,
            index: end,
            len,
        });
    }
    Ok((start_us, start_us..end_us))
}

/// Sign-extend a big-endian byte slice (1..=8 bytes) to `i64`.
#[expect(
    clippy::arithmetic_side_effects,
    reason = "`n` is `bytes.len().min(8)`, so `n * 8` is at most 64 and the shift stays in 0..=56"
)]
fn sign_extend_be(bytes: &[u8]) -> i64 {
    debug_assert!(!bytes.is_empty() && bytes.len() <= 8);
    let mut v = 0i64;
    for &b in bytes {
        v = (v << 8) | i64::from(b);
    }
    // bytes.len() is 1..=8 per invariant; try_from never fails for these values,
    // unwrap_or(8) is a no-panic fallback for any impossible length > u32::MAX.
    let n = u32::try_from(bytes.len().min(8)).unwrap_or(8);
    let shift = 64u32 - n * 8;
    (v << shift) >> shift
}

// ---------------------------------------------------------------------------
// VM struct
// ---------------------------------------------------------------------------

/// Default step limit to guard against infinite loops.
///
/// Public because it is the number every host that does not choose its own
/// has to agree on, and there are four such hosts: `xqcli run`'s
/// `--step-limit` default, `xquad`'s Python VM wrapper, the conformance
/// harness's per-vector default, and this crate. Each used to restate the
/// literal with a comment claiming to match this constant, and nothing
/// enforced the claim.
pub const DEFAULT_STEP_LIMIT: u64 = 10_000_000;

/// Default allocation budget, in bytes.
///
/// Generous for off-chain use (a 1 GiB budget holds a sample of 134 million
/// variables, far beyond anything the toolchain compiles today) while still
/// bounding what a hostile program can ask a host for. Embedders that run
/// untrusted bytecode -- the Substrate pallet above all -- should set a much
/// smaller budget with [`Vm::set_memory_limit`].
const DEFAULT_MEMORY_LIMIT: u64 = 1 << 30;

/// Bytes charged per XQMX variable.
///
/// A variable costs one `i64` in a sample's value buffer. Model allocators are
/// charged at the same rate even though [`XqmxModel`] stores its coefficients
/// sparsely: the declared size is a promise that every consumer of the model --
/// `ENERGY`, the sample the program must allocate to evaluate it, every solver
/// backend -- has to materialise, so leaving it uncharged would let a program
/// hand the host an unbounded obligation for free.
const VARIABLE_BYTES: u64 = size_of::<i64>() as u64;

/// Bytes charged per element appended to a `vec<int>`.
///
/// A `Vec` grown one element at a time doubles its capacity, so it holds
/// between one and two elements' worth of buffer per live element. As with the
/// coefficient maps the budget charges the upper end.
const VEC_ELEMENT_BYTES: u64 = 2 * size_of::<i64>() as u64;

/// Bytes charged per nonzero linear coefficient.
///
/// A `BTreeMap` node holds up to eleven key/value pairs behind a fixed header
/// and is typically between half and completely full, so an entry's live cost
/// lies between one and two times its key/value payload. The budget charges the
/// upper end: the accounted total is meant to bound real heap use, not
/// approximate it from below.
///
/// The rate is a literal, not `2 * (size_of::<usize>() + size_of::<i64>())`.
/// The charge schedule is normative and target-independent, so it may not be
/// derived from the executing target's pointer width: the derivation gave 32
/// on a 64-bit host and 24 on wasm32, which is the runtime the pallet executes
/// in, while `xqvm_py/executor.py` and the published documentation both state
/// 32. That made the deployed VM enforce a schedule nothing in this repository
/// reproduced.
const LINEAR_ENTRY_BYTES: u64 = 32;

/// Bytes charged per nonzero quadratic coefficient. See [`LINEAR_ENTRY_BYTES`]
/// for the factor of two and for why the rate is a literal; the key here is an
/// index pair rather than a single index.
const QUAD_ENTRY_BYTES: u64 = 48;

/// Bytes charged for one model's fixed header when `ITER` copies a
/// `vec<xqmx>`, on top of the entries the model actually holds.
///
/// Worst-case number of coefficient entries an equality expansion over `n`
/// terms writes: `n` linear terms and one quadratic term per unordered pair.
///
/// Saturating throughout, so an `n` large enough to overflow the arithmetic
/// yields `u64::MAX` and is rejected by the budget rather than wrapping into a
/// small charge. The count is a worst case: repeated indices collide on the
/// same map key and cancelling coefficients are removed again, so an expansion
/// can write fewer entries than it is charged for.
fn equality_expansion_bytes(n: u64) -> u64 {
    let pairs = n.saturating_mul(n.saturating_sub(1)) / 2;
    n.saturating_mul(LINEAR_ENTRY_BYTES)
        .saturating_add(pairs.saturating_mul(QUAD_ENTRY_BYTES))
}

/// Bytes one whole [`XqmxModel`] costs to duplicate.
///
/// Priced at exactly what the allocator and the coefficient writes charged
/// to build it: the declared size at [`VARIABLE_BYTES`] plus one entry per
/// live coefficient. Measuring the copy the same way the original was
/// measured is what keeps a copy from being cheaper than the thing it copies.
fn model_bytes(m: &XqmxModel) -> u64 {
    let size = u64::try_from(m.size).unwrap_or(u64::MAX);
    let linear = u64::try_from(m.linear_len()).unwrap_or(u64::MAX);
    let quadratic = u64::try_from(m.quadratic_len()).unwrap_or(u64::MAX);
    size.saturating_mul(VARIABLE_BYTES)
        .saturating_add(linear.saturating_mul(LINEAR_ENTRY_BYTES))
        .saturating_add(quadratic.saturating_mul(QUAD_ENTRY_BYTES))
}

/// Bytes one whole [`RegVal`] costs to duplicate.
///
/// The rates are the schedule's own, applied to the same program-visible
/// quantities the allocating opcodes charge for, so a value costs the same
/// to copy as it cost to build. `Unset` is free because there is nothing to
/// copy.
fn regval_bytes(val: &RegVal) -> u64 {
    let count = |n: usize| u64::try_from(n).unwrap_or(u64::MAX);
    match val {
        RegVal::Unset => 0,
        RegVal::Int(_) => VARIABLE_BYTES,
        RegVal::VecInt(v) => count(v.len()).saturating_mul(VEC_ELEMENT_BYTES),
        RegVal::VecXqmx(v) => v
            .iter()
            .fold(0u64, |acc, m| acc.saturating_add(model_bytes(m))),
        RegVal::Model(m) => model_bytes(m),
        RegVal::Sample(s) => count(s.values.len()).saturating_mul(VARIABLE_BYTES),
    }
}

/// The XQVM bytecode interpreter.
///
/// # Examples
///
/// ```rust
/// use xqvm::Vm;
/// use xqvm::InstructionBuilder;
///
/// let mut b = InstructionBuilder::new();
/// b.emit_push(6).emit_push(7).emit_mul().emit_halt();
/// let program = b.build().unwrap();
///
/// let mut vm = Vm::new();
/// vm.run(&program).unwrap();
/// assert_eq!(vm.stack(), &[42]);
/// ```
#[derive(Debug)]
pub struct Vm {
    stack: Vec<i64>,
    regs: Vec<RegVal>,
    loop_stack: Vec<LoopFrame>,
    calldata: Vec<RegVal>,
    outputs: Vec<RegVal>,
    step_limit: u64,
    steps: u64,
    memory_limit: u64,
    memory_used: u64,
}

impl Default for Vm {
    fn default() -> Self {
        Self::new()
    }
}

impl Vm {
    /// Create a new VM with default settings.
    pub fn new() -> Self {
        Self {
            stack: Vec::new(),
            regs: {
                let mut v = Vec::with_capacity(256);
                v.resize_with(256, || RegVal::Unset);
                v
            },
            loop_stack: Vec::new(),
            calldata: Vec::new(),
            outputs: Vec::new(),
            step_limit: DEFAULT_STEP_LIMIT,
            steps: 0,
            memory_limit: DEFAULT_MEMORY_LIMIT,
            memory_used: 0,
        }
    }

    /// Set calldata slots available to the program via `INPUT`.
    ///
    /// Any [`RegVal`] can be placed in a calldata slot and loaded into a
    /// register using `INPUT`.  This allows passing models, samples, and
    /// vectors between programs without extra assembly instructions.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Vm;
    /// use xqvm::RegVal;
    /// use xqvm::{InstructionBuilder, Register};
    ///
    /// let mut vm = Vm::new();
    /// vm.set_calldata(vec![RegVal::Int(7)]);
    ///
    /// let mut b = InstructionBuilder::new();
    /// b.emit_push(0).emit_input(Register(0)).emit_halt();
    /// let program = b.build().unwrap();
    /// vm.run(&program).unwrap();
    /// assert_eq!(vm.register(0), &RegVal::Int(7));
    /// ```
    pub fn set_calldata(&mut self, data: Vec<RegVal>) -> &mut Self {
        self.calldata = data;
        self
    }

    /// Set the number of output slots writable by the program via `OUTPUT`.
    ///
    /// Slots are initialised to [`RegVal::Unset`]. Use [`Vm::outputs`] after
    /// execution to inspect results; `Unset` means the slot was never written.
    pub fn set_output_slots(&mut self, n: usize) -> &mut Self {
        self.outputs = vec![RegVal::Unset; n];
        self
    }

    /// Set the maximum number of instructions that may execute.
    ///
    /// The limit is exact: it bounds instructions executed, so `0` permits no
    /// instructions at all and a program whose first instruction is reached
    /// under it fails with
    /// [`Error::StepLimitExceeded`](crate::Error::StepLimitExceeded). A
    /// program that ends -- by `HALT` or by running off the end of the
    /// stream -- having executed at most `limit` instructions succeeds.
    ///
    /// For an unbounded run, say so explicitly with
    /// [`set_unlimited_steps`](Self::set_unlimited_steps).
    ///
    /// # Note for embedders
    ///
    /// Until 0.4.0 this method treated `0` as "unlimited". That made a zero
    /// budget the most dangerous value a caller could pass rather than the
    /// safest, which is the wrong way round for anything that takes a step
    /// limit from untrusted input.
    pub fn set_step_limit(&mut self, limit: u64) -> &mut Self {
        self.step_limit = limit;
        self
    }

    /// Remove the step limit, allowing the program to run to completion.
    ///
    /// Only safe where the caller controls the program or can abandon the
    /// thread. A program that never halts will not return.
    pub fn set_unlimited_steps(&mut self) -> &mut Self {
        self.step_limit = u64::MAX;
        self
    }

    /// Set the allocation budget, in bytes, for a single [`run`](Self::run).
    ///
    /// Every instruction that allocates a sample buffer, declares model
    /// variables, grows a vec, or expands constraint coefficients is charged
    /// against this budget before it allocates; an instruction that cannot pay
    /// fails with [`Error::MemoryLimitExceeded`] and allocates nothing. The
    /// default is 1 GiB.
    ///
    /// The budget is *cumulative*, not a high-water mark of live memory:
    /// bytes are charged when they are allocated and are never refunded, so a
    /// loop that allocates and discards cannot spend more than the budget in
    /// total. Pass `u64::MAX` for an effectively unlimited budget -- unlike a
    /// step limit of `0`, there is no sentinel value here.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::{Error, InstructionBuilder, Register, Vm};
    ///
    /// // A three-instruction program that asks for a 1 GiB sample.
    /// let mut b = InstructionBuilder::new();
    /// b.emit_push(1 << 27).emit_bsmx(Register(0)).emit_halt();
    /// let program = b.build().unwrap();
    ///
    /// let mut vm = Vm::new();
    /// vm.set_memory_limit(1 << 20); // 1 MiB
    /// let err = vm.run(&program).unwrap_err();
    /// assert!(matches!(err, Error::MemoryLimitExceeded { .. }));
    /// ```
    pub fn set_memory_limit(&mut self, bytes: u64) -> &mut Self {
        self.memory_limit = bytes;
        self
    }

    /// Return the configured allocation budget in bytes.
    pub fn memory_limit(&self) -> u64 {
        self.memory_limit
    }

    /// Return the bytes charged against the budget by the last
    /// [`run`](Self::run) call.
    pub fn memory_used(&self) -> u64 {
        self.memory_used
    }

    /// Return the current stack (bottom first).
    pub fn stack(&self) -> &[i64] {
        &self.stack
    }

    /// Return the output slots written by `OUTPUT`.
    pub fn outputs(&self) -> &[RegVal] {
        &self.outputs
    }

    /// Return the value of register `r`.
    pub fn register(&self, r: u8) -> &RegVal {
        self.regs
            .get(usize::from(r))
            .unwrap_or_else(|| unreachable!("register slot {} always valid in a 256-slot file", r))
    }

    /// Write `val` into register `r`.
    ///
    /// Use this to pre-load registers before calling [`run`](Self::run),
    /// for example when passing a model or a vec between programs.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::Vm;
    /// use xqvm::RegVal;
    /// use xqvm::InstructionBuilder;
    ///
    /// let mut vm = Vm::new();
    /// vm.set_register(0, RegVal::Int(42));
    ///
    /// let mut b = InstructionBuilder::new();
    /// b.emit_load(xqvm::Register(0)).emit_halt();
    /// let program = b.build().unwrap();
    /// vm.run(&program).unwrap();
    /// assert_eq!(vm.stack(), &[42]);
    /// ```
    pub fn set_register(&mut self, r: u8, val: RegVal) -> &mut Self {
        *self.regs.get_mut(usize::from(r)).unwrap_or_else(|| {
            unreachable!("register slot {} always valid in a 256-slot file", r)
        }) = val;
        self
    }

    /// Return the number of steps executed by the last [`run`](Self::run) call.
    pub fn steps(&self) -> u64 {
        self.steps
    }

    /// Reset the VM to its initial state.
    ///
    /// Restores everything a run touches to the state a freshly-constructed
    /// [`Vm`] is in: the value stack, the register file, the loop stack, the
    /// step and memory counters, the output slots, the calldata and the
    /// outputs themselves. The configured budgets ([`set_step_limit`] and
    /// [`set_memory_limit`]) are settings rather than run state and survive.
    ///
    /// Clearing the outputs and the calldata is the point. Leaving them
    /// standing meant a reused VM answered with the previous run's outputs:
    /// the Rust backend returned `[42]` where the Python one returned `[]`
    /// for the same reuse. A host that resets and runs again must call
    /// [`set_calldata`] and [`set_output_slots`] again too, exactly as it
    /// does after [`Vm::new`].
    ///
    /// [`set_step_limit`]: Self::set_step_limit
    /// [`set_memory_limit`]: Self::set_memory_limit
    /// [`set_calldata`]: Self::set_calldata
    /// [`set_output_slots`]: Self::set_output_slots
    ///
    /// # Examples
    ///
    /// ```rust
    /// use xqvm::{InstructionBuilder, Register, Vm};
    ///
    /// let mut b = InstructionBuilder::new();
    /// b.emit_push(42).emit_stow(Register(0));
    /// b.emit_push(0).emit_output(Register(0)).emit_halt();
    /// let program = b.build().unwrap();
    ///
    /// let mut vm = Vm::new();
    /// vm.set_output_slots(1);
    /// vm.run(&program).unwrap();
    /// assert_eq!(vm.outputs().len(), 1);
    ///
    /// vm.reset();
    /// assert!(vm.outputs().is_empty());
    /// ```
    pub fn reset(&mut self) {
        self.stack.clear();
        self.regs.iter_mut().for_each(|r| *r = RegVal::Unset);
        self.loop_stack.clear();
        self.calldata.clear();
        self.outputs.clear();
        self.steps = 0;
        self.memory_used = 0;
    }

    /// Execute a [`Program`].
    ///
    /// Executes the instruction stream of `program`. Inline constants are
    /// encoded directly in `PUSH1`..`PUSH8` instructions -- no separate
    /// constant pool is needed.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] on any runtime fault (stack underflow, bad jump, etc.).
    pub fn run(&mut self, program: &Program) -> Result<(), Error> {
        self.run_trace(&mut NoopTracer, program)
    }

    /// Execute a [`Program`] with a [`Tracer`].
    ///
    /// Behaves identically to [`run`](Self::run) but invokes `tracer.on_step`
    /// after every instruction, providing a snapshot of the VM state.
    ///
    /// When `T` is [`NoopTracer`], the compiler eliminates all tracing
    /// overhead via dead-code elimination.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] on any runtime fault, or [`Error::TraceFailed`] if
    /// the tracer callback returns an error.
    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`self.steps < self.step_limit` is checked immediately above, so the step counter stops at u64::MAX rather than wrapping (`spec/xqvm/SPEC.md`'s Step budget)"
    )]
    pub fn run_trace<T: Tracer>(&mut self, tracer: &mut T, program: &Program) -> Result<(), Error>
    where
        T::Error: core::fmt::Display,
    {
        let mut stream = InstructionStream::from_program(program);
        let table = program.jump_table();
        self.steps = 0;
        self.memory_used = 0;

        // Probe for the next instruction before charging: the limit bounds
        // instructions executed, so a program that ends -- HALT or end of
        // stream -- having executed exactly `step_limit` instructions
        // succeeds, and `steps()` never counts the probe. This is the Python
        // VM's loop shape (`while pc < len`).
        while let Some(item) = stream.next_instruction() {
            if self.steps >= self.step_limit {
                return Err(Error::StepLimitExceeded {
                    limit: self.step_limit,
                });
            }
            self.steps += 1;
            let (pos, _label, instr) = item.map_err(Error::from)?;

            let result = if T::ENABLED {
                // Snapshot read registers before dispatch.
                let read_slots = instr.read_registers();
                let read_regs: Vec<(u8, RegVal)> = read_slots
                    .as_slice()
                    .iter()
                    .filter_map(|&i| Some((i, self.regs.get(usize::from(i))?.clone())))
                    .collect();

                // Snapshot written register values before dispatch.
                let write_slots = instr.written_registers();
                let pre_write: Vec<RegVal> = write_slots
                    .as_slice()
                    .iter()
                    .filter_map(|&i| self.regs.get(usize::from(i)).cloned())
                    .collect();

                // Execute the instruction.
                let result = self.dispatch(pos, instr)?;

                // Collect registers that actually changed.
                let written_regs: Vec<(u8, RegVal)> = write_slots
                    .as_slice()
                    .iter()
                    .zip(pre_write.iter())
                    .filter_map(|(&i, pre)| {
                        let cur = self.regs.get(usize::from(i))?;
                        (cur != pre).then(|| (i, cur.clone()))
                    })
                    .collect();

                let state = StepState {
                    pos,
                    step: self.steps,
                    instruction: &instr,
                    stack: &self.stack,
                    read_regs: &read_regs,
                    written_regs: &written_regs,
                    loop_depth: self.loop_stack.len(),
                };

                tracer.on_step(&state).map_err(|e| Error::TraceFailed {
                    pos,
                    message: format!("{e}"),
                })?;

                result
            } else {
                self.dispatch(pos, instr)?
            };

            match result {
                StepResult::Continue => {}
                StepResult::Halt => break,
                StepResult::Jump(label) => {
                    let target = table.get(label).ok_or(Error::InvalidLabel { pos, label })?;
                    stream.seek(target).map_err(Error::from)?;
                }
                StepResult::Seek(target) => {
                    stream.seek(target).map_err(Error::from)?;
                }
                StepResult::StartLoop { kind } => {
                    if self.loop_stack.len() >= Self::LOOP_LIMIT {
                        return Err(Error::LoopStackOverflow { pos });
                    }
                    let body_start = stream.pos();
                    self.loop_stack.push(LoopFrame { kind, body_start });
                }
                #[expect(
                    clippy::arithmetic_side_effects,
                    reason = "one `depth` step per instruction scanned, and the stream is bounded by `spec/xqvm/ENCODING.md`'s u32 `code_len`; the decrement runs only while `depth >= 1` because reaching 0 breaks"
                )]
                StepResult::SkipLoop => {
                    let mut depth: u32 = 1;
                    loop {
                        let Some(item) = stream.next_instruction() else {
                            return Err(Error::UnmatchedLoop { pos });
                        };
                        let (_scan_pos, _label, scan_instr) = item.map_err(Error::from)?;
                        match scan_instr {
                            Instruction::Range {} | Instruction::Iter { .. } => depth += 1,
                            Instruction::Next {} => {
                                depth -= 1;
                                if depth == 0 {
                                    break;
                                }
                            }
                            _ => {}
                        }
                    }
                }
            }
        }

        Ok(())
    }
}

// ---- define the dispatcher-generating macro ----

macro_rules! impl_dispatch {
    (
        $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
            $_delta:expr, {$($field:ident: $ftype:ty),*}) ),*
        $(,)?
    ) => {
        impl Vm {
            fn dispatch(
                &mut self,
                pos: usize,
                instr: Instruction,
            ) -> Result<StepResult, Error> {
                match instr {
                    $(
                        Instruction::$variant { $($field),* } => {
                            ::pastey::paste! {
                                self.[<exec_ $variant:snake>](pos $(, $field)*)
                            }
                        }
                    )*
                }
            }
        }
    };
}

opcodes!(impl_dispatch);

// ---- hand-written instruction handlers ----

impl Vm {
    // -- helpers --

    fn pop(&mut self, pos: usize) -> Result<i64, Error> {
        self.stack.pop().ok_or(Error::StackUnderflow { pos })
    }

    /// Stack depth limit required by the spec.
    const STACK_LIMIT: usize = 8192;

    /// Loop-nesting limit required by the spec.
    ///
    /// `RANGE` and `ITER` each push a frame and only `NEXT` pops one, so a
    /// program that jumps back over a loop header without running its `NEXT`
    /// grows the loop stack without bound -- and a `LoopFrame` is not charged
    /// against the allocation budget, so nothing else bounds it either. The
    /// value stack has been capped since the first release; this is the same
    /// cap on the other stack.
    const LOOP_LIMIT: usize = 8192;

    fn push_stack(&mut self, v: i64, pos: usize) -> Result<(), Error> {
        if self.stack.len() >= Self::STACK_LIMIT {
            return Err(Error::StackOverflow { pos });
        }
        self.stack.push(v);
        Ok(())
    }

    /// Charge `bytes` against the allocation budget.
    ///
    /// Callers must charge *before* they allocate. Letting the allocator fail
    /// instead is not an option for the embedders that matter: inside a Wasm
    /// runtime a failed allocation traps the whole execution rather than
    /// returning an error the host can map to a dispatch error.
    fn charge(&mut self, pos: usize, bytes: u64) -> Result<(), Error> {
        let total = self.memory_used.saturating_add(bytes);
        if total > self.memory_limit {
            return Err(Error::MemoryLimitExceeded {
                pos,
                requested: bytes,
                used: self.memory_used,
                limit: self.memory_limit,
            });
        }
        self.memory_used = total;
        Ok(())
    }

    /// Charge for `count` XQMX variables. See [`VARIABLE_BYTES`].
    fn charge_variables(&mut self, pos: usize, count: usize) -> Result<(), Error> {
        let count = u64::try_from(count).unwrap_or(u64::MAX);
        self.charge(pos, count.saturating_mul(VARIABLE_BYTES))
    }

    /// Validate, charge for, and convert an allocator's size operand.
    ///
    /// The order is normative and the whole point of the helper: reject a
    /// size that is not an allocation, then charge the budget off the `i64`,
    /// and only then narrow to `usize`. Charging before the conversion is
    /// what keeps the fault identity target-independent -- a negative size
    /// raises [`Error::InvalidAllocation`] on every target and an oversized
    /// one raises [`Error::MemoryLimitExceeded`] on every target, including
    /// the wasm32 runtime the Substrate pallet executes in, where `usize` is
    /// 32 bits wide and a `usize::try_from` would otherwise decide the
    /// answer.
    fn allocation_size(&mut self, pos: usize, size: i64) -> Result<usize, Error> {
        if size < 0 {
            return Err(Error::InvalidAllocation { pos, size });
        }
        let bytes = u64::try_from(size).unwrap_or(u64::MAX);
        self.charge(pos, bytes.saturating_mul(VARIABLE_BYTES))?;
        usize::try_from(size).map_err(|_| Error::InvalidAllocation { pos, size })
    }

    /// Charge `bytes` for one coefficient written into `reg`, but only when
    /// `reg` holds a model.
    ///
    /// A model stores its coefficients sparsely, so writing one can create a
    /// map entry. A sample writes into a buffer that was charged when it was
    /// allocated, so it costs nothing further. Writing a coefficient that
    /// already exists is charged too: the alternative is measuring the map
    /// before and after every write, and the step limit already bounds how
    /// many of these a program can run.
    fn charge_coefficient(&mut self, pos: usize, reg: Register, bytes: u64) -> Result<(), Error> {
        if matches!(self.reg(reg), RegVal::Model(_)) {
            self.charge(pos, bytes)?;
        }
        Ok(())
    }

    /// Charge for one copy of `bytes` worth of register value crossing the
    /// host boundary. See [`regval_bytes`] for the measurement.
    ///
    /// `OUTPUT` and `INPUT` both duplicate a whole register: `OUTPUT` clones
    /// it into an output slot the host keeps after the run, `INPUT` clones a
    /// calldata entry into a register. Neither had a charge site, so the only
    /// guards were a slot bound and an `Unset` check -- and `run_trace` does
    /// not clear `outputs`, which makes those copies live memory no other
    /// charge site ever sees. At the 1 GiB default and the CLI's 16 slots,
    /// `PUSH 134217728 / BSMX r0` spends the budget exactly and thirty-five
    /// further instructions then reach roughly 17 GiB resident with
    /// `memory_used()` still reading 1073741824.
    ///
    /// The copy is charged rather than the overwritten slot refunded, which
    /// keeps the budget cumulative: charges are never given back, so an
    /// output-and-overwrite loop cannot spend more than the budget in total.
    fn charge_clone(&mut self, pos: usize, bytes: u64) -> Result<(), Error> {
        self.charge(pos, bytes)
    }

    /// Charge the worst-case cost of an equality expansion over `n` terms.
    /// See [`equality_expansion_bytes`].
    fn charge_equality_expansion(&mut self, pos: usize, n: usize) -> Result<(), Error> {
        let n = u64::try_from(n).unwrap_or(u64::MAX);
        self.charge(pos, equality_expansion_bytes(n))
    }

    fn reg(&self, r: Register) -> &RegVal {
        self.regs.get(usize::from(r.slot())).unwrap_or_else(|| {
            unreachable!("register slot {} always valid in a 256-slot file", r.slot())
        })
    }

    fn reg_mut(&mut self, r: Register) -> &mut RegVal {
        self.regs.get_mut(usize::from(r.slot())).unwrap_or_else(|| {
            unreachable!("register slot {} always valid in a 256-slot file", r.slot())
        })
    }

    /// Raise unless `r` holds a vec of either kind.
    ///
    /// Separated from the arms that consume the vec so that a caller can
    /// discriminate the register kind without also borrowing its contents.
    ///
    /// An unset register raises [`Error::UnsetRegister`] rather than
    /// [`Error::RegisterType`]. The two are distinct fault identities --
    /// `UnsetRegister` is what `xqvm_py`'s `RegisterNotFound` maps onto and
    /// `RegisterType` is what its `TypeMismatch` maps onto -- so folding
    /// "never written" into the type arm makes an unset register a
    /// `TypeMismatch` here and an `UnsetRegister` there. `LOAD` and `OUTPUT`
    /// already discriminate the two; this is the helper that did not.
    fn require_vec(&self, pos: usize, r: Register) -> Result<(), Error> {
        match self.reg(r) {
            RegVal::VecInt(_) | RegVal::VecXqmx(_) => Ok(()),
            RegVal::Unset => Err(Error::UnsetRegister { pos, reg: r.slot() }),
            other => Err(Error::RegisterType {
                reg: r.slot(),
                expected: "vec",
                got: other.kind().kind_name(),
            }),
        }
    }

    /// Raise unless `r` holds a `vec<int>` specifically.
    ///
    /// The narrower counterpart to [`Vm::require_vec`], for the opcodes whose
    /// element type is fixed rather than either vec kind. It discriminates
    /// `Unset` from a wrong value kind for the same reason, and it borrows
    /// immutably, so a caller can validate a register it is about to take a
    /// mutable borrow of and validate a second register in between.
    fn require_vec_int(&self, pos: usize, r: Register) -> Result<(), Error> {
        match self.reg(r) {
            RegVal::VecInt(_) => Ok(()),
            RegVal::Unset => Err(Error::UnsetRegister { pos, reg: r.slot() }),
            other => Err(Error::RegisterType {
                reg: r.slot(),
                expected: "vec<int>",
                got: other.kind().kind_name(),
            }),
        }
    }

    // -- Control flow --

    #[expect(
        clippy::unused_self,
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires &mut self and Result<StepResult, Error> for all exec methods"
    )]
    fn exec_nop(&mut self, _pos: usize) -> Result<StepResult, Error> {
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::unused_self,
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires &mut self and Result<StepResult, Error> for all exec methods"
    )]
    fn exec_target(&mut self, _pos: usize) -> Result<StepResult, Error> {
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::unused_self,
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires &mut self and Result<StepResult, Error> for all exec methods"
    )]
    fn exec_jump1(&mut self, _pos: usize, label: u8) -> Result<StepResult, Error> {
        Ok(StepResult::Jump(u16::from(label)))
    }

    #[expect(
        clippy::unused_self,
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires &mut self and Result<StepResult, Error> for all exec methods"
    )]
    fn exec_jump2(&mut self, _pos: usize, label: u16) -> Result<StepResult, Error> {
        Ok(StepResult::Jump(label))
    }

    fn exec_jump_i1(&mut self, pos: usize, label: u8) -> Result<StepResult, Error> {
        let cond = self.pop(pos)?;
        if cond != 0 {
            Ok(StepResult::Jump(u16::from(label)))
        } else {
            Ok(StepResult::Continue)
        }
    }

    fn exec_jump_i2(&mut self, pos: usize, label: u16) -> Result<StepResult, Error> {
        let cond = self.pop(pos)?;
        if cond != 0 {
            Ok(StepResult::Jump(label))
        } else {
            Ok(StepResult::Continue)
        }
    }

    fn exec_next(&mut self, pos: usize) -> Result<StepResult, Error> {
        // Extract the values we need before mutating the loop stack.
        let (should_loop, body_start) = {
            let frame = self
                .loop_stack
                .last_mut()
                .ok_or(Error::NoActiveLoop { pos })?;
            match &mut frame.kind {
                LoopKind::Range { current, end } => {
                    // Checked, not bare: `SPEC.md`'s overflow rule covers loop
                    // control. `RANGE` now range-checks `start + count`, so
                    // `current` can no longer reach `i64::MAX` with an
                    // iteration left to run and this cannot fire from
                    // bytecode -- it is the second half of the same guard,
                    // kept so the invariant is enforced where it is relied on
                    // rather than only where it is established.
                    *current = current
                        .checked_add(1)
                        .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
                    let looping = *current < *end;
                    (looping, frame.body_start)
                }
                #[expect(
                    clippy::arithmetic_side_effects,
                    reason = "`index` walks `elements` and the frame is popped once it reaches the length, so the ITER cursor is bounded by a live Vec length"
                )]
                LoopKind::Iter {
                    elements, index, ..
                } => {
                    *index += 1;
                    let looping = *index < elements.len();
                    (looping, frame.body_start)
                }
            }
        };

        if should_loop {
            Ok(StepResult::Seek(body_start))
        } else {
            let _ = self.loop_stack.pop();
            Ok(StepResult::Continue)
        }
    }

    fn exec_lidx(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        // Per `spec/xqvm/SPEC.md` (`LIDX`), copy the current loop index into `reg`.
        // For `RANGE` loops the values *are* indices, so `LIDX` and `LVAL`
        // produce the same result. For `ITER` loops `LIDX` reports the
        // *original* vec position (`start_offset + index`), so loop bodies
        // can reach back into the source vec by absolute index even after
        // `ITER` slicing.
        let frame = self.loop_stack.last().ok_or(Error::NoActiveLoop { pos })?;
        let value = match &frame.kind {
            LoopKind::Range { current, .. } => *current,
            LoopKind::Iter {
                start_offset,
                index,
                ..
            } => i64::try_from(start_offset.saturating_add(*index)).unwrap_or(i64::MAX),
        };
        *self
            .regs
            .get_mut(usize::from(reg.slot()))
            .unwrap_or_else(|| unreachable!("register slot always valid")) = RegVal::Int(value);
        Ok(StepResult::Continue)
    }

    fn exec_l_val(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let frame = self.loop_stack.last().ok_or(Error::NoActiveLoop { pos })?;
        match &frame.kind {
            LoopKind::Range { current, .. } => {
                *self
                    .regs
                    .get_mut(usize::from(reg.slot()))
                    .unwrap_or_else(|| unreachable!("register slot always valid")) =
                    RegVal::Int(*current);
            }
            LoopKind::Iter {
                elements, index, ..
            } => {
                let idx = *index;
                let val = match elements {
                    IterElements::Int(v) => {
                        RegVal::Int(*v.get(idx).ok_or(Error::IndexOutOfBounds {
                            pos,
                            index: i64::try_from(idx).unwrap_or(i64::MAX),
                            len: v.len(),
                        })?)
                    }
                    IterElements::Xqmx(v) => RegVal::Model(
                        v.get(idx)
                            .ok_or(Error::IndexOutOfBounds {
                                pos,
                                index: i64::try_from(idx).unwrap_or(i64::MAX),
                                len: v.len(),
                            })?
                            .clone(),
                    ),
                };
                *self
                    .regs
                    .get_mut(usize::from(reg.slot()))
                    .unwrap_or_else(|| unreachable!("register slot always valid")) = val;
            }
        }
        Ok(StepResult::Continue)
    }

    fn exec_range(&mut self, pos: usize) -> Result<StepResult, Error> {
        let count = self.pop(pos)?;
        let start = self.pop(pos)?;
        if count <= 0 {
            return Ok(StepResult::SkipLoop);
        }
        // The exclusive bound is checked, not wrapped. `SPEC.md` ranges its
        // overflow rule over every i64 operation the VM performs on a
        // program's behalf, and loop control is not carved out of it. Wrapping
        // made `PUSH 2^63-2 / PUSH 3 / RANGE / LIDX r0 / NEXT / HALT` run one
        // iteration here and three on xqvm_py, with different register
        // contents and different step counts -- and step count is metering on
        // a chain that prices per step.
        let end = start
            .checked_add(count)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        Ok(StepResult::StartLoop {
            kind: LoopKind::Range {
                current: start,
                end,
            },
        })
    }

    fn exec_iter(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        // Per `spec/xqvm/SPEC.md` (`ITER`): pop `end_idx`, then `start_idx`, read
        // the source vec from `reg`, and copy `vec[start_idx..end_idx]` into
        // the loop frame. The slice is duplicated so that mutations to the
        // source vec inside the loop body do not affect what `LVAL` sees.
        // `LIDX` later reports `start_offset + index` as the absolute vec
        // position. Errors:
        //
        //   * `RegisterType`        -- `reg` does not hold a vec
        //   * `IndexOutOfBounds`    -- `start_idx` or `end_idx` is negative
        //                              or greater than `vec.len()`
        //
        // If `start_idx >= end_idx` the slice is empty and the body is
        // skipped, per the empty-loop-skip clause in `spec/xqvm/ISA.md`. An
        // earlier comment here justified do-while semantics as "mirroring the
        // existing RANGE behaviour", but RANGE skips on `count <= 0`, so that
        // reading was mistaken and the two openers had drifted apart.
        let end = self.pop(pos)?;
        let start = self.pop(pos)?;
        // The register kind is discriminated before the skip, not after it:
        // `ITER` carries a `read` effect on `reg` that the verifier's static
        // type system depends on, and skipping the loop without ever
        // resolving the register discards it. `spec/xqvm/ISA.md`'s `ITER` row
        // orders the steps register-read-before-skip and its error clause has
        // no emptiness carve-out. Returning `SkipLoop` first made
        // `INPUT r0 / PUSH 1 / PUSH 1 / ITER r0 / NEXT / ...` complete and
        // write outputs where 0.3.x aborted with a register-type fault, on a
        // program the verifier admits because `INPUT` writes `RegType::Any`.
        self.require_vec(pos, reg)?;
        if start >= end {
            return Ok(StepResult::SkipLoop);
        }
        // The copy is charged: a loop frame is only popped by `NEXT`, so a
        // back-edge that re-enters an `ITER` without reaching its `NEXT` piles
        // up one copy of the slice per execution.
        match self.reg(reg) {
            RegVal::VecInt(v) => {
                let len = v.len();
                let (start_offset, range) = resolve_iter_slice(pos, start, end, len)?;
                self.charge_variables(pos, range.len())?;
                let v = self
                    .reg(reg)
                    .as_vec_int()
                    .unwrap_or_else(|_| unreachable!("register still holds the vec just matched"));
                let copy = v
                    .get(range)
                    .unwrap_or_else(|| unreachable!("resolve_iter_slice already validated"))
                    .to_vec();
                Ok(StepResult::StartLoop {
                    kind: LoopKind::Iter {
                        elements: IterElements::Int(copy),
                        start_offset,
                        index: 0,
                    },
                })
            }
            RegVal::VecXqmx(v) => {
                let len = v.len();
                let (start_offset, range) = resolve_iter_slice(pos, start, end, len)?;
                // Cloning a model clones its coefficient maps, so charge for
                // what each one actually holds rather than per element.
                // `model_bytes` is the schedule's single measure of one model
                // copy: the same number `OUTPUT` and `INPUT` charge, so a
                // model does not get cheaper by being copied through a loop
                // header instead of a register.
                let cost = v
                    .get(range.clone())
                    .unwrap_or_else(|| unreachable!("resolve_iter_slice already validated"))
                    .iter()
                    .fold(0u64, |acc, m| acc.saturating_add(model_bytes(m)));
                self.charge(pos, cost)?;
                let RegVal::VecXqmx(v) = self.reg(reg) else {
                    unreachable!("register still holds the vec just matched")
                };
                let copy = v
                    .get(range)
                    .unwrap_or_else(|| unreachable!("resolve_iter_slice already validated"))
                    .to_vec();
                Ok(StepResult::StartLoop {
                    kind: LoopKind::Iter {
                        elements: IterElements::Xqmx(copy),
                        start_offset,
                        index: 0,
                    },
                })
            }
            other => Err(Error::RegisterType {
                reg: reg.slot(),
                expected: "vec",
                got: other.kind().kind_name(),
            }),
        }
    }

    #[expect(
        clippy::unused_self,
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires &mut self and Result<StepResult, Error> for all exec methods"
    )]
    fn exec_halt(&mut self, _pos: usize) -> Result<StepResult, Error> {
        Ok(StepResult::Halt)
    }

    // -- Stack & register I/O --

    fn exec_push1(&mut self, pos: usize, val: [u8; 1]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_push2(&mut self, pos: usize, val: [u8; 2]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_push3(&mut self, pos: usize, val: [u8; 3]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_push4(&mut self, pos: usize, val: [u8; 4]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_push5(&mut self, pos: usize, val: [u8; 5]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_push6(&mut self, pos: usize, val: [u8; 6]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_push7(&mut self, pos: usize, val: [u8; 7]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_push8(&mut self, pos: usize, val: [u8; 8]) -> Result<StepResult, Error> {
        self.push_stack(sign_extend_be(&val), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_pop(&mut self, pos: usize) -> Result<StepResult, Error> {
        let _ = self.pop(pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_copy(&mut self, pos: usize) -> Result<StepResult, Error> {
        let top = *self.stack.last().ok_or(Error::StackUnderflow { pos })?;
        self.push_stack(top, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_swap(&mut self, pos: usize) -> Result<StepResult, Error> {
        let len = self.stack.len();
        if len < 2 {
            return Err(Error::StackUnderflow { pos });
        }
        #[expect(
            clippy::arithmetic_side_effects,
            reason = "`len < 2` returned StackUnderflow above"
        )]
        self.stack.swap(len - 1, len - 2);
        Ok(StepResult::Continue)
    }

    fn exec_load(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        if matches!(self.reg(reg), RegVal::Unset) {
            return Err(Error::UnsetRegister {
                pos,
                reg: reg.slot(),
            });
        }
        let v = self.reg(reg).as_int().map_err(|e| Error::RegisterType {
            reg: reg.slot(),
            expected: "int",
            got: e.actual.kind_name(),
        })?;
        self.push_stack(v, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_stow(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let v = self.pop(pos)?;
        *self.reg_mut(reg) = RegVal::Int(v);
        Ok(StepResult::Continue)
    }

    fn exec_input(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let idx = self.pop(pos)?;
        let usize_idx = usize::try_from(idx)
            .ok()
            .filter(|&i| i < self.calldata.len())
            .ok_or(Error::CallDataIndex {
                index: idx,
                len: self.calldata.len(),
            })?;
        // Charge before the clone: a calldata entry the host supplied is
        // duplicated into the register file, and the copy is as real as an
        // allocation the program made itself.
        let bytes = regval_bytes(
            self.calldata
                .get(usize_idx)
                .unwrap_or_else(|| unreachable!("usize_idx < calldata.len() checked above")),
        );
        self.charge_clone(pos, bytes)?;
        let val = self
            .calldata
            .get(usize_idx)
            .unwrap_or_else(|| unreachable!("usize_idx < calldata.len() checked above"))
            .clone();
        *self.reg_mut(reg) = val;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires Result<StepResult, Error> for all exec methods"
    )]
    fn exec_drop(&mut self, _pos: usize, reg: Register) -> Result<StepResult, Error> {
        *self.reg_mut(reg) = RegVal::Unset;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires Result<StepResult, Error> for all exec methods"
    )]
    fn exec_sclr(&mut self, _pos: usize) -> Result<StepResult, Error> {
        self.stack.clear();
        Ok(StepResult::Continue)
    }

    fn exec_output(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let idx = self.pop(pos)?;
        // Charge before the clone, and before the two validations below.
        // The output slots survive the run, so this copy is live memory the
        // host holds on to; without a charge site a program can spend the
        // whole budget once and then hand the host an unbounded multiple of
        // it, one OUTPUT at a time.
        //
        // The charge precedes the slot bound and the unset check because
        // spec/xqvm/SPEC.md's error-precedence rule puts operand pops first,
        // then the charge, then type and range validation, and says in
        // as many words that an instruction may raise `MemoryLimitExceeded`
        // for work it would never have done "because ... the index it was
        // given is out of range". Validating first made a near-exhausted
        // budget plus an out-of-range slot raise `OutputIndex` here and
        // `MemoryLimitExceeded` on xqvm_py. `regval_bytes` of an unset
        // register is 0, so an unset register still charges nothing and
        // still faults.
        let bytes = regval_bytes(self.reg(reg));
        self.charge_clone(pos, bytes)?;
        if matches!(self.reg(reg), RegVal::Unset) {
            return Err(Error::UnsetRegister {
                pos,
                reg: reg.slot(),
            });
        }
        let usize_idx = usize::try_from(idx)
            .ok()
            .filter(|&i| i < self.outputs.len())
            .ok_or(Error::OutputIndex {
                index: idx,
                len: self.outputs.len(),
            })?;
        let val = self.reg(reg).clone();
        *self
            .outputs
            .get_mut(usize_idx)
            .unwrap_or_else(|| unreachable!("usize_idx < outputs.len() checked above")) = val;
        Ok(StepResult::Continue)
    }

    // -- Arithmetic --

    fn exec_add(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        let sum = a
            .checked_add(b)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(sum, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_sub(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        let difference = a
            .checked_sub(b)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(difference, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_mul(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        let product = a
            .checked_mul(b)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(product, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_div(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        if b == 0 {
            return Err(Error::DivisionByZero { pos });
        }
        // Floor division (rounds toward −∞), matching Python `a // b`.
        // Truncate first, then subtract 1 when the remainder is nonzero and
        // the operands have opposite signs. `i64::MIN / -1` is the one pair
        // whose quotient leaves the range; it raises rather than wrapping.
        let q = a
            .checked_div(b)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        let r = a
            .checked_rem(b)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        let floored = if r != 0 && (r ^ b) < 0 {
            q.checked_sub(1)
                .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?
        } else {
            q
        };
        self.push_stack(floored, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_modulo(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        if b == 0 {
            return Err(Error::DivisionByZero { pos });
        }
        // Divisor-sign modulo, matching Python `a % b`.
        // Adjust the C-style truncating remainder to have the same sign as `b`.
        //
        // `i64::MIN % -1` is the one pair `checked_rem` rejects, because the
        // division it performs internally overflows. The remainder itself is
        // 0, which is representable, so the normative rule -- raise only when
        // the *result* leaves the range -- says this yields 0 rather than
        // faulting. DIV differs: its quotient 2^63 genuinely has no i64.
        let r = a.checked_rem(b).unwrap_or(0);
        let m = if r != 0 && (r ^ b) < 0 {
            r.checked_add(b)
                .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?
        } else {
            r
        };
        self.push_stack(m, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_neg(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        let negated = a
            .checked_neg()
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(negated, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_sqr(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        let square = a
            .checked_mul(a)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(square, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_abs(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        let magnitude = a
            .checked_abs()
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(magnitude, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_min(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(a.min(b), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_max(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(a.max(b), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_inc(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        let incremented = a
            .checked_add(1)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(incremented, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_dec(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        let decremented = a
            .checked_sub(1)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(decremented, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_bit_len(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        let result = if a > 0 {
            #[expect(
                clippy::arithmetic_side_effects,
                reason = "`leading_zeros()` returns at most `i64::BITS`, so the bit-length subtraction stays in 0..=64"
            )]
            i64::from(i64::BITS - a.leading_zeros())
        } else {
            0
        };
        self.push_stack(result, pos)?;
        Ok(StepResult::Continue)
    }

    // -- Comparison --

    fn exec_eq(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a == b), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_lt(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a < b), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_gt(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a > b), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_lte(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a <= b), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_gte(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a >= b), pos)?;
        Ok(StepResult::Continue)
    }

    // -- Logical boolean --

    fn exec_not(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a == 0), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_and(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a != 0 && b != 0), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_or(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from(a != 0 || b != 0), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_xor(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(i64::from((a != 0) ^ (b != 0)), pos)?;
        Ok(StepResult::Continue)
    }

    // -- Bitwise --

    fn exec_b_and(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(a & b, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_b_or(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(a | b, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_b_xor(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        self.push_stack(a ^ b, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_b_not(&mut self, pos: usize) -> Result<StepResult, Error> {
        let a = self.pop(pos)?;
        self.push_stack(!a, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_shl(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        if !(0..64).contains(&b) {
            return Err(Error::InvalidShift { pos, amount: b });
        }
        // A shift that discards significant bits leaves the i64 range, so it
        // raises like any other overflowing operation. Shifting back recovers
        // the operand exactly when nothing was lost.
        //
        // Allow-list entry, outside `arithmetic_side_effects` because the wrap
        // is a method call: `spec/xqvm/SPEC.md`'s overflow rule tests the
        // result rather than the intermediate hardware operation, which is
        // exactly what wrapping and then shifting back does. `b` is in 0..64
        // by the guard above, so the `unwrap_or` is unreachable.
        let shifted = a.wrapping_shl(u32::try_from(b).unwrap_or(u32::MAX));
        if shifted >> b != a {
            return Err(Error::ArithmeticOverflow { pos: Some(pos) });
        }
        self.push_stack(shifted, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_shr(&mut self, pos: usize) -> Result<StepResult, Error> {
        let b = self.pop(pos)?;
        let a = self.pop(pos)?;
        if !(0..64).contains(&b) {
            return Err(Error::InvalidShift { pos, amount: b });
        }
        // Arithmetic (sign-preserving) right shift, matching xq-py: the sign
        // bit is replicated, so negative values stay negative and `i64::MIN >> 1`
        // halves the magnitude rather than overflowing.
        self.push_stack(a >> b, pos)?;
        Ok(StepResult::Continue)
    }

    // -- Allocators --
    //
    // Every allocator takes its size from the value stack, where any `i64` is
    // reachable in a single `PUSH`. Each one routes that value through
    // `allocation_size`, which validates the sign, charges the allocation
    // budget off the `i64`, and only then narrows to `usize`; a size that is
    // not an allocation, or a request that does not fit, leaves the target
    // register alone.

    fn exec_bqmx(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let size = self.pop(pos)?;
        let size = self.allocation_size(pos, size)?;
        *self.reg_mut(reg) = RegVal::Model(XqmxModel::new(Domain::Binary, size));
        Ok(StepResult::Continue)
    }

    fn exec_sqmx(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let size = self.pop(pos)?;
        let size = self.allocation_size(pos, size)?;
        *self.reg_mut(reg) = RegVal::Model(XqmxModel::new(Domain::Spin, size));
        Ok(StepResult::Continue)
    }

    fn exec_xqmx(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let k = self.pop(pos)?;
        let size = self.pop(pos)?;
        if k < 2 {
            return Err(Error::InvalidDiscreteK { pos, k });
        }
        let size = self.allocation_size(pos, size)?;
        *self.reg_mut(reg) = RegVal::Model(XqmxModel::new(Domain::Discrete(k), size));
        Ok(StepResult::Continue)
    }

    fn exec_bsmx(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let size = self.pop(pos)?;
        let size = self.allocation_size(pos, size)?;
        *self.reg_mut(reg) = RegVal::Sample(XqmxSample::new(Domain::Binary, vec![0; size]));
        Ok(StepResult::Continue)
    }

    fn exec_ssmx(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let size = self.pop(pos)?;
        let size = self.allocation_size(pos, size)?;
        *self.reg_mut(reg) = RegVal::Sample(XqmxSample::new(Domain::Spin, vec![-1; size]));
        Ok(StepResult::Continue)
    }

    fn exec_xsmx(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let k = self.pop(pos)?;
        let size = self.pop(pos)?;
        if k < 2 {
            return Err(Error::InvalidDiscreteK { pos, k });
        }
        let size = self.allocation_size(pos, size)?;
        *self.reg_mut(reg) = RegVal::Sample(XqmxSample::new(Domain::Discrete(k), vec![0; size]));
        Ok(StepResult::Continue)
    }

    // -- Vec allocators --
    //
    // `VEC`, `VECI` and `VECX` install an empty `Vec`, which allocates nothing.
    // Their storage is charged as it is created, by `VECPUSH` and `SLACK`.

    #[expect(
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires Result<StepResult, Error> for all exec methods"
    )]
    fn exec_vec(&mut self, _pos: usize, reg: Register) -> Result<StepResult, Error> {
        // Untyped vec -- becomes VecInt (integer vec is the default untyped container).
        *self.reg_mut(reg) = RegVal::VecInt(Vec::new());
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires Result<StepResult, Error> for all exec methods"
    )]
    fn exec_vec_i(&mut self, _pos: usize, reg: Register) -> Result<StepResult, Error> {
        *self.reg_mut(reg) = RegVal::VecInt(Vec::new());
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::unnecessary_wraps,
        reason = "dispatch macro requires Result<StepResult, Error> for all exec methods"
    )]
    fn exec_vec_x(&mut self, _pos: usize, reg: Register) -> Result<StepResult, Error> {
        *self.reg_mut(reg) = RegVal::VecXqmx(Vec::new());
        Ok(StepResult::Continue)
    }

    // -- Vector access --

    fn exec_vec_push(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let v = self.pop(pos)?;
        self.charge(pos, VEC_ELEMENT_BYTES)?;
        let vec = self
            .reg_mut(reg)
            .as_vec_int_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?;
        vec.push(v);
        Ok(StepResult::Continue)
    }

    fn exec_vec_get(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let idx = self.pop(pos)?;
        let vec = self
            .reg(reg)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?;
        let usize_idx = usize::try_from(idx).ok().filter(|&i| i < vec.len()).ok_or(
            Error::IndexOutOfBounds {
                pos,
                index: idx,
                len: vec.len(),
            },
        )?;
        self.push_stack(
            *vec.get(usize_idx)
                .unwrap_or_else(|| unreachable!("usize_idx < vec.len() checked above")),
            pos,
        )?;
        Ok(StepResult::Continue)
    }

    fn exec_vec_set(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let val = self.pop(pos)?;
        let idx = self.pop(pos)?;
        let vec = self
            .reg_mut(reg)
            .as_vec_int_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?;
        let usize_idx = usize::try_from(idx).ok().filter(|&i| i < vec.len()).ok_or(
            Error::IndexOutOfBounds {
                pos,
                index: idx,
                len: vec.len(),
            },
        )?;
        *vec.get_mut(usize_idx)
            .unwrap_or_else(|| unreachable!("usize_idx < vec.len() checked above")) = val;
        Ok(StepResult::Continue)
    }

    fn exec_vec_len(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let len = match self.reg(reg) {
            RegVal::VecInt(v) => v.len(),
            RegVal::VecXqmx(v) => v.len(),
            other => {
                return Err(Error::RegisterType {
                    reg: reg.slot(),
                    expected: "vec",
                    got: other.kind().kind_name(),
                });
            }
        };
        // Vec lengths are bounded by isize::MAX ≤ i64::MAX on all supported platforms;
        // try_from never fails in practice.
        self.push_stack(i64::try_from(len).unwrap_or(i64::MAX), pos)?;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "NOT COVERED BY THE SPEC: `spec/xqvm/SPEC.md`'s overflow rule reaches the index sequence SLACK appends (which is checked), but nothing normative permits the `power = power.wrapping_mul(2)` that drives these two loops; recorded here pending normative text. The behaviour is correct and verified equivalent to xqvm_py across the whole domain: `power > 0` consumes the wrap to i64::MIN, so Rust runs `i64::BITS - capacity.leading_zeros()` iterations where Python runs `capacity.bit_length()`, equal for every positive i64. `capacity.leading_zeros() <= i64::BITS` bounds the charge, and `i` is bounded by the same 63 iterations"
    )]
    fn exec_slack(
        &mut self,
        pos: usize,
        indices: Register,
        coeffs: Register,
    ) -> Result<StepResult, Error> {
        let capacity = self.pop(pos)?;
        let start_index = self.pop(pos)?;
        // Both registers are discriminated before the empty-capacity skip,
        // and before either is mutated. `SLACK` carries a `mutate` effect on
        // both, and the verifier's static type system depends on it:
        // `xqvm/src/verifier/reg_type.rs` requires `vec<int>` of each, and
        // `RegType::Any` -- what `INPUT` writes -- satisfies that, so
        // `INPUT r0 / INPUT r1 / PUSH 0 / PUSH 0 / SLACK r0 r1` is
        // verifier-clean and reaches here with two ints. Returning on
        // `capacity <= 0` before touching either register discarded the
        // effect and halted Ok where xqvm_py faulted. Same rule, and same
        // reason, as `ITER`'s register-read-before-skip.
        //
        // Resolving both up front also fixes the order within the non-empty
        // path: the second register used to be discriminated only after the
        // first loop had already appended to the first, so a bad `coeffs`
        // left `indices` mutated by a faulting instruction.
        self.require_vec_int(pos, indices)?;
        self.require_vec_int(pos, coeffs)?;
        if capacity <= 0 {
            return Ok(StepResult::Continue);
        }
        // Both loops below run once per set bit position in `capacity`, so at
        // most 63 iterations each; charge for the entries they will append.
        let entries = u64::from(i64::BITS - capacity.leading_zeros());
        self.charge(pos, entries.saturating_mul(2 * VEC_ELEMENT_BYTES))?;
        // Append index entries to the indices register.
        {
            let vec = self
                .reg_mut(indices)
                .as_vec_int_mut()
                .unwrap_or_else(|_| unreachable!("require_vec_int already validated indices"));
            let mut power = 1i64;
            let mut i = 0i64;
            // `power > 0` guards against wrapping_mul overflow: once `power`
            // reaches 2^62 the next doubling wraps to i64::MIN, which would
            // otherwise be `<= capacity` and loop forever.
            while power > 0 && power <= capacity {
                // Checked: `start_index` comes straight off the value stack,
                // so `start_index + i` leaves the range for any start within
                // 63 of `i64::MAX`. `VECI r0 / VECI r1 / PUSH 2^63-1 /
                // PUSH 3 / SLACK r0, r1` charged 64 bytes and passed at any
                // default while iteration 2 panicked under `ci-test`, wrapped
                // under `release`, and stored 2^63 on xqvm_py.
                vec.push(
                    start_index
                        .checked_add(i)
                        .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?,
                );
                power = power.wrapping_mul(2);
                i += 1;
            }
        }
        // Append power-of-two coefficient entries to the coeffs register.
        {
            let vec = self
                .reg_mut(coeffs)
                .as_vec_int_mut()
                .unwrap_or_else(|_| unreachable!("require_vec_int already validated coeffs"));
            let mut power = 1i64;
            while power > 0 && power <= capacity {
                vec.push(power);
                power = power.wrapping_mul(2);
            }
        }
        Ok(StepResult::Continue)
    }

    // -- Index math --

    fn exec_idx_grid(&mut self, pos: usize) -> Result<StepResult, Error> {
        let cols = self.pop(pos)?;
        let col = self.pop(pos)?;
        let row = self.pop(pos)?;
        let index = row
            .checked_mul(cols)
            .and_then(|offset| offset.checked_add(col))
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(index, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_idx_triu(&mut self, pos: usize) -> Result<StepResult, Error> {
        let j = self.pop(pos)?;
        let i = self.pop(pos)?;
        // The pair is unordered: `spec/xqvm/ISA.md` requires the operands to
        // be swapped when i > j, so (i, j) and (j, i) address the same cell.
        let (i, j) = if i > j { (j, i) } else { (i, j) };
        // Upper-triangular index for (i, j) with i <= j:
        // index = j*(j-1)/2 + i
        //
        // j*(j-1) is a product of consecutive integers, so it is non-negative
        // and even for every operand, negative ones included. Truncating and
        // flooring division therefore agree here and cannot diverge.
        let idx = j
            .checked_sub(1)
            .and_then(|jm1| j.checked_mul(jm1))
            .and_then(|product| product.checked_div(2))
            .and_then(|half| half.checked_add(i))
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        self.push_stack(idx, pos)?;
        Ok(StepResult::Continue)
    }

    // -- XQMX coefficient access --

    fn exec_get_line(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let i = self.pop(pos)?;
        let grid = self
            .reg(reg)
            .as_xqmx_grid()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        let size = grid.size();
        let usize_i = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: size,
        })?;
        if usize_i >= size {
            return Err(Error::IndexOutOfBounds {
                pos,
                index: i,
                len: size,
            });
        }
        self.push_stack(grid.linear(usize_i), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_set_line(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let val = self.pop(pos)?;
        let i = self.pop(pos)?;
        self.charge_coefficient(pos, reg, LINEAR_ENTRY_BYTES)?;
        let mut grid = self
            .reg_mut(reg)
            .as_xqmx_grid_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        let size = grid.size();
        let usize_i = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: size,
        })?;
        if usize_i >= size {
            return Err(Error::IndexOutOfBounds {
                pos,
                index: i,
                len: size,
            });
        }
        grid.linear_set(usize_i, val);
        Ok(StepResult::Continue)
    }

    fn exec_add_line(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let delta = self.pop(pos)?;
        let i = self.pop(pos)?;
        self.charge_coefficient(pos, reg, LINEAR_ENTRY_BYTES)?;
        let mut grid = self
            .reg_mut(reg)
            .as_xqmx_grid_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        let size = grid.size();
        let usize_i = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: size,
        })?;
        if usize_i >= size {
            return Err(Error::IndexOutOfBounds {
                pos,
                index: i,
                len: size,
            });
        }
        grid.linear_add(usize_i, delta).map_err(at_pos(pos))?;
        Ok(StepResult::Continue)
    }

    fn exec_get_quad(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let j = self.pop(pos)?;
        let i = self.pop(pos)?;
        let m = self.reg(reg).as_model().map_err(|e| Error::RegisterType {
            reg: reg.slot(),
            expected: "model",
            got: e.actual.kind_name(),
        })?;
        let usize_i = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: m.size,
        })?;
        let usize_j = usize::try_from(j).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: j,
            len: m.size,
        })?;
        self.push_stack(m.get_quad(usize_i, usize_j), pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_set_quad(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let val = self.pop(pos)?;
        let j = self.pop(pos)?;
        let i = self.pop(pos)?;
        self.charge_coefficient(pos, reg, QUAD_ENTRY_BYTES)?;
        let m = self
            .reg_mut(reg)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        let usize_i = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: m.size,
        })?;
        let usize_j = usize::try_from(j).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: j,
            len: m.size,
        })?;
        m.set_quad(usize_i, usize_j, val);
        Ok(StepResult::Continue)
    }

    fn exec_add_quad(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let delta = self.pop(pos)?;
        let j = self.pop(pos)?;
        let i = self.pop(pos)?;
        self.charge_coefficient(pos, reg, QUAD_ENTRY_BYTES)?;
        let m = self
            .reg_mut(reg)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        let usize_i = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: m.size,
        })?;
        let usize_j = usize::try_from(j).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: j,
            len: m.size,
        })?;
        m.add_quad(usize_i, usize_j, delta).map_err(at_pos(pos))?;
        Ok(StepResult::Continue)
    }

    // -- XQMX grid --

    fn exec_resize(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let cols = self.pop(pos)?;
        let rows = self.pop(pos)?;
        if rows <= 0 || cols <= 0 {
            return Err(Error::InvalidGridDimensions { pos, rows, cols });
        }
        let usize_rows =
            usize::try_from(rows).map_err(|_| Error::InvalidGridDimensions { pos, rows, cols })?;
        let usize_cols =
            usize::try_from(cols).map_err(|_| Error::InvalidGridDimensions { pos, rows, cols })?;
        let mut grid = self
            .reg_mut(reg)
            .as_xqmx_grid_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        // A grid is a reinterpretation of variables the program already
        // declared and already paid for at allocation, so it cannot describe
        // cells that do not exist. Without this bound the four read-only grid
        // opcodes scan an arbitrary extent for one metered step: a size-4
        // model resized to `1 x 2^62` makes a single ROWSUM run for
        // geological time while charging nothing at all, which a step budget
        // cannot see and a memory budget is never asked about.
        //
        // The rule is `rows * cols <= size`, not `==`: EQUALITY, ATLEAST,
        // ATLEASTW and REDUCE append slack and auxiliary variables past the
        // grid. They only ever grow `size` and nothing shrinks it, so a grid
        // that fits when RESIZE runs still fits afterwards.
        if usize_rows
            .checked_mul(usize_cols)
            .is_none_or(|cells| cells > grid.size())
        {
            return Err(Error::InvalidGridDimensions { pos, rows, cols });
        }
        grid.set_grid(usize_rows, usize_cols);
        Ok(StepResult::Continue)
    }

    /// Validate a grid-addressed opcode's row operand and return it as a
    /// `usize` line number. See [`Vm::grid_axis_index`] for the checks.
    fn grid_row_index(
        pos: usize,
        rows: usize,
        cols: usize,
        size: usize,
        index: i64,
    ) -> Result<usize, Error> {
        Self::grid_axis_index(pos, rows, cols, size, index, rows)
    }

    /// Validate a grid-addressed opcode's column operand and return it as a
    /// `usize` line number. See [`Vm::grid_axis_index`] for the checks.
    fn grid_col_index(
        pos: usize,
        rows: usize,
        cols: usize,
        size: usize,
        index: i64,
    ) -> Result<usize, Error> {
        Self::grid_axis_index(pos, rows, cols, size, index, cols)
    }

    /// Validate a grid-addressed opcode's operands and return `index` as a
    /// `usize` line number along axis `extent`.
    ///
    /// Call [`Vm::grid_row_index`] or [`Vm::grid_col_index`] rather than this
    /// directly. `extent` has to agree with the axis `index` names, and as a
    /// bare parameter it is a duplicate of `rows` or `cols` that the caller
    /// restates -- so passing `cols` where `rows` belongs is a mistake this
    /// signature cannot see and a square grid cannot detect. The two wrappers
    /// pick it, which makes the swap unrepresentable at the six call sites.
    ///
    /// Three things are required. The register must carry non-zero grid
    /// extents; their product must be addressable *and* within the register's
    /// declared `size`; and `index` must lie in `[0, extent)`. An ungridded or
    /// unaddressable grid raises `InvalidGridDimensions` -- the same identity
    /// ONEHOTR/ONEHOTC raise without a grid -- and an out-of-range index
    /// raises `IndexOutOfBounds`, so an absent row is an error rather than a
    /// silent sum of zeroes (`xqvm_py` has always raised here).
    ///
    /// The `rows * cols` arm is unreachable from bytecode once `RESIZE`
    /// enforces the identical `rows * cols <= size` rule, but it is *not*
    /// dead: a host can install a register directly through
    /// [`Vm::set_register`] or [`Vm::set_calldata`], and `xqffi` exposes both
    /// extents to Python unvalidated (QUI-1164 tracks the boundary itself and
    /// owns the decision on whether this arm eventually goes). It is what
    /// keeps the flat-index arithmetic below every caller addressing declared
    /// variables. For the four read-only handlers an unchecked `usize_row *
    /// cols` would otherwise panic under `ci-test` and wrap under `release`;
    /// for the two ONEHOT handlers, which write, an index past `size` would
    /// create coefficients on variables the model never declared, because
    /// `XqmxModel::add_linear` and `add_quad` grow a sparse map with no bound
    /// of their own.
    fn grid_axis_index(
        pos: usize,
        rows: usize,
        cols: usize,
        size: usize,
        index: i64,
        extent: usize,
    ) -> Result<usize, Error> {
        if rows == 0 || cols == 0 || rows.checked_mul(cols).is_none_or(|cells| cells > size) {
            return Err(Error::InvalidGridDimensions {
                pos,
                rows: i64::try_from(rows).unwrap_or(i64::MAX),
                cols: i64::try_from(cols).unwrap_or(i64::MAX),
            });
        }
        usize::try_from(index)
            .ok()
            .filter(|&i| i < extent)
            .ok_or(Error::IndexOutOfBounds {
                pos,
                index,
                len: extent,
            })
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`grid_row_index` has already proved `rows * cols` fits usize and is within `size`, and `usize_row < rows`, so `row_start + col` addresses a declared variable (`spec/xqvm/ISA.md`'s XQMX Grid precondition)"
    )]
    fn exec_row_find(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let value = self.pop(pos)?;
        let row = self.pop(pos)?;
        let grid = self
            .reg(reg)
            .as_xqmx_grid()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        let cols = grid.cols();
        let usize_row = Self::grid_row_index(pos, grid.rows(), cols, grid.size(), row)?;
        // grid_row_index guarantees rows*cols fits usize and is within size,
        // and that usize_row is in range, so row addressing cannot overflow.
        let row_start = usize_row * cols;
        // cols ≤ i64::MAX (validated via exec_resize); try_from never fails.
        let result = (0..cols)
            .find(|&col| grid.linear(row_start + col) == value)
            .map_or(-1, |c| i64::try_from(c).unwrap_or(-1));
        self.push_stack(result, pos)?;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`grid_col_index` has already proved `rows * cols` fits usize and is within `size`, and `usize_col < cols`, so `row * cols + usize_col` addresses a declared variable (`spec/xqvm/ISA.md`'s XQMX Grid precondition)"
    )]
    fn exec_col_find(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let value = self.pop(pos)?;
        let col = self.pop(pos)?;
        let grid = self
            .reg(reg)
            .as_xqmx_grid()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        let rows = grid.rows();
        let cols = grid.cols();
        let usize_col = Self::grid_col_index(pos, rows, cols, grid.size(), col)?;
        // rows ≤ i64::MAX (validated via exec_resize); try_from never fails.
        let result = (0..rows)
            .find(|&row| grid.linear(row * cols + usize_col) == value)
            .map_or(-1, |r| i64::try_from(r).unwrap_or(-1));
        self.push_stack(result, pos)?;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`grid_row_index` has already proved `rows * cols` fits usize and is within `size`, and `usize_row < rows`, so `row_start + c` addresses a declared variable (`spec/xqvm/ISA.md`'s XQMX Grid precondition); the i64 fold over the coefficients is checked"
    )]
    fn exec_row_sum(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let row = self.pop(pos)?;
        let grid = self
            .reg(reg)
            .as_xqmx_grid()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        let cols = grid.cols();
        let usize_row = Self::grid_row_index(pos, grid.rows(), cols, grid.size(), row)?;
        // grid_row_index guarantees rows*cols fits usize and is within size,
        // and that usize_row is in range, so row addressing cannot overflow.
        let row_start = usize_row * cols;
        // A reduction over coefficients is checked per partial sum
        // (spec/xqvm/SPEC.md overflow rule): wrapping here was observable.
        let sum = (0..cols).try_fold(0i64, |acc, c| {
            acc.checked_add(grid.linear(row_start + c))
                .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })
        })?;
        self.push_stack(sum, pos)?;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`grid_col_index` has already proved `rows * cols` fits usize and is within `size`, and `usize_col < cols`, so `r * cols + usize_col` addresses a declared variable (`spec/xqvm/ISA.md`'s XQMX Grid precondition); the i64 fold over the coefficients is checked"
    )]
    fn exec_col_sum(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let col = self.pop(pos)?;
        let grid = self
            .reg(reg)
            .as_xqmx_grid()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model|sample",
                got: e.actual.kind_name(),
            })?;
        let rows = grid.rows();
        let cols = grid.cols();
        let usize_col = Self::grid_col_index(pos, rows, cols, grid.size(), col)?;
        // Checked like ROWSUM: partial sums are normative, and
        // grid_col_index keeps the column addressing in range.
        let sum = (0..rows).try_fold(0i64, |acc, r| {
            acc.checked_add(grid.linear(r * cols + usize_col))
                .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })
        })?;
        self.push_stack(sum, pos)?;
        Ok(StepResult::Continue)
    }

    // -- Constraints --

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`grid_row_index` has already proved `rows * cols` fits usize and is within `m.size`, and `usize_row < m.rows`, so `row_start + c` and the pair addressing reach declared variables (`spec/xqvm/ISA.md`'s XQMX Grid precondition). `ci + 1` is bounded by `m.cols`, a live extent RESIZE keeps within `size`"
    )]
    fn exec_one_hot_r(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let penalty = self.pop(pos)?;
        let row = self.pop(pos)?;
        // The expansion writes one linear term per column and one quadratic
        // term per pair of columns, so a single `ONEHOTR` costs O(cols^2) map
        // entries -- and `RESIZE` takes `cols` straight off the value stack.
        // Charge before expanding. A register holding something other than a
        // model charges nothing and falls through to the type error below.
        let cols = match self.reg(reg) {
            RegVal::Model(m) => m.cols,
            _ => 0,
        };
        self.charge_equality_expansion(pos, cols)?;
        let m = self
            .reg_mut(reg)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        // Both halves of `spec/xqvm/ISA.md`'s grid precondition, on the same
        // helper the read-only grid opcodes use. A model with no grid has no
        // row to constrain, and a row outside `[0, rows)` names variables the
        // program never declared: writing either one leaves the constraint
        // absent from -- or misplaced in -- a model that then solves cleanly
        // and answers wrongly. `usize::try_from(row)` alone accepted any
        // positive row, and `usize_row * m.cols` was the multiply that turned
        // `row = 2^62` on a 1x4 grid into a one-hot constraint on row 0.
        let usize_row = Self::grid_row_index(pos, m.rows, m.cols, m.size, row)?;
        // grid_row_index guarantees rows*cols fits usize and is within size,
        // and that usize_row is in range, so row addressing cannot overflow
        // and every index below names a variable the model declared.
        let row_start = usize_row * m.cols;
        // H = penalty * (sum(x_i) - 1)^2
        // Linear: -penalty per variable in row
        // Quadratic: 2*penalty per pair in row
        let neg_penalty = checked(penalty.checked_neg(), pos)?;
        let two_penalty = checked(penalty.checked_mul(2), pos)?;
        for c in 0..m.cols {
            m.add_linear(row_start + c, neg_penalty)
                .map_err(at_pos(pos))?;
        }
        for ci in 0..m.cols {
            for cj in (ci + 1)..m.cols {
                m.add_quad(row_start + ci, row_start + cj, two_penalty)
                    .map_err(at_pos(pos))?;
            }
        }
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`grid_col_index` has already proved `rows * cols` fits usize and is within `m.size`, and `col_idx < m.cols`, so `ri * m.cols + col_idx` and the pair addressing reach declared variables (`spec/xqvm/ISA.md`'s XQMX Grid precondition). `ri + 1` is bounded by `m.rows`, a live extent RESIZE keeps within `size`"
    )]
    fn exec_one_hot_c(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let penalty = self.pop(pos)?;
        let col = self.pop(pos)?;
        // O(rows^2) map entries in one step; see `exec_one_hot_r`.
        let rows = match self.reg(reg) {
            RegVal::Model(m) => m.rows,
            _ => 0,
        };
        self.charge_equality_expansion(pos, rows)?;
        let m = self
            .reg_mut(reg)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        // The grid precondition, as in `exec_one_hot_r`. A column outside
        // `[0, cols)` cannot wrap the way a row can -- nothing multiplies by
        // it -- but `ri * m.cols + col_idx` still lands outside the addressed
        // column, aliasing another column's variables or variables past
        // `size`, so the same helper rejects it.
        let col_idx = Self::grid_col_index(pos, m.rows, m.cols, m.size, col)?;
        // H = penalty * (sum(x_{r,col}) - 1)^2 over all rows.
        // Linear: -penalty per variable in column.
        // Quadratic: 2*penalty per pair in column.
        let neg_penalty = checked(penalty.checked_neg(), pos)?;
        let two_penalty = checked(penalty.checked_mul(2), pos)?;
        for ri in 0..m.rows {
            m.add_linear(ri * m.cols + col_idx, neg_penalty)
                .map_err(at_pos(pos))?;
        }
        for ri in 0..m.rows {
            for rj in (ri + 1)..m.rows {
                m.add_quad(ri * m.cols + col_idx, rj * m.cols + col_idx, two_penalty)
                    .map_err(at_pos(pos))?;
            }
        }
        Ok(StepResult::Continue)
    }

    fn exec_exclude(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let penalty = self.pop(pos)?;
        let j = self.pop(pos)?;
        let i = self.pop(pos)?;
        self.charge(pos, QUAD_ENTRY_BYTES)?;
        let m = self
            .reg_mut(reg)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        // Penalise x_i * x_j = 1 (mutual exclusion).
        let i_idx = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: m.size,
        })?;
        let j_idx = usize::try_from(j).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: j,
            len: m.size,
        })?;
        m.add_quad(i_idx, j_idx, penalty).map_err(at_pos(pos))?;
        Ok(StepResult::Continue)
    }

    fn exec_implies(&mut self, pos: usize, reg: Register) -> Result<StepResult, Error> {
        let penalty = self.pop(pos)?;
        let j = self.pop(pos)?;
        let i = self.pop(pos)?;
        self.charge(pos, LINEAR_ENTRY_BYTES + QUAD_ENTRY_BYTES)?;
        let m = self
            .reg_mut(reg)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: reg.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        // Penalise x_i=1, x_j=0: penalty * x_i * (1 - x_j) = penalty*x_i - penalty*x_i*x_j.
        let i_idx = usize::try_from(i).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: i,
            len: m.size,
        })?;
        let j_idx = usize::try_from(j).map_err(|_| Error::IndexOutOfBounds {
            pos,
            index: j,
            len: m.size,
        })?;
        let neg_penalty = checked(penalty.checked_neg(), pos)?;
        m.add_linear(i_idx, penalty).map_err(at_pos(pos))?;
        m.add_quad(i_idx, j_idx, neg_penalty).map_err(at_pos(pos))?;
        Ok(StepResult::Continue)
    }

    fn exec_equality(
        &mut self,
        pos: usize,
        model: Register,
        indices: Register,
        coeffs: Register,
    ) -> Result<StepResult, Error> {
        let penalty = self.pop(pos)?;
        let target = self.pop(pos)?;
        let idx_vec: Vec<i64> = self
            .reg(indices)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: indices.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?
            .clone();
        let coeff_vec: Vec<i64> = self
            .reg(coeffs)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: coeffs.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?
            .clone();
        if idx_vec.len() != coeff_vec.len() {
            return Err(Error::VecLengthMismatch {
                what: "indices",
                a: idx_vec.len(),
                other: "coeffs",
                b: coeff_vec.len(),
            });
        }
        // The expansion is quadratic in the number of terms, and `EQUALITY`
        // also grows the model to cover the largest index it was handed --
        // both from vec contents the program controls. Charge for the growth
        // and the expansion before either happens.
        let current_size = match self.reg(model) {
            RegVal::Model(m) => m.size,
            _ => 0,
        };
        let needed = idx_vec
            .iter()
            .max()
            .and_then(|&max_idx| max_idx.checked_add(1))
            .and_then(|needed| usize::try_from(needed).ok())
            .unwrap_or(0);
        self.charge_variables(pos, needed.saturating_sub(current_size))?;
        self.charge_equality_expansion(pos, idx_vec.len())?;
        let m = self
            .reg_mut(model)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: model.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        if needed > m.size {
            m.size = needed;
        }
        let idx_us = indices_to_usize(&idx_vec, pos, m.size)?;
        expand_equality(m, &idx_us, &coeff_vec, target, penalty, pos)?;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`1 <= k <= n_i64` is checked above so the excess is in `0..n_i64`, `leading_zeros()` returns at most `i64::BITS` so `num_slacks` is at most 63, and the model growth was charged against the allocation budget before the model was touched (`spec/xqvm/SPEC.md`'s Allocation budget), which bounds `m.size + num_slacks`, `slack_start + i` and `1i64 << i` for `i <= 62`"
    )]
    fn exec_at_least(
        &mut self,
        pos: usize,
        model: Register,
        indices: Register,
    ) -> Result<StepResult, Error> {
        let penalty = self.pop(pos)?;
        let k = self.pop(pos)?;
        let idx_vec: Vec<i64> = self
            .reg(indices)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: indices.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?
            .clone();
        let n = idx_vec.len();
        // `idx_vec.len()` is bounded by isize::MAX ≤ i64::MAX on every
        // supported platform; try_from never fails in practice.
        let n_i64 = i64::try_from(n).unwrap_or(i64::MAX);
        if k <= 0 || k > n_i64 {
            return Err(Error::IndexOutOfBounds {
                pos,
                index: k,
                len: n,
            });
        }
        let max_excess = n_i64 - k;
        // `max_excess > 0` implies `leading_zeros` operates on a positive i64,
        // so the bit-length fits in u32; widening u32 → usize is lossless on
        // every supported target.
        let num_slacks = if max_excess <= 0 {
            0
        } else {
            (i64::BITS - max_excess.leading_zeros()) as usize
        };
        // The slack variables grow the model, and the expansion is quadratic
        // in the total term count. Charge for both before either happens.
        self.charge_variables(pos, num_slacks)?;
        self.charge_equality_expansion(pos, n.saturating_add(num_slacks))?;
        let m = self
            .reg_mut(model)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: model.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        if num_slacks == 0 {
            let unit_coeffs = vec![1i64; n];
            let idx_us = indices_to_usize(&idx_vec, pos, m.size)?;
            expand_equality(m, &idx_us, &unit_coeffs, k, penalty, pos)?;
            return Ok(StepResult::Continue);
        }
        let slack_start = m.size;
        let idx_us = indices_to_usize(&idx_vec, pos, slack_start)?;
        m.size += num_slacks;
        let mut all_indices = idx_us;
        let mut all_coeffs = vec![1i64; n];
        for i in 0..num_slacks {
            all_indices.push(slack_start + i);
            all_coeffs.push(-(1i64 << i));
        }
        expand_equality(m, &all_indices, &all_coeffs, k, penalty, pos)?;
        Ok(StepResult::Continue)
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`max_excess` comes from a checked subtraction so `leading_zeros()` returns at most `i64::BITS` and `num_slacks` is at most 63, and the model growth was charged against the allocation budget before the model was touched (`spec/xqvm/SPEC.md`'s Allocation budget), which bounds `m.size + num_slacks`, `slack_start + i` and `1i64 << i` for `i <= 62`"
    )]
    fn exec_at_least_w(
        &mut self,
        pos: usize,
        model: Register,
        indices: Register,
        coeffs: Register,
    ) -> Result<StepResult, Error> {
        let penalty = self.pop(pos)?;
        let k = self.pop(pos)?;
        let idx_vec: Vec<i64> = self
            .reg(indices)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: indices.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?
            .clone();
        let coeff_vec: Vec<i64> = self
            .reg(coeffs)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: coeffs.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?
            .clone();
        let n = idx_vec.len();
        if n != coeff_vec.len() {
            return Err(Error::VecLengthMismatch {
                what: "indices",
                a: n,
                other: "coeffs",
                b: coeff_vec.len(),
            });
        }
        if k <= 0 {
            return Err(Error::IndexOutOfBounds {
                pos,
                index: k,
                len: n,
            });
        }
        // Weights are program-controlled, so the sum is accumulated in index
        // order with every partial sum checked: wrapping here would derive
        // the slack count from a wrapped excess (spec/xqvm/HLF.md).
        let weight_sum = coeff_vec
            .iter()
            .try_fold(0i64, |acc, &w| acc.checked_add(w))
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        let max_excess = weight_sum
            .checked_sub(k)
            .ok_or(Error::ArithmeticOverflow { pos: Some(pos) })?;
        // See `exec_at_least` for the `leading_zeros` widening argument.
        let num_slacks = if max_excess <= 0 {
            0
        } else {
            (i64::BITS - max_excess.leading_zeros()) as usize
        };
        self.charge_variables(pos, num_slacks)?;
        self.charge_equality_expansion(pos, n.saturating_add(num_slacks))?;
        let m = self
            .reg_mut(model)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: model.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        if num_slacks == 0 {
            let idx_us = indices_to_usize(&idx_vec, pos, m.size)?;
            expand_equality(m, &idx_us, &coeff_vec, k, penalty, pos)?;
            return Ok(StepResult::Continue);
        }
        let slack_start = m.size;
        let idx_us = indices_to_usize(&idx_vec, pos, slack_start)?;
        m.size += num_slacks;
        let mut all_indices = idx_us;
        let mut all_coeffs = coeff_vec.clone();
        for i in 0..num_slacks {
            all_indices.push(slack_start + i);
            all_coeffs.push(-(1i64 << i));
        }
        expand_equality(m, &all_indices, &all_coeffs, k, penalty, pos)?;
        Ok(StepResult::Continue)
    }

    fn exec_reduce(&mut self, pos: usize, model: Register) -> Result<StepResult, Error> {
        let p_aux = self.pop(pos)?;
        let var_b = self.pop(pos)?;
        let var_a = self.pop(pos)?;
        // Rosenberg reduction adds one auxiliary variable, three quadratic
        // terms and one linear term.
        self.charge_variables(pos, 1)?;
        self.charge(pos, 3 * QUAD_ENTRY_BYTES + LINEAR_ENTRY_BYTES)?;
        let m = self
            .reg_mut(model)
            .as_model_mut()
            .map_err(|e| Error::RegisterType {
                reg: model.slot(),
                expected: "model",
                got: e.actual.kind_name(),
            })?;
        let ua =
            usize::try_from(var_a)
                .ok()
                .filter(|&v| v < m.size)
                .ok_or(Error::IndexOutOfBounds {
                    pos,
                    index: var_a,
                    len: m.size,
                })?;
        let ub =
            usize::try_from(var_b)
                .ok()
                .filter(|&v| v < m.size)
                .ok_or(Error::IndexOutOfBounds {
                    pos,
                    index: var_b,
                    len: m.size,
                })?;
        let w = expand_reduce(m, ua, ub, p_aux, pos)?;
        // `model.size` is bounded by isize::MAX ≤ i64::MAX on every
        // supported platform; try_from never fails in practice.
        self.push_stack(i64::try_from(w).unwrap_or(i64::MAX), pos)?;
        Ok(StepResult::Continue)
    }

    // -- Energy --

    fn exec_energy(
        &mut self,
        pos: usize,
        model: Register,
        sample: Register,
    ) -> Result<StepResult, Error> {
        // Per `spec/xqvm/SPEC.md` (`ENERGY`) and the xq-py reference
        // (`compute_energy` in `xqvm/core/xqmx.py`), the model register must
        // hold a Model and the sample register must hold a Sample. A Model
        // passed in the sample slot is rejected -- that "model-as-sample"
        // shortcut existed only in xq-rs and produced different programs
        // from xq-py for the same source.
        let sample_values: Vec<i64> = match self.reg(sample) {
            RegVal::Sample(s) => s.values.clone(),
            other => {
                return Err(Error::RegisterType {
                    reg: sample.slot(),
                    expected: "sample",
                    got: other.kind().kind_name(),
                });
            }
        };
        let m = match self.reg(model) {
            RegVal::Model(m) => m,
            other => {
                return Err(Error::RegisterType {
                    reg: model.slot(),
                    expected: "model",
                    got: other.kind().kind_name(),
                });
            }
        };
        let energy = m.energy(&sample_values).map_err(at_pos(pos))?;
        self.push_stack(energy, pos)?;
        Ok(StepResult::Continue)
    }
}

// ---------------------------------------------------------------------------
// XQMX high-level expansion helpers
// ---------------------------------------------------------------------------

/// Expand P*(∑ aₖ·xₖ - b)² into linear and quadratic QUBO terms.
///
/// For binary variables (x² = x):
///   linear[idxₖ]       += P·aₖ·(aₖ - 2b)
///   quadratic[idxₖ,idxₘ] += 2P·aₖ·aₘ   for k < m
///
/// `indices` and `coeffs` are required by the caller to have equal length;
/// the iterator-based access here cannot panic on out-of-range indexing.
fn expand_equality(
    model: &mut XqmxModel,
    indices: &[usize],
    coeffs: &[i64],
    target: i64,
    penalty: i64,
    pos: usize,
) -> Result<(), Error> {
    let two_b = checked(target.checked_mul(2), pos)?;
    for (&idx, &a_k) in indices.iter().zip(coeffs.iter()) {
        let coefficient = checked(
            a_k.checked_sub(two_b)
                .and_then(|diff| a_k.checked_mul(diff))
                .and_then(|scaled| penalty.checked_mul(scaled)),
            pos,
        )?;
        model.add_linear(idx, coefficient).map_err(at_pos(pos))?;
    }
    let two_p = checked(penalty.checked_mul(2), pos)?;
    #[expect(
        clippy::arithmetic_side_effects,
        reason = "`k` is an `enumerate` index into `indices`, so `k + 1` is at most its length; every coefficient product is checked (`spec/xqvm/SPEC.md`'s per-step rule)"
    )]
    for (k, (&idx_k, &a_k)) in indices.iter().zip(coeffs.iter()).enumerate() {
        for (&idx_m, &a_m) in indices.iter().zip(coeffs.iter()).skip(k + 1) {
            let coefficient = checked(
                two_p
                    .checked_mul(a_k)
                    .and_then(|partial| partial.checked_mul(a_m)),
                pos,
            )?;
            model
                .add_quad(idx_k, idx_m, coefficient)
                .map_err(at_pos(pos))?;
        }
    }
    Ok(())
}

/// Wrap a checked-arithmetic result as an overflow at `pos`.
fn checked(value: Option<i64>, pos: usize) -> Result<i64, Error> {
    value.ok_or(Error::ArithmeticOverflow { pos: Some(pos) })
}

/// Attach an instruction position to an overflow raised by the model layer.
///
/// Model mutations and reductions carry no program counter of their own, so
/// they raise with `pos: None`; the handler that called them knows the byte
/// offset the diagnostic needs to point at.
fn at_pos(pos: usize) -> impl Fn(Error) -> Error {
    move |err| match err {
        Error::ArithmeticOverflow { pos: None } => Error::ArithmeticOverflow { pos: Some(pos) },
        other => other,
    }
}

/// Convert vector-of-i64 indices to vector-of-usize, validating each is
/// in `[0, model_size)`. Returns the first out-of-range index as
/// [`Error::IndexOutOfBounds`].
fn indices_to_usize(idxs: &[i64], pos: usize, model_size: usize) -> Result<Vec<usize>, Error> {
    idxs.iter()
        .map(|&i| {
            usize::try_from(i)
                .ok()
                .filter(|&u| u < model_size)
                .ok_or(Error::IndexOutOfBounds {
                    pos,
                    index: i,
                    len: model_size,
                })
        })
        .collect()
}

/// Rosenberg degree reduction: replace `x_a·x_b` with auxiliary variable w.
///
/// Allocates w at `model.size`, adds 4 enforcement terms, returns w.
#[expect(
    clippy::arithmetic_side_effects,
    reason = "the one auxiliary variable was charged against the allocation budget before the call (`spec/xqvm/SPEC.md`'s Allocation budget), so `size` is bounded by it"
)]
fn expand_reduce(
    model: &mut XqmxModel,
    var_a: usize,
    var_b: usize,
    p_aux: i64,
    pos: usize,
) -> Result<usize, Error> {
    let minus_two_p = checked(p_aux.checked_mul(-2), pos)?;
    let three_p = checked(p_aux.checked_mul(3), pos)?;
    let w = model.size;
    model.size += 1;
    model.add_quad(var_a, var_b, p_aux).map_err(at_pos(pos))?;
    model.add_quad(var_a, w, minus_two_p).map_err(at_pos(pos))?;
    model.add_quad(var_b, w, minus_two_p).map_err(at_pos(pos))?;
    model.add_linear(w, three_p).map_err(at_pos(pos))?;
    Ok(w)
}

#[cfg(test)]
mod tests {
    extern crate alloc;
    use alloc::format;
    use alloc::string::String;
    use alloc::vec::Vec;

    use crate::bytecode::{Instruction, InstructionBuilder, Register};

    use crate::Vm;
    use crate::error::Error;
    use crate::tracer::{NoopTracer, StepState, Tracer};
    use crate::value::RegVal;

    /// Test tracer that records all step states.
    struct RecordingTracer {
        steps: Vec<RecordedStep>,
    }

    #[expect(
        dead_code,
        reason = "struct fields are available for debugging inspections"
    )]
    struct RecordedStep {
        pos: usize,
        step: u64,
        instruction: Instruction,
        stack: Vec<i64>,
        read_regs: Vec<(u8, RegVal)>,
        written_regs: Vec<(u8, RegVal)>,
        loop_depth: usize,
    }

    impl RecordingTracer {
        fn new() -> Self {
            Self { steps: Vec::new() }
        }
    }

    impl Tracer for RecordingTracer {
        type Error = core::convert::Infallible;

        fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error> {
            self.steps.push(RecordedStep {
                pos: state.pos,
                step: state.step,
                instruction: *state.instruction,
                stack: state.stack.to_vec(),
                read_regs: state.read_regs.to_vec(),
                written_regs: state.written_regs.to_vec(),
                loop_depth: state.loop_depth,
            });
            Ok(())
        }
    }

    /// Tracer that errors on a specific step.
    struct FailingTracer {
        fail_at: u64,
    }

    impl Tracer for FailingTracer {
        type Error = String;

        fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error> {
            if state.step == self.fail_at {
                Err(format!("intentional failure at step {}", self.fail_at))
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn failing_tracer_propagates_error() {
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(1).emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        let mut tracer = FailingTracer { fail_at: 1 };
        let err = vm.run_trace(&mut tracer, &program).unwrap_err();
        assert!(
            matches!(err, Error::TraceFailed { .. }),
            "expected TraceFailed, got {err:?}",
        );
    }

    #[test]
    fn run_delegates_to_run_trace() {
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(3).emit_push(4).emit_add().emit_halt();
        let program = b.build().unwrap();

        let mut vm1 = Vm::new();
        vm1.run(&program).unwrap();

        let mut vm2 = Vm::new();
        vm2.run_trace(&mut NoopTracer, &program).unwrap();

        assert_eq!(vm1.stack(), vm2.stack());
    }

    #[test]
    fn recording_tracer_captures_steps() {
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(3)
            .emit_push(4)
            .emit_add()
            .emit_stow(Register(0))
            .emit_halt();
        let program = b.build().unwrap();

        let mut tracer = RecordingTracer::new();
        let mut vm = Vm::new();
        vm.run_trace(&mut tracer, &program).unwrap();

        assert_eq!(tracer.steps.len(), 5);

        // Step 1: PUSH 3 -> stack=[3], no regs
        let s0 = tracer.steps.first().expect("step 0");
        assert_eq!(s0.step, 1);
        assert_eq!(s0.stack, &[3]);
        assert!(s0.read_regs.is_empty());
        assert!(s0.written_regs.is_empty());

        // Step 2: PUSH 4 -> stack=[3, 4], no regs
        let s1 = tracer.steps.get(1).expect("step 1");
        assert_eq!(s1.stack, &[3, 4]);

        // Step 3: ADD -> stack=[7], no regs
        let s2 = tracer.steps.get(2).expect("step 2");
        assert_eq!(s2.stack, &[7]);

        // Step 4: STOW r0 -> stack=[], writes r0=7
        let s3 = tracer.steps.get(3).expect("step 3");
        assert_eq!(s3.stack, &[] as &[i64]);
        assert!(s3.read_regs.is_empty());
        assert_eq!(s3.written_regs.len(), 1);
        assert_eq!(s3.written_regs.first(), Some(&(0, RegVal::Int(7))));

        // Step 5: HALT -> stack=[]
        let s4 = tracer.steps.get(4).expect("step 4");
        assert_eq!(s4.stack, &[] as &[i64]);
    }

    #[test]
    fn recording_tracer_captures_read_regs() {
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(42)
            .emit_stow(Register(0))
            .emit_load(Register(0))
            .emit_halt();
        let program = b.build().unwrap();

        let mut tracer = RecordingTracer::new();
        let mut vm = Vm::new();
        vm.run_trace(&mut tracer, &program).unwrap();

        // Step 3: LOAD r0 reads r0=42
        let s2 = tracer.steps.get(2).expect("step 2");
        assert_eq!(s2.read_regs.len(), 1);
        assert_eq!(s2.read_regs.first(), Some(&(0, RegVal::Int(42))));
    }

    #[test]
    fn div_floor_negative_dividend() {
        // -7 // 2 = -4 (floor), not -3 (truncating)
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(-7).emit_push(2).emit_div().emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[-4]);
    }

    #[test]
    fn div_floor_negative_divisor() {
        // 7 // -2 = -4 (floor)
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(7).emit_push(-2).emit_div().emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[-4]);
    }

    #[test]
    fn div_floor_both_positive() {
        // 7 // 2 = 3 (unchanged by floor correction)
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(7).emit_push(2).emit_div().emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[3]);
    }

    #[test]
    fn mod_divisor_sign_negative_dividend() {
        // -7 % 2 = 1 (divisor-sign), not -1 (dividend-sign)
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(-7).emit_push(2).emit_modulo().emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[1]);
    }

    #[test]
    fn mod_divisor_sign_negative_divisor() {
        // 7 % -2 = -1 (divisor-sign)
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(7).emit_push(-2).emit_modulo().emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[-1]);
    }

    #[test]
    fn mod_divisor_sign_both_positive() {
        // 7 % 3 = 1 (unchanged)
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(7).emit_push(3).emit_modulo().emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[1]);
    }

    #[test]
    fn load_on_never_set_register_faults() {
        let mut b = InstructionBuilder::new();
        let _ = b.emit_load(Register(5)).emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        let err = vm.run(&program).unwrap_err();
        assert!(
            matches!(err, Error::UnsetRegister { reg: 5, .. }),
            "expected UnsetRegister, got {err:?}"
        );
    }

    #[test]
    fn drop_then_load_faults() {
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(42)
            .emit_stow(Register(0))
            .emit_drop(Register(0))
            .emit_load(Register(0))
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        let err = vm.run(&program).unwrap_err();
        assert!(
            matches!(err, Error::UnsetRegister { reg: 0, .. }),
            "expected UnsetRegister, got {err:?}"
        );
    }

    #[test]
    fn output_on_unset_register_faults() {
        // OUTPUT must fault on an unset register so a missing STOW surfaces
        // as an error rather than silently leaving the slot "never written".
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(0).emit_output(Register(7)).emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        let _ = vm.set_output_slots(1);
        let err = vm.run(&program).unwrap_err();
        assert!(
            matches!(err, Error::UnsetRegister { reg: 7, .. }),
            "expected UnsetRegister, got {err:?}"
        );
    }

    #[test]
    fn stow_then_load_works_after_unset_init() {
        // Register is initially Unset; STOW sets it; LOAD retrieves it.
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(99)
            .emit_stow(Register(3))
            .emit_load(Register(3))
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[99]);
    }

    #[test]
    fn range_count_zero_skips_body() {
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(0)
            .emit_push(0)
            .emit_range()
            .emit_push(99)
            .emit_next()
            .emit_push(42)
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[42]);
    }

    #[test]
    fn range_count_negative_skips_body() {
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(0)
            .emit_push(-3)
            .emit_range()
            .emit_push(99)
            .emit_next()
            .emit_push(42)
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[42]);
    }

    #[test]
    fn range_count_zero_nested_skips() {
        // Outer RANGE has count=0. Its body contains an inner RANGE(count=3)/NEXT.
        // The skip-forward scan must track nesting depth to find the outer NEXT.
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(0)
            .emit_push(0)
            .emit_range() // outer: count=0, should skip to outer NEXT
            .emit_push(0)
            .emit_push(3)
            .emit_range() // inner: count=3
            .emit_push(99)
            .emit_next() // inner NEXT
            .emit_next() // outer NEXT
            .emit_push(42)
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(vm.stack(), &[42]);
    }

    #[test]
    fn range_count_one_executes_body_once() {
        // Boundary: count=1 must execute the body exactly once.
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(0)
            .emit_push(0)
            .emit_stow(Register(0)) // r0 = 0 (counter)
            .emit_push(5) // start
            .emit_push(1) // count = 1
            .emit_range()
            .emit_load(Register(0))
            .emit_inc()
            .emit_stow(Register(0)) // r0 += 1
            .emit_next()
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(*vm.reg(Register(0)), RegVal::Int(1));
    }

    #[test]
    fn range_positive_nested_still_works() {
        // Both loops have positive counts; verify nesting is unaffected.
        // outer: count=2, inner: count=3 -> body runs 6 times total.
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(0)
            .emit_stow(Register(0)) // r0 = 0 (counter)
            .emit_push(0)
            .emit_push(2)
            .emit_range() // outer
            .emit_push(0)
            .emit_push(3)
            .emit_range() // inner
            .emit_load(Register(0))
            .emit_inc()
            .emit_stow(Register(0))
            .emit_next() // inner NEXT
            .emit_next() // outer NEXT
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        vm.run(&program).unwrap();
        assert_eq!(*vm.reg(Register(0)), RegVal::Int(6));
    }

    #[test]
    fn range_count_zero_unmatched_faults() {
        // RANGE with count=0 and no matching NEXT must fault.
        let mut b = InstructionBuilder::new();
        let _ = b.emit_push(0).emit_push(0).emit_range().emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        let err = vm.run(&program).unwrap_err();
        assert!(
            matches!(err, Error::UnmatchedLoop { .. }),
            "expected UnmatchedLoop, got {err:?}"
        );
    }

    #[test]
    fn idx_triu_raises_when_the_row_term_overflows() {
        // j = 3 gives a triangular term of 3*(3-1)/2 = 3, which leaves the
        // range when added to i = i64::MAX. Index math raises like any other
        // overflowing operation rather than producing a wrapped index.
        let mut b = InstructionBuilder::new();
        let _ = b
            .emit_push(i64::MAX)
            .emit_push(3)
            .emit_idx_triu()
            .emit_halt();
        let program = b.build().unwrap();
        let mut vm = Vm::new();
        let err = vm.run(&program).unwrap_err();
        assert!(
            matches!(err, Error::ArithmeticOverflow { .. }),
            "expected ArithmeticOverflow, got {err:?}"
        );
    }
}
