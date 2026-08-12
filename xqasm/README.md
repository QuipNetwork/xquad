# xqasm

Text assembler for the [XQuad Toolchain](https://gitlab.com/quip.network/xquad).

`xqasm` turns `.xqasm` assembly source into `xqvm::Program` bytecode: a
[`pest`](https://crates.io/crates/pest) grammar parses source text into an
AST, the assembler lowers the AST to instructions via
`xqvm::InstructionBuilder`, and errors are reported as rich
[`miette`](https://crates.io/crates/miette) diagnostics with source spans.
This crate declares no binary; the `xquad asm` subcommand in
[`xqcli`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcli) is the
command-line entry point.

## Install

```sh
cargo add xqasm
```

## Quick start

The one-call entry point is `assemble_source`:

```rust
use xqasm::assemble_source;
use xqvm::Vm;

let program = assemble_source("PUSH 10\nPUSH 32\nADD\nHALT").unwrap();

let mut vm = Vm::new();
vm.run(&program).unwrap();
assert_eq!(vm.stack(), &[42]);
```

For finer control over the two phases, `parse` and `assemble` are exposed
separately:

```rust
use xqasm::{assemble, parse};

let source = "PUSH 10\nPUSH 32\nADD\nHALT";
let lines = parse(source, "<input>").unwrap();
let program = assemble(&lines, source, "<input>").unwrap();
assert!(!program.code().is_empty());
```

## Assembly syntax, briefly

- Line comments start with `;` and run to end of line.
- Jump targets are declared with `TARGET .N` and referenced as `JUMP .N` /
  `JUMPI .N`, where `.N` is a numeric label ID (`.0`, `.1`, and so on)
  assigned sequentially during pre-scan; the `.N` text is assembler-only and
  never appears in the bytecode.
- `PUSH <value>` desugars to the smallest `PUSH1`-`PUSH8` opcode that fits
  the signed integer literal.
- Registers are `r0`..`r255`.

## Links out

- [Bytecode encoding spec](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ENCODING.md) -- assembly syntax and binary format
- [Normative VM spec](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/SPEC.md)
- [`xqvm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm) -- bytecode types and interpreter this crate targets
- [`xqcli`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcli) -- the `xquad` CLI, wraps this crate as `xquad asm`
- [Repository](https://gitlab.com/quip.network/xquad)

## License

AGPL-3.0-or-later.
