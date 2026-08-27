# Coefficient Access

Read and write the linear (bias) and quadratic (coupling) terms of the
Hamiltonian

$$H(x) = \sum_i \text{linear}[i] \cdot x_i + \sum_{i \le j} \text{quadratic}[i, j] \cdot x_i \cdot x_j$$

that a [Model](allocators.md) accumulates. Byte values, operand layouts and
stack effects are in the
[XQMX Coefficient Access](../opcodes.md#xqmx-coefficient-access) section of
the opcode reference. All coefficient values are `i64`.

## Linear Access Also Reads Samples at the VM Level, but Not Under Verification

At the VM level, `GETLINE`, `SETLINE` and `ADDLINE` accept `reg` holding
either a `Model` or a `Sample`, not a `Model` only: on a `Model` register
they read or write the sparse bias map, and on a `Sample` register the
identical instruction reads or writes the sample's per-variable assignment
at the same index instead. `GETLINE r0` on a freshly allocated `BSMX`
sample (no coefficients, only assignments) runs without a `RegisterType`
error and returns the assigned value at that index.

`xquad verify` rejects the same program.
[`xqvm/src/verifier/reg_type.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/verifier/reg_type.rs)
groups `GETLINE`, `SETLINE`, `ADDLINE`, `GETQUAD`, `SETQUAD`, `ADDQUAD`,
`ONEHOTR`, `ONEHOTC`, `EXCLUDE` and `IMPLIES` under a single check that
requires `Model`, with no exception for the linear trio on a `Sample`:
`GETLINE r0` on the program above fails verification with `register r0 at
byte 0x0006: expected model, got sample`. So the VM's sample-mode linear
access cannot appear in any program that passes `xquad verify` -- the VM
and the verifier disagree about what `reg` may hold for `GETLINE`,
`SETLINE` and `ADDLINE`, and the verifier is what gates a program before
it runs in a verified pipeline. Use a `Model` register for the linear trio
if the program must pass verification; this is a known VM-versus-verifier
disagreement, not a documented alternative.

The quadratic trio, `GETQUAD`, `SETQUAD` and `ADDQUAD`, has no sample
equivalent at either level and rejects a `Sample` register with
`RegisterType` ("expected model, got sample"), since a sample has no
coupling terms to read or write.

## Every Index Is Bounds-Checked

All six instructions bounds-check their popped indices against the
register's declared size (`model.size` or `sample.values.len()`) and error
`IndexOutOfBounds` if an index is negative or at least `size`: `GETLINE r0`
for `i = 99` on a 4-variable model fails with `index 99 out of bounds (len
4)`, even though the coefficient map holds no fixed-size backing array. The
size bound comes from the model's declared variable count, not from the map
itself, which is why an absent in-range coefficient reads as `0` while a
read past the end raises.

Reads are bounded exactly as writes are. `GETQUAD r0` for `(i, j) = (99,
100)` on a 4-variable model raises rather than answering `0`, because a
`GETQUAD` that reads back `0` from a pair `SETQUAD` refuses would leave the
family disagreeing with itself about which variables exist.

`SETQUAD` and `ADDQUAD` check `i` before `j`, and both before the `i > j`
normalisation below, so the reported index is the operand the program
supplied rather than whichever one sorted lower.

The same bound covers `EXCLUDE` and `IMPLIES`, which write coefficients
through the same surfaces. Nothing in either family can put a coefficient
outside the variable count a `BQMX`/`SQMX`/`XQMX` call declared: an
unbounded write would grow the sparse map and leave the model carrying a
constraint over variables that do not exist, which still solves cleanly and
answers wrongly.

## Sparse Storage

Coefficients live in sparse `BTreeMap` structures, not a fixed-size array:

- **Linear:** `BTreeMap<usize, i64>` keyed by variable index.
- **Quadratic:** `BTreeMap<(usize, usize), i64>` keyed by variable pair.

An absent key reads as `0`. Setting a coefficient to exactly `0` removes
its entry rather than storing a zero, for both the linear and the
quadratic map, so memory usage tracks the number of non-zero terms rather
than the number of `SETLINE`/`SETQUAD` calls ever made; writing `0` and
never reading that index again leaves no trace in the map.

## Key Normalisation

Quadratic keys are normalised so that \\(i \le j\\): `SETQUAD`, `ADDQUAD`
and `GETQUAD` all swap `i` and `j` internally when `i > j`, so
\\(\text{quad}[3, 5]\\) and \\(\text{quad}[5, 3]\\) name the same entry.
`SETQUAD r0` with `(i, j) = (5, 3)` followed by `GETQUAD r0` with
`(i, j) = (3, 5)` returns the value just written, not `0`.
