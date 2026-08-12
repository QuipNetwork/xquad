# Logical Boolean

`NOT`, `AND`, `OR` and `XOR` treat their operands as booleans under the
integer convention: `0` is false, any non-zero value, including negative
values, is true. Results are always exactly `1` or `0`. Byte values,
operand layouts and stack effects are in the
[Logical Boolean](../opcodes.md#logical-boolean) section of the opcode
reference. None of these instructions touch a register.

`AND`, `OR` and `XOR` each pop both operands unconditionally before
producing a result; there is no short-circuit evaluation the way there
would be in a language with lazy boolean operators, since a stack VM has
already evaluated both operand expressions and pushed both values by the
time the logical instruction runs. This only matters if evaluating one of
the operand expressions has a side effect, register writes, or a
[Coefficient Access](coefficient-access.md) mutation, that a
short-circuiting language would skip; `AND`/`OR`/`XOR` never skip it.

## Logical vs. Bitwise

These instructions perform boolean operations on the truthiness of a
value, not on its bit pattern. For bit-pattern operations, see
[Bitwise](bitwise.md) (`BAND`, `BOR`, `BXOR`, `BNOT`). The two families
diverge sharply outside `{0, 1}` inputs: `NOT 5` is `0` (`5` is truthy, so
its logical negation is false), while `BNOT 5` is `~5`, the bitwise
complement, a large negative number (`-6`). `NOT` on `5` returns `0`, and
`BNOT` on `5` returns `-6`, from the same program run against both
instructions.
