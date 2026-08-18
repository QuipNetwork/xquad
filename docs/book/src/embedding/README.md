# Embedding Overview

Everything so far in this book drives XQuad from Python or the `xquad` CLI.
This chapter is for embedding the Rust crates directly. That means building
bytecode with a fluent Rust API instead of `.xqasm` text, running it without
a Python process in the loop, and compiling for `no_std` targets.

## Crate Map

| Crate | Role | `no_std` |
|---|---|---|
| [`xqvm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm) | Opcode table, `InstructionBuilder`, the wire codec, and the `Vm` interpreter | Yes, with `--no-default-features` |
| [`xqasm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqasm) | Parses `.xqasm` text into an `xqvm::Program` | No |
| [`xqcli`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcli) | Builds the `xquad` CLI binary on top of the two crates above | No |

A Rust program that only needs to build and run bytecode depends on `xqvm`
alone; add `xqasm` to parse `.xqasm` source instead of building bytecode by
hand with [`InstructionBuilder`](builder-api.md). `xqcli` is the CLI's own
crate, useful as a reference for wiring the other two together rather than
as a library dependency.

## The `no_std` Story

`xqvm` builds `no_std + alloc`. Two layers are always compiled without
`std`:

- **Bytecode** -- the opcode table, `Instruction`, `Register`,
  `InstructionBuilder`, `Program`, and the wire codec.
- **Interpreter core** -- `Vm`, `Error`, `RegVal`, `XqmxModel` /
  `XqmxSample`, the bytecode verifier, and the `Tracer` trait with its
  no-op implementation.

The `std` feature, enabled by default, adds three things a `no_std`
embedder gives up: the disassembler (`disasm::Disassembly`), `miette`-backed
runtime diagnostics (`RuntimeDiagnostic`), and the `JsonTracer` /
`TextTracer` implementations. None of these change what a program computes;
they change how a fault or a trace is reported. Build for a `no_std` target
with:

```sh
cargo add xqvm --no-default-features
```

This is exercised, not just claimed: `fixtures/xqvm-wasm` is a freestanding
`wasm32-unknown-unknown` test crate depending on `xqvm` with
`default-features = false`, and CI's `test:wasm` job runs `cargo build -p
xqvm --target wasm32v1-none --no-default-features` followed by
`wasm-pack test --node` against it. The job is path-gated: it runs on
merge requests and feature branches whenever the diff can reach `xqvm` or
the fixture, and unconditionally on protected refs and release tags.

## Running In-Process, Without `.xqasm`

[Builder API](builder-api.md) covers `InstructionBuilder` in full: emitting
instructions one at a time, resolving forward and backward labels, and
reading back the resulting `JumpTable`. It is the API a `no_std` embedder
uses when parsing text is not an option.

## On-Chain: The Pallet Fixture

[Pallet Fixture](pallet.md) documents `fixtures/pallet-xqvm`, a Substrate
FRAME pallet that embeds `xqvm` inside a runtime as an integration gate, not
a production deployment path. It is a real, CI-tested example of a `no_std`
embedder, and the one currently in this repository.

## Checking an Implementation Against the Spec

[Conformance](conformance.md) covers the harness that holds the Rust `xqvm`
and the Python `xqvm_py` to the same observable behaviour. Read it if you
are embedding `xqvm` somewhere that needs to trust its output matches the
reference implementation.
