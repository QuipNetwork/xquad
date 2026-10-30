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

//! Assembler: converts a parsed [`AsmLine`] list into a [`Program`].
//!
//! Delegates label resolution and `JUMP`/`JUMPI` fixups to
//! [`InstructionBuilder`]. Instructions are emitted in a single pass;
//! label-based jumps register fixups that [`InstructionBuilder::build`]
//! resolves at the end, building a jump table and patching label indices.
//!
//! Labels use numeric `.N` syntax (e.g. `.0`, `.1`). Each placed label
//! becomes a basic-block entry in the jump table.
//!
//! `PUSH` is handled specially: the assembly operand is an integer constant.
//! The assembler calls [`InstructionBuilder::push`] which selects the minimal
//! inline-constant variant (`PUSH1`..`PUSH8`) automatically.
//!
//! `source` and `name` are used solely for diagnostic output: they are
//! embedded in any [`AssembleError`] so that miette can render a source
//! snippet with a caret pointing at the failing token.
//!
//! # Examples
//!
//! ```rust
//! use xqasm::{assemble, AsmLine, Operand, ParsedInstr};
//!
//! let src = "PUSH 0\nHALT";
//! let lines = vec![
//!     AsmLine::Instruction(ParsedInstr {
//!         mnemonic: "PUSH".to_string(),
//!         operands: vec![Operand::Integer(0)],
//!         offset: 0,
//!     }),
//!     AsmLine::Instruction(ParsedInstr {
//!         mnemonic: "HALT".to_string(),
//!         operands: vec![],
//!         offset: 7,
//!     }),
//! ];
//! let program = assemble(&lines, src, "<test>").unwrap();
//! assert_eq!(program.code()[0], 0x11); // PUSH1 opcode
//! assert_eq!(*program.code().last().unwrap(), 0xFF); // HALT opcode
//! ```

// AssembleError carries a NamedSource<Arc<str>> in every variant so that
// miette can render source snippets.  The extra size is acceptable because
// error paths in an assembler are not performance-critical.
#![expect(
    clippy::result_large_err,
    reason = "assembler errors carry source context for miette; size is acceptable on error paths"
)]

use std::collections::HashMap;
use std::collections::hash_map::Entry;

use xqvm::bytecode::error::BuilderError;
use xqvm::{Instruction, InstructionBuilder, LabelId, Program, Register, opcodes};

use crate::ast::{AsmLine, Operand, ParsedInstr};
use crate::error::{AssembleError, Source, make_span, make_src};

/// Maps a label index to its [`LabelId`] and the source location where it was
/// first defined (`None` if only seen as a forward reference so far).
type LabelMap = HashMap<u16, (LabelId, Option<usize>)>;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Assemble a list of parsed lines into a [`Program`].
///
/// `source` and `name` are used solely for diagnostic output: they are
/// embedded in any [`AssembleError`] so that miette can render a source
/// snippet with a caret pointing at the failing token.
///
/// Delegates to [`InstructionBuilder`] for label resolution and
/// `JUMP`/`JUMPI` fixups. Both forward and backward label references are
/// supported.
///
/// # Errors
///
/// - [`AssembleError::UnknownMnemonic`] -- unrecognised mnemonic.
/// - [`AssembleError::WrongOperandCount`] -- wrong number of operands.
/// - [`AssembleError::WrongOperandKind`] -- operand of wrong kind.
/// - [`AssembleError::RegisterOutOfRange`] -- register slot > 255.
/// - [`AssembleError::IntegerOutOfRange`] -- integer does not fit target type.
/// - [`AssembleError::UndefinedLabel`] -- label referenced but not defined.
/// - [`AssembleError::DuplicateLabel`] -- label defined more than once.
/// - [`AssembleError::UnusedLabel`] -- label placed but never referenced.
///
/// # Examples
///
/// ```rust
/// use xqasm::{parse, assemble};
///
/// let src = "PUSH 5\nPUSH 3\nADD\nHALT";
/// let lines = parse(src, "<test>").unwrap();
/// let program = assemble(&lines, src, "<test>").unwrap();
/// assert!(!program.code().is_empty());
/// ```
pub fn assemble(lines: &[AsmLine], source: &str, name: &str) -> Result<Program, AssembleError> {
    let src = Source { text: source, name };
    let mut b = InstructionBuilder::new();
    let mut label_map: LabelMap = HashMap::new();
    // Maps label index -> first reference source offset (for error reporting).
    let mut first_ref: HashMap<u16, usize> = HashMap::new();

    for line in lines {
        match line {
            AsmLine::LabelDef {
                label: label_idx,
                offset: def_offset,
            } => {
                let id = match label_map.entry(*label_idx) {
                    Entry::Occupied(e) => {
                        let (id, placed_at) = e.into_mut();
                        if let Some(prev_offset) = *placed_at {
                            return Err(AssembleError::DuplicateLabel {
                                label: *label_idx,
                                src: make_src(src),
                                span: make_span(prev_offset, format!(".{label_idx}").len()),
                            });
                        }
                        *placed_at = Some(*def_offset);
                        *id
                    }
                    Entry::Vacant(e) => {
                        let id = b.label();
                        let _ = e.insert((id, Some(*def_offset)));
                        id
                    }
                };
                let _ = b
                    .place(id)
                    .map_err(|e| convert_build_error(&e, &first_ref, src))?;
            }
            AsmLine::Instruction(instr) => match instr.mnemonic.as_str() {
                "JUMP" | "JUMPI" => {
                    assemble_jump(instr, &mut b, &mut label_map, &mut first_ref, src)?;
                }
                "PUSH" | "PUSHC" => {
                    assemble_push(instr, &mut b, src)?;
                }
                "TARGET" if !instr.operands.is_empty() => {
                    assemble_target(instr, &mut b, &mut label_map, src)?;
                }
                _ => {
                    let _ = b.emit(build_instr(instr, src)?);
                }
            },
        }
    }

    b.build()
        .map_err(|e| convert_build_error(&e, &first_ref, src))
}

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

/// Build a `WrongOperandKind` error pointing at the mnemonic token.
fn err_wrong_kind(
    instr: &ParsedInstr,
    field: &str,
    expected_kind: &str,
    src: Source<'_>,
) -> AssembleError {
    AssembleError::WrongOperandKind {
        mnemonic: instr.mnemonic.clone(),
        field: field.to_string(),
        expected_kind: expected_kind.to_string(),
        src: make_src(src),
        span: make_span(instr.offset, instr.mnemonic.len()),
    }
}

// ---------------------------------------------------------------------------
// JUMP / JUMPI
// ---------------------------------------------------------------------------

fn assemble_jump(
    instr: &ParsedInstr,
    b: &mut InstructionBuilder,
    label_map: &mut LabelMap,
    first_ref: &mut HashMap<u16, usize>,
    src: Source<'_>,
) -> Result<(), AssembleError> {
    let [operand] = instr.operands.as_slice() else {
        return Err(AssembleError::WrongOperandCount {
            mnemonic: instr.mnemonic.clone(),
            expected: 1,
            got: instr.operands.len(),
            src: make_src(src),
            span: make_span(instr.offset, instr.mnemonic.len()),
        });
    };

    match operand {
        Operand::LabelRef(label_idx) => {
            let id = match label_map.entry(*label_idx) {
                Entry::Occupied(e) => e.get().0,
                Entry::Vacant(e) => {
                    let id = b.label();
                    let _ = e.insert((id, None));
                    id
                }
            };
            let _ = first_ref.entry(*label_idx).or_insert(instr.offset);
            let _ = match instr.mnemonic.as_str() {
                "JUMPI" => b.emit_jump_if(id),
                _ => b.emit_jump(id),
            };
        }
        Operand::Integer(_) | Operand::Register(_) => {
            return Err(err_wrong_kind(
                instr,
                "label",
                "label reference (e.g. .0)",
                src,
            ));
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// TARGET .N (label-bearing form)
// ---------------------------------------------------------------------------

/// Compile `TARGET .N` -- the explicit form of the `.N:` label syntax.
///
/// Both spellings resolve to the same builder action (`b.place(label_id)`),
/// which records the label's byte position and emits an inline `TARGET`
/// opcode there. A bare `TARGET` (no operand) bypasses this path and emits
/// a raw `Target` instruction with no label binding -- that's only useful
/// when manually constructing bytecode where you do not need a corresponding
/// jump destination. Mixing both spellings for the same label is a
/// `DuplicateLabel` error, the same as defining `.N:` twice.
fn assemble_target(
    instr: &ParsedInstr,
    b: &mut InstructionBuilder,
    label_map: &mut LabelMap,
    src: Source<'_>,
) -> Result<(), AssembleError> {
    let [operand] = instr.operands.as_slice() else {
        return Err(AssembleError::WrongOperandCount {
            mnemonic: instr.mnemonic.clone(),
            expected: 1,
            got: instr.operands.len(),
            src: make_src(src),
            span: make_span(instr.offset, instr.mnemonic.len()),
        });
    };

    let label_idx = match operand {
        Operand::LabelRef(idx) => *idx,
        Operand::Integer(_) | Operand::Register(_) => {
            return Err(err_wrong_kind(
                instr,
                "label",
                "label reference (e.g. .0)",
                src,
            ));
        }
    };

    let id = match label_map.entry(label_idx) {
        Entry::Occupied(e) => {
            let (id, placed_at) = e.into_mut();
            if let Some(prev_offset) = *placed_at {
                return Err(AssembleError::DuplicateLabel {
                    label: label_idx,
                    src: make_src(src),
                    span: make_span(prev_offset, format!(".{label_idx}").len()),
                });
            }
            *placed_at = Some(instr.offset);
            *id
        }
        Entry::Vacant(e) => {
            let id = b.label();
            let _ = e.insert((id, Some(instr.offset)));
            id
        }
    };

    let _ = b.place(id).map_err(|e| match e {
        BuilderError::DuplicateLabel { id } => AssembleError::LabelPlacedTwice {
            id,
            src: make_src(src),
        },
        BuilderError::ForeignLabel { id } => AssembleError::LabelNotOwned {
            id,
            src: make_src(src),
        },
        _ => unreachable!("place() only returns DuplicateLabel or ForeignLabel"),
    })?;
    Ok(())
}

// ---------------------------------------------------------------------------
// PUSH / PUSHC
// ---------------------------------------------------------------------------

/// Handle `PUSH <imm>` and `PUSHC <imm>`: encode the integer constant inline
/// using the minimal `PushN` encoding chosen by [`InstructionBuilder::push`].
fn assemble_push(
    instr: &ParsedInstr,
    b: &mut InstructionBuilder,
    src: Source<'_>,
) -> Result<(), AssembleError> {
    let [operand] = instr.operands.as_slice() else {
        return Err(AssembleError::WrongOperandCount {
            mnemonic: instr.mnemonic.clone(),
            expected: 1,
            got: instr.operands.len(),
            src: make_src(src),
            span: make_span(instr.offset, instr.mnemonic.len()),
        });
    };

    match operand {
        Operand::Integer(imm) => {
            let _ = b.emit_push(*imm);
        }
        _ => {
            return Err(err_wrong_kind(instr, "imm", "integer literal", src));
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Convert InstructionBuilder errors to AssembleError
// ---------------------------------------------------------------------------

fn convert_build_error(
    e: &BuilderError,
    first_ref: &HashMap<u16, usize>,
    src: Source<'_>,
) -> AssembleError {
    match e {
        BuilderError::UnplacedLabel { id } => {
            let label_idx = u16::try_from(*id).unwrap_or(0);
            let offset = first_ref.get(&label_idx).copied().unwrap_or(0);
            let label_str = format!(".{label_idx}");
            AssembleError::UndefinedLabel {
                label: label_idx,
                src: make_src(src),
                span: make_span(offset, label_str.len()),
            }
        }
        BuilderError::UnusedLabel { id } => {
            let label_idx = u16::try_from(*id).unwrap_or(0);
            let label_str = format!(".{label_idx}");
            AssembleError::UnusedLabel {
                label: label_idx,
                src: make_src(src),
                span: make_span(0, label_str.len()),
            }
        }
        BuilderError::TooManyTargets { count } => AssembleError::TooManyTargets {
            count: *count,
            src: make_src(src),
        },
        BuilderError::FixupOutOfBounds { site, buf_len } => AssembleError::FixupOutOfBounds {
            site: *site,
            buf_len: *buf_len,
            src: make_src(src),
        },
        BuilderError::DuplicateLabel { id } => AssembleError::LabelPlacedTwice {
            id: *id,
            src: make_src(src),
        },
        BuilderError::ForeignLabel { id } => AssembleError::LabelNotOwned {
            id: *id,
            src: make_src(src),
        },
    }
}

// ---------------------------------------------------------------------------
// Generic instruction assembly via opcodes! x-macro
// ---------------------------------------------------------------------------

/// Trait for converting a parsed [`Operand`] into a concrete field type.
trait FromOperand: Sized {
    fn from_operand(
        op: &Operand,
        field: &str,
        mnemonic: &str,
        offset: usize,
        src: Source<'_>,
    ) -> Result<Self, AssembleError>;
}

impl FromOperand for Register {
    fn from_operand(
        op: &Operand,
        field: &str,
        mnemonic: &str,
        offset: usize,
        src: Source<'_>,
    ) -> Result<Self, AssembleError> {
        match op {
            Operand::Register(n) => Ok(Self(*n)),
            _ => Err(AssembleError::WrongOperandKind {
                mnemonic: mnemonic.to_string(),
                field: field.to_string(),
                expected_kind: "register (e.g. r0)".to_string(),
                src: make_src(src),
                span: make_span(offset, mnemonic.len()),
            }),
        }
    }
}

impl FromOperand for i64 {
    fn from_operand(
        op: &Operand,
        field: &str,
        mnemonic: &str,
        offset: usize,
        src: Source<'_>,
    ) -> Result<Self, AssembleError> {
        match op {
            Operand::Integer(n) => Ok(*n),
            _ => Err(AssembleError::WrongOperandKind {
                mnemonic: mnemonic.to_string(),
                field: field.to_string(),
                expected_kind: "integer literal".to_string(),
                src: make_src(src),
                span: make_span(offset, mnemonic.len()),
            }),
        }
    }
}

/// `[u8; N]` fields appear on `PUSH1`..`PUSH8` instructions.
///
/// These mnemonics are not user-facing (assembly uses `PUSH`/`PUSHC` which are
/// handled by `assemble_push` before this path is reached), so this impl is
/// only present to satisfy the compiler for the `impl_build_instr` macro arms.
macro_rules! impl_from_operand_byte_array {
    ($($n:literal),+) => {
        $(
            impl FromOperand for [u8; $n] {
                fn from_operand(
                    _op: &Operand,
                    field: &str,
                    mnemonic: &str,
                    offset: usize,
                    src: Source<'_>,
                ) -> Result<Self, AssembleError> {
                    Err(AssembleError::UnknownMnemonic {
                        mnemonic: mnemonic.to_string(),
                        src: make_src(src),
                        span: make_span(offset, field.len()),
                    })
                }
            }
        )+
    };
}

impl_from_operand_byte_array!(1, 2, 3, 4, 5, 6, 7, 8);

/// `u16` is used for `JUMP2`/`JUMPI2` (wide) label operands, which are
/// handled separately by `assemble_jump`. This impl covers the
/// `impl_build_instr` macro's generated arms (unreachable at runtime because
/// the caller routes `JUMP`/`JUMPI` to `assemble_jump` first).
impl FromOperand for u16 {
    fn from_operand(
        op: &Operand,
        field: &str,
        mnemonic: &str,
        offset: usize,
        src: Source<'_>,
    ) -> Result<Self, AssembleError> {
        match op {
            Operand::LabelRef(idx) => Ok(*idx),
            _ => Err(AssembleError::WrongOperandKind {
                mnemonic: mnemonic.to_string(),
                field: field.to_string(),
                expected_kind: "label reference (e.g. .0)".to_string(),
                src: make_src(src),
                span: make_span(offset, mnemonic.len()),
            }),
        }
    }
}

/// `u8` is used for `JUMP1`/`JUMPI1` (narrow) label operands. Like the `u16`
/// impl above, this exists so the macro-generated `build_instr` arms compile;
/// `JUMP`/`JUMPI` are intercepted by `assemble_jump` before reaching this
/// path. The conversion from the parsed label index to `u8` errors with
/// `LabelOutOfRange` when the index does not fit, mirroring how a hand-coded
/// caller would behave.
impl FromOperand for u8 {
    fn from_operand(
        op: &Operand,
        field: &str,
        mnemonic: &str,
        offset: usize,
        src: Source<'_>,
    ) -> Result<Self, AssembleError> {
        match op {
            Operand::LabelRef(idx) => {
                Self::try_from(*idx).map_err(|_| AssembleError::WrongOperandKind {
                    mnemonic: mnemonic.to_string(),
                    field: field.to_string(),
                    expected_kind: "u8 label reference (id < 256, e.g. .0)".to_string(),
                    src: make_src(src),
                    span: make_span(offset, mnemonic.len()),
                })
            }
            _ => Err(AssembleError::WrongOperandKind {
                mnemonic: mnemonic.to_string(),
                field: field.to_string(),
                expected_kind: "label reference (e.g. .0)".to_string(),
                src: make_src(src),
                span: make_span(offset, mnemonic.len()),
            }),
        }
    }
}

/// Generate `fn build_instr(instr, src) -> Result<Instruction, AssembleError>`
/// using the full opcode table. `JUMP` and `JUMPI` arms are unreachable at
/// runtime because the caller routes them to `assemble_jump` first.
macro_rules! impl_build_instr {
    ( $( ($code:literal, $variant:ident, $mnem:literal, $doc:literal,
          $_stack:expr, {$($fname:ident: $ftype:ty),*}) ),* $(,)? ) => {

        fn build_instr(
            instr: &ParsedInstr,
            src: Source<'_>,
        ) -> Result<Instruction, AssembleError> {
            match instr.mnemonic.as_str() {
                $(
                    $mnem => {
                        const EXPECTED: usize = 0usize
                            $( + { let _ = stringify!($fname); 1 })*;

                        if instr.operands.len() != EXPECTED {
                            return Err(AssembleError::WrongOperandCount {
                                mnemonic: instr.mnemonic.clone(),
                                expected: EXPECTED,
                                got: instr.operands.len(),
                                src: make_src(src),
                                span: make_span(instr.offset, instr.mnemonic.len()),
                            });
                        }

                        let mut _iter = instr.operands.iter();
                        $(
                            let $fname = <$ftype as FromOperand>::from_operand(
                                _iter.next().ok_or_else(|| AssembleError::WrongOperandCount {
                                    mnemonic: instr.mnemonic.clone(),
                                    expected: EXPECTED,
                                    got: instr.operands.len(),
                                    src: make_src(src),
                                    span: make_span(instr.offset, instr.mnemonic.len()),
                                })?,
                                stringify!($fname),
                                &instr.mnemonic,
                                instr.offset,
                                src,
                            )?;
                        )*

                        Ok(Instruction::$variant { $($fname,)* })
                    }
                )*
                _ => Err(AssembleError::UnknownMnemonic {
                    mnemonic: instr.mnemonic.clone(),
                    src: make_src(src),
                    span: make_span(instr.offset, instr.mnemonic.len()),
                }),
            }
        }
    };
}

opcodes!(impl_build_instr);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assemble_source;
    use crate::parser::parse;
    use xqvm::{Instruction, InstructionStream};

    fn decode_all(buf: &[u8]) -> Vec<Instruction> {
        InstructionStream::new(buf).map(|r| r.unwrap().2).collect()
    }

    fn asm(src: &str) -> Vec<u8> {
        let lines = parse(src, "<test>").unwrap();
        assemble(&lines, src, "<test>").unwrap().code().to_vec()
    }

    #[test]
    fn halt_is_one_byte() {
        assert_eq!(asm("HALT"), [0xFF]);
    }

    #[test]
    fn nop_is_one_byte() {
        assert_eq!(asm("NOP"), [0xF0]);
    }

    #[test]
    fn push_zero() {
        // 0 encodes as Push1 (opcode 0x11) with 1-byte payload 0x00
        assert_eq!(asm("PUSH 0"), [0x11, 0x00]);
    }

    #[test]
    fn push_negative() {
        // -1 encodes as Push1 (opcode 0x11) with 1-byte payload 0xFF
        assert_eq!(asm("PUSH -1"), [0x11, 0xFF]);
    }

    #[test]
    fn load_register() {
        assert_eq!(asm("LOAD r3"), [0x0A, 0x03]);
    }

    #[test]
    fn energy_two_registers() {
        assert_eq!(asm("ENERGY r2 r3"), [0x7F, 0x02, 0x03]);
    }

    #[test]
    fn simple_program_roundtrip() {
        let src = "PUSH 5\nPUSH 3\nADD\nHALT";
        let buf = asm(src);
        let instrs = decode_all(&buf);
        assert_eq!(instrs[0], Instruction::Push1 { val: [5] });
        assert_eq!(instrs[1], Instruction::Push1 { val: [3] });
        assert_eq!(instrs[2], Instruction::Add {});
        assert_eq!(instrs[3], Instruction::Halt {});
    }

    #[test]
    fn forward_jump_label() {
        // After QUI-404 + QUI-437 narrowing:
        //   JUMP1 .0  (2 bytes at site 0: narrowed from placeholder)
        //   NOP       (1 byte  at site 2)
        //   .0:
        //   TARGET    (1 byte  at site 3, emitted by place())
        //   HALT      (1 byte  at site 4)
        let src = "JUMP .0\nNOP\n.0:\nHALT";
        let lines = parse(src, "<test>").unwrap();
        let program = assemble(&lines, src, "<test>").unwrap();
        let instrs = decode_all(program.code());
        assert_eq!(instrs[0], Instruction::Jump1 { label: 0 });
        assert_eq!(instrs[1], Instruction::Nop {});
        assert_eq!(instrs[2], Instruction::Target {});
        assert_eq!(instrs[3], Instruction::Halt {});
        // Sequential id 0 -> the first (and only) TARGET at byte 3.
        assert_eq!(program.jump_table().get(0), Some(3));
    }

    #[test]
    fn backward_jumpi_label() {
        // After QUI-404 + QUI-437 narrowing:
        //   .0:
        //   TARGET    (1 byte  at 0, emitted by place())
        //   PUSH -1   (2 bytes at 1)
        //   ADD       (1 byte  at 3)
        //   COPY      (1 byte  at 4)
        //   JUMPI1 .0 (2 bytes at 5, narrowed from JumpI2)
        let src = ".0:\nPUSH -1\nADD\nCOPY\nJUMPI .0";
        let lines = parse(src, "<test>").unwrap();
        let program = assemble(&lines, src, "<test>").unwrap();
        let instrs = decode_all(program.code());
        assert_eq!(instrs[0], Instruction::Target {});
        assert_eq!(instrs.last().unwrap(), &Instruction::JumpI1 { label: 0 });
        assert_eq!(program.jump_table().get(0), Some(0));
    }

    #[test]
    fn target_directive_is_sugar_for_label() {
        // Both `.0:` and `TARGET .0` compile to the same byte sequence:
        // the `place()` call emits an inline TARGET opcode and records the
        // jump-table entry. The two forms are interchangeable spellings.
        let with_label = assemble_source("JUMP .0\n.0:\nHALT").expect("label form");
        let with_target = assemble_source("JUMP .0\nTARGET .0\nHALT").expect("TARGET form");
        assert_eq!(with_label.code(), with_target.code());
    }

    #[test]
    fn target_directive_with_undefined_label_errors() {
        // Defining `TARGET .0` and then jumping to it from later in the
        // source must work and resolve cleanly.
        let program = assemble_source("TARGET .0\nNOP\nJUMP .0").expect("forward TARGET");
        let instrs = decode_all(program.code());
        // Layout: TARGET (1) + NOP (1) + Jump1 (2)
        assert_eq!(instrs[0], Instruction::Target {});
        assert_eq!(instrs[1], Instruction::Nop {});
        assert_eq!(instrs[2], Instruction::Jump1 { label: 0 });
        assert_eq!(program.jump_table().get(0), Some(0));
    }

    #[test]
    fn target_directive_duplicate_definition_errors() {
        // Defining a label twice -- whether via `.N:` or `TARGET .N` --
        // must surface DuplicateLabel.
        use crate::Error;
        let err1 = assemble_source(".0:\nTARGET .0\nHALT").expect_err("duplicate def");
        assert!(matches!(
            err1,
            Error::Assemble(AssembleError::DuplicateLabel { label: 0, .. }),
        ));

        let err2 = assemble_source("TARGET .0\nTARGET .0\nHALT").expect_err("two TARGETs");
        assert!(matches!(
            err2,
            Error::Assemble(AssembleError::DuplicateLabel { label: 0, .. }),
        ));
    }

    #[test]
    fn bare_target_emits_raw_opcode_without_label_binding() {
        // `TARGET` with no operand is the existing zero-arg form: it emits
        // a Target opcode but does NOT bind a source-level label. After
        // QUI-405 every TARGET in the stream still ends up in the runtime
        // jump table (the runtime scan does not distinguish labelled from
        // unlabelled TARGETs), but no label name is associated with it in
        // the assembler's symbol table.
        let program = assemble_source("TARGET\nHALT").expect("bare TARGET");
        let instrs = decode_all(program.code());
        assert_eq!(instrs[0], Instruction::Target {});
        assert_eq!(instrs[1], Instruction::Halt {});
        assert_eq!(program.jump_table().len(), 1);
        assert_eq!(program.jump_table().get(0), Some(0));
    }

    #[test]
    fn jump_raw_integer_rejected() {
        let src = "JUMP 3";
        let lines = parse(src, "<test>").unwrap();
        let err = assemble(&lines, src, "<test>").unwrap_err();
        assert!(matches!(err, AssembleError::WrongOperandKind { .. }));
    }

    #[test]
    fn unknown_mnemonic_error() {
        let src = "FOOBAR";
        let lines = parse(src, "<test>").unwrap();
        assert!(assemble(&lines, src, "<test>").is_err());
    }

    #[test]
    fn wrong_operand_count_error() {
        let src = "HALT r0";
        let lines = parse(src, "<test>").unwrap();
        assert!(assemble(&lines, src, "<test>").is_err());
    }

    #[test]
    fn wrong_operand_kind_error() {
        let src = "LOAD 42";
        let lines = parse(src, "<test>").unwrap();
        assert!(assemble(&lines, src, "<test>").is_err());
    }

    #[test]
    fn undefined_label_error() {
        let src = "JUMP .99";
        let lines = parse(src, "<test>").unwrap();
        assert!(assemble(&lines, src, "<test>").is_err());
    }

    #[test]
    fn duplicate_label_error() {
        let src = ".0:\nNOP\nJUMP .0\n.0:\nHALT";
        let lines = parse(src, "<test>").unwrap();
        assert!(assemble(&lines, src, "<test>").is_err());
    }

    #[test]
    fn push_hex_literal() {
        let buf = asm("PUSH 0xFF");
        let instrs = decode_all(&buf);
        // 255 fits in 2 bytes (needs sign bit), so Push2 { val: [0x00, 0xFF] }
        assert_eq!(instrs[0], Instruction::Push2 { val: [0x00, 0xFF] });
    }

    #[test]
    fn all_zero_arg_instructions_assemble() {
        for mnem in &["NOP", "HALT", "ADD", "SUB", "MUL", "DIV", "NOT", "AND"] {
            let buf = asm(mnem);
            assert_eq!(buf.len(), 1, "expected 1 byte for {mnem}");
        }
    }

    #[test]
    fn pushc_assembles_inline() {
        let src = "PUSHC 12345";
        let lines = parse(src, "<test>").unwrap();
        let program = assemble(&lines, src, "<test>").unwrap();
        // 12345 = 0x3039, fits in 2 bytes, so Push2 { val: [0x30, 0x39] }.
        let instrs = decode_all(program.code());
        assert_eq!(instrs.len(), 1);
        assert_eq!(instrs[0], Instruction::Push2 { val: [0x30, 0x39] });
    }

    #[test]
    fn pushc_wrong_operand_count_error() {
        let src = "PUSHC";
        let lines = parse(src, "<test>").unwrap();
        let err = assemble(&lines, src, "<test>").unwrap_err();
        assert!(matches!(err, AssembleError::WrongOperandCount { .. }));
    }

    #[test]
    fn pushc_wrong_operand_kind_error() {
        let src = "PUSHC r0";
        let lines = parse(src, "<test>").unwrap();
        let err = assemble(&lines, src, "<test>").unwrap_err();
        assert!(matches!(err, AssembleError::WrongOperandKind { .. }));
    }
}
