# CLI Reference

`xquad` is the unified command-line driver for the XQVM toolchain. It is
built by the `xqcli` crate, but the crate and the binary have different
names: `cargo install xqcli` installs a command called `xquad`, not
`xqcli`. Running `xqcli --help` after installing fails because no such
binary exists.

## Installation

```sh
cargo install xqcli
xquad --help
```

Or build from this repository:

```sh
cargo build -p xqcli --release
```

The binary is at `target/release/xquad`.

## Subcommands

`xquad` has four subcommands, one per stage of the toolchain:

| Command | Description |
|---------|-------------|
| [`xquad asm`](asm.md) | Assemble `.xqasm` source into binary bytecode. |
| [`xquad dism`](dism.md) | Disassemble bytecode into a human-readable listing. |
| [`xquad run`](run.md) | Execute bytecode or assembly, with optional tracing. |
| [`xquad verify`](verify.md) | Run the bytecode verifier over a program without running it. |

## General usage

```sh
xquad <COMMAND> [OPTIONS] [ARGS]
xquad --help
xquad <COMMAND> --help
```

`xquad asm` reads `.xqasm` source and writes bytecode; it has no `--text`
flag and rejects a `.xqb` file. `xquad dism` reads bytecode from a file or
stdin, also with no `--text` flag. `xquad run` and `xquad verify` read
bytecode by default and read `.xqasm` assembly instead when `--text` is
passed.

Error reporting is not uniform across the four subcommands. Only `xquad
asm` propagates the real `xqasm::Error`, with its `NamedSource` and
`SourceSpan`, so only `asm` prints a highlighted source snippet pointing
at the failing token. `xquad run --text` and `xquad verify --text` both
flatten an assembly failure through `miette::miette!("{e}")`, which
formats the error to a plain string and discards the span; `xquad verify`
on a `.xqb` file flattens a verifier failure the same way. Decode errors
(a malformed `.xqb` file) go through `.into_diagnostic()`, which also
carries no span. So an assembly, verification or decode failure reaches
you as a bare message -- see [`xquad verify`](verify.md#a-failing-program)
for what that looks like in practice. A VM runtime fault under `xquad
run` is the one exception: it goes through `into_diagnostic()`, which
disassembles the program around the failing offset and annotates it, so
that path does print a snippet. Argument-parsing failures, such as
`asm`'s `-o`/`--stdout` conflict, are plain `clap` errors, a third and
unrelated error path.

## A minimal round trip

```sh
cat > add.xqasm <<'EOF'
; add.xqasm -- push two integers and add them
PUSH 10
PUSH 32
ADD
HALT
EOF

xquad asm add.xqasm -o add.xqb
xquad verify add.xqb
xquad run add.xqb
```

`xquad run add.xqb` prints the residual stack:

```
stack (bottom to top):
  42
```
