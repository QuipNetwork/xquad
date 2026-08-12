# xquad dism

Disassemble XQVM bytecode into a human-readable listing.

## Usage

```sh
xquad dism [FILE]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `FILE` | Bytecode file to disassemble. Reads from stdin when omitted. |

`dism` decodes `FILE` (or stdin) as a full XQBC container, the same
format `xquad asm` writes: a 15-byte header (magic, version, code length,
CRC32) followed by the raw instruction stream. It has no path for a bare
instruction stream missing that header -- a buffer that starts straight
with opcode bytes fails to decode on the magic check before disassembly
gets a chance to run. This is why the stdin example below pipes from
`xquad asm --stdout`, which emits a full container, rather than from a
raw instruction buffer.

## Examples

### Disassemble a file

```sh
xquad dism add.xqb
```

```
  0x0000:  PUSH1   10
  0x0002:  PUSH1   32
  0x0004:  ADD     
  0x0005:  HALT    
```

### Disassemble from stdin

```sh
xquad asm add.xqasm --stdout | xquad dism
```

Produces the same listing as above; `dism` reads the bytes either way.

### A program with jumps

`branch.xqb`, assembled from a program with two labels (`.0`, `.1`) and two
jumps:

```
  0x0000:       PUSH1   5
  0x0002:       PUSH1   10
  0x0004:       GT      
  0x0005:       JUMPI1  .0
  0x0007:       PUSH1   99
  0x0009:       JUMP1   .1
  0x000B:  .0:  TARGET  
  0x000C:       PUSH1   0
  0x000E:  .1:  TARGET  
  0x000F:       HALT    
```

## Output format

Each line shows a byte offset, an optional label, and a decoded
instruction:

- **Byte offset** (`0x0000:`) -- position in the instruction stream.
- **Label column** -- present only when the program contains at least one
  `TARGET`. When present, it holds `.0:`, `.1:`, and so on at the offset
  where each `TARGET` opcode sits, and is blank on every other line so the
  instruction column still lines up. When the program has no `TARGET` at
  all, as in `add.xqb` above, the column is omitted entirely rather than
  printed blank on every line.
- **Instruction** -- mnemonic and decoded operands. `JUMP`/`JUMPI` operands
  are printed as the same `.N` label that marks their destination, not as
  a raw byte offset or an internal id.
- **PUSH values** -- shown as sign-extended decimal integers (`PUSH1 10`,
  not a hex byte).
- **Undecodable bytes** -- an invalid opcode or a truncated operand does
  not abort the listing. It is rendered as a `.byte 0xNN` pseudo-instruction
  and the walk continues from the next byte, so no byte is silently
  dropped from the output.

The label numbering comes from a single left-to-right scan over the
decoded program: the first `TARGET` found is `.0`, the second is `.1`, and
so on. This is the same numbering the assembler resolves `.0`/`.1`-style
source labels against, so a disassembly's labels read back as valid
`.xqasm` jump targets -- provided the listing contains no `.byte` lines.
`.byte` is not a recognised mnemonic, so a listing that hit an
undecodable byte is not itself valid `.xqasm` input, even though its
labels are correctly numbered. See [Bytecode Format](../bytecode-format.md) for
how that scan works and why a `JUMP` operand cannot be validated in
isolation from it.
