# Verification

`xquad verify` rejects a program before it runs, printing the first
structural or semantic problem the bytecode verifier finds. This page takes
you from that message to a working program. [Verifier](../xqvm/verifier.md)
is the reference half, covering what each phase checks and what every
error means. This page does not repeat that; it shows each error actually
firing and what to change.

`xquad verify --text file.xqasm` assembles the source in memory and
verifies the result; `xquad verify file.xqb` verifies pre-assembled
bytecode. Both forms and their exit codes are covered in
[CLI: verify](../xqvm/cli/verify.md); this page assumes you already know how
to invoke it.

## Reading an error

Every example below is the smallest program that reproduces its error,
followed by the exact `xquad verify` output it produces.

### Structural errors

**`BadOpcode`** -- a byte in the instruction stream does not map to any of
the 93 known instructions. This cannot come from a `.xqasm` file the
assembler accepted; it means a `.xqb` file was hand-edited or corrupted:

```
Error:   × unknown opcode 0x0d at byte 0x0000
```

Fix: reassemble from source rather than editing bytecode by hand, and check
the `.xqb` file was not truncated or modified in transit.

**`TruncatedInstruction`** -- the byte stream ends partway through an
instruction's operand bytes. A `PUSH1` opcode byte with no value byte
after it triggers it:

```
Error:   × truncated instruction at byte 0x0000
```

Fix: same as `BadOpcode` -- the `.xqb` file is not a complete, valid
encoding. Reassemble it.

### Jump-target and loop-nesting errors

**`UndefinedJumpTarget`** -- a jump instruction's label id is at or past the
program's target count. Text assembly cannot produce this: the assembler
resolves every `.N` label reference against the labels actually defined in
the source and refuses to emit a jump to one that does not exist --
`xqasm::undefined_label`, at assemble time, before the verifier ever runs.
`UndefinedJumpTarget` is a defense for bytecode assembled some other way,
or corrupted after assembly: a jump instruction's label-id byte, changed to
a value the program's target count does not cover, produces:

```
Error:   × jump at byte 0x0003 references undefined target label 5 (program has 1
  │ targets)
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
Error:   × loop instruction at byte 0x0000 executed outside any active loop
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
Error:   × unmatched loop: RANGE/ITER at byte 0x0004 has no corresponding NEXT (1
  │ loop(s) still open at end of program)
```

Fix: add the missing `NEXT`. If this program came from `xqcp`, it usually
means a `with problem.range(...)` block (see
[Control Flow](../modelling/control-flow.md)) whose body raised or returned
before the DSL closed it correctly -- check the generated `.xqasm` for the
loop in question.

### Register errors

**`ReadUnsetRegister`** -- a register is read before anything writes it, or
before every incoming control-flow path writes it (must-init analysis,
phase 3, catches the second case even when phase 2's type check alone would
not):

```asm
LOAD r0
HALT
```

```
Error:   × register r0 read at byte 0x0000 before being written
```

Fix: write the register before reading it. If the read follows a branch,
confirm both arms write it -- a register set on only one side of a
conditional is `ReadUnsetRegister` at the first read after the join, even
though nothing about the type looked wrong on either arm individually.
`DROP` also resets a register to unset; a `LOAD` right after `DROP` on the
same register fails the same way.

**`RegisterTypeMismatch`** -- a register holds a `RegVal` variant other
than the one the instruction needs. Here `BQMX` writes a `Model`, and
`LOAD` only handles `Int`:

```asm
PUSH 4
BQMX r0
LOAD r0
HALT
```

```
Error:   × register r0 at byte 0x0004: expected int, got model
```

Fix: `LOAD` is for scalar integers. To move a `Model` out of a register for
inspection or as another instruction's operand, pass the register directly
to an instruction that accepts `Model` (`GETLINE`, `ENERGY`, and others);
do not route it through `LOAD`.

### Stack-depth errors

**`StackUnderflow`** -- a reachable instruction would pop more items than
the analysis can prove are on the stack:

```asm
ADD
HALT
```

```
Error:   × stack underflow at byte 0x0000
```

Fix: push the operands an instruction needs before it runs. This same
check also catches loop-related shortfalls; see the "does not guarantee"
section below for its blind spot.

**`StackOverflowRisk`** -- the analysis converges on a depth past the
8,192-item limit for some reachable block. A straight-line run of 8,200
`PUSH` instructions with no loop involved:

```
Error:   × potential stack overflow at byte 0x0000 (depth 8200)
```

Fix: this fires on a single basic block's static depth, so the usual cause
is building up a large flat structure with individual `PUSH`/`VECPUSH`
calls rather than a loop. Reducing the item count, or restructuring the
build as a loop the analysis can bound per iteration, both work; see the
next section for why a loop is not automatically safe either.

**`LoopStackImbalance`** -- a loop body's net stack effect is non-zero: the
depth the analysis computes at `NEXT` differs from the depth at the
matching `RANGE`/`ITER`. An unmatched `PUSH` inside the loop body:

```asm
PUSH 0
PUSH 3
RANGE
PUSH 99
NEXT
HALT
```

```
Error:   × loop starting at byte 0x0004 has non-zero stack effect (entry depth 0,
  │ exit depth 1)
```

Fix: every value a loop body pushes needs a matching pop (directly, or via
`STOW` into a register) before `NEXT`, so each iteration leaves the stack
exactly as it found it. `SCLR` inside a loop body is a special case: it
resets the tracked depth to zero unconditionally, so this error fires
when the loop's *entry* depth was already non-zero, not when the body's
depth before the reset was non-zero. If the entry depth was zero, the
same reset can instead mask a real imbalance earlier in the body, and
the mismatch surfaces later, at the next downstream join point, rather
than here.

**`StackDepthMismatch`** -- two control-flow paths reach the same join
point with different stack depths, so the depth at that point is not
well-defined. A conditional that pushes on only one arm:

```asm
PUSH 1
JUMPI .0
PUSH 2
.0: HALT
```

```
Error:   × stack depth mismatch at join point byte 0x0006: one path has depth 0,
  │ another has depth 1
```

Fix: make every arm of a branch leave the same stack depth before it
rejoins. If only one arm pushes a value, move that push above the branch
so both arms inherit it.

## What passing verification does not guarantee

<!-- xquad:defect QUI-1062 -->
> **Known issue.** The bytecode verifier passes programs that fault at runtime: the
> net-delta stack scan cannot see an operand-ordering error, so a stack underflow
> can pass verification. Treat a verification pass as a static check, not a
> guarantee that the program runs to completion. Report problems at the
> [issue tracker](https://gitlab.com/quip.network/xquad/-/issues).

A pass means every phase's static checks succeeded. It does not mean the
program runs to completion. The stack-depth phase reasons about each basic
block's *net* effect, so an instruction that pops more operands than it
pushes has its pop requirement absorbed whenever the running depth stays
non-negative -- an operand-ordering error is invisible to it. `PUSH 1 / ADD
/ HALT` passes because the scan sees a net effect of `+1 - 1 = 0` for the
two instructions together, not that `ADD` needs two operands and only one
was ever pushed:

```sh
$ xquad verify --text add_underflow.xqasm
ok: add_underflow.xqasm (3 instructions)
```

```sh
$ xquad run --text add_underflow.xqasm
Error: xqvm::runtime_error

  × stack underflow at byte 0x0002
   ╭─[add_underflow.xqasm:2:1]
 1 │   0x0000:  PUSH1   1
 2 │   0x0002:  ADD     
   · ─────────┬─────────
   ·          ╰── execution failed here
 3 │   0x0003:  HALT    
   ╰────
```

This is a documented, current limitation of the per-basic-block analysis,
not a bug specific to this program. [Verifier: what passing verification
guarantees](../xqvm/verifier.md#what-passing-verification-guarantees) states
the precise scope. Treat a pass as "structurally sound," not as "will run
to completion."

## The generated verifier program is not the bytecode verifier

`xquad verify` and the *verifier program* are two different things that
share a name by coincidence of vocabulary.

`xquad verify` runs the bytecode verifier described above and in
[Verifier](../xqvm/verifier.md): a static analysis over any `.xqasm` or
`.xqb` file, checking that the program is well-formed before anything
executes. It has no idea what problem, if any, the program encodes.

A compiled problem's *verifier program* is one of the three `.xqasm`
outputs `problem.compile()` returns -- see [Three
Programs](../concepts/three-programs.md) and
[Compiling](../modelling/compiling.md#what-compile_verifier-and-compile_decoder-do).
It is an ordinary program like any other: `xquad verify` can check *it* is
well-formed, the same as it checks the encoder or the decoder. What the
verifier program itself does at runtime -- taking a model and a candidate
sample as calldata and producing `(energy, valid)` -- is domain logic the
DSL emitted, unrelated to the bytecode verifier's job. Running the
verifier program does not verify a program in the bytecode-verifier sense,
and running `xquad verify` against the verifier program's `.xqasm` text
does not check whether a sample is a good answer.

## The generated verifier's `valid` flag does not check every constraint

<!-- xquad:defect QUI-1062 -->
> **Known issue.** The generated solution verifier emits a row-sum check only for
> `onehot_row` and a column-sum check only for `onehot_col`, and otherwise checks only
> domain membership, so a sample violating any other constraint kind can still report
> `valid = 1`. Check feasibility in the host for problems built from other constraint
> kinds, rather than trusting the `valid` flag. Report problems at the
> [issue tracker](https://gitlab.com/quip.network/xquad/-/issues).

The verifier program's `valid` output checks the sample's domain, plus a
row- or column-sum check when the problem used `onehot_row` or
`onehot_col`. [Compiling](../modelling/compiling.md#what-compile_verifier-and-compile_decoder-do)
describes exactly how `compile_verifier` picks between these three shapes.
A problem built only from `EQUALITY`, `ATLEAST`, `ATLEASTW`, `EXCLUDE`, or
`IMPLIES` constraints gets a verifier whose `valid` check never touches
them -- domain membership is the only thing tested, so an infeasible
sample can still read `valid = 1`.

[Running Programs](./#a-complete-run-across-three-programs) runs
this end to end on the knapsack example: a sample selecting items that
weigh `12` against a capacity of `8` -- an infeasible selection -- still
comes back `valid = 1`, because knapsack's only constraint is `EQUALITY`
and the verifier's binary-domain check has no way to see the capacity
violation. `ENERGY` still recomputes the true objective independently of
the sample's origin, so the *energy* on an infeasible sample is typically
worse than a feasible one's -- but nothing marks the sample invalid on that
basis. Do not treat `valid = 1` from a generated verifier as proof a sample
satisfies every constraint the problem declared; it proves only what that
problem's specific `valid` check happens to test.
