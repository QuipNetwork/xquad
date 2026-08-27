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

Verification is a set of static, per-basic-block checks. The stack-depth
phase sees each block's *net* stack effect, so an instruction that pops
more operands than it pushes has its pop requirement absorbed by earlier
pushes. `idx_underflow.xqasm` pushes two values and then runs `IDXGRID`,
which pops three:

```asm
PUSH 1
PUSH 2
IDXGRID
HALT
```

```sh
xquad verify --text idx_underflow.xqasm
```

```
ok: idx_underflow.xqasm (4 instructions)
```

`xquad run --text idx_underflow.xqasm` then underflows the value stack at
the `IDXGRID`:

```
Error: xqvm::runtime_error

  × stack underflow at byte 0x0004
   ╭─[idx_underflow.xqasm:3:1]
 2 │   0x0002:  PUSH1   2
 3 │   0x0004:  IDXGRID 
   · ─────────┬─────────
   ·          ╰── execution failed here
 4 │   0x0005:  HALT    
   ╰────
```

Nor does verification look at values. An allocator size, a grid extent, a
loop bound and every arithmetic operand are runtime quantities, so a
program that allocates a negative model, resizes past its own variable
count, overflows an `i64` or exhausts its step or allocation budget
verifies cleanly and faults when run. See
[Verifier](../verifier.md#what-passing-verification-guarantees) for the
precise scope of what a pass proves, and [Limits and
Errors](../limits-and-errors.md#vm-runtime-errors) for the faults that
have no static counterpart.
