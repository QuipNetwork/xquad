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

## Wrapping Semantics

Every arithmetic instruction on this page uses Rust's `wrapping_*` methods
on `i64`: `ADD`, `SUB`, `MUL`, `NEG`, `ABS`, `SQR`, `INC` and `DEC` all wrap
silently on overflow rather than trapping:

```asm
PUSH 9223372036854775807   ; i64::MAX
PUSH 1
ADD                        ; wraps to i64::MIN
```

reading the result back with `STOW`/`OUTPUT` produces
`-9223372036854775808`, not an error. Two more wrap the same way: `ABS` of
`i64::MIN` stays `i64::MIN` rather than becoming positive (there is no
positive `i64` with that magnitude to wrap to), and `i64::MIN * -1` wraps
back to `i64::MIN` for the same reason.

[`spec/xqvm/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/SPEC.md)
specifies that overflow raises `ArithmeticOverflow` by default, and
permits silent wrapping only as an implementation-defined choice for
fixed-width backends, explicitly calling programs that rely on it
non-portable. The `xquad` Rust VM takes that permitted choice: it always
wraps and never raises `ArithmeticOverflow` for any arithmetic, shift or
`INC`/`DEC`/`NEG`/`ABS`/`SQR` opcode, and there is no configuration flag to
opt into trapping instead. The fact worth acting on is that the two
reference interpreters differ: `xqvm_py` raises `ArithmeticOverflow` on
the same program, while the Rust VM wraps. Write bytecode that keeps
values away from the `i64` boundary rather than relying on either
behaviour.

<!-- xquad:defect QUI-998 -->
> **Known issue.** The spec leaves overflow implementation-defined, and the two
> reference interpreters took opposite options: the same program wraps on the
> Rust VM and raises `ArithmeticOverflow` on the Python VM. Keep values away
> from the `i64` boundary rather than relying on either behaviour. Report
> problems at the [issue tracker](https://gitlab.com/quip.network/xquad/-/issues).

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
