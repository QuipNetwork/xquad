# XQVM Technical Specification

## X-Quadratic Virtual Machine

A specialized virtual machine for encoding, verifying, and decoding quantum optimization problems. Provides a unified instruction set for manipulating quadratic models across variable domains (binary, spin, chromatic).

## Three-Program Architecture

Each optimization problem is defined by three independent programs sharing the same instruction set:

- **Encoder** — Transforms problem-specific input into an XQMX model suitable for quantum optimization.
- **Verifier** — Validates solution quality and constraint satisfaction.
- **Decoder** — Transforms quantum solution back into problem-specific output.

Programs execute independently with no shared state. Communication occurs only through calldata (`input`) and results (`output`).

---

## Machine State

```python
{
    "pc": 0,                  # Program counter
    "stack": [],              # Integer-only stack

    "registers": {            # slot (0-255) → value
        0: 42,                # int
        1: [1, 2, 3, 4],      # vec
        2: {                  # xqmx
            "mode": "model",          # "model" | "sample"
            "domain": [0, 1],         # [0,1] | [-1,1] | [-k,...,k-1]
            "size": 9,
            "rows": None,
            "cols": None,
            "linear": {},             # sparse: var_index → value
            "quadratic": {}           # sparse: (i,j) → value, i < j
        }
    },

    "jc": {
        "targets": {},        # target_id → pc
        "loop_stack": []      # loop state stack (LIFO)
    },

    "input": {},              # calldata: slot → value
    "output": {}              # results: slot → value
}
```

---

## Type System

### Stack

- Integer only. All primitive operations work on integers.
- **Value type:** signed 64-bit integer. Valid range `[-2^63, 2^63 - 1]`.
- **Overflow:** any operation that would produce a value outside the i64 range raises `ArithmeticOverflow` (same class as `DivisionByZero`, `StackOverflow`). This applies to arithmetic, shift, and `INC`/`DEC`/`NEG`/`ABS`/`SQR` opcodes, as well as integer values entering the VM through `INPUT`. Implementations backed by fixed-width 64-bit integers may instead wrap silently — such wrapping is implementation-defined and programs that rely on it are non-portable.
- Maximum depth: 8192 (2^13)

### Registers (`r0`–`r255`)

- 8-bit slot ID (0-255)
- Types: `int` | `vec` | `xqmx`
- No pointers, no type coercion
- Only `int` registers exchange with stack via `LOAD`/`STOW`
- `vec` and `xqmx` accessed only through specialized opcodes

### `vec`

- Homogeneous dynamic array: `vec<int>`, `vec<xqmx>` with support for nesting (`vec<vec<int>>`)
- Element type inferred and locked on first push, or explicit via `VECI`/`VECX` opcodes. Type validation on mutate operations
- Tracks length and capacity

### `xqmx`

- Sparse x-quadratic matrix
- **Mode:** `model` (linear & quadratic are hamiltonian coefficients) or `sample` (linear are variable assignments, quadratic is nil)
- **Domain:** `[0,1]` binary, `[-1,1]` spin, `[-k, ..., k-1]` chromatic
- **Dimension:** `size` (total linear variables), optional `rows`/`cols` for grid layout
- **Storage:** Sparse tables for `linear` and `quadratic`
- Constraint opcodes (ONEHOTR, ONEHOTC, EXCLUDE, IMPLIES, EQUALITY, ATLEAST, ATLEASTW, REDUCE) are only valid in model mode
- ENERGY computes the Hamiltonian energy of a sample against a model

---

## Runtime Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Stack depth | 8192 (2^13) | `StackOverflow` if exceeded. |
| Stack value range | `[-2^63, 2^63 - 1]` | Signed 64-bit. `ArithmeticOverflow` if exceeded. |
| Register slots | 256 (r0–r255) | 8-bit addressing. |
| Target IDs | 0–65535 | `u8` via `JUMP1`/`JUMPI1`, `u16` big-endian via `JUMP2`/`JUMPI2`. Sequential assignment during pre-scan. |
| Loop nesting | Unbounded | Limited by available memory. |
| XQMX size | Implementation-defined | No spec limit. |
| Program length | Implementation-defined | No spec limit. |

---

## Instruction Set Architecture

The XQVM defines 93 opcodes organised into 13 categories: control flow, register I/O, stack manipulation, arithmetic, comparison, logical boolean, bitwise, allocators, vector operations, index math, XQMX coefficient access, XQMX grid, and XQMX high-level functions. Each opcode specifies its stack effect, register access mode, and error conditions. Opcodes not assigned to an instruction are reserved and must be rejected by the decoder.

Full opcode tables, semantic notes, and the reserved opcode list: **[ISA.md](ISA.md)**

## High-Level Functions

A subset of opcodes (ONEHOTR, ONEHOTC, EXCLUDE, IMPLIES, EQUALITY, ATLEAST, ATLEASTW, REDUCE, ENERGY) implement high-level combinatorial constraint patterns. These opcodes expand into linear and quadratic coefficient deltas on an XQMX model, injecting QUBO penalty terms automatically. Some (ATLEAST, ATLEASTW, REDUCE) allocate auxiliary variables during execution, growing the model size.

Expansion formulas, derivations, and the ENERGY computation: **[HLF.md](HLF.md)**

## Encoding and File Formats

Programs exist in two representations. `.xqasm` is the human-readable text assembly format with line comments, register names (`r0`–`r255`), target labels (`.N`), and syntactic sugar for `PUSH` and `JUMP`/`JUMPI` families. `.xqb` is the binary bytecode format -- a 15-byte XQBC header (magic, version, slot counts, payload length, CRC-32) followed by a raw instruction stream where each instruction is encoded as an opcode byte followed by zero to eight operand bytes. Instruction length is determined solely by the opcode.

File format definitions, assembly syntax, and binary encoding rules: **[ENCODING.md](ENCODING.md)**

## Bytecode Verification

Bytecode verification phases, error semantics, composable architecture, and per-opcode stack effects: **[VERIFIER.md](VERIFIER.md)**

## Related Specifications

- **[../xqsa/SPEC.md](../xqsa/SPEC.md)** -- solver adapter interface. Defines how external solvers plug into the pipeline between encoder and verifier execution.
- **[../xqcp/SPEC.md](../xqcp/SPEC.md)** -- constraint-programming DSL. Compiles high-level problem descriptions into the three XQVM programs (encoder, verifier, decoder).
