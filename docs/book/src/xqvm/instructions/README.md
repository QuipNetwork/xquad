# Instruction Set Reference

This section documents all 93 XQVM instructions, organised into the 14
categories below. Each page covers the semantics, error conditions, and
worked examples for one category. For the opcode byte, mnemonic, operand
layout, and stack effect of every instruction, see the generated
[Opcode Reference](../opcodes.md) -- that table is the single source of
truth for wire-level facts, so the pages in this section carry prose and
teaching content instead of repeating it.

## Notation

- **Stack effect** -- described in prose on this page ("Pop `b`, then `a`")
  and summarised on the generated [Opcode Reference](../opcodes.md) as a
  `Stack` column of the form `2 → 1`, meaning two values popped and one
  pushed. The value stack is last-in-first-out: whichever value was pushed
  most recently is popped first, so `PUSH a; PUSH b; SUB` computes
  `a - b`, not `b - a`. Every "pop `x`, then `y`" operand order on this
  section's pages, including the ones on [constraints.md](constraints.md) and
  [index-math.md](index-math.md), depends on this rule.
- **`reg`** -- the `u8` operand encoded in the instruction byte stream,
  identifying a register slot (r0--r255).
- **`label`** -- a sequential id assigned to each `TARGET` instruction, in
  program order, during the load-time pre-scan described in
  [Bytecode Format](../bytecode-format.md). It is not a byte offset, and not
  the `.N` token used in `.xqasm` source -- that token is assembler-only and
  is resolved to the sequential id before encoding. `JUMP1`/`JUMPI1` encode
  the id as a single `u8` operand; `JUMP2`/`JUMPI2` encode it as a `u16`
  operand (big-endian). See [Control Flow](control-flow.md) for how the
  assembler picks between the two.
- Assignments use \\(\leftarrow\\) (register write) and \\(\to\\) (stack push).
- **Iverson brackets** -- \\([P]\\) equals \\(1\\) if \\(P\\) is true, \\(0\\) otherwise.
- **Checked arithmetic** -- every integer operation is checked against the
  `i64` range. A result that would leave it raises `ArithmeticOverflow`
  rather than wrapping or panicking; see
  [Arithmetic](arithmetic.md#overflow).

### Register Effect Vocabulary

A handful of pages, [constraints.md](constraints.md) and
[energy.md](energy.md), annotate individual opcodes with a `**Register
effect:**` line using three terms:

- **`read`** -- register contents are inspected but not changed.
- **`write`** -- register is replaced wholesale with a new value.
- **`mutate`** -- register's existing value is modified in-place (e.g.
  appending to a vec, incrementing a coefficient).

Most category pages group opcodes by topic or mechanism rather than
walking through them one at a time, so they carry no such line.

## `RegVal` -- The Register Value Type

Each of the 256 registers holds one variant of `RegVal` -- `Unset`, `Int`,
`VecInt`, `VecXqmx`, `Model`, or `Sample`. See [VM
Architecture](../machine-model.md#regval-variants) for the full table.

Type mismatches at runtime produce a `RegisterType` error with the expected
and actual variant names; reading an `Unset` register via `LOAD` or `OUTPUT`
produces `UnsetRegister` instead, since there is no variant to compare
against.

## Categories

| Category | Page | Opcodes |
|---|---|---|
| Control Flow | [control-flow.md](control-flow.md) | `TARGET`, `JUMP1`, `JUMP2`, `JUMPI1`, `JUMPI2`, `NEXT`, `LVAL`, `LIDX`, `RANGE`, `ITER`, `NOP`, `HALT` |
| Register I/O | [register-io.md](register-io.md) | `LOAD`, `STOW`, `DROP`, `INPUT`, `OUTPUT` |
| Stack Manipulation | [stack-manipulation.md](stack-manipulation.md) | `POP`, `PUSH1`-`PUSH8`, `SCLR`, `SWAP`, `COPY` |
| Arithmetic | [arithmetic.md](arithmetic.md) | `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `SQR`, `ABS`, `NEG`, `MIN`, `MAX`, `INC`, `DEC`, `BITLEN` |
| Comparison | [comparison.md](comparison.md) | `EQ`, `LT`, `GT`, `LTE`, `GTE` |
| Logical Boolean | [logical.md](logical.md) | `NOT`, `AND`, `OR`, `XOR` |
| Bitwise | [bitwise.md](bitwise.md) | `BAND`, `BOR`, `BXOR`, `BNOT`, `SHL`, `SHR` |
| Allocators | [allocators.md](allocators.md) | `BQMX`, `SQMX`, `XQMX`, `BSMX`, `SSMX`, `XSMX`, `VEC`, `VECI`, `VECX` |
| Vector Operations | [vector-ops.md](vector-ops.md) | `VECPUSH`, `VECGET`, `VECSET`, `VECLEN`, `SLACK` |
| Index Math | [index-math.md](index-math.md) | `IDXGRID`, `IDXTRIU` |
| Coefficient Access | [coefficient-access.md](coefficient-access.md) | `GETLINE`, `SETLINE`, `ADDLINE`, `GETQUAD`, `SETQUAD`, `ADDQUAD` |
| Grid Operations | [grid.md](grid.md) | `RESIZE`, `ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM` |
| High-Level Constraints | [constraints.md](constraints.md) | `ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `EQUALITY`, `ATLEAST`, `ATLEASTW`, `REDUCE` |
| Energy Evaluation | [energy.md](energy.md) | `ENERGY` |

## Reserved Opcodes

Every byte value not listed in [Opcode Reference](../opcodes.md) is illegal;
the decoder rejects it. The 93 assigned bytes are not contiguous -- there are
gaps both between and within the ranges above, plus one large unassigned
block above the normal instruction space. The full gap set, derived by
diffing the 93 assigned byte values against the complete `0x00`-`0xFF` space:

| Range | Unassigned bytes |
|---|---|
| Register I/O | `0x0D` |
| Stack Manipulation | `0x19`, `0x1D`-`0x1F` |
| Arithmetic | `0x2D`-`0x2F` |
| Comparison | `0x35` |
| Allocators | `0x46`-`0x49`, `0x4D`-`0x4F` |
| Vector Operations | `0x55`-`0x59` |
| Index Math / Coefficient Access | `0x5C`-`0x5F` |
| Grid Operations | `0x6B`-`0x6F` |
| High-Level Constraints | `0x78`-`0x7E` |
| Outside the normal instruction space | `0x80`-`0xEF`, `0xF1`-`0xFE` |

`NOP` (`0xF0`) and `HALT` (`0xFF`) are the only two assigned bytes outside
`0x00`-`0x7F`; every other byte in `0x80`-`0xFF` is illegal. See
[`spec/xqvm/ISA.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ISA.md#reserved-opcodes)
for the normative list, organised the same way.
