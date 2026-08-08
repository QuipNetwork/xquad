# Stack Manipulation

Instructions for pushing constants, duplicating, swapping, and removing stack
elements.

| Code | Mnemonic | Arguments | Stack Effect | Description |
|------|----------|-----------|--------------|-------------|
| `0x10` | `POP` | -- | \\([\ldots, a] \to [\ldots]\\) | Discard the top of the stack. |
| `0x11` | `PUSH1` | `val: [u8; 1]` | \\([\ldots] \to [\ldots, v]\\) | Interpret `val` as a 1-byte big-endian signed integer, sign-extend to `i64`, push \\(v\\). |
| `0x12` | `PUSH2` | `val: [u8; 2]` | \\([\ldots] \to [\ldots, v]\\) | Same, 2 bytes. |
| `0x13` | `PUSH3` | `val: [u8; 3]` | \\([\ldots] \to [\ldots, v]\\) | Same, 3 bytes. |
| `0x14` | `PUSH4` | `val: [u8; 4]` | \\([\ldots] \to [\ldots, v]\\) | Same, 4 bytes. |
| `0x15` | `PUSH5` | `val: [u8; 5]` | \\([\ldots] \to [\ldots, v]\\) | Same, 5 bytes. |
| `0x16` | `PUSH6` | `val: [u8; 6]` | \\([\ldots] \to [\ldots, v]\\) | Same, 6 bytes. |
| `0x17` | `PUSH7` | `val: [u8; 7]` | \\([\ldots] \to [\ldots, v]\\) | Same, 7 bytes. |
| `0x18` | `PUSH8` | `val: [u8; 8]` | \\([\ldots] \to [\ldots, v]\\) | Interpret `val` as a full 8-byte big-endian `i64`, push \\(v\\). |
| `0x1A` | `SCLR` | -- | \\([\ldots] \to []\\) | Clear the entire value stack. |
| `0x1B` | `SWAP` | -- | \\([\ldots, a, b] \to [\ldots, b, a]\\) | Swap the top two elements. Errors if stack depth \\(< 2\\). |
| `0x1C` | `COPY` | -- | \\([\ldots, a] \to [\ldots, a, a]\\) | Duplicate the top of the stack without consuming it. |

## PUSH Size Selection

In assembly, you write a single `PUSH` mnemonic with an integer literal:

```asm
PUSH 42       ; assembler selects PUSH1 (fits in i8)
PUSH 1000     ; assembler selects PUSH2 (fits in i16)
PUSH -1       ; assembler selects PUSH1 (0xFF sign-extends to -1)
```

The assembler (and the `InstructionBuilder::push()` method) automatically
selects the smallest `PUSH1`--`PUSH8` variant that faithfully represents the
value. This keeps bytecode compact: small constants use 2 bytes total, while the
full 8-byte `PUSH8` is only emitted for values that require all 64 bits.

The encoding is big-endian and sign-extended. For example, `PUSH1 0xFF` decodes
as \\(-1_{i64}\\), not \\(255_{i64}\\).
