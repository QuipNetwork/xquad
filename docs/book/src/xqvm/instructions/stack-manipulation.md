# Stack Manipulation

Instructions for pushing constants and rearranging the top of the value
stack: `POP`, `PUSH1`..`PUSH8`, `SCLR`, `SWAP` and `COPY`. Byte values,
operand layouts and stack effects are in the
[Stack Manipulation](../opcodes.md#stack-manipulation) section of the
opcode reference. This page is mostly about the constant-pushing family,
since the four rearrangement instructions (`POP`, `SCLR`, `SWAP`, `COPY`)
are exactly what their names say: discard the top, clear everything,
exchange the top two, or duplicate the top without consuming it. `SWAP`
and `COPY` both error `StackUnderflow` rather than reading past an empty
stack: a bare `PUSH 1; SWAP` needs a second element that is not there.

## PUSH Size Selection

`PUSH1` through `PUSH8` are not mnemonics you write. In assembly, and
through `InstructionBuilder::push()`, there is only `PUSH <value>`:

```asm
PUSH 42       ; assembler selects PUSH1 (fits in i8)
PUSH 1000     ; assembler selects PUSH2 (fits in i16)
PUSH -1       ; assembler selects PUSH1 (0xFF sign-extends to -1)
```

The assembler picks the narrowest `PUSH1`..`PUSH8` variant that faithfully
round-trips the literal, so small constants cost 2 bytes total (1 opcode +
1 operand byte) and the full 8-byte `PUSH8` is only ever emitted for values
that need all 64 bits. In the program above, `PUSH 42` assembles to
`PUSH1 42` (0x2A), `PUSH 1000` assembles to `PUSH2 1000` (0x03E8), and
`PUSH -1` assembles to `PUSH1 -1`, whose single operand byte is `0xFF`.

Writing `PUSH1` directly does not work: it is not a recognised mnemonic,
and the assembler rejects it with `unknown mnemonic 'PUSH1'`. `PUSHC` is
accepted as an alias for `PUSH` and goes through the same size-selection
path; there is no reason to prefer one over the other beyond house style.

The encoding itself is big-endian and sign-extended regardless of which
`PUSHn` variant is chosen, which is what makes `PUSH -1` a 1-byte operand
rather than an 8-byte one: `PUSH1 0xFF` decodes as \\(-1_{i64}\\), not
\\(255_{i64}\\), because the single byte is read as signed and then sign
extended, not as an unsigned magnitude.
