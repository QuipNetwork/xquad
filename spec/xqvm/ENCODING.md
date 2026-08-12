# XQVM Encoding and File Formats

## File Formats

The XQVM toolchain uses two file formats for programs:

- **`.xqasm`** — Human-readable text assembly source. Line comments begin with `;`. Supports syntactic sugar for `PUSH` and `JUMP`/`JUMPI` families. This is the authoring format.
- **`.xqb`** — Binary bytecode. A 15-byte XQBC header followed by the instruction stream. This is the execution format produced by the assembler.

---

## Assembly Syntax

Line comments begin with `;` and run to end of line. Whitespace is insignificant outside operands.

```assembly
; Comments start with ;
; Registers: r0, r1, ... r255 (8-bit slot ID)
; Targets: .0, .1, .2 (dot-prefixed numeric, sugar for TARGET; resolved to sequential IDs by assembler)
; Hex literals: 0x0A, 0xFF

PUSH 0x00         ; start = 0
PUSH 0x0A         ; count = 10
RANGE             ; range loop [0, 10)
  LVAL r0         ; r0 = current loop value
  LOAD r0
  PUSH 0x05
  GT
  JUMPI .0        ; break if r0 > 5
NEXT
TARGET .0
HALT
```

### `PUSH` Sugar

The assembler accepts `PUSH <value>` as syntactic sugar for the `PUSH1`–`PUSH8` family. The assembler parses the signed integer, selects the smallest `PUSHn` opcode that fits, and encodes the value as big-endian signed two's complement byte operands. The desugared forms (`PUSH1 0xFF`, `PUSH2 0x01 0x00`, etc.) are not accepted as assembler input; only the `PUSH <value>` sugar is typeable, and the assembler selects the width.

```assembly
PUSH 42            ; → PUSH1 42
PUSH -1            ; → PUSH1 0xFF
PUSH 256           ; → PUSH2 0x01 0x00
PUSH 2147483647    ; → PUSH4 0x7F 0xFF 0xFF 0xFF
```

### `JUMP` / `JUMPI` Sugar

The assembler accepts `JUMP .N` and `JUMPI .N` as syntactic sugar for the `JUMP1`/`JUMP2` and `JUMPI1`/`JUMPI2` pairs. The assembler resolves `.N` to the sequential target ID and selects the `*1` (u8) form when the ID fits in one byte, or the `*2` (u16 big-endian) form otherwise. The desugared forms remain valid.

---

## Binary Bytecode Format

Every `.xqb` file begins with a fixed 15-byte XQBC header, followed immediately by the instruction stream.

### XQBC Header

```
 Offset  Width  Description
 ------  -----  -----------
   0..4    4    Magic: b"XQBC"
      4    1    Version: 0x01 (current format version)
      5    1    input_slots: u8 -- count of INPUT instructions (calldata arity)
      6    1    output_slots: u8 -- count of OUTPUT instructions (minimum output-slot count)
   7..11   4    code_len: u32 big-endian -- byte length of the instruction stream
  11..15   4    crc32: u32 big-endian -- CRC-32/ISO-HDLC of the instruction stream
  15+      *    instruction stream (raw opcode + operand bytes)
```

Decoders must:

1. Reject files shorter than 15 bytes.
2. Reject files whose first four bytes are not `b"XQBC"`.
3. Reject files whose version byte is not `0x01`.
4. Reject files where the payload length differs from `code_len`.
5. Reject files where the CRC-32/ISO-HDLC of the payload does not match `crc32`.

`input_slots` and `output_slots` are informational; decoders may use them to pre-size calldata and output-slot arrays without scanning the instruction stream. They are not validated by the decoder.

### Instruction Stream

Each instruction in the stream is laid out as:

```
[ opcode : 1 byte ] [ operand bytes : 0 – 8 bytes ]
```

Instruction length is determined solely by the opcode (see the [Instruction Length Table](#instruction-length-table)). Multi-operand opcodes concatenate their operands in the order listed in the main opcode tables.

### Opcode Byte

A single byte drawn from:

- `0x00`–`0x7F` — the normal instruction space
- `0xF0` — `NOP`
- `0xFF` — `HALT`

All other bytes in `0x80`–`0xFF` are reserved and must be rejected by the decoder. See [Reserved Opcodes](ISA.md#reserved-opcodes) for the full list of unassigned byte values.

**`TARGET` (`0x00`) has no operand.** The one-byte opcode is the entire instruction. `TARGET` marks a jump destination in the instruction stream and is a no-op at runtime; its only purpose is to establish the target table during pre-scan. Each `TARGET` encountered in program order is assigned the next sequential ID (0, 1, 2, …) and registered at its PC. The `.N` label seen in assembly source (e.g. `TARGET .3`) is assembler-only syntax: the assembler uses it to resolve `JUMP`/`JUMPI` references; it is **never emitted into bytecode**.

### Operand Bytes

| Operand | Width | Encoding | Used by |
|---------|-------|----------|---------|
| Register | 1 byte | `u8`, range `0`–`255` | all register-taking opcodes |
| Label (u8) | 1 byte | `u8`, range `0`–`255` | `JUMP1`, `JUMPI1` |
| Label (u16) | 2 bytes | `u16` big-endian, range `0`–`65535` | `JUMP2`, `JUMPI2` |
| Immediate | `n` bytes (`n` = 1..8) | big-endian signed two's complement | `PUSH1`–`PUSH8` |

Label operands encode the **sequential target ID produced by the `TARGET` pre-scan** — not a PC offset, and not the source-level `.N` token. The assembler emits `JUMP1`/`JUMPI1` when the ID fits in one byte and `JUMP2`/`JUMPI2` otherwise (see the [`JUMP` / `JUMPI` sugar](#jump--jumpi-sugar) description).

Multi-register opcodes concatenate their register operands in the order listed in the opcode table. For example, `ENERGY r0 r1 → 0x7F 0x00 0x01` and `EQUALITY r0 r5 r6 → 0x74 0x00 0x05 0x06`.

### Instruction Length Table

| Length | Opcodes |
|--------|---------|
| 1 byte (opcode only) | `TARGET`, `NEXT`, `RANGE`, `NOP`, `HALT`, `POP`, `SCLR`, `SWAP`, `COPY`, `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `SQR`, `ABS`, `NEG`, `MIN`, `MAX`, `INC`, `DEC`, `BITLEN`, `EQ`, `LT`, `GT`, `LTE`, `GTE`, `NOT`, `AND`, `OR`, `XOR`, `BAND`, `BOR`, `BXOR`, `BNOT`, `SHL`, `SHR`, `IDXGRID`, `IDXTRIU` |
| 2 bytes (opcode + 1) | `JUMP1` (1 + u8 label), `JUMPI1` (1 + u8 label), `LIDX`, `LVAL`, `ITER`, `LOAD`, `STOW`, `DROP`, `INPUT`, `OUTPUT`, `PUSH1`, `VEC`, `VECI`, `VECX`, `BQMX`, `SQMX`, `XQMX`, `BSMX`, `SSMX`, `XSMX`, `VECPUSH`, `VECGET`, `VECSET`, `VECLEN`, `GETLINE`, `SETLINE`, `ADDLINE`, `GETQUAD`, `SETQUAD`, `ADDQUAD`, `RESIZE`, `ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM`, `ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `REDUCE` |
| 3 bytes (opcode + 2) | `JUMP2` (1 + u16 label), `JUMPI2` (1 + u16 label), `PUSH2` (1 + 2 imm), `ENERGY` (1 + 2 reg), `ATLEAST` (1 + 2 reg), `SLACK` (1 + 2 reg) |
| 4 bytes | `PUSH3`, `EQUALITY` (1 + 3 reg), `ATLEASTW` (1 + 3 reg) |
| 5 bytes | `PUSH4` |
| 6 bytes | `PUSH5` |
| 7 bytes | `PUSH6` |
| 8 bytes | `PUSH7` |
| 9 bytes | `PUSH8` |

> `XQMX` and `XSMX` take one register operand (2 bytes on the wire) but additionally pop two values from the stack. Their bytecode length is 2, not 3 — the popped stack values are not part of the encoding.

### Encoding Examples

```
NOP                  → 0xF0
HALT                 → 0xFF
TARGET               → 0x00                (no operand; .N is assembler-only)
PUSH1 42             → 0x11 0x2A
PUSH2 -1             → 0x12 0xFF 0xFF
LOAD r5              → 0x0A 0x05
JUMP1 .100           → 0x01 0x64           (JUMP .100 → JUMP1 — label fits in u8)
JUMP2 .1000          → 0x03 0x03 0xE8      (JUMP .1000 → JUMP2 — u16 big-endian)
JUMPI1 .5            → 0x02 0x05
ENERGY r0 r1         → 0x7F 0x00 0x01
BQMX r2              → 0x40 0x02
```
