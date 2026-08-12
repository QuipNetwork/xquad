# Control Flow

Instructions for branching, looping, and program termination: `TARGET`,
`JUMP1`/`JUMP2`, `JUMPI1`/`JUMPI2`, `NEXT`, `LVAL`, `LIDX`, `RANGE`, `ITER`,
`NOP`, and `HALT`. For each instruction's opcode byte, operand layout, and
stack effect, see the [Opcode Reference](../opcodes.md).

## The `TARGET` pre-scan

`TARGET` has no operand and does nothing at runtime -- it exists purely to
mark a valid jump destination at a fixed byte position. Before execution
begins, the VM scans the raw instruction stream once for `TARGET` opcodes:
the first one encountered is assigned sequential id `0`, the second id `1`,
and so on in program order, with each id recorded against the byte offset
where its `TARGET` starts. This is the same scan the
[verifier](../verifier.md) uses to check that every jump references a valid
id, and the [Bytecode Format](../bytecode-format.md) chapter covers its
wire-level details.

`JUMP1`, `JUMP2`, `JUMPI1`, and `JUMPI2` operands carry one of these
sequential ids -- never a raw byte offset, and never the `.N` token that
appears in `.xqasm` source. `.N` is resolved to the sequential id by the
assembler before encoding and is never itself emitted into the bytecode.
When the VM executes a jump, it looks up the id's recorded byte offset and
seeks the instruction stream there; see [Execution Model](../execution.md#control-flow-results)
for how this fits into the fetch-decode-execute loop.

`TARGET` must appear at every label destination. The assembler inserts one
automatically wherever a label is *placed* in source, so authors do not
normally type it by hand:

```asm
; Shorthand: label form
.0: HALT
```

```asm
; Explicit form: TARGET directive bound to a label
TARGET .0
HALT
```

Both compile to `[TARGET, HALT]`. Use whichever is clearer in context. A
bare `TARGET` (with no operand) emits a raw `Target` opcode without binding
any label; that is only useful for direct bytecode construction, and most
user programs should prefer one of the label-bearing forms above.

## Branching

`JUMP` unconditionally seeks to the byte offset recorded for a label.
`JUMPI` pops the top of the stack and seeks only if that value is
non-zero; otherwise it falls through to the next instruction.

Each has a narrow and a wide encoding, distinguished by the width of the
label operand:

- `JUMP1` / `JUMPI1` encode the label as a single `u8` byte.
- `JUMP2` / `JUMPI2` encode the label as a `u16` big-endian pair.

At the assembly level, both are written as `JUMP .N` / `JUMPI .N` (`.N` being
the dot-prefixed label token, e.g. `.0`, `.1`); the assembler resolves `.N`
to its sequential id and picks the narrowest encoding automatically -- the
`*1` form whenever the id fits in a `u8` (the common case, since most
programs have fewer than 256 labels), falling back to the `*2` form
otherwise. The desugared forms (`JUMP1 .N`, `JUMP2 .N`, `JUMPI1 .N`,
`JUMPI2 .N`) are accepted by the grammar, but the assembler's unused-label
check does not count them as a use: a program whose only reference to `.0`
is `JUMP1 .0` fails with `xqasm::unused_label`, whichever side of the label
the jump sits on. The one exception is a label whose `TARGET` lands at byte
offset 0, the entry block, which the check exempts. Write `JUMP`/`JUMPI`
and let the assembler pick the width. Disassembled output
always shows the explicit form the program was actually encoded with, so a
round-tripped disassembly preserves the exact wire encoding rather than
the assembler's shorthand.

## Looping

`RANGE` and `ITER` each push a loop frame and hand control to the loop body;
`NEXT` advances the innermost frame, seeking back to the body's start until
the loop is exhausted, then pops the frame. `LVAL` copies the current loop
*value* into a register, and `LIDX` copies the current loop *index*. Calling
`LVAL`, `LIDX`, or `NEXT` with no active loop frame raises `NoActiveLoop`.

```asm
PUSH 0       ; start
PUSH 10      ; count
RANGE
  LVAL r0    ; r0 = current iteration value (0, 1, ..., 9)
NEXT
HALT
```

```asm
VECI r1
PUSH 10
VECPUSH r1
PUSH 20
VECPUSH r1
PUSH 30
VECPUSH r1
PUSH 40
VECPUSH r1

PUSH 0       ; start_idx
PUSH 4       ; end_idx
ITER r1      ; r1 must hold a VecInt or VecXqmx
  LVAL r2    ; r2 = current element (10, 20, 30, 40)
  LIDX r3    ; r3 = absolute position in r1 (0, 1, 2, 3)
NEXT
HALT
```

Loops nest to arbitrary depth; `LVAL`, `LIDX`, and `NEXT` always act on the
innermost frame. [Loops](../loops.md) covers frame contents, the `RANGE`
versus `ITER` distinction, nesting, and the full error conditions in depth
-- this page only orients; that one is the reference.

## `NOP` and `HALT`

`NOP` does nothing and advances to the next instruction; it exists mainly
for hand-assembled bytecode and testing. `HALT` stops execution
immediately, leaving the stack and registers as they were at the point of
the halt.
