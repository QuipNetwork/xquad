# Instruction Set Reference

This section documents all 93 XQVM instructions, organised by category. Each
instruction page includes the opcode byte, mnemonic, operands, stack effect,
register effect, and a prose description.

## Notation

- **Stack diagrams** -- \\([\ldots, a, b] \to [\ldots, r]\\), where rightmost = **top**.
  \\(b\\) is popped first.
- **`reg`** -- the `u8` operand encoded in the instruction byte stream,
  identifying a register slot (r0--r255).
- **`label`** -- a `u16` index into the jump table.
- Assignments use \\(\leftarrow\\) (register write) and \\(\to\\) (stack push).
- **Iverson brackets** -- \\([P]\\) equals \\(1\\) if \\(P\\) is true, \\(0\\) otherwise.
- **Wrapping** -- all integer arithmetic uses wrapping semantics on `i64`
  (no panic on overflow; result truncated to 64 bits).

### Register Effect Modes

- **`read`** -- register contents are inspected but not changed.
- **`write`** -- register is replaced wholesale with a new value.
- **`mutate`** -- register's existing value is modified in-place (e.g.
  appending to a vec, incrementing a coefficient).

## `RegVal` -- The Register Value Type

Each of the 256 registers holds one variant of `RegVal`:

| Variant | Rust Type | Notes |
|---------|-----------|-------|
| `Int(i64)` | `i64` | Default value for every register. |
| `VecInt(Vec<i64>)` | `Vec<i64>` | Integer vector. |
| `VecXqmx(Vec<XqmxModel>)` | `Vec<XqmxModel>` | Vector of models. |
| `Model(XqmxModel)` | struct | QUBO/Ising/discrete Hamiltonian. |
| `Sample(XqmxSample)` | struct | Variable-assignment vector. |

Type mismatches at runtime produce a `RegisterType` error with the expected and
actual variant names.

## Reserved Opcodes

The following byte values are unassigned gaps; the decoder rejects them as
illegal:

`0x0D`, `0x19`, `0x35`

All other byte values outside the assigned ranges are likewise illegal.
