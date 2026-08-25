# Energy Evaluation

`ENERGY` is the sole instruction in this category: it evaluates a model's
Hamiltonian against a candidate sample and pushes the result. Byte value,
operand layout and stack effect are in the
[XQMX High-Level Constraints](../opcodes.md#xqmx-high-level-constraints)
section of the opcode reference. See [Allocators](allocators.md) for how a
`Model` and a `Sample` are built in the first place.

## `ENERGY model sample`

**Register effect:** `read` -- both `model` and `sample` are read-only

`ENERGY` takes two register operands, `model` and `sample`. Both checks are
strict: the `model` register must hold a `Model`, the `sample` register must
hold a `Sample`, and a `RegisterType` error is raised if either register
holds any other variant -- a `Model` cannot be passed in the `sample` slot
or vice versa.

To populate a sample with concrete variable assignments, construct an
`xqvm::XqmxSample` in the host and pass it to the program through a
calldata slot, then `INPUT` it into a register before calling `ENERGY`.
At the VM level, `SETLINE` and `ADDLINE` can also write a sample's
per-variable assignment values in place, the same way they write a
model's linear bias map -- see [Coefficient Access](coefficient-access.md).
`xquad verify` requires a `Model` register for that instruction family,
though, so a program that mutates a sample this way cannot pass
verification; building a new `XqmxSample` in the host and passing it in
through calldata is the supported way to change what a verified program
evaluates.

## Hamiltonian

Evaluates the quadratic Hamiltonian:

$$E = \sum_{i} \text{linear}[i] \cdot x_i \;+\; \sum_{i \le j} \text{quad}[i,j] \cdot x_i \cdot x_j$$

where \\(x_i\\) is `sample.values[i]`, the variable assignment at index
\\(i\\). The sum runs over \\(i \le j\\): the diagonal is legal, and a
self-coupling `quad[i,i]` is evaluated as \\(\text{quad}[i,i] \cdot x_i
\cdot x_i\\) like any other entry. The result is pushed as `i64`.

## Overflow Is a Fault, Not a Wrap

Every term product and every partial sum is range-checked, and one that
leaves the `i64` range raises `ArithmeticOverflow`. The check is per
step rather than on the final total, so a model whose mathematical energy
is perfectly representable still faults when an intermediate is not.
Accumulation runs in sorted key order, linear terms before quadratic
ones, and that order is normative precisely because it decides which
partial sums a checked implementation sees.

Take a 3-variable binary model with `linear = {0: i64::MAX, 1: 1, 2: -1}`
and a sample assigning all three variables `1`. The energy is
\\(2^{63} - 1 + 1 - 1 = 2^{63} - 1\\), which is `i64::MAX` and representable.
Wrapping arithmetic would have computed it correctly, overflowing to
`i64::MIN` at the second term and back at the third. Per-step checking
raises at the second term instead:

```
Error: xqvm::runtime_error

  × arithmetic overflow
```

Reordering the model's coefficients so no partial sum leaves the range
makes the same program succeed. A program that relies on cancellation
between large terms has to be written to keep every running total
representable.

## Errors

- **`RegisterType`** -- if `model` is not a `Model` or `sample` is not a `Sample`.
- **`SizeMismatch`** -- if \\(\lvert\text{sample}\rvert \neq \text{model.size}\\).
- **`ArithmeticOverflow`** -- if any term product or partial sum leaves the `i64` range.

## Example

```asm
; Build a 2-variable binary model in r0:
;   linear[0] = 3, linear[1] = -2, quad[0,1] = 5.
PUSH 2
BQMX r0

PUSH 0
PUSH 3
SETLINE r0
PUSH 1
PUSH -2
SETLINE r0

PUSH 0
PUSH 1
PUSH 5
SETQUAD r0

; A freshly-allocated binary sample is initialised to all zeros, so
; H(0, 0) = 0.
PUSH 2
BSMX r1

ENERGY r0 r1
HALT
```

In this example, the sample is `[0, 0]` and the Hamiltonian evaluates to
\\(E = 0\\). To exercise a non-zero assignment, construct an `XqmxSample` in
host code with `XqmxSample::new(Domain::Binary, vec![1, 1])` and `INPUT` it
into `r1` before calling `ENERGY`.
