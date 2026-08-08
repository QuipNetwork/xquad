# Introduction

XQVM is a hardware-agnostic virtual machine for quantum computing. It is the
module within the Aglais platform responsible for expressing binary optimisation
models and objective functions. The current scope targets X-quadratic models for
quantum annealers (QUBO/Ising formulations).

Think of it as **LLVM for quantum computing**: write a problem once, compile it
to XQVM bytecode, and run it on any supported backend.

## Goals

- **Hardware-agnostic** -- write a problem once, run it on any supported backend.
- **Unified bytecode** -- a common intermediate representation for binary
  optimisation problems targeting quantum annealers.
- **Embeddable** -- the core VM and bytecode crates support `no_std + alloc`,
  enabling deployment in WASM runtimes, bare-metal environments, and Substrate
  pallets.

## Workspace Crates

The project is organised into five Rust crates:

| Crate | Binary | Description |
|---|---|---|
| `aglais-xqvm-bytecode` | -- | Opcode table, instruction types, builder, binary codec, stream reader |
| `aglais-xqvm-asm` | -- | Text assembler: `.xqasm` source &rarr; bytecode |
| `aglais-xqvm-disasm` | -- | Bytecode &rarr; human-readable listing |
| `aglais-xqvm-vm` | -- | Bytecode interpreter: stack, register file, QUBO/Ising model execution |
| `aglais-xqvm-cli` | `xq` | Unified CLI driver (`xq asm`, `xq dism`, `xq run`) |

## Architecture at a Glance

XQVM is a **stack-based interpreter** with a **256-slot register file**. The
value stack holds `i64` integers. Registers hold typed values (`RegVal`):
integers, integer vectors, QUBO/Ising models (`XqmxModel`), model vectors, and
candidate solutions (`XqmxSample`). A dedicated loop stack drives `RANGE`/`ITER`
iteration.

The instruction set comprises **93 instructions** across 14 categories:
control flow, register I/O, stack manipulation, arithmetic, comparison, logical,
bitwise, allocators, vector operations, index math, coefficient access, grid
operations, high-level constraints, and energy evaluation.

The opcode table (`opcodes!` x-macro in `crates/bytecode/src/types/table.rs`) is
the single source of truth. The `Opcode` enum, `Instruction` enum, mnemonic
strings, operand arity, codec, and builder methods are all derived from it.

Programs are serialised as a jump table followed by a raw instruction stream.
Each instruction is an opcode byte followed by its operands in big-endian byte
order.

## What This Book Covers

- **[Getting Started](start/README.md)** -- installation, building, and
  running your first program.
- **[CLI Reference](xqvm/cli/README.md)** -- the `xq` command-line tool.
- **[VM Architecture](xqvm/machine-model.md)** -- stack, registers, loops,
  I/O, and the execution model.
- **[Assembly Language](xqvm/assembly.md)** -- the `.xqasm` syntax.
- **[Instruction Set Reference](xqvm/instructions/README.md)** -- all 93
  instructions with full semantics.
- **[Bytecode Format](xqvm/bytecode-format.md)** -- the binary wire format.
- **[Builder API](embedding/builder-api.md)** -- programmatic bytecode construction in
  Rust.
- **[Pallet Integration](embedding/pallet.md)** -- running XQVM on-chain
  via a Substrate pallet.
- **[Examples](examples/README.md)** -- worked examples including a Travelling
  Salesman Problem.

## License

Licensed under the
[GNU Affero General Public License v3.0 or later](https://www.gnu.org/licenses/agpl-3.0.html).
