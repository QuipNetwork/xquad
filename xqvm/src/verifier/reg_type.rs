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

use crate::Register;
use crate::bytecode::Instruction;

use super::error::VerifierError;

/// Static type tag used during register type-state analysis.
///
/// Mirrors the [`crate::RegVal`] variants without carrying any value, plus an
/// [`Any`](Self::Any) variant for registers whose type is known to be set but
/// cannot be determined statically (e.g. written by `INPUT` or `LVAL`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum RegType {
    /// Register has never been written (default state).
    #[default]
    Unset,
    /// Holds an `i64` integer.
    Int,
    /// Holds a `Vec<i64>` integer vector.
    VecInt,
    /// Holds a `Vec<XqmxModel>` model vector.
    VecXqmx,
    /// Holds an `XqmxModel`.
    Model,
    /// Holds an `XqmxSample`.
    Sample,
    /// A `Model` or a `Sample`, which one not known statically. Produced by
    /// a branch join of the two, and satisfies only what they share: the
    /// grid surface, not `Model` or `Sample` on its own.
    Grid,
    /// A `VecInt` or a `VecXqmx`, which one not known statically. The vec
    /// twin of [`Grid`](Self::Grid); satisfies `AnyVec` but not `VecInt`.
    AnyVec,
    /// A branch join of two types with nothing in common. The register is
    /// set, and that is all that is known: every requirement past
    /// `NonUnset` would fault at run time on at least one of the joined
    /// paths.
    Conflict,
    /// Set to an unknown type (e.g. after `INPUT` or `LVAL`). Satisfies any
    /// read requirement so the verifier does not emit false positives.
    Any,
}

impl RegType {
    /// Human-readable name used in [`VerifierError`] messages.
    pub fn type_name(self) -> &'static str {
        match self {
            Self::Unset => "unset",
            Self::Int => "int",
            Self::VecInt => "vec<int>",
            Self::VecXqmx => "vec<xqmx>",
            Self::Model => "model",
            Self::Sample => "sample",
            Self::Grid => "model or sample",
            Self::AnyVec => "vec<int> or vec<xqmx>",
            Self::Conflict => "conflicting types",
            Self::Any => "any",
        }
    }

    /// Whether this type satisfies `req`.
    ///
    /// `Unset` never satisfies any requirement. `Any` always does (it is
    /// non-unset, just of unknown specific type). All other types match
    /// only the requirements they implement.
    ///
    /// [`Grid`](Self::Grid) and [`AnyVec`](Self::AnyVec) stand for a pair
    /// whose member is not known statically, so each matches exactly the
    /// intersection of what its two members match. That is the whole point
    /// of having them: a join of `Model` and `Sample` that collapsed to
    /// `Any` would satisfy `Model`, and a program could reach `SETQUAD`
    /// with a sample without the verifier objecting (QUI-1160).
    /// [`Conflict`](Self::Conflict) is the same rule for a pair that shares
    /// nothing: the intersection is `NonUnset` alone.
    pub(crate) fn satisfies(self, req: RegTypeReq) -> bool {
        match self {
            Self::Unset => false,
            Self::Any => true,
            Self::Int => matches!(req, RegTypeReq::NonUnset | RegTypeReq::Int),
            Self::VecInt => {
                matches!(
                    req,
                    RegTypeReq::NonUnset | RegTypeReq::VecInt | RegTypeReq::AnyVec
                )
            }
            Self::VecXqmx | Self::AnyVec => {
                matches!(req, RegTypeReq::NonUnset | RegTypeReq::AnyVec)
            }
            Self::Model => {
                matches!(
                    req,
                    RegTypeReq::NonUnset | RegTypeReq::Model | RegTypeReq::Grid
                )
            }
            Self::Sample => {
                matches!(
                    req,
                    RegTypeReq::NonUnset | RegTypeReq::Sample | RegTypeReq::Grid
                )
            }
            Self::Grid => matches!(req, RegTypeReq::NonUnset | RegTypeReq::Grid),
            Self::Conflict => matches!(req, RegTypeReq::NonUnset),
        }
    }
}

/// Type requirement at a register read site.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RegTypeReq {
    /// Any non-`Unset` value (e.g. `OUTPUT`, `LOAD` raw check).
    NonUnset,
    /// Specifically `Int` (e.g. `LOAD`).
    Int,
    /// Specifically `Model`.
    Model,
    /// Specifically `Sample`.
    Sample,
    /// Specifically `VecInt`.
    VecInt,
    /// `VecInt` or `VecXqmx` (e.g. `ITER`, `VECLEN`).
    AnyVec,
    /// `Model` or `Sample` (XQMX grid operations: `RESIZE`, `ROWFIND`, …).
    Grid,
}

impl RegTypeReq {
    pub(crate) fn type_name(self) -> &'static str {
        match self {
            Self::NonUnset => "any",
            Self::Int => "int",
            Self::Model => "model",
            Self::Sample => "sample",
            Self::VecInt => "vec<int>",
            Self::AnyVec => "vec",
            Self::Grid => "model or sample",
        }
    }
}

/// Check that `slot` satisfies `req`; emit the appropriate [`VerifierError`].
pub(crate) fn check_reg(
    offset: usize,
    slot: u8,
    req: RegTypeReq,
    regs: &[RegType; 256],
) -> Result<(), VerifierError> {
    // slot is u8 (0–255) and regs has exactly 256 elements, so get() never returns None.
    debug_assert!(
        usize::from(slot) < regs.len(),
        "slot u8 always fits in [RegType; 256]"
    );
    let current = regs.get(usize::from(slot)).copied().unwrap_or_default();
    if current == RegType::Unset {
        return Err(VerifierError::ReadUnsetRegister { offset, reg: slot });
    }
    if !current.satisfies(req) {
        return Err(VerifierError::RegisterTypeMismatch {
            offset,
            reg: slot,
            expected: req.type_name(),
            got: current.type_name(),
        });
    }
    Ok(())
}

/// Validate register type requirements for all register-reading instructions.
pub(crate) fn check_reads(
    pos: usize,
    instr: &Instruction,
    regs: &[RegType; 256],
) -> Result<(), VerifierError> {
    use RegTypeReq as R;
    match instr {
        // Register I/O
        Instruction::Load { reg } => check_reg(pos, reg.slot(), R::Int, regs)?,
        Instruction::Output { reg } => check_reg(pos, reg.slot(), R::NonUnset, regs)?,

        // ITER and VECLEN accept both VecInt and VecXqmx.
        Instruction::Iter { reg } | Instruction::VecLen { reg } => {
            check_reg(pos, reg.slot(), R::AnyVec, regs)?;
        }

        // Vector operations that specifically require VecInt
        Instruction::VecGet { reg }
        | Instruction::VecSet { reg }
        | Instruction::VecPush { reg } => check_reg(pos, reg.slot(), R::VecInt, regs)?,
        Instruction::Slack { indices, coeffs } => {
            check_reg(pos, indices.slot(), R::VecInt, regs)?;
            check_reg(pos, coeffs.slot(), R::VecInt, regs)?;
        }

        // Quadratic coefficient access and the high-level constraint helpers
        // require Model. A coupling term and a penalty expansion are both
        // statements about the problem, and a sample carries no place to put
        // one: it holds one value per variable and nothing else.
        Instruction::GetQuad { reg }
        | Instruction::SetQuad { reg }
        | Instruction::AddQuad { reg }
        | Instruction::OneHotR { reg }
        | Instruction::OneHotC { reg }
        | Instruction::Exclude { reg }
        | Instruction::Implies { reg } => check_reg(pos, reg.slot(), R::Model, regs)?,

        // Linear coefficient access and the grid operations accept both Model
        // and Sample. A sample's dense values and a model's sparse biases are
        // the same addressable surface, which is what `spec/xqvm/ISA.md`
        // states and what both interpreters have done since QUI-454 and
        // QUI-461. This rule was the lone dissenter until QUI-1168: it
        // rejected every program that wrote into a sample, which made the
        // sample-domain check unreachable through a verified program.
        Instruction::GetLine { reg }
        | Instruction::SetLine { reg }
        | Instruction::AddLine { reg }
        | Instruction::Resize { reg }
        | Instruction::RowFind { reg }
        | Instruction::ColFind { reg }
        | Instruction::RowSum { reg }
        | Instruction::ColSum { reg } => check_reg(pos, reg.slot(), R::Grid, regs)?,

        // Constraint instructions that read multiple registers
        Instruction::Equality {
            model,
            indices,
            coeffs,
        }
        | Instruction::AtLeastW {
            model,
            indices,
            coeffs,
        } => {
            check_reg(pos, model.slot(), R::Model, regs)?;
            check_reg(pos, indices.slot(), R::VecInt, regs)?;
            check_reg(pos, coeffs.slot(), R::VecInt, regs)?;
        }
        Instruction::AtLeast { model, indices } => {
            check_reg(pos, model.slot(), R::Model, regs)?;
            check_reg(pos, indices.slot(), R::VecInt, regs)?;
        }
        Instruction::Reduce { model } => check_reg(pos, model.slot(), R::Model, regs)?,

        // ENERGY requires Model + Sample (sample slot must be Sample, not Model).
        Instruction::Energy { model, sample } => {
            check_reg(pos, model.slot(), R::Model, regs)?;
            check_reg(pos, sample.slot(), R::Sample, regs)?;
        }

        _ => {}
    }
    Ok(())
}

/// The register write effect of `instr`, if it writes a register.
///
/// Returns `(register, new_type)` for instructions that set a register's type.
/// Returns `None` for instructions that do not write any register.
pub(crate) fn write_effect(instr: &Instruction) -> Option<(Register, RegType)> {
    Some(match instr {
        Instruction::Stow { reg } | Instruction::Lidx { reg } => (*reg, RegType::Int),
        Instruction::Drop { reg } => (*reg, RegType::Unset),
        Instruction::Input { reg } | Instruction::LVal { reg } => (*reg, RegType::Any),
        Instruction::Bqmx { reg } | Instruction::Sqmx { reg } | Instruction::Xqmx { reg } => {
            (*reg, RegType::Model)
        }
        Instruction::Bsmx { reg } | Instruction::Ssmx { reg } | Instruction::Xsmx { reg } => {
            (*reg, RegType::Sample)
        }
        Instruction::Vec { reg } | Instruction::VecI { reg } => (*reg, RegType::VecInt),
        Instruction::VecX { reg } => (*reg, RegType::VecXqmx),
        _ => return None,
    })
}

/// Apply register write effects: update `regs` to reflect what `instr` writes.
///
/// Only instructions that change a register's type (or set it for the first
/// time) appear here. Read-modify-write instructions that preserve type
/// (e.g. `VECPUSH`, `SETLINE`) are intentionally absent.
pub(crate) fn apply_writes(instr: &Instruction, regs: &mut [RegType; 256]) {
    if let Some((reg, reg_type)) = write_effect(instr)
        && let Some(slot) = regs.get_mut(usize::from(reg.slot()))
    {
        *slot = reg_type;
    }
}
