# Coefficient Access

Read and write the linear (bias) and quadratic (coupling) terms of the
Hamiltonian

$$H(x) = \sum_i \text{linear}[i] \cdot x_i + \sum_{i \le j} \text{quadratic}[i, j] \cdot x_i \cdot x_j$$

that a [Model](allocators.md) accumulates. Byte values, operand layouts and
stack effects are in the
[XQMX Coefficient Access](../opcodes.md#xqmx-coefficient-access) section of
the opcode reference. All coefficient values are `i64`.

## Linear Access Reads and Writes Samples Too

`GETLINE`, `SETLINE` and `ADDLINE` accept `reg` holding either a `Model` or
a `Sample`: on a `Model` register they read or write the sparse bias map,
and on a `Sample` register the identical instruction reads or writes the
sample's per-variable assignment at the same index instead. `GETLINE r0` on
a freshly allocated `BSMX` sample (no coefficients, only assignments)
returns the assigned value at that index.

`xquad verify` accepts the same programs. It did not until QUI-1168:
[`xqvm/src/verifier/reg_type.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/verifier/reg_type.rs)
grouped the linear trio with the quadratic and constraint opcodes under a
single check requiring `Model`, so `GETLINE r0` on the program above failed
with `register r0 at byte 0x0006: expected model, got sample` even though
the VM ran it. `spec/xqvm/ISA.md` had said all along that the linear
opcodes accept either mode, so that was a defect in the verifier rather
than a documented restriction, and it is fixed.

The quadratic trio, `GETQUAD`, `SETQUAD` and `ADDQUAD`, has no sample
equivalent at either level and rejects a `Sample` register with
`RegisterType` ("expected model, got sample"), since a sample has no
coupling terms to read or write.

## Sample Writes Are Domain-Checked

A sample's entry is an assignment, so it has to be a value the variable can
actually take. `SETLINE` and `ADDLINE` raise `SampleOutOfDomain` when the
value they would store is not a member of the register's domain -- `{0, 1}`
for `BSMX`, `{-1, +1}` for `SSMX`, `{0, ..., k-1}` for `XSMX`. Note that
`0` is out of domain for spin: the spin domain has two members and a gap
between them, so a check written as a range would wrongly admit it.

`ADDLINE` checks the result of the addition, not the delta. Adding `1` to a
binary variable already holding `1` raises; adding `-1` to it succeeds and
leaves `0`, even though `-1` is not itself a binary value.

A `Model` register is never checked. Its `linear[i]` is a bias coefficient,
whose magnitude is the weight the objective gives that variable and has
nothing to do with the values the variable may take -- a binary model
routinely carries large negative biases.

The guarantee is about the two opcodes and not about the register. A host
that installs a whole sample through calldata bypasses them, and `GETLINE`
will read back whatever it installed; the Python and FFI bindings validate
at that boundary instead.

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
