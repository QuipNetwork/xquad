# xq dism

Disassemble XQVM bytecode into a human-readable listing.

## Usage

```sh
xq dism [FILE]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `FILE` | Bytecode file to disassemble. Reads from stdin when omitted. |

## Examples

```sh
# Disassemble a file
xq dism program.xqb

# Disassemble from stdin (pipe from assembler)
xq asm program.xqasm --stdout | xq dism
```

## Output Format

The disassembler prints one line per instruction with byte offsets and decoded
operands. Jump targets from the jump table are shown as `.0`, `.1`, etc.

```
0x0000: .0: TARGET
0x0001:     PUSH1          42
0x0003:     JUMP           .0
```

- **Byte offset** (`0x0000:`) -- position in the instruction stream.
- **Label** (`.0:`) -- jump table label, if one starts at this offset.
- **Instruction** -- mnemonic and decoded operands.
- **PUSH values** -- shown as sign-extended decimal integers.
