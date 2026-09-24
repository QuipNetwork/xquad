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

//! The vector file format, and running one vector on the VM.
//!
//! Each vector is a directory `tests/vectors/<category>/<name>/` holding:
//!
//! - `program.xqasm` -- assembly source, assembled in-process on every run.
//! - `inputs.json` -- calldata and the run's budgets; see [`Inputs`].
//! - `expected.json` -- either `{"outputs": [i64|null, ...], "final_stack":
//!   [i64, ...], "steps": u64}` for a program that runs to completion, or
//!   `{"error": "<FAULT>"}` for one that must fault. `steps` is the exact
//!   metered execution cost (`spec/xqvm/METERING.md`) and is required: it is
//!   what the chain prices a run by, so a successful vector that left it
//!   out would pin the result and not the cost.
//!
//! `tests/vectors/README.md` documents the format and the fault vocabulary
//! for vector authors.

use std::fmt::Write as _;
use std::fs;
use std::io;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// Parsed form of `inputs.json`.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct Inputs {
    /// Calldata values exposed to `INPUT` instructions in slot order.
    ///
    /// `null` is a slot the host fixed but left unset -- in range for
    /// `INPUT`, and copied into the register as unset. It is spelled here
    /// rather than left out because leaving it out would shorten the slot
    /// space and move the `CallDataIndex` bound.
    #[serde(default)]
    pub(crate) calldata: Vec<Option<i64>>,
    /// Number of output slots; defaults to 16 to match `xquad run`.
    #[serde(default = "default_output_slots")]
    pub(crate) output_slots: usize,
    /// Step budget for the run.
    ///
    /// Set it only for a vector that exercises the budget; every other
    /// vector runs under the same default `xquad run` uses.
    #[serde(default = "default_step_limit")]
    pub(crate) step_limit: u64,
    /// Allocation budget in bytes for the run.
    ///
    /// The allocation-charge half of the fault-ordering rule is only
    /// reachable against a budget the charge exhausts, so a vector pinning
    /// it has to be able to name a tight one.
    #[serde(default = "default_memory_limit")]
    pub(crate) memory_limit: u64,
}

const fn default_output_slots() -> usize {
    16
}

/// The step budget for a vector that does not name one.
///
/// Is `xqvm::DEFAULT_STEP_LIMIT`, rather than a literal restating it, so a
/// vector that is not about the budget behaves exactly as `xquad run`
/// would and cannot drift from the VM it is testing.
const fn default_step_limit() -> u64 {
    xqvm::DEFAULT_STEP_LIMIT
}

/// The allocation budget for a vector that does not name one, for the
/// reason [`default_step_limit`] names the step constant.
const fn default_memory_limit() -> u64 {
    xqvm::DEFAULT_MEMORY_LIMIT
}

/// Identity of a VM fault, as a vector writes it in `expected.json`.
///
/// Coarser than [`xqvm::Error`] on purpose: byte position is not part of
/// the identity, so vectors do not turn brittle against unrelated codegen
/// changes, and `RegisterType` and `IncompatibleType` share one name.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub(crate) enum Fault {
    /// A pop was attempted on an empty stack.
    StackUnderflow,
    /// The value stack exceeded its depth limit.
    StackOverflow,
    /// An operand had the wrong value kind, including a model-only
    /// opcode applied to a sample register (or the reverse).
    TypeMismatch,
    /// A register was read while unset.
    UnsetRegister,
    /// Division or modulo by zero.
    DivisionByZero,
    /// An index fell outside the addressed container.
    IndexOutOfBounds,
    /// A loop instruction executed with no active loop.
    NoActiveLoop,
    /// A `RANGE`/`ITER` had no matching `NEXT`.
    UnmatchedLoop,
    /// A jump named a target outside the program.
    BadJumpTarget,
    /// A jump named a label the program does not define.
    InvalidLabel,
    /// An unknown opcode byte was decoded.
    BadOpcode,
    /// An instruction's operands ran past the end of the program.
    TruncatedInstruction,
    /// An `INPUT` addressed a calldata slot that does not exist.
    CallDataIndex,
    /// An `OUTPUT` addressed an output slot that does not exist.
    OutputIndex,
    /// A model and a sample disagreed on variable count.
    SizeMismatch,
    /// Two vector operands disagreed on length.
    VecLengthMismatch,
    /// An operation produced a value outside the signed 64-bit range.
    ArithmeticOverflow,
    /// Execution ran past its step budget.
    StepLimitExceeded,
    /// An allocating instruction ran past its allocation budget.
    MemoryLimitExceeded,
    /// A shift amount fell outside the representable range.
    InvalidShift,
    /// Grid dimensions were not positive, or did not fit the model.
    InvalidGridDimensions,
    /// An `XQMX`/`XSMX` allocation used `k < 2`.
    InvalidIntegerK,
    /// A `SETLINE`/`ADDLINE` write put a value outside a sample's domain.
    SampleOutOfDomain,
    /// A tracer refused a step.
    TraceFailed,
    /// An allocator was handed a size that is not an allocation.
    InvalidAllocation,
    /// Loop nesting exceeded its depth limit.
    LoopStackOverflow,
}

impl From<&xqvm::Error> for Fault {
    /// Map a VM error onto the identity a vector asserts.
    ///
    /// The match is exhaustive by construction: adding a variant to
    /// [`xqvm::Error`] without extending [`Fault`] and this arm fails to
    /// compile, rather than silently degrading to an "unknown" fault.
    fn from(error: &xqvm::Error) -> Self {
        use xqvm::Error as E;

        match *error {
            E::StackUnderflow { .. } => Self::StackUnderflow,
            E::StackOverflow { .. } => Self::StackOverflow,
            E::RegisterType { .. } | E::IncompatibleType(_) => Self::TypeMismatch,
            E::UnsetRegister { .. } => Self::UnsetRegister,
            E::DivisionByZero { .. } => Self::DivisionByZero,
            E::ArithmeticOverflow { .. } => Self::ArithmeticOverflow,
            E::IndexOutOfBounds { .. } => Self::IndexOutOfBounds,
            E::NoActiveLoop { .. } => Self::NoActiveLoop,
            E::UnmatchedLoop { .. } => Self::UnmatchedLoop,
            E::BadJumpTarget { .. } => Self::BadJumpTarget,
            E::InvalidLabel { .. } => Self::InvalidLabel,
            E::BadOpcode { .. } => Self::BadOpcode,
            E::TruncatedInstruction { .. } => Self::TruncatedInstruction,
            E::CallDataIndex { .. } => Self::CallDataIndex,
            E::OutputIndex { .. } => Self::OutputIndex,
            E::SizeMismatch { .. } => Self::SizeMismatch,
            E::VecLengthMismatch { .. } => Self::VecLengthMismatch,
            E::StepLimitExceeded { .. } => Self::StepLimitExceeded,
            E::MemoryLimitExceeded { .. } => Self::MemoryLimitExceeded,
            E::InvalidShift { .. } => Self::InvalidShift,
            E::InvalidGridDimensions { .. } => Self::InvalidGridDimensions,
            E::InvalidIntegerK { .. } => Self::InvalidIntegerK,
            E::SampleOutOfDomain { .. } => Self::SampleOutOfDomain,
            E::TraceFailed { .. } => Self::TraceFailed,
            E::InvalidAllocation { .. } => Self::InvalidAllocation,
            E::LoopStackOverflow { .. } => Self::LoopStackOverflow,
        }
    }
}

/// Parsed form of `expected.json`.
///
/// A vector asserts either an outcome (`outputs` plus `final_stack` plus
/// `steps`) or a fault (`error`), never both and never neither. Outputs use
/// `null` for unset slots so the JSON representation is stable across runs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Expected {
    /// The program must run to completion with these results.
    Success {
        /// Expected output slots; `None` means the slot must be unset.
        outputs: Vec<Option<i64>>,
        /// Expected residual stack contents after HALT.
        final_stack: Vec<i64>,
        /// Expected step count, per `spec/xqvm/METERING.md`.
        steps: u64,
    },
    /// The program must fault with this identity.
    Failure {
        /// The fault the VM is required to raise.
        error: Fault,
    },
}

/// Raw shape of `expected.json` before the success/failure split is
/// validated. Deserialising through this lets a vector that asserts both
/// (or neither), a success without its step count, or a fault with a field
/// only a success is compared on, fail as an authoring mistake rather than
/// silently preferring one half or asserting less.
#[derive(Deserialize)]
struct RawExpected {
    outputs: Option<Vec<Option<i64>>>,
    #[serde(default)]
    final_stack: Option<Vec<i64>>,
    #[serde(default)]
    steps: Option<u64>,
    error: Option<Fault>,
}

impl<'de> Deserialize<'de> for Expected {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        use serde::de::Error as _;

        let raw = RawExpected::deserialize(deserializer)?;
        match (raw.outputs, raw.error) {
            (Some(_), Some(_)) => Err(D::Error::custom(
                "expected.json asserts both an outcome and a fault: \
                 supply either `outputs`/`final_stack` or `error`, not both",
            )),
            (Some(outputs), None) => {
                let steps = raw.steps.ok_or_else(|| {
                    D::Error::custom(
                        "expected.json asserts an outcome without `steps`: a \
                         successful vector must assert its metered step count",
                    )
                })?;
                Ok(Self::Success {
                    outputs,
                    final_stack: raw.final_stack.unwrap_or_default(),
                    steps,
                })
            }
            (None, Some(_)) if raw.steps.is_some() => Err(D::Error::custom(
                "expected.json asserts `steps` on a fault: no step count is \
                 compared for a faulting run, so the value would go \
                 unchecked; drop `steps` or `error`",
            )),
            (None, Some(_)) if raw.final_stack.is_some() => Err(D::Error::custom(
                "expected.json asserts `final_stack` on a fault: the harness \
                 compares no stack for a faulting run, so the value would go \
                 unchecked; drop `final_stack` or `error`",
            )),
            (None, Some(error)) => Ok(Self::Failure { error }),
            (None, None) => Err(D::Error::custom(
                "expected.json asserts nothing: supply `outputs` for a \
                 successful run or `error` for a fault",
            )),
        }
    }
}

/// Execution result in a form directly comparable to [`Expected`].
///
/// `PartialEq` is written out rather than derived because
/// [`Outcome::Failure`] carries a diagnostic `detail` that is not part of
/// the result: [`run`] compares the source and decoded-bytecode runs as
/// whole outcomes, and a `detail` naming the byte offset would fail that
/// comparison for two spellings of the same fault.
#[derive(Debug, Clone)]
pub(crate) enum Outcome {
    /// The program ran to completion.
    Success {
        /// Output slot contents observed after execution.
        outputs: Vec<Option<i64>>,
        /// Residual stack contents after HALT.
        final_stack: Vec<i64>,
        /// Steps metered by the run, per `spec/xqvm/METERING.md`.
        steps: u64,
    },
    /// The program faulted.
    Failure {
        /// Identity of the fault the VM raised.
        error: Fault,
        /// The VM's `Error`, in full.
        ///
        /// The identity is deliberately coarse, so on its own a mismatch
        /// would report two names and nothing else. This carries the offset
        /// and operand values into the failure message without putting them
        /// in the identity.
        detail: String,
    },
}

impl PartialEq for Outcome {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (
                Self::Success {
                    outputs,
                    final_stack,
                    steps,
                },
                Self::Success {
                    outputs: other_outputs,
                    final_stack: other_stack,
                    steps: other_steps,
                },
            ) => outputs == other_outputs && final_stack == other_stack && steps == other_steps,
            // `detail` is diagnostics, not identity -- see the type's docs.
            (
                Self::Failure { error, .. },
                Self::Failure {
                    error: other_error, ..
                },
            ) => error == other_error,
            _ => false,
        }
    }
}

impl Eq for Outcome {}

/// Loaded vector with all on-disk artifacts materialised.
#[derive(Debug, Clone)]
pub(crate) struct Vector {
    /// Contents of `program.xqasm`.
    pub(crate) program_xqasm: String,
    /// Parsed `inputs.json`.
    pub(crate) inputs: Inputs,
    /// Parsed `expected.json`.
    pub(crate) expected: Expected,
}

/// The `tests/vectors/` directory.
fn vectors_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("vectors")
}

/// List every complete vector as `(category, name)`, sorted.
///
/// "Complete" means a directory holding all three of `program.xqasm`,
/// `inputs.json` and `expected.json`. A half-authored directory is skipped,
/// so the set walked here is exactly the set the suite runs and the
/// coverage report measures.
pub(crate) fn discover() -> Vec<(String, String)> {
    let mut out = Vec::new();
    let Ok(categories) = fs::read_dir(vectors_root()) else {
        return out;
    };
    for category_entry in categories.flatten() {
        if !category_entry.file_type().is_ok_and(|t| t.is_dir()) {
            continue;
        }
        let category = category_entry.file_name().to_string_lossy().into_owned();
        let Ok(vectors) = fs::read_dir(category_entry.path()) else {
            continue;
        };
        for vector_entry in vectors.flatten() {
            if !vector_entry.file_type().is_ok_and(|t| t.is_dir()) {
                continue;
            }
            let dir = vector_entry.path();
            if dir.join("program.xqasm").exists()
                && dir.join("inputs.json").exists()
                && dir.join("expected.json").exists()
            {
                out.push((
                    category.clone(),
                    vector_entry.file_name().to_string_lossy().into_owned(),
                ));
            }
        }
    }
    out.sort();
    out
}

/// Load a vector by its `<category>/<name>` path segments.
///
/// # Errors
/// Returns any I/O error reading the three required files, or an
/// [`io::Error`] wrapping a JSON parse failure for `inputs.json` or
/// `expected.json`.
pub(crate) fn load(category: &str, name: &str) -> io::Result<Vector> {
    let dir = vectors_root().join(category).join(name);
    let program_xqasm = fs::read_to_string(dir.join("program.xqasm"))?;
    let inputs: Inputs = serde_json::from_str(&fs::read_to_string(dir.join("inputs.json"))?)
        .map_err(io::Error::other)?;
    let expected: Expected = serde_json::from_str(&fs::read_to_string(dir.join("expected.json"))?)
        .map_err(io::Error::other)?;
    Ok(Vector {
        program_xqasm,
        inputs,
        expected,
    })
}

/// Assemble a vector's program.
///
/// # Errors
/// Returns the assembler's diagnostic as a string.
pub(crate) fn assemble(vector: &Vector) -> Result<xqvm::Program, String> {
    xqasm::assemble_source(&vector.program_xqasm)
        .map_err(|e| format!("assemble_source failed: {e}"))
}

/// Run the vector on the VM.
///
/// The program runs twice: as assembled, and after an encode/decode round
/// trip through the bytecode format. The two must agree, so a codec defect
/// that changes behaviour fails the vector rather than passing unseen.
///
/// # Errors
/// Returns an error message if assembly or decoding fails, or if the two
/// runs disagree.
pub(crate) fn run(vector: &Vector) -> Result<Outcome, String> {
    let program = assemble(vector)?;
    let outcome = run_program(&program, &vector.inputs);

    let decoded = xqvm::Program::decode(&program.encode())
        .map_err(|e| format!("bytecode decode failed: {e:?}"))?;
    let decoded_outcome = run_program(&decoded, &vector.inputs);
    if outcome != decoded_outcome {
        return Err(format!(
            "bytecode round-trip mismatch:\n  source:   {outcome:?}\n  bytecode: {decoded_outcome:?}"
        ));
    }

    Ok(outcome)
}

/// A VM carrying every budget and input a vector declares.
///
/// The single place `Inputs` becomes VM state. The coverage report runs
/// vectors too, and measuring a run configured differently from the one
/// [`run`] judges would report coverage of something the suite never
/// executes.
pub(crate) fn vm_for(inputs: &Inputs) -> xqvm::Vm {
    let calldata = inputs
        .calldata
        .iter()
        .map(|v| v.map_or(xqvm::RegVal::Unset, xqvm::RegVal::Int))
        .collect();
    let mut vm = xqvm::Vm::new();
    let _ = vm
        .set_calldata(calldata)
        .set_output_slots(inputs.output_slots);
    let _ = vm.set_step_limit(inputs.step_limit);
    let _ = vm.set_memory_limit(inputs.memory_limit);
    vm
}

fn run_program(program: &xqvm::Program, inputs: &Inputs) -> Outcome {
    use xqvm::RegVal;

    let mut vm = vm_for(inputs);

    if let Err(e) = vm.run(program) {
        return Outcome::Failure {
            error: Fault::from(&e),
            detail: format!("{e:?}"),
        };
    }

    let mut outputs: Vec<Option<i64>> = vm
        .outputs()
        .iter()
        .map(|rv| match rv {
            RegVal::Unset => None,
            RegVal::Int(n) => Some(*n),
            // Vectors, models and samples have no `i64` spelling in the
            // vector format. A sentinel keeps the comparison uniform: a
            // vector producing one fails the equality check with a clear
            // mismatch message.
            _ => Some(i64::MIN),
        })
        .collect();
    trim_trailing_unset(&mut outputs);
    Outcome::Success {
        outputs,
        final_stack: vm.stack().to_vec(),
        steps: vm.steps(),
    }
}

/// Drop trailing `None` entries so outputs report a sparse map rather than
/// a fixed-width array padded with `null`. A vector that writes only slot 0
/// out of 16 reserved slots thus produces `[value]`, not
/// `[value, null, null, ...]` -- matching `spec/xqvm/SPEC.md`'s Machine
/// State sketch.
fn trim_trailing_unset(outputs: &mut Vec<Option<i64>>) {
    while matches!(outputs.last(), Some(None)) {
        let _ = outputs.pop();
    }
}

/// Compare an [`Outcome`] against an [`Expected`], returning a
/// multi-line diff on mismatch.
///
/// # Errors
/// Returns a human-readable diff when the run's shape (success versus
/// fault), its fault identity, its output slots, its residual stack or its
/// step count disagrees with the vector.
pub(crate) fn check(actual: &Outcome, expected: &Expected) -> Result<(), String> {
    let mut msg = String::from("vector mismatch:\n");
    match (actual, expected) {
        (
            Outcome::Success {
                outputs,
                final_stack,
                steps,
            },
            Expected::Success {
                outputs: exp_outputs,
                final_stack: exp_stack,
                steps: exp_steps,
            },
        ) => {
            if outputs == exp_outputs && final_stack == exp_stack && steps == exp_steps {
                return Ok(());
            }
            if outputs != exp_outputs {
                let _ = writeln!(
                    msg,
                    "  outputs:\n    expected: {exp_outputs:?}\n    actual:   {outputs:?}"
                );
            }
            if final_stack != exp_stack {
                let _ = writeln!(
                    msg,
                    "  final_stack:\n    expected: {exp_stack:?}\n    actual:   {final_stack:?}"
                );
            }
            if steps != exp_steps {
                let _ = writeln!(
                    msg,
                    "  steps:\n    expected: {exp_steps}\n    actual:   {steps}"
                );
            }
        }
        (Outcome::Failure { error, detail }, Expected::Failure { error: exp_error }) => {
            if error == exp_error {
                return Ok(());
            }
            let _ = writeln!(
                msg,
                "  fault:\n    expected: {}\n    actual:   {} -- {detail}",
                fault_name(*exp_error),
                fault_name(*error)
            );
        }
        (Outcome::Failure { error, detail }, Expected::Success { .. }) => {
            let _ = writeln!(
                msg,
                "  expected the program to run to completion, but it faulted \
                 with {} -- {detail}",
                fault_name(*error)
            );
        }
        (
            Outcome::Success {
                outputs,
                final_stack,
                ..
            },
            Expected::Failure { error },
        ) => {
            let _ = writeln!(
                msg,
                "  expected the program to fault with {}, but it ran to completion\n    \
                 outputs: {outputs:?}\n    final_stack: {final_stack:?}",
                fault_name(*error)
            );
        }
    }
    Err(msg)
}

/// Render a fault under the name a vector writes in `expected.json`.
fn fault_name(fault: Fault) -> String {
    serde_json::to_string(&fault).unwrap_or_else(|_| "<unserialisable fault>".to_owned())
}

/// Self-tests of the format and the comparison.
///
/// A `harness = false` target runs these as plain functions, not `#[test]`
/// items, so `clippy.toml`'s test allowances do not reach them.
#[expect(
    clippy::expect_used,
    reason = "self-tests: a failed expectation is the test failing"
)]
pub(crate) mod self_tests {
    use super::{Expected, Fault, Outcome, check};

    /// The self-tests, as `(name, test)` pairs.
    pub(crate) const TESTS: &[(&str, fn())] = &[
        (
            "expected_parses_a_success_vector",
            expected_parses_a_success_vector,
        ),
        (
            "expected_rejects_a_success_vector_without_steps",
            expected_rejects_a_success_vector_without_steps,
        ),
        (
            "expected_rejects_steps_on_an_error_vector",
            expected_rejects_steps_on_an_error_vector,
        ),
        (
            "expected_rejects_a_final_stack_on_an_error_vector",
            expected_rejects_a_final_stack_on_an_error_vector,
        ),
        (
            "expected_parses_an_error_vector",
            expected_parses_an_error_vector,
        ),
        (
            "expected_rejects_a_vector_asserting_both_success_and_failure",
            expected_rejects_a_vector_asserting_both_success_and_failure,
        ),
        (
            "expected_rejects_a_vector_asserting_neither",
            expected_rejects_a_vector_asserting_neither,
        ),
        (
            "expected_rejects_an_unknown_fault_name",
            expected_rejects_an_unknown_fault_name,
        ),
        (
            "check_reports_a_fault_where_success_was_expected",
            check_reports_a_fault_where_success_was_expected,
        ),
        (
            "check_reports_success_where_a_fault_was_expected",
            check_reports_success_where_a_fault_was_expected,
        ),
        (
            "check_rejects_a_step_count_mismatch",
            check_rejects_a_step_count_mismatch,
        ),
        (
            "check_accepts_a_matching_fault",
            check_accepts_a_matching_fault,
        ),
        (
            "check_rejects_the_wrong_fault",
            check_rejects_the_wrong_fault,
        ),
        (
            "a_fault_mismatch_reports_what_the_vm_said",
            a_fault_mismatch_reports_what_the_vm_said,
        ),
        (
            "a_fault_where_success_was_expected_reports_the_detail_too",
            a_fault_where_success_was_expected_reports_the_detail_too,
        ),
        (
            "outcomes_compare_on_the_fault_identity_alone",
            outcomes_compare_on_the_fault_identity_alone,
        ),
    ];

    fn expected_parses_a_success_vector() {
        let parsed: Expected =
            serde_json::from_str(r#"{"outputs": [7], "final_stack": [], "steps": 42}"#)
                .expect("parse");
        assert_eq!(
            parsed,
            Expected::Success {
                outputs: vec![Some(7)],
                final_stack: vec![],
                steps: 42,
            }
        );
    }

    fn expected_rejects_a_success_vector_without_steps() {
        let err = serde_json::from_str::<Expected>(r#"{"outputs": [7], "final_stack": []}"#)
            .expect_err("a successful vector must assert its step count");
        assert!(
            err.to_string().contains("steps"),
            "error should name the missing field, got: {err}"
        );
    }

    fn expected_rejects_steps_on_an_error_vector() {
        let err = serde_json::from_str::<Expected>(r#"{"error": "DIVISION_BY_ZERO", "steps": 3}"#)
            .expect_err("a fault vector cannot assert a step count");
        assert!(
            err.to_string().contains("steps"),
            "error should name the stray field, got: {err}"
        );
    }

    fn expected_rejects_a_final_stack_on_an_error_vector() {
        let err = serde_json::from_str::<Expected>(
            r#"{"error": "DIVISION_BY_ZERO", "final_stack": [1]}"#,
        )
        .expect_err("a fault vector cannot assert a final stack");
        assert!(
            err.to_string().contains("final_stack"),
            "error should name the stray field, got: {err}"
        );
    }

    fn expected_parses_an_error_vector() {
        let parsed: Expected =
            serde_json::from_str(r#"{"error": "DIVISION_BY_ZERO"}"#).expect("parse");
        assert_eq!(
            parsed,
            Expected::Failure {
                error: Fault::DivisionByZero
            }
        );
    }

    fn expected_rejects_a_vector_asserting_both_success_and_failure() {
        let err = serde_json::from_str::<Expected>(
            r#"{"outputs": [7], "final_stack": [], "error": "DIVISION_BY_ZERO"}"#,
        )
        .expect_err("a vector cannot assert both an outcome and a fault");
        assert!(
            err.to_string().contains("both"),
            "error should name the conflict, got: {err}"
        );
    }

    fn expected_rejects_a_vector_asserting_neither() {
        let err = serde_json::from_str::<Expected>("{}")
            .expect_err("a vector must assert an outcome or a fault");
        assert!(
            err.to_string().contains("outputs"),
            "error should name the missing field, got: {err}"
        );
    }

    fn expected_rejects_an_unknown_fault_name() {
        let _ = serde_json::from_str::<Expected>(r#"{"error": "NOT_A_REAL_FAULT"}"#)
            .expect_err("unknown fault names are vector-authoring mistakes");
    }

    fn check_reports_a_fault_where_success_was_expected() {
        let actual = Outcome::Failure {
            error: Fault::DivisionByZero,
            detail: "DivisionByZero { pos: 12 }".to_owned(),
        };
        let expected = Expected::Success {
            outputs: vec![Some(1)],
            final_stack: vec![],
            steps: 1,
        };
        let err = check(&actual, &expected).expect_err("mismatch");
        assert!(err.contains("DIVISION_BY_ZERO"), "got: {err}");
    }

    fn check_reports_success_where_a_fault_was_expected() {
        let actual = Outcome::Success {
            outputs: vec![Some(1)],
            final_stack: vec![],
            steps: 3,
        };
        let expected = Expected::Failure {
            error: Fault::StepLimitExceeded,
        };
        let err = check(&actual, &expected).expect_err("mismatch");
        assert!(err.contains("STEP_LIMIT_EXCEEDED"), "got: {err}");
    }

    fn check_rejects_a_step_count_mismatch() {
        let actual = Outcome::Success {
            outputs: vec![Some(1)],
            final_stack: vec![],
            steps: 5,
        };
        let expected = Expected::Success {
            outputs: vec![Some(1)],
            final_stack: vec![],
            steps: 6,
        };
        let err = check(&actual, &expected).expect_err("mismatch");
        assert!(
            err.contains("expected: 6") && err.contains("actual:   5"),
            "got: {err}"
        );
    }

    fn check_accepts_a_matching_fault() {
        let actual = Outcome::Failure {
            error: Fault::DivisionByZero,
            detail: "DivisionByZero { pos: 12 }".to_owned(),
        };
        let expected = Expected::Failure {
            error: Fault::DivisionByZero,
        };
        check(&actual, &expected).expect("matching faults compare equal");
    }

    fn check_rejects_the_wrong_fault() {
        let actual = Outcome::Failure {
            error: Fault::StackUnderflow,
            detail: "StackUnderflow { pos: 3, needed: 2, got: 0 }".to_owned(),
        };
        let expected = Expected::Failure {
            error: Fault::DivisionByZero,
        };
        let err = check(&actual, &expected).expect_err("mismatch");
        assert!(
            err.contains("DIVISION_BY_ZERO") && err.contains("STACK_UNDERFLOW"),
            "got: {err}"
        );
    }

    fn a_fault_mismatch_reports_what_the_vm_said() {
        let actual = Outcome::Failure {
            error: Fault::ArithmeticOverflow,
            detail: "ArithmeticOverflow { pos: 49 }".to_owned(),
        };
        let expected = Expected::Failure {
            error: Fault::DivisionByZero,
        };
        let err = check(&actual, &expected).expect_err("mismatch");
        assert!(err.contains("pos: 49"), "got: {err}");
    }

    fn a_fault_where_success_was_expected_reports_the_detail_too() {
        let actual = Outcome::Failure {
            error: Fault::ArithmeticOverflow,
            detail: "ArithmeticOverflow { pos: 49 }".to_owned(),
        };
        let expected = Expected::Success {
            outputs: vec![Some(1)],
            final_stack: vec![],
            steps: 1,
        };
        let err = check(&actual, &expected).expect_err("mismatch");
        assert!(err.contains("pos: 49"), "got: {err}");
    }

    fn outcomes_compare_on_the_fault_identity_alone() {
        // What the bytecode round-trip comparison in `run` rests on: source and
        // decoded runs report the same fault from different byte offsets.
        let source = Outcome::Failure {
            error: Fault::ArithmeticOverflow,
            detail: "ArithmeticOverflow { pos: 49 }".to_owned(),
        };
        let bytecode = Outcome::Failure {
            error: Fault::ArithmeticOverflow,
            detail: "ArithmeticOverflow { pos: 12 }".to_owned(),
        };
        assert_eq!(source, bytecode);
    }
}
