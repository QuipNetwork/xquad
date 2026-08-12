# xquad verify

Run the bytecode verifier over a program without executing it, and report
the first violation found.

## Usage

```sh
xquad verify [OPTIONS] <FILE>
```

## Arguments

| Argument | Description |
|----------|-------------|
| `FILE` | Program to verify: bytecode (`.xqb`) by default, assembly (`.xqasm`) when `--text` is set. |

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--text` | off | Treat `FILE` as assembly source and assemble it before verifying. |

`xquad verify` runs the same default pipeline documented in
[Verifier](../verifier.md): structural checks, jump-target checks,
loop-nesting balance, register type-state, must-init analysis, and stack
depth, in that order, stopping at the first phase that fails. This page
covers only the subcommand's own behaviour; for what each phase checks and
what each error means, see that page. For a task-oriented walkthrough of
fixing a rejected program, see
[Verification](../../running/verification.md).

## A passing program

```sh
xquad verify add.xqb
```

```
ok: add.xqb (4 instructions)
```

The instruction count includes the whole decoded stream: labelled
positions as well as visible mnemonics. `verify` writes this line to
stdout and exits `0`. With `--text`, the same message names the `.xqasm`
source path rather than a `.xqb` file, since the source is assembled
in memory first and never written to disk.

## A failing program

`uninit.xqasm` reads register `r0` before anything ever writes it:

```asm
LOAD r0
HALT
```

```sh
xquad verify --text uninit.xqasm
```

```
Error:   × register r0 read at byte 0x0000 before being written
```

`verify` exits `1` on the first violation any phase finds. A program with
two independent defects reports only this one; fixing it and re-running
may surface the next.

## Passing verification is not a runtime guarantee

Verification is a set of static, per-basic-block checks. It does not
simulate how many times a loop body actually executes, so a program whose
defect only shows up after enough iterations can pass. `selfloop.xqasm`
pushes a value and jumps back to the same point forever:

```asm
.0: PUSH 1
JUMP .0
HALT
```

```sh
xquad verify --text selfloop.xqasm
```

```
ok: selfloop.xqasm (4 instructions)
```

`xquad run --text selfloop.xqasm` then overflows the value stack once the
loop has pushed past the runtime limit:

```
Error: xqvm::runtime_error

  × stack overflow at byte 0x0001 (limit: 8192)
   ╭─[selfloop.xqasm:2:1]
 1 │   0x0000:  .0:  TARGET  
 2 │   0x0001:       PUSH1   1
   · ────────────┬────────────
   ·             ╰── execution failed here
 3 │   0x0003:       JUMP1   .0
   ╰────
```

See [Verifier](../verifier.md#what-passing-verification-guarantees) for the
precise scope of what a pass proves.
