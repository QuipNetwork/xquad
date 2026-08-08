# Energy Evaluation

## `0x7F` -- `ENERGY model sample`

**Stack:** \\([\ldots] \to [\ldots, E]\\)
**Register effect:** `read` -- both `model` and `sample` are read-only

This is the only instruction with two register operands.

The `model` register must hold a `Model` and the `sample` register must hold
a `Sample`. Both checks are strict: a `RegisterType` error is raised if either
register holds the wrong kind of value. The previous "model-as-sample"
shortcut, where a `Model` could appear in the sample slot and have its linear
table read as variable assignments, was removed in QUI-410 to align with
`spec/xqvm/HLF.md` and the xq-py reference.

To populate a sample with concrete variable assignments, construct an
`XqmxSample` in the host (via `aglais_xqvm_vm::XqmxSample`) and pass it to the
program through a calldata slot, then `INPUT` it into a register before
calling `ENERGY`. xq-rs does not currently expose a bytecode opcode to mutate
sample values in-place.

## Hamiltonian

Evaluates the quadratic Hamiltonian:

$$E = \sum_{i} \text{linear}[i] \cdot x_i \;+\; \sum_{i < j} \text{quad}[i,j] \cdot x_i \cdot x_j$$

The result is pushed as `i64`. Arithmetic uses wrapping semantics on overflow.

## Errors

- **`RegisterType`** -- if `model` is not a `Model` or `sample` is not a `Sample`.
- **`SizeMismatch`** -- if \\(\lvert\text{sample}\rvert \neq \text{model.size}\\).

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
