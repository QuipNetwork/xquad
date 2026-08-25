# xqcli

Unified CLI for the [XQuad Toolchain](https://gitlab.com/quip.network/xquad).

This crate is named `xqcli`; it builds the `xquad` binary. That is:
`cargo install xqcli` installs a command called `xquad`, not `xqcli`.
Running `xqcli --help` after installing will fail because no such binary
exists.

## Install

```sh
cargo install xqcli
xquad --help
```

## Subcommands

`xquad` has four subcommands, one per stage of the toolchain: `asm`,
`dism`, `run`, `verify`.

| Subcommand | Does |
|---|---|
| `xquad asm <INPUT>` | Assemble `.xqasm` source into `.xqb` bytecode |
| `xquad dism [FILE]` | Disassemble bytecode into a human-readable listing |
| `xquad run <FILE>` | Run bytecode (or assembly, with `--text`) on the interpreter |
| `xquad verify <FILE>` | Run the bytecode verifier (all phases) |

Run `xquad <subcommand> --help` for the full option list; the summaries
below are the parts most people need first.

- `xquad asm`: `-o, --output <OUTPUT>` (defaults to `<input>.xqb`), `--stdout` to write bytecode to stdout instead of a file.
- `xquad dism`: reads from stdin when `FILE` is omitted.
- `xquad run`: `--text` to treat `FILE` as assembly, `--calldata <CALLDATA>` (comma-separated integers), `--outputs <OUTPUTS>` (default 16), `--step-limit <STEP_LIMIT>` (default 10000000; the value is an exact limit, so `0` executes nothing), `--unlimited-steps` to run without a step limit, `--trace` with `--trace-format text|json` and `--trace-file <FILE>`.
- `xquad verify`: `--text` to treat `FILE` as assembly before verifying.

## Quick start

```sh
cat > add.xqasm <<'EOF'
; add.xqasm -- push two integers and add them
PUSH 10
PUSH 32
ADD
HALT
EOF

xquad asm add.xqasm -o add.xqb
xquad run add.xqb
xquad dism add.xqb
xquad verify add.xqb
```

`xquad run add.xqb` prints the residual stack:

```
stack (bottom to top):
  42
```

## Links out

- [`xqvm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm) -- bytecode types and interpreter this CLI drives
- [`xqasm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqasm) -- text assembler behind `xquad asm`
- [Normative VM spec](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/SPEC.md)
- [Bytecode encoding spec](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ENCODING.md)
- [Repository](https://gitlab.com/quip.network/xquad)

## License

AGPL-3.0-or-later.
