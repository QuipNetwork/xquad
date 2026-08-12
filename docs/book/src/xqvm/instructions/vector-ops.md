# Vector Operations

Instructions for reading, writing and querying the `VecInt` and `VecXqmx`
containers that [Allocators](allocators.md) create. Byte values, operand
layouts and stack effects are in the
[Vector Operations](../opcodes.md#vector-operations) section of the opcode
reference. This page covers the type rules that apply across the family and
the details of `SLACK`, the one instruction here that does more than plain
container access.

## Reading, Writing and Sizing

`VECPUSH` appends to the end of a `VecInt`, growing it by one element.
`VECGET` and `VECSET` read and write by index, both bounds-checked against
the current length: an out-of-range index errors `IndexOutOfBounds` at
runtime rather than reading past the end of the vector. `VECLEN` reads the
current length as an `i64`, and is the only instruction in this family that
accepts either `VecInt` or `VecXqmx`; the other three require `VecInt`
specifically, since `VecXqmx` elements are whole models rather than
integers and there is no `VECXGET`/`VECXSET`.

```asm
VEC r0          ; r0 = empty VecInt
PUSH 10
VECPUSH r0      ; r0 = [10]
PUSH 20
VECPUSH r0      ; r0 = [10, 20]
PUSH 0
VECGET r0       ; stack top = 10
```

An `OUTPUT` of the loaded value writes `10` to the output slot, and a
`VECLEN r0` issued after the two `VECPUSH`es reads `2`.

## SLACK

`SLACK indices coeffs` is the one instruction in this family that is not a
plain accessor: it exists to turn an inequality constraint into an equality
one, by appending binary-weighted slack variables to two parallel vecs.
Pop `capacity` (top of stack), then `start_index`. Compute

$$S = \lfloor \log_2(\text{capacity}) \rfloor + 1$$

and append \\(S\\) entries to each register:

- to `indices`: \\([\text{start}, \text{start}{+}1, \ldots, \text{start}{+}S{-}1]\\),
  consecutive variable indices for the new slack variables;
- to `coeffs`: \\([1, 2, 4, \ldots, 2^{S-1}]\\), their binary weights.

`SLACK` appends rather than overwrites, so item variables and slack
variables coexist in the same `indices`/`coeffs` pair, ready to hand to
[`EQUALITY`](constraints.md). This is what makes a knapsack-style "total
weight at most `capacity`" constraint expressible as a single weighted
equality: the slack variables absorb any unused capacity, so the equality
holds exactly whenever the inequality would have held. If `capacity <= 0`,
no slack variables are needed and `SLACK` appends nothing.

```asm
VEC r5            ; indices
VEC r6            ; coeffs
; ... populate with item indices and weights ...
PUSH 3            ; start_index (first slack variable index)
PUSH 10           ; capacity
SLACK r5 r6       ; appends 4 slack entries: floor(log2(10)) + 1 = 4
```

With `start_index = 3`, `capacity = 10`: `VECLEN r5` after `SLACK` reads
`4`, `indices` starts at `3` and ends at `6` (`[3, 4, 5, 6]`), and
`coeffs` is `[1, 2, 4, 8]`, matching \\(S = 4\\) and the powers of two up
to \\(2^{S-1}\\).
