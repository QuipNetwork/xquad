# CLI Reference

The `xq` binary is the unified command-line interface for XQVM. It provides
three subcommands:

| Command | Description |
|---------|-------------|
| [`xq asm`](asm.md) | Assemble `.xqasm` source into binary bytecode. |
| [`xq dism`](dism.md) | Disassemble bytecode into a human-readable listing. |
| [`xq run`](run.md) | Execute bytecode or assembly with optional tracing. |

## Installation

Build from source:

```sh
cargo build --release
```

The binary is at `target/release/xq`.

## General Usage

```sh
xq <COMMAND> [OPTIONS] [ARGS]
xq --help
xq <COMMAND> --help
```
