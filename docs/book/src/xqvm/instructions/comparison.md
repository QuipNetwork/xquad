# Comparison

`EQ`, `LT`, `GT`, `LTE` and `GTE` compare two signed `i64` values and push
exactly `1` (true) or `0` (false). Byte values, operand layouts and stack
effects are in the [Comparison](../opcodes.md#comparison) section of the
opcode reference. None of these instructions touch a register, and none
of them can fail beyond the ordinary `StackUnderflow` every popping
instruction shares; every `i64` value is comparable to every other.

The Iverson bracket notation \\([P]\\), used throughout this book, equals
\\(1\\) if \\(P\\) is true and \\(0\\) otherwise; `EQ a b` is exactly
\\([a = b]\\), and the other four follow the same pattern.

## Boolean Convention

XQVM uses the integer convention for booleans: `0` is false, any non-zero
value is true. Comparison instructions always produce exactly `1` or `0`,
never some other non-zero encoding of "true", which is what makes their
output directly usable as a `JUMPI` condition or as an operand to
[Logical](logical.md) `AND`/`OR`/`XOR`/`NOT` without an extra normalisation
step. `PUSH 3; PUSH 5; LT` pushes `1`, and `PUSH 3; PUSH 5; GT` pushes `0`,
both read back through `STOW`/`OUTPUT`.
