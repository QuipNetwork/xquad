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

`xquad` groups its commands by what they work on:

| Group | Description |
|-------|-------------|
| [`xquad vm`](vm/) | Assemble, disassemble, run and verify XQVM programs. |
| [`xquad quip`](quip/) | Accounts and job orders on a Quip network. Needs the `quip` extra. |

```sh
xquad --help
xquad <GROUP> --help
xquad --version
```
