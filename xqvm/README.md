# xqvm

X-Quadratic Virtual Machine -- bytecode interpreter for the [XQuad Toolchain](https://gitlab.com/quip.network/xquad).

`xqvm` is the runtime layer of XQuad: the opcode table, instruction types, a
bytecode builder and codec, an incremental instruction-stream reader, the
VM interpreter, and a disassembler. A problem compiled once to XQVM
bytecode runs unchanged on this interpreter or on the pure-Python
reference VM (`xqvm_py`); the two are checked for behavioural parity in CI.

The crate is `no_std + alloc`-compatible. The bytecode layer (opcode
table, instruction codec, `InstructionBuilder`, `Program`) and the
interpreter core always build without `std`. The `std` feature, enabled
by default, adds the disassembler, JSON/text execution tracers, and
`miette`-backed runtime diagnostics.

## Install

```sh
cargo add xqvm
```

For a `no_std` target (WASM runtimes, Substrate pallets, embedded):

```sh
cargo add xqvm --no-default-features
```

## Quick start

Build a program with `InstructionBuilder`, then run it on `Vm`:

```rust
use xqvm::{InstructionBuilder, Vm};

let mut builder = InstructionBuilder::new();
builder.emit_push(10).emit_push(32).emit_add().emit_halt();
let program = builder.build().unwrap();

let mut vm = Vm::new();
vm.run(&program).unwrap();
assert_eq!(vm.stack(), &[42]);
```

This builds the same four instructions (`PUSH 10`, `PUSH 32`, `ADD`,
`HALT`) that the [`xqasm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqasm)
text assembler produces from source; see that crate to load `.xqasm`
files instead of building bytecode by hand.

## What is in this crate

| Layer | Contents |
|---|---|
| Bytecode (`no_std`) | `opcodes!` x-macro (single source of truth for the opcode table), `Opcode`, `Instruction`, `Register`, `Program`, `InstructionBuilder`, `InstructionStream`, `bytecode::codec` |
| Interpreter | `Vm`, `Error`, `RegVal`, `XqmxModel` / `XqmxSample` (QUBO/Ising/discrete models and candidate solutions) |
| `std`-only | `disasm::Disassembly`, `RuntimeDiagnostic`, `JsonTracer` / `TextTracer` |

## Wire format

A `.xqb` file opens with a fixed 15-byte XQBC header (magic, version,
calldata/output-slot counts, code length, and a CRC-32 checksum of the
payload), followed immediately by the instruction stream: an opcode byte
followed by its operands in big-endian byte order. See the encoding spec
linked below for the full field layout.

## Links out

- [Normative VM spec](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/SPEC.md)
- [Bytecode encoding spec](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ENCODING.md)
- [`xqasm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqasm) -- text assembler that emits `xqvm::Program`
- [`xqcli`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcli) -- the `xquad` CLI, built on this crate
- [Repository](https://gitlab.com/quip.network/xquad)

## License

AGPL-3.0-or-later.
