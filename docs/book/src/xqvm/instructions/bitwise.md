# Bitwise

Operate on raw `i64` bit patterns.

| Code | Mnemonic | Stack Effect | Description |
|------|----------|--------------|-------------|
| `0x3A` | `BAND` | \\([\ldots, a, b] \to [\ldots, a \mathbin{\\&} b]\\) | Bitwise AND. |
| `0x3B` | `BOR` | \\([\ldots, a, b] \to [\ldots, a \mathbin{\mid} b]\\) | Bitwise OR. |
| `0x3C` | `BXOR` | \\([\ldots, a, b] \to [\ldots, a \oplus b]\\) | Bitwise XOR. |
| `0x3D` | `BNOT` | \\([\ldots, a] \to [\ldots, \mathord{\sim}a]\\) | Bitwise NOT (one's complement). |
| `0x3E` | `SHL` | \\([\ldots, a, b] \to [\ldots, a \ll b]\\) | Left shift. \\(b\\) must satisfy \\(0 \le b < 64\\); otherwise errors. |
| `0x3F` | `SHR` | \\([\ldots, a, b] \to [\ldots, a \gg b]\\) | Arithmetic (sign-preserving) right shift. \\(b\\) must satisfy \\(0 \le b < 64\\). The sign bit is replicated. |

None of these instructions have register effects.

## Shift Behaviour

- `SHL` performs a signed left shift. Bits shifted out of the high end are
  discarded. The shift amount must be in \\([0, 64)\\).
- `SHR` performs an **arithmetic** (sign-preserving) right shift, not a
  logical shift. The sign bit is replicated, so negative values stay
  negative and \\(\mathit{i64}{::}\mathit{MIN} \gg 1\\) halves the magnitude
  instead of producing a positive result. This matches Rust's `i64 >> b`
  operator and Python's `>>` on integers, and it is what xq-py
  (`spec/xqvm/ISA.md`) prescribes. Where logical (zero-filling) right shift is
  required, mask with `BAND` first.
- Both shift instructions error with `InvalidShift` if \\(b\\) is outside \\([0, 64)\\).
