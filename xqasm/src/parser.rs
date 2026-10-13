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

//! Pest-based parser for XQVM assembly source.
//!
//! Converts raw text into a flat list of [`AsmLine`] values.  No semantic
//! validation is done here; the assembler handles unknown mnemonics, wrong
//! operand counts, and similar errors.
//!
//! # Examples
//!
//! ```rust
//! use xqasm::{parse, AsmLine, Operand};
//!
//! let lines = parse("PUSH 42\nHALT", "<test>").unwrap();
//! assert_eq!(lines.len(), 2);
//! ```

use pest::Parser;

use crate::ast::{AsmLine, Operand, ParsedInstr};
use crate::error::{ParseError, Source, make_span, make_src};

// ---------------------------------------------------------------------------
// Pest parser generated from grammar.pest
// ---------------------------------------------------------------------------

// The pest_derive macro generates a public `Rule` enum and associated items
// that cannot carry doc comments.  Wrapping the derive in a private module
// silences the missing_docs lint for the generated code without disabling it
// for the rest of the parser module.
mod generated {
    #![expect(
        unreachable_pub,
        reason = "pest-derive generates pub items inside this private module"
    )]
    use pest_derive::Parser;

    #[derive(Parser)]
    #[grammar = "src/grammar.pest"]
    pub struct AsmParser;
}

use generated::AsmParser;
use generated::Rule;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Parse `source` into a flat list of [`AsmLine`] values.
///
/// `name` is used as the file name in diagnostic output (e.g. `"prog.xqasm"`
/// or `"<input>"`).  Blank lines and comment-only lines produce no entries.
/// A line that contains both a label definition and an instruction produces
/// two entries: first a [`AsmLine::LabelDef`], then an
/// [`AsmLine::Instruction`].
///
/// # Errors
///
/// Returns [`ParseError`] if the input does not conform to the grammar.
///
/// # Examples
///
/// ```rust
/// use xqasm::{parse, AsmLine, Operand};
///
/// let lines = parse(".0:\n  JUMPI .0", "<test>").unwrap();
/// assert!(matches!(lines[0], AsmLine::LabelDef { .. }));
/// assert!(matches!(lines[1], AsmLine::Instruction(_)));
/// ```
pub fn parse(source: &str, name: &str) -> Result<Vec<AsmLine>, ParseError> {
    let src = Source { text: source, name };
    let pairs = AsmParser::parse(Rule::program, source).map_err(|e| {
        let offset = match e.location {
            pest::error::InputLocation::Pos(o) | pest::error::InputLocation::Span((o, _)) => o,
        };
        ParseError {
            message: e.variant.to_string(),
            src: make_src(src),
            span: make_span(offset, 1),
        }
    })?;

    let mut out = Vec::new();

    pairs
        .filter(|p| p.as_rule() == Rule::program)
        .flat_map(pest::iterators::Pair::into_inner)
        .filter(|p| p.as_rule() == Rule::line)
        .try_for_each(|line_pair| visit_line(line_pair, src, &mut out))?;

    Ok(out)
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

fn visit_line(
    line_pair: pest::iterators::Pair<'_, Rule>,
    src: Source<'_>,
    out: &mut Vec<AsmLine>,
) -> Result<(), ParseError> {
    line_pair
        .into_inner()
        .try_for_each(|inner| match inner.as_rule() {
            Rule::label_def => {
                let offset = inner.as_span().start();
                let label_text = inner
                    .into_inner()
                    .next()
                    .unwrap_or_else(|| {
                        unreachable!("grammar guarantees label_def contains label_id")
                    })
                    .as_str();
                // Strip leading '.' and parse as u16.
                let idx: u16 =
                    label_text
                        .get(1..)
                        .unwrap_or("")
                        .parse()
                        .map_err(|_| ParseError {
                            message: format!("label index '{label_text}' is not a valid u16"),
                            src: make_src(src),
                            span: make_span(offset, label_text.len()),
                        })?;
                out.push(AsmLine::LabelDef { label: idx, offset });
                Ok(())
            }
            Rule::instruction => {
                out.push(visit_instruction(inner, src)?);
                Ok(())
            }
            _ => Ok(()),
        })
}

fn visit_instruction(
    pair: pest::iterators::Pair<'_, Rule>,
    src: Source<'_>,
) -> Result<AsmLine, ParseError> {
    let offset = pair.as_span().start();
    let mut inner = pair.into_inner();

    let mnemonic_pair = inner
        .next()
        .unwrap_or_else(|| unreachable!("grammar guarantees instruction starts with mnemonic"));
    let mnemonic = mnemonic_pair.as_str().to_ascii_uppercase();

    let operands = inner
        .filter(|p| p.as_rule() == Rule::operand)
        .map(|p| visit_operand(p, src))
        .collect::<Result<Vec<_>, _>>()?;

    Ok(AsmLine::Instruction(ParsedInstr {
        mnemonic,
        operands,
        offset,
    }))
}

fn visit_operand(
    pair: pest::iterators::Pair<'_, Rule>,
    src: Source<'_>,
) -> Result<Operand, ParseError> {
    let inner = pair
        .into_inner()
        .next()
        .unwrap_or_else(|| unreachable!("grammar guarantees operand has one inner rule"));
    let offset = inner.as_span().start();
    let text = inner.as_str();

    match inner.as_rule() {
        Rule::register => {
            let digits = &text[1..]; // strip leading 'r'
            let slot: u64 = digits.parse().map_err(|_| ParseError {
                message: format!("register index '{digits}' is not a valid number"),
                src: make_src(src),
                span: make_span(offset, text.len()),
            })?;
            let reg = u8::try_from(slot).map_err(|_| ParseError {
                message: format!("register index {slot} is out of range [0, 255]"),
                src: make_src(src),
                span: make_span(offset, text.len()),
            })?;
            Ok(Operand::Register(reg))
        }
        Rule::integer => {
            let value = parse_integer(text, offset, src)?;
            Ok(Operand::Integer(value))
        }
        Rule::label_id => {
            // Strip leading '.' and parse as u16.
            let idx: u16 = text
                .get(1..)
                .unwrap_or("")
                .parse()
                .map_err(|_| ParseError {
                    message: format!("label index '{text}' is not a valid u16"),
                    src: make_src(src),
                    span: make_span(offset, text.len()),
                })?;
            Ok(Operand::LabelRef(idx))
        }
        r => unreachable!("unexpected operand rule: {r:?}"),
    }
}

fn parse_integer(text: &str, offset: usize, src: Source<'_>) -> Result<i64, ParseError> {
    let (neg, rest) = match text.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, text.strip_prefix('+').unwrap_or(text)),
    };

    let magnitude: u64 = if let Some(hex) = rest.strip_prefix("0x") {
        u64::from_str_radix(hex, 16)
    } else {
        rest.parse::<u64>()
    }
    .map_err(|_| ParseError {
        message: format!("invalid integer literal '{text}'"),
        src: make_src(src),
        span: make_span(offset, text.len()),
    })?;

    if neg {
        // -magnitude must fit in i64: magnitude <= 2^63 = i64::MIN.unsigned_abs()
        if magnitude > i64::MIN.unsigned_abs() {
            return Err(ParseError {
                message: format!("integer literal '{text}' underflows i64"),
                src: make_src(src),
                span: make_span(offset, text.len()),
            });
        }
        // For magnitude == 2^63 the plain cast wraps to i64::MIN; wrapping_neg
        // converts it back to i64::MIN, which is the correct result.
        #[expect(
            clippy::cast_possible_wrap,
            reason = "magnitude is bounded to ≤2^63 by the prior check; 2^63 wraps to i64::MIN which wrapping_neg correctly leaves as i64::MIN"
        )]
        Ok((magnitude as i64).wrapping_neg())
    } else {
        i64::try_from(magnitude).map_err(|_| ParseError {
            message: format!("integer literal '{text}' overflows i64"),
            src: make_src(src),
            span: make_span(offset, text.len()),
        })
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ast::Operand;

    fn instr(lines: &[AsmLine]) -> &ParsedInstr {
        lines
            .iter()
            .find_map(|l| {
                if let AsmLine::Instruction(i) = l {
                    Some(i)
                } else {
                    None
                }
            })
            .expect("no instruction found")
    }

    fn parse_test(src: &str) -> Result<Vec<AsmLine>, ParseError> {
        parse(src, "<test>")
    }

    #[test]
    fn parse_nop() {
        let lines = parse_test("NOP").unwrap();
        assert_eq!(lines.len(), 1);
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "NOP");
        assert_eq!(i.operands, []);
    }

    #[test]
    fn parse_push_positive() {
        let lines = parse_test("PUSH 42").unwrap();
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "PUSH");
        assert_eq!(i.operands, [Operand::Integer(42)]);
    }

    #[test]
    fn parse_push_negative() {
        let lines = parse_test("PUSH -99").unwrap();
        let i = instr(&lines);
        assert_eq!(i.operands, [Operand::Integer(-99)]);
    }

    #[test]
    fn parse_push_hex() {
        let lines = parse_test("PUSH 0xFF").unwrap();
        let i = instr(&lines);
        assert_eq!(i.operands, [Operand::Integer(255)]);
    }

    #[test]
    fn parse_load_register() {
        let lines = parse_test("LOAD r3").unwrap();
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "LOAD");
        assert_eq!(i.operands, [Operand::Register(3)]);
    }

    #[test]
    fn parse_energy_two_registers() {
        let lines = parse_test("ENERGY r0 r1").unwrap();
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "ENERGY");
        assert_eq!(i.operands, [Operand::Register(0), Operand::Register(1)]);
    }

    #[test]
    fn parse_jump_label_ref() {
        let lines = parse_test("JUMP .0").unwrap();
        let i = instr(&lines);
        assert_eq!(i.operands, [Operand::LabelRef(0)]);
    }

    #[test]
    fn parse_jump_integer_offset() {
        // Raw integer operands for JUMP are parsed as integers (the assembler
        // will reject them since jumps require labels in the new model).
        let lines = parse_test("JUMP -10").unwrap();
        let i = instr(&lines);
        assert_eq!(i.operands, [Operand::Integer(-10)]);
    }

    #[test]
    fn parse_label_def() {
        let lines = parse_test(".0:").unwrap();
        assert_eq!(lines.len(), 1);
        assert!(matches!(&lines[0], AsmLine::LabelDef { label: 0, .. }));
    }

    #[test]
    fn parse_label_and_instruction_same_line() {
        let lines = parse_test(".0: NOP").unwrap();
        assert_eq!(lines.len(), 2);
        assert!(matches!(&lines[0], AsmLine::LabelDef { label: 0, .. }));
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "NOP");
    }

    #[test]
    fn parse_multiline_program() {
        let src = "PUSH 1\nPUSH 2\nADD\nHALT";
        let lines = parse_test(src).unwrap();
        assert_eq!(lines.len(), 4);
    }

    #[test]
    fn parse_comment_only_line_ignored() {
        let lines = parse_test("; this is a comment\nHALT").unwrap();
        assert_eq!(lines.len(), 1);
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "HALT");
    }

    #[test]
    fn parse_inline_comment() {
        let lines = parse_test("NOP ; do nothing").unwrap();
        assert_eq!(lines.len(), 1);
    }

    #[test]
    fn parse_blank_lines_ignored() {
        let src = "\nNOP\n\nHALT\n";
        let lines = parse_test(src).unwrap();
        assert_eq!(lines.len(), 2);
    }

    #[test]
    fn parse_register_out_of_range() {
        assert!(parse_test("LOAD r256").is_err());
    }

    #[test]
    fn parse_invalid_syntax_returns_error() {
        assert!(parse_test("@@@").is_err());
    }

    #[test]
    fn parse_vecpush_instruction() {
        let lines = parse_test("VECPUSH r5").unwrap();
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "VECPUSH");
        assert_eq!(i.operands, [Operand::Register(5)]);
    }

    #[test]
    fn mnemonic_lowercase_normalized() {
        let lines = parse_test("push 1").unwrap();
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "PUSH");
        assert_eq!(i.operands, [Operand::Integer(1)]);
    }

    #[test]
    fn mnemonic_mixed_case_normalized() {
        let lines = parse_test("pUsH 7").unwrap();
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "PUSH");
        assert_eq!(i.operands, [Operand::Integer(7)]);
    }

    #[test]
    fn mnemonic_titlecase_normalized() {
        let lines = parse_test("Halt").unwrap();
        let i = instr(&lines);
        assert_eq!(i.mnemonic, "HALT");
        assert_eq!(i.operands, []);
    }

    #[test]
    fn mnemonic_case_insensitive_program() {
        let src = "push 1\npush 2\nadd\nhalt";
        let lines = parse_test(src).unwrap();
        assert_eq!(lines.len(), 4);
        let mnemonics: Vec<&str> = lines
            .iter()
            .filter_map(|l| {
                if let AsmLine::Instruction(i) = l {
                    Some(i.mnemonic.as_str())
                } else {
                    None
                }
            })
            .collect();
        assert_eq!(mnemonics, ["PUSH", "PUSH", "ADD", "HALT"]);
    }
}
