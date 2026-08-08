# Getting Started

This chapter walks through installing the toolchain, building the project, and
running your first XQVM program.

## Prerequisites

```sh
# Install Rust (stable)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install development tools (clippy, rustfmt, taplo, cargo-deny, cargo-nextest)
make deps
```

## Build

```sh
cargo build --release
```

The `xq` binary is placed at `target/release/xq`.

## A Minimal Program

Create a file called `add.xqasm`:

```asm
; push two integers and add them
PUSH 10
PUSH 32
ADD
HALT
```

Assemble and run:

```sh
xq asm add.xqasm -o add.xqb
xq run add.xqb
```

Expected output:

```
stack (bottom to top):
  42
```

The program pushes 10 and 32 onto the stack, adds them, and halts. The result
(42) remains on the stack and is printed by `xq run`.

## Inspect the Bytecode

Disassemble the compiled program to see its binary encoding:

```sh
xq dism add.xqb
```

This prints a human-readable listing with byte offsets and decoded instructions.

## Run Assembly Directly

Use the `--text` flag to skip the separate assembly step:

```sh
xq run --text add.xqasm
```

## Using Calldata and Outputs

Programs can receive input via calldata and write results to output slots:

```asm
; Read calldata[0] into r0, write it to output[0]
PUSH 0
INPUT r0
PUSH 0
OUTPUT r0
HALT
```

```sh
xq run --text program.xqasm --calldata 42 --outputs 1
```

Expected output:

```
outputs:
  [0] = Int(42)
```

## Next Steps

- Learn the full [CLI Reference](../xqvm/cli/README.md)
- Understand the [VM Architecture](../xqvm/machine-model.md)
- Browse the [Assembly Language](../xqvm/assembly.md) syntax
- See the complete [Instruction Set Reference](../xqvm/instructions/README.md)
- Walk through the [TSP Example](../examples/tsp.md) for a real-world use case
