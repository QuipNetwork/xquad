# CLI Reference

`xquad` is the command-line driver for the XQuad toolchain. It ships with
the `xquad` Python package, so the install that gives you the Python API
also puts the command on your `PATH`.

## Installation

Install XQuad into a project environment first: a virtual environment,
or a `uv` project.

```sh
python -m venv .venv && source .venv/bin/activate
pip install xquad
# or, in a uv project:
uv add xquad
```

For the `xquad` command alone, outside any project, install it as a
`uv` tool, with the `quip` extra for the `xquad quip` commands:

```sh
uv tool install xquad            # the xquad command
uv tool install 'xquad[quip]'    # with the xquad quip commands
```

On macOS, a user install (`pip install --user`, or pip falling back to
one) puts the `xquad` command in `~/Library/Python/3.x/bin`, which is
not on `PATH`; `python -m xquad` runs the same command without it.

More generally, if your shell reports `xquad: command not found`, the
script directory of that Python environment is not on `PATH`, and
`python -m xquad --help` works in its place.

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
