# Bitwise

`BAND`, `BOR`, `BXOR` and `BNOT` operate on the raw two's-complement bit
pattern of an `i64`; `SHL` and `SHR` shift it. Byte values, operand layouts
and stack effects are in the [Bitwise](../opcodes.md#bitwise) section of
the opcode reference. None of these instructions touch a register, and
none but the shifts can fail: `BAND`/`BOR`/`BXOR`/`BNOT` are defined for
every `i64` bit pattern, with no invalid input. `SHL` and `SHR` fault on
a shift amount outside \\([0, 64)\\), and `SHL` also faults when the
shift would lose a significant bit.

For the boolean-algebra equivalents that treat `0`/non-zero as
false/true instead of operating bit by bit, see [Logical](logical.md).

## Shift Behaviour

`SHL` performs a left shift, filling the vacated low bits with zero. It
does not discard bits that leave the high end: a shift that loses a
significant bit takes the value outside the `i64` range, so it raises
`ArithmeticOverflow` like any other overflowing operation. `PUSH
4611686018427387904; PUSH 1; SHL` fails rather than yielding
`i64::MIN`. Shifting the result back recovers the operand exactly
whenever nothing was lost, which is the test the VM applies.

`SHR` performs an **arithmetic** (sign-preserving) right shift, not
a logical (zero-filling) one: the sign bit is replicated into the vacated
high bits, so a negative operand stays negative. `-8 >> 1` yields `-4`,
and `i64::MIN >> 1` yields `-4611686018427387904`, half the magnitude of
`i64::MIN` and still negative, rather than a small positive number a
logical shift would produce. This matches Rust's `i64 >> b` operator and
Python's `>>` on integers.

Both instructions require the shift amount to satisfy \\(0 \le b < 64\\)
and error `InvalidShift` otherwise; a shift by 64 or more is not defined
as "shift everything out to zero" the way it might be on some hardware,
it is rejected outright: `PUSH 8; PUSH 64; SHR` fails with `invalid shift
amount 64`.

Where a logical right shift is actually needed, mask the sign-extended
bits off after `SHR` with `BAND`. For a shift of exactly 1, `BAND` with
`0x7FFFFFFFFFFFFFFF` (`i64::MAX`) clears only the replicated sign bit,
which is everything an arithmetic shift by 1 could have filled from the
sign: `SHR -8 1` followed by masking with `i64::MAX` gives
`9223372036854775804`, the same result a zero-filling right shift by 1
would give. A shift by more than 1 replicates the sign
into more than one bit, so the mask has to widen to match: `BAND` with a
fixed `i64::MAX` mask only emulates a logical shift for `b = 1`.
