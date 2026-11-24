# Checking

`xquad vm check` rejects a program before it runs, printing the first
structural problem the bytecode checker finds. This page takes
you from that message to a working program. [Checker](../xqvm/checker.md)
is the reference half, covering what each phase checks and what every
error means. This page does not repeat that; it shows each error actually
firing and what to change.

`xquad vm check --text file.xqasm` assembles the source in memory and
checks the result; `xquad vm check file.xqb` checks pre-assembled
bytecode. Both forms and their exit codes are covered in
[CLI: check](../cli/vm/check.md); this page assumes you already know how
to invoke it.

## Reading an error

Every example below is the smallest program that reproduces its error,
followed by the exact `xquad vm check` output it produces.

### Structural errors

**`BadOpcode`** -- a byte in the instruction stream does not map to any of
the 93 known instructions. This cannot come from a `.xqasm` file the
assembler accepted; it means a `.xqb` file was hand-edited or corrupted:

```
Error: check failed: BadOpcode: unknown opcode 0x0d at byte 0x0000
```

Fix: reassemble from source rather than editing bytecode by hand, and check
the `.xqb` file was not truncated or modified in transit.

**`TruncatedInstruction`** -- the byte stream ends partway through an
instruction's operand bytes. A `PUSH1` opcode byte with no value byte
after it triggers it:

```
Error: check failed: TruncatedInstruction: truncated instruction at byte 0x0000
```

Fix: same as `BadOpcode` -- the `.xqb` file is not a complete, valid
encoding. Reassemble it.

### Jump-target and loop-nesting errors

**`UndefinedJumpTarget`** -- a jump instruction's label id is at or past the
program's target count. Text assembly cannot produce this: the assembler
resolves every `.N` label reference against the labels actually defined in
the source and refuses to emit a jump to one that does not exist --
`xqasm::undefined_label`, at assemble time, before the checker ever runs.
`UndefinedJumpTarget` is a defense for bytecode assembled some other way,
or corrupted after assembly: a jump instruction's label-id byte, changed to
a value the program's target count does not cover, produces:

```
Error: check failed: UndefinedJumpTarget: jump at byte 0x0003 references undefined target label 5 (program has 1 targets)
```

Fix: this is not something valid `.xqasm` can trigger, so seeing it means
the `.xqb` file in hand is not what the assembler produced. Reassemble from
source.

**`NoActiveLoop`** -- `NEXT`, `LVAL`, or `LIDX` appears with no `RANGE` or
`ITER` open at that point:

```asm
NEXT
HALT
```

```
Error: check failed: NoActiveLoop: loop instruction at byte 0x0000 executed outside any active loop
```

Fix: every `NEXT`, `LVAL`, and `LIDX` needs an enclosing `RANGE`/`ITER` on
every path that reaches it -- check you have not placed one outside the
loop body, or past a jump that skips the loop opener.

**`UnmatchedLoop`** -- a `RANGE` or `ITER` opens with no matching `NEXT`
before the program ends:

```asm
PUSH 0
PUSH 3
RANGE
HALT
```

```
Error: check failed: UnmatchedLoop: unmatched loop: RANGE/ITER at byte 0x0004 has no corresponding NEXT (1 loop(s) still open at end of program)
```

Fix: add the missing `NEXT`. If this program came from `xqcp`, it usually
means a `with problem.range(...)` block (see
[Control Flow](../modelling/control-flow.md)) whose body raised or returned
before the DSL closed it correctly -- check the generated `.xqasm` for the
loop in question.

## What passing the check does not guarantee

A pass means every phase's static checks succeeded. It does not mean the
program runs to completion. [Checker: what passing the check
guarantees](../xqvm/checker.md#what-passing-the-check-guarantees) states
the precise scope. Treat a pass as "structurally sound," not as "will run
to completion."

The checker reads the shape of the bytecode, not what it does. A register
read before anything writes it, an instruction that pops more values than
the stack holds, a register holding the wrong kind of value, and every
fault that depends on a computed value -- an allocator size, a grid extent,
a loop bound, a calldata index, an arithmetic operand -- all pass the check
and fault when the VM reaches them: `UnsetRegister`, `StackUnderflow`,
`RegisterType`, `InvalidAllocation`, `InvalidGridDimensions`,
`ArithmeticOverflow`, `IndexOutOfBounds` and the rest. For an embedder
that is the point: the budgets and the run-time checks are what bound a
program the checker has already passed. See [Limits and
Errors](../xqvm/limits-and-errors.md#vm-runtime-errors).

## The generated verifier program is not the bytecode checker

`xquad vm check` and the *verifier program* are two different things.

`xquad vm check` runs the bytecode checker described above and in
[Checker](../xqvm/checker.md): a static analysis over any `.xqasm` or
`.xqb` file, checking that the program is well-formed before anything
executes. It has no idea what problem, if any, the program encodes.

A compiled problem's *verifier program* is one of the three `.xqasm`
outputs `problem.compile()` returns -- see [Three
Programs](../concepts/three-programs.md) and
[Compiling](../modelling/compiling.md#what-compile_verifier-and-compile_decoder-do).
It is an ordinary program like any other: `xquad vm check` can check *it* is
well-formed, the same as it checks the encoder or the decoder. What the
verifier program itself does at runtime -- taking a model and a candidate
sample as calldata and producing `(energy, valid)` -- is domain logic the
DSL emitted, unrelated to the bytecode checker's job. Running the
verifier program does not check that a program is well-formed, and running
`xquad vm check` against the verifier program's `.xqasm` text does not
check whether a sample is a good answer.

## What the generated verifier's `valid` flag covers

`valid` is the conjunction of one check per constraint the problem
declared, plus a domain check over every declared variable. A sample that
violates an `onehot_row`, `onehot_col`, `equality`, `inequality`,
`atleast`, `atleastw`, `exclude`, `implies`, or the Rosenberg auxiliary a
`reduce()` allocated, comes back `valid = 0`.
[Compiling](../modelling/compiling.md#what-compile_verifier-and-compile_decoder-do)
shows how each check is emitted.

The checks are over the variables the problem declared, not over the
encoding. `slack()` extends an equality's index vector with the encoder's
own variables; the verifier checks that constraint as `sum <= bound` over
the real variables and leaves the slack bits unconstrained. Checking the
expanded form instead would report `valid = 0` for a feasible sample whose
slack bits a solver happened to leave inconsistent, which on a settlement
path means refusing to pay for a correct answer.

Two things `valid = 1` does not claim. The domain check runs to the
model's *declared* size, so slack and `REDUCE` auxiliaries allocated past
that size are not domain-checked. And `valid` says nothing about
optimality: `ENERGY` recomputes the objective independently of where the
sample came from, and a feasible sample can still be a poor one.
