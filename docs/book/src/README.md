# Introduction

XQuad is a hardware-agnostic toolchain for expressing and running quadratic
optimisation problems -- QUBO, Ising, and discrete formulations -- on quantum
annealers and classical solvers. A problem is written once against the XQVM
instruction set and runs unchanged on any backend the toolchain supports.

Think of it as **LLVM for quantum computing**: write a problem once, compile
it to XQVM bytecode, and run it on any supported backend.

## What XQuad solves

Combinatorial optimisation problems -- travelling salesman, graph colouring,
knapsack, set cover, and their relatives -- can be expressed as quadratic
binary models and handed to a quantum annealer or a classical sampler. XQuad
gives that pipeline a single intermediate representation, XQVM bytecode, and
a reference virtual machine, so the same compiled program runs on a D-Wave
QPU, a local simulated-annealing sampler, or the Quip network without a
rewrite. See [Quadratic Models](concepts/quadratic-models.md) for what a
QUBO/Ising model actually is, and [Backends](concepts/backends.md) for the
solvers XQuad targets.

## The real components

The toolchain is dual-language: a Rust core (VM, assembler, bytecode, CLI)
with Python interfaces (reference VM, constraint-programming DSL, solver
adapters, FFI bindings).

Three Rust crates, published to crates.io:

| Crate | Binary | Role |
|---|---|---|
| [`xqvm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm) | -- | Bytecode definitions, opcode table, instruction builder, binary codec, an incremental instruction-stream reader, the VM interpreter, and a disassembler |
| [`xqasm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqasm) | -- | Text assembler: `.xqasm` source to bytecode |
| [`xqcli`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcli) | `xquad` | The unified CLI -- `xquad asm`, `xquad dism`, `xquad run`, `xquad verify` |

A fourth crate, [`xqffi`](https://gitlab.com/quip.network/xquad/-/tree/main/xqffi),
is a PyO3 bridge that exposes `xqvm` and `xqasm` to Python. It ships as a
Python wheel rather than a crates.io library.

Five Python distributions, published to PyPI:

| Package | Role |
|---|---|
| [`xqvm_py`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm_py) | Pure-Python reference VM, used as the cross-implementation conformance oracle |
| [`xqcp`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcp) | High-level constraint-programming DSL that compiles to XQVM assembly |
| [`xqsa`](https://gitlab.com/quip.network/xquad/-/tree/main/xqsa) | Solver adapters for XQMX models: local simulated annealing, D-Wave, and the Quip network |
| [`xqffi`](https://gitlab.com/quip.network/xquad/-/tree/main/xqffi) | The PyO3 FFI bindings crate above, packaged as a wheel |
| [`xquad`](https://gitlab.com/quip.network/xquad/-/tree/main/xquad) | Umbrella package re-exporting `xqffi`, `xqcp`, and `xqsa` under one namespace, with an interactive `Program` / `Session` / `RunResult` API |

Parity on every committed conformance vector between the Rust `xqvm`
interpreter and the Python `xqvm_py` reference VM is enforced mechanically:
each vector runs on both implementations in CI, and disagreement fails the
build. See [Conformance](embedding/conformance.md) for what a vector does
and does not cover.

## Architecture at a glance

XQVM is a stack-based interpreter with a 256-slot register file. The value
stack holds `i64` integers; registers hold typed values (`RegVal`): integers,
integer vectors, QUBO/Ising/discrete models (`XqmxModel`), model vectors, and
candidate solutions (`XqmxSample`). A dedicated loop stack drives `RANGE` and
`ITER` iteration.

The instruction set comprises 93 instructions, declared once in the
`opcodes!` table at
[`xqvm/src/bytecode/types/table.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm/src/bytecode/types/table.rs).
The `Opcode` enum, the `Instruction` enum, mnemonic strings, and operand
arity are all derived from that single table.

Every `.xqb` file opens with a fixed 15-byte XQBC header -- magic bytes,
format version, calldata/output-slot counts, instruction-stream length, and
a CRC-32 checksum -- followed by the raw instruction stream: an opcode byte
followed by its operands in big-endian byte order. See
[Bytecode Format](xqvm/bytecode-format.md) for the full field layout.

## Where to go next

- **[Getting Started](start/README.md)** -- install the toolchain and run
  your first program.
- **[Concepts](concepts/README.md)** -- what a QUBO/Ising model is, the ways
  to use XQuad, and the backends it targets.
- **[Modelling with XQCP](modelling/README.md)** -- write a problem with the
  constraint-programming DSL.
- **[Running Programs](running/README.md)** and
  **[Solving](solving/README.md)** -- execute a compiled program and hand it
  to a solver.
- **[XQVM Reference](xqvm/README.md)** -- the machine model, assembly
  language, instruction set, and bytecode format.
- **[Embedding](embedding/README.md)** -- using the Rust crates directly,
  `no_std` support, and cross-implementation conformance.
- **[Examples](examples/README.md)** -- worked problems including a
  Travelling Salesman Problem.

## License

Licensed under the
[GNU Affero General Public License v3.0 or later](https://www.gnu.org/licenses/agpl-3.0.html).
