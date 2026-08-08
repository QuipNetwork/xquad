# Arithmetic

All operations are on `i64` with **wrapping** semantics (no overflow trap).

| Code | Mnemonic | Stack Effect | Description |
|------|----------|--------------|-------------|
| `0x20` | `ADD` | \\([\ldots, a, b] \to [\ldots, a + b]\\) | Wrapping addition. |
| `0x21` | `SUB` | \\([\ldots, a, b] \to [\ldots, a - b]\\) | Wrapping subtraction. |
| `0x22` | `MUL` | \\([\ldots, a, b] \to [\ldots, a \cdot b]\\) | Wrapping multiplication. |
| `0x23` | `DIV` | \\([\ldots, a, b] \to [\ldots, \lfloor a / b \rfloor]\\) | Truncating integer division. Errors if \\(b = 0\\). |
| `0x24` | `MOD` | \\([\ldots, a, b] \to [\ldots, a \bmod b]\\) | Truncating remainder. Errors if \\(b = 0\\). |
| `0x25` | `SQR` | \\([\ldots, a] \to [\ldots, a^2]\\) | Wrapping square. |
| `0x26` | `ABS` | \\([\ldots, a] \to [\ldots, \lvert a \rvert]\\) | Wrapping absolute value. |
| `0x27` | `NEG` | \\([\ldots, a] \to [\ldots, -a]\\) | Wrapping negation. |
| `0x28` | `MIN` | \\([\ldots, a, b] \to [\ldots, \min(a, b)]\\) | Signed minimum. |
| `0x29` | `MAX` | \\([\ldots, a, b] \to [\ldots, \max(a, b)]\\) | Signed maximum. |
| `0x2A` | `INC` | \\([\ldots, a] \to [\ldots, a + 1]\\) | Wrapping increment. |
| `0x2B` | `DEC` | \\([\ldots, a] \to [\ldots, a - 1]\\) | Wrapping decrement. |
| `0x2C` | `BITLEN` | \\([\ldots, a] \to [\ldots, \lfloor\log_2(a)\rfloor + 1]\\) | Bit length. Returns 0 if \\(a \le 0\\). |

None of these instructions have register effects.

## Wrapping Semantics

All arithmetic uses Rust's `wrapping_*` methods on `i64`. This means overflow
silently wraps around rather than trapping. For example:

- \\(\texttt{i64::MAX} + 1\\) wraps to \\(\texttt{i64::MIN}\\)
- \\(\lvert\texttt{i64::MIN}\rvert\\) wraps to \\(\texttt{i64::MIN}\\) (not a positive number)
- \\(\texttt{i64::MIN} \cdot (-1)\\) wraps to \\(\texttt{i64::MIN}\\)

## Division and Remainder

`DIV` and `MOD` both error with `DivisionByZero` when the divisor is zero.
Division truncates toward zero (Rust's default integer division behaviour).

## Bit Length

`BITLEN` pops a value and pushes the number of bits needed to represent it in
binary: \\(\lfloor\log_2(a)\rfloor + 1\\). Returns 0 for non-positive inputs.

Examples: `BITLEN(1) = 1`, `BITLEN(7) = 3`, `BITLEN(8) = 4`, `BITLEN(255) = 8`.
