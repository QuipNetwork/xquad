# xquad vm check

Run the bytecode checker over a program without executing it, and report
the first violation found.

## Usage

```sh
xquad vm check [OPTIONS] <FILE>
```

## Arguments

| Argument | Description |
|----------|-------------|
| `FILE` | Program to check: bytecode (`.xqb`) by default, assembly (`.xqasm`) when `--text` is set. |

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--text` | off | Treat `FILE` as assembly source and assemble it before checking. |

`xquad vm check` runs the same default pipeline documented in
[Checker](../../xqvm/checker.md): structural checks, jump-target checks
and loop-nesting balance, in that order, stopping at the first phase that
fails. This page
covers only the command's own behaviour; for what each phase checks and
what each error means, see that page. For a task-oriented walkthrough of
fixing a rejected program, see
[Checking](../../running/checking.md).

## A passing program

```sh
xquad vm check add.xqb
```

```
ok: add.xqb (4 instructions)
```

The instruction count includes the whole decoded stream: labelled
positions as well as visible mnemonics. `check` writes this line to
stdout and exits `0`. With `--text`, the same message names the `.xqasm`
source path rather than a `.xqb` file, since the source is assembled
in memory first and never written to disk.

## A failing program

`stray.xqasm` closes a loop it never opened:

```asm
NEXT
HALT
```

```sh
xquad vm check --text stray.xqasm
```

```
Error: check failed: NoActiveLoop: loop instruction at byte 0x0000 executed outside any active loop
```

`check` exits `1` on the first violation any phase finds. A program with
two independent defects reports only this one; fixing it and re-running
may surface the next.

## Passing the check is not a runtime guarantee

The check is structural: it looks at how the bytecode is laid out, not at
what it does. A program that reads a register before writing it, pops an
empty stack, allocates a negative model, resizes past its own variable
count, overflows an `i64` or exhausts its step or allocation budget checks
cleanly and faults when run. See
[Checker](../../xqvm/checker.md#what-passing-the-check-guarantees) for the
precise scope of what a pass proves, and [Limits and
Errors](../../xqvm/limits-and-errors.md#vm-runtime-errors) for the faults that
have no static counterpart.
