# Logical Boolean

Operands are treated as booleans: \\(0\\) is false, any non-zero value is true.
Results are \\(1_{i64}\\) or \\(0_{i64}\\).

| Code | Mnemonic | Stack Effect | Description |
|------|----------|--------------|-------------|
| `0x36` | `NOT` | \\([\ldots, a] \to [\ldots, [a = 0]]\\) | Logical NOT. |
| `0x37` | `AND` | \\([\ldots, a, b] \to [\ldots, [a \neq 0 \;\wedge\; b \neq 0]]\\) | Logical AND. Both operands are already popped; no short-circuit. |
| `0x38` | `OR` | \\([\ldots, a, b] \to [\ldots, [a \neq 0 \;\vee\; b \neq 0]]\\) | Logical OR. |
| `0x39` | `XOR` | \\([\ldots, a, b] \to [\ldots, [a \neq 0 \;\oplus\; b \neq 0]]\\) | Logical XOR. True iff exactly one operand is non-zero. |

None of these instructions have register effects.

## Logical vs. Bitwise

These instructions perform **logical** (boolean) operations. For bitwise
operations on the raw `i64` bit pattern, see the
[Bitwise](bitwise.md) instructions (`BAND`, `BOR`, `BXOR`, `BNOT`).

The key difference: \\(\text{NOT}\; 5 = 0\\) (logically false), while
\\(\text{BNOT}\; 5 = \mathord{\sim}5\\) (bitwise complement, a large negative number).
