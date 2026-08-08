# Assembly Language

XQVM programs are written in a simple assembly language and stored in `.xqasm`
files. The assembler (`aglais-xqvm-asm` crate, invoked via `xq asm`) parses
the source, resolves labels, and emits compact bytecode.

## Overview

- Line-oriented format: one instruction per line.
- Comments start with `;` and run to end of line.
- Mnemonics are case-insensitive (`PUSH`, `push`, `Push` all work).
- Labels use numeric `.N` syntax (`.0`, `.1`, `.42`).
- Registers use `r<digits>` syntax (`r0`, `r255`).
- Integer literals may be signed decimal or `0x`-prefixed hexadecimal.

## Quick Example

```asm
; Compute 10 + 32 = 42
PUSH 10
PUSH 32
ADD
HALT
```

## Assembly Syntax

This page defines the complete syntax of the XQVM assembly language, derived
from the canonical PEG grammar in `crates/asm/src/grammar.pest`.

## Line Structure

Each source line has the form:

```
[label_def:] [INSTRUCTION [operands...]] [; comment]
```

All three parts are optional. Blank lines and comment-only lines are valid.

### Examples

```asm
                        ; blank line (valid)
; this is a comment     ; comment-only line
PUSH 42                 ; instruction only
.0: TARGET              ; label + instruction
.1:                     ; label only (anchors a jump target)
LOAD r0                 ; register operand
JUMP .0                 ; label reference operand
```

## Comments

Comments begin with `;` and extend to the end of the line. They can appear
on their own or after an instruction:

```asm
; full-line comment
PUSH 10  ; inline comment
```

## Mnemonics

Instruction mnemonics are **case-insensitive** ASCII identifiers. All of these
are equivalent:

```asm
PUSH 42
push 42
Push 42
```

The assembler recognises all 93 XQVM instruction mnemonics. `PUSH` is a
special mnemonic that accepts an integer operand and automatically selects the
smallest `PUSH1`--`PUSH8` encoding. `PUSHC` is an alias for `PUSH`.

## Operands

Three operand types exist:

### Registers

```
r0, r1, r2, ..., r255
```

A lowercase `r` followed by 1--3 decimal digits. Valid range: `r0`--`r255`.

### Integer Literals

```
42          ; positive decimal
-99         ; negative decimal
+7          ; explicit positive
0xFF        ; hexadecimal (0x prefix)
0x0         ; hex zero
```

Integers are signed `i64` values. Decimal and hexadecimal (`0x` prefix) formats
are supported. An optional `+` or `-` sign may precede the digits.

### Label References

```
.0, .1, .42, .255
```

A dot followed by one or more decimal digits. Label references are used as
operands for `JUMP` and `JUMPI` instructions.

## Labels

Labels are defined either with the `.N:` shorthand or the explicit `TARGET .N`
directive:

```asm
.0: NOP            ; shorthand: define label .0 at this position
.1:                ; label on its own line (useful for readability)

TARGET .2          ; explicit form: identical to ".2:"
HALT
```

Both forms compile to the same bytecode: the assembler emits an inline
`TARGET` opcode at the label position *and* records the position in the jump
table. `.0:` and `TARGET .0` are interchangeable spellings for the same
operation; pick whichever reads better in context. Defining the same label
with both forms is a `DuplicateLabel` error, the same as defining `.N:`
twice.

A bare `TARGET` (no operand) emits a raw `Target` opcode without binding any
label. That's useful only for hand-built bytecode where you do not need a
corresponding jump destination; user-facing programs should use the labelled
forms.

Labels must be defined before or after they are referenced -- both forward and
backward references are resolved by the assembler. Every label used as a
`JUMP`/`JUMPI` target must be defined somewhere in the program.

The assembler converts labels to jump table entries. At runtime, `JUMP .N` looks
up the byte offset of label `.N` in the jump table and seeks the instruction
stream to that position (which is the byte holding the inline `TARGET`).

## Whitespace

Spaces and tabs between tokens are ignored. Lines are separated by `\n` or
`\r\n`. Indentation is purely cosmetic and has no semantic meaning. A common
convention is to indent loop bodies:

```asm
PUSH 0
PUSH 10
RANGE
  LVAL r0
  LOAD r0
  PUSH 2
  MUL
  POP
NEXT
```

## Error Reporting

The assembler uses [miette](https://crates.io/crates/miette) for rich terminal
diagnostics. Errors include the source file name, line/column numbers, and a
snippet highlighting the problematic token:

```
Error: unknown mnemonic
  ┌─ program.xqasm:3:1
  │
3 │ INVALID_MNEMONIC r0
  │ ^^^^^^^^^^^^^^^^ unknown instruction
```
