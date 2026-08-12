# Bytecode Format

A `.xqb` file is the binary form of an XQVM program: a fixed 15-byte XQBC
header followed immediately by the raw instruction stream. This page covers
that wire format. For the human-readable `.xqasm` source format and how it
compiles down to this, see [Assembly](assembly.md).

The normative source is
[`spec/xqvm/ENCODING.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ENCODING.md).
The Rust implementation of the header is
[`Program::encode`/`Program::decode`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/bytecode/program.rs).

## The XQBC header

| Offset | Width | Field | Description |
|---|---|---|---|
| 0..4 | 4 bytes | Magic | The ASCII bytes `XQBC` |
| 4 | 1 byte | Version | Format version, currently `0x01` |
| 5 | 1 byte | `input_slots` | Count of `INPUT` instructions in the program (calldata arity) |
| 6 | 1 byte | `output_slots` | Count of `OUTPUT` instructions (minimum output-slot count) |
| 7..11 | 4 bytes | `code_len` | Byte length of the instruction stream, `u32` big-endian |
| 11..15 | 4 bytes | `crc32` | CRC-32/ISO-HDLC checksum of the instruction stream, `u32` big-endian |
| 15+ | -- | Instruction stream | Raw opcode and operand bytes |

`input_slots` and `output_slots` are informational: a decoder may use them to
pre-size calldata and output-slot arrays without scanning the instruction
stream, but nothing enforces that a program's `INPUT`/`OUTPUT` instructions
actually match the header's counts. Both counts saturate at 255 (`u8::MAX`):
a program with 300 `INPUT` instructions still encodes `input_slots` as 255,
and there is no error path for the overflow. The count is also best-effort
in another sense -- it is produced by walking the instruction stream and
skipping any instruction that fails to decode, so a malformed stream still
yields a (possibly incomplete) count rather than aborting the walk.

A decoder rejects a file if any of the following hold:

1. It is shorter than 15 bytes.
2. Its first four bytes are not `XQBC`.
3. Its version byte is not `0x01`.
4. The instruction-stream length does not match `code_len`.
5. The CRC-32/ISO-HDLC of the instruction stream does not match `crc32`.

## The instruction stream

Every instruction is an opcode byte followed by zero to eight operand bytes:

```text
[ opcode : 1 byte ] [ operand bytes : 0-8 bytes ]
```

Instruction length is fixed per opcode -- it is never encoded in the stream
itself, so a decoder needs the opcode table, not a length prefix, to know how
many operand bytes follow a given opcode byte. Register operands are a
single `u8` (`0`-`255`); `PUSH1`-`PUSH8` operands are 1 to 8 bytes of
big-endian signed two's complement; label operands are a `u8`
(`JUMP1`/`JUMPI1`) or a `u16` big-endian (`JUMP2`/`JUMPI2`). Multi-operand
and multi-register opcodes concatenate their operands in the order the
opcode table lists them -- `ENERGY r0 r1` encodes as `0x7F 0x00 0x01`.

The opcode byte occupies `0x00`-`0x7F` for the normal instruction space, plus
two single-byte opcodes outside that range: `0xF0` (`NOP`) and `0xFF`
(`HALT`). Every other byte value in `0x80`-`0xFF`, and any unassigned gap
below `0x80`, is rejected by the decoder as an unknown opcode.

## `TARGET` and the label pre-scan

`TARGET` (`0x00`) has no operand -- the opcode byte is the whole instruction.
It marks a jump destination and does nothing at runtime; its only job is to
exist at a fixed byte position so a decoder can find it.

A decoder builds an id-to-offset lookup by scanning the instruction stream
once for `TARGET` opcodes: the first one encountered is assigned id `0`, the
second id `1`, and so on in program order, each recorded against the byte
offset where it starts. `JUMP1`/`JUMP2`/`JUMPI1`/`JUMPI2` operands carry
these sequential ids -- not byte offsets, and not the `.N` label token that
appears in `.xqasm` source. `.N` is assembler-only syntax used to resolve
jump references before encoding; it is never emitted into the bytecode. In
the Rust implementation this scan produces a `JumpTable` value (the
`JumpTable::scan` associated function), and `Program::new` runs it once when
a program is constructed from raw bytes.

This pre-scan is why a decoder cannot fully validate a single instruction in
isolation: whether `JUMP1 .3` is well-formed depends on how many `TARGET`
opcodes exist anywhere in the program, which is only known once the whole
stream has been scanned. See [Verifier](verifier.md) for how an out-of-range
label is rejected before execution.

## Instruction lengths by opcode

| Length | Opcodes |
|---|---|
| 1 byte (opcode only) | `TARGET`, `NEXT`, `RANGE`, `NOP`, `HALT`, `POP`, `SCLR`, `SWAP`, `COPY`, `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `SQR`, `ABS`, `NEG`, `MIN`, `MAX`, `INC`, `DEC`, `BITLEN`, `EQ`, `LT`, `GT`, `LTE`, `GTE`, `NOT`, `AND`, `OR`, `XOR`, `BAND`, `BOR`, `BXOR`, `BNOT`, `SHL`, `SHR`, `IDXGRID`, `IDXTRIU` |
| 2 bytes (opcode + 1) | `JUMP1`, `JUMPI1`, `LIDX`, `LVAL`, `ITER`, `LOAD`, `STOW`, `DROP`, `INPUT`, `OUTPUT`, `PUSH1`, `VEC`, `VECI`, `VECX`, `BQMX`, `SQMX`, `XQMX`, `BSMX`, `SSMX`, `XSMX`, `VECPUSH`, `VECGET`, `VECSET`, `VECLEN`, `GETLINE`, `SETLINE`, `ADDLINE`, `GETQUAD`, `SETQUAD`, `ADDQUAD`, `RESIZE`, `ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM`, `ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `REDUCE` |
| 3 bytes (opcode + 2) | `JUMP2`, `JUMPI2`, `PUSH2`, `ENERGY`, `ATLEAST`, `SLACK` |
| 4 bytes | `PUSH3`, `EQUALITY`, `ATLEASTW` |
| 5 bytes | `PUSH4` |
| 6 bytes | `PUSH5` |
| 7 bytes | `PUSH6` |
| 8 bytes | `PUSH7` |
| 9 bytes | `PUSH8` |

`XQMX` and `XSMX` are exceptions worth flagging: each takes one register
operand (2 bytes on the wire) but additionally pops two values off the value
stack at runtime. Their bytecode length is 2, not 3 -- the popped stack
values never appear in the encoding.

## Examples

| Instruction | Bytes |
|---|---|
| `NOP` | `0xF0` |
| `HALT` | `0xFF` |
| `TARGET` | `0x00` |
| `PUSH1 42` | `0x11 0x2A` |
| `PUSH2 -1` | `0x12 0xFF 0xFF` |
| `LOAD r5` | `0x0A 0x05` |
| `JUMP1 .100` | `0x01 0x64` |
| `JUMP2 .1000` | `0x03 0x03 0xE8` |
| `JUMPI1 .5` | `0x02 0x05` |
| `ENERGY r0 r1` | `0x7F 0x00 0x01` |
| `BQMX r2` | `0x40 0x02` |

The `PUSHn` rows show the encoded instruction, not source you can type: the
assembler accepts the `PUSH <value>` sugar (and its `PUSHC` alias) and
selects the width itself. The `JUMPn`/`JUMPIn` forms are typeable but
interact badly with the unused-label check -- see [Control
Flow](instructions/control-flow.md#branching). Write `JUMP`/`JUMPI` and let
the assembler pick the width.

## Where this is implemented

- [`spec/xqvm/ENCODING.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ENCODING.md) is the normative reference.
- [`xqvm/src/bytecode/program.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/bytecode/program.rs) implements the header encode/decode and owns the pre-scanned `JumpTable`.
- [`xqvm/src/bytecode/codec.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/bytecode/codec.rs) implements the per-instruction operand encoding.
- [`xqvm/src/verifier/scan.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/verifier/scan.rs) implements the single linear pass that builds the id-to-offset lookup and checks for undefined jump targets in the same walk.
