# Comparison

Results are \\(1_{i64}\\) (true) or \\(0_{i64}\\) (false). All comparisons are signed.

| Code | Mnemonic | Stack Effect | Description |
|------|----------|--------------|-------------|
| `0x30` | `EQ` | \\([\ldots, a, b] \to [\ldots, [a = b]]\\) | Signed equality. |
| `0x31` | `LT` | \\([\ldots, a, b] \to [\ldots, [a < b]]\\) | Signed less-than. |
| `0x32` | `GT` | \\([\ldots, a, b] \to [\ldots, [a > b]]\\) | Signed greater-than. |
| `0x33` | `LTE` | \\([\ldots, a, b] \to [\ldots, [a \le b]]\\) | Signed less-or-equal. |
| `0x34` | `GTE` | \\([\ldots, a, b] \to [\ldots, [a \ge b]]\\) | Signed greater-or-equal. |

None of these instructions have register effects.

The Iverson bracket notation \\([P]\\) equals \\(1\\) if \\(P\\) is true, \\(0\\) otherwise.

## Boolean Convention

XQVM uses the integer convention for booleans: \\(0\\) is false, any non-zero value
is true. Comparison instructions always produce exactly \\(1\\) or \\(0\\), making them
directly usable as `JUMPI` conditions or logical operands.
