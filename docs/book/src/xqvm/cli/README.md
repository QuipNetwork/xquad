# CLI Reference

`xquad` is the command-line driver for the XQuad toolchain. It ships with
the `xquad` Python package, so the install that gives you the Python API
also puts the command on your `PATH`.

## Installation

```sh
pip install xquad
xquad --help
```

If your shell reports `xquad: command not found`, the script directory
of that Python environment is not on `PATH`. `python -m xquad` runs the
same command without it:

```sh
python -m xquad --help
```

From a checkout of this repository, `make xquad` syncs the workspace
virtual environment, and `uv run xquad` runs the command from it.

The command used to be a Rust binary from the `xqcli` crate, installed
with `cargo install`. That crate is retired. If you installed it, run
`cargo uninstall xqcli`, or the old binary in `~/.cargo/bin` may shadow
the new command on `PATH`.

## Commands

The XQVM tools sit under `xquad vm`, one per stage of the toolchain:

| Command | Description |
|---------|-------------|
| [`xquad vm asm`](asm.md) | Assemble `.xqasm` source into binary bytecode. |
| [`xquad vm dis`](dis.md) | Disassemble bytecode into a human-readable listing. |
| [`xquad vm run`](run.md) | Execute bytecode or assembly, with optional tracing. |
| [`xquad vm check`](check.md) | Run the bytecode verifier over a program without running it. |

The old top-level names `xquad asm`, `xquad dism`, `xquad run` and `xquad
verify` still work in this release. Each prints one deprecation warning
to stderr when it runs and otherwise behaves exactly like its `xquad vm`
counterpart, so stdout and the exit code are unchanged. They are removed
in v0.5.0.

## General usage

```sh
xquad vm <COMMAND> [OPTIONS] [ARGS]
xquad --help
xquad vm <COMMAND> --help
xquad --version
```

`xquad vm asm` reads `.xqasm` source and writes bytecode; it has no
`--text` flag. `xquad vm dis` reads bytecode from a file or stdin, also
with no `--text` flag. `xquad vm run` and `xquad vm check` read bytecode
by default and read `.xqasm` assembly instead when `--text` is passed.

## Exit codes and errors

| Code | Meaning |
|------|---------|
| `0` | Success. |
| `1` | The command ran and failed: an unreadable file, an assembly or decode error, a verifier violation, or a VM fault. |
| `2` | A usage error: an unknown option, a missing argument, or two options that cannot be combined. |

Errors are one line on stderr, prefixed `Error:` and naming the stage
that failed:

```
Error: assembly failed: unknown mnemonic `BADOP`
```

A usage error prints the command's usage line first. Errors carry no
source snippet: a VM fault names the error variant and its byte offset
(`run failed: StackUnderflow { pos: 2 }`), and `xquad vm dis` on the same
bytecode shows the instruction at that offset.

## A minimal round trip

```sh
cat > add.xqasm <<'EOF'
; add.xqasm -- push two integers and add them
PUSH 10
PUSH 32
ADD
HALT
EOF

xquad vm asm add.xqasm -o add.xqb
xquad vm check add.xqb
xquad vm run add.xqb
```

`xquad vm run add.xqb` prints the residual stack:

```
stack (bottom to top):
  42
```
