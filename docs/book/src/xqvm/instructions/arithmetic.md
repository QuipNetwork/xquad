# Arithmetic

The usual signed-integer operations, all on `i64`, plus two helpers that
show up often enough in penalty-weight and slack-variable arithmetic to
earn their own opcodes: `SQR`, a one-instruction shorthand for `COPY` plus
`MUL`, and `BITLEN`, which the VM has no other way to compute at all,
since there is no logarithm instruction to compose it from. Byte values,
operand layouts and stack effects are in the
[Arithmetic](../opcodes.md#arithmetic) section of the opcode reference.
None of these instructions touch a register; every operand comes off the
stack and every result goes back onto it.

## Overflow

Every arithmetic instruction on this page is checked. `ADD`, `SUB`, `MUL`,
`NEG`, `ABS`, `SQR`, `INC` and `DEC` raise `ArithmeticOverflow` when the
result would leave the `i64` range, rather than wrapping to a value the
program never asked for:

```asm
PUSH 9223372036854775807   ; i64::MAX
PUSH 1
ADD                        ; raises ArithmeticOverflow
```

The rule is on the result, not on the machine operation underneath it.
`ABS` and `NEG` of `i64::MIN` raise, because no positive `i64` has that
magnitude. `i64::MIN / -1` raises for the same reason. `i64::MIN % -1`
does *not*: the remainder is `0`, which is perfectly representable, even
though a fixed-width machine reaches it through an overflowing division.

Values built from several operations -- a constraint expansion's
coefficients, an `ENERGY` accumulation -- are checked at every step, so a
computation that leaves the range on its way to an in-range answer raises
rather than quietly recovering. Both reference interpreters implement the
same rule, so a program that raises on one raises on the other.

## Division and Remainder

`DIV` rounds toward negative infinity: it is floor division, matching
Python's `//`, not Rust's default `/`, which truncates toward zero. `MOD`
returns a remainder with the sign of the divisor, matching Python's `%`,
so the identity \\(a = \lfloor a / b \rfloor \cdot b + (a \bmod b)\\) holds
under floored division. `PUSH -7; PUSH 2; DIV` pushes `-4`, not the `-3`
that truncating division would give, and `PUSH -7; PUSH 2; MOD` pushes
`1`, not `-1`. Both error with `DivisionByZero` when the divisor is zero
rather than wrapping or producing a sentinel value: `PUSH 5; PUSH 0; DIV`
halts execution with `division by zero` instead of continuing.

## MIN, MAX and BITLEN

`MIN` and `MAX` are signed comparisons with no domain restriction. `BITLEN`
pops a value and pushes the number of bits needed to represent it in
binary, \\(\lfloor\log_2(a)\rfloor + 1\\), returning \\(0\\) for
non-positive input rather than erroring (there is no bit length for a
number that is not positive, and `0` is a more useful sentinel here than a
fault, since `BITLEN` output typically feeds straight into `SLACK`'s
capacity calculation). `BITLEN(1) = 1`, `BITLEN(7) = 3`, `BITLEN(8) = 4`,
`BITLEN(255) = 8`.
