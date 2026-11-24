# Migrating from 0.5 to 0.6

v0.6.0 is a breaking release. This page lists every change in it that can
break code written against 0.5, each with what to write instead. On the
Python side, upgrade `xquad`, which is now the only distribution. On the Rust
side, move `xqvm` and `xqasm` to 0.6 together.

## One Distribution

`xquad` is the only Python distribution. It carries `xqffi`, `xqcp`, `xqsa`
and the `xquad` CLI in one wheel. `xqffi`, `xqcp` and `xqsa` have no 0.6
release; their existing releases, up to 0.5.2, stay on PyPI.

| 0.5 | 0.6 |
|---|---|
| `pip install xqcp`, `pip install xqsa`, `pip install xqffi` | `pip install xquad` |
| `pip install xqsa[cuda]` (also `dwave`, `metal`, `quip`) | `pip install xquad[cuda]` |
| `xqcp`, `xqsa` or `xqffi` listed as a dependency | `xquad>=0.6,<0.7` |
| `import xqffi`, `import xqcp`, `import xqsa`, `from xquad import sa` | Unchanged |

Remove the old distributions, then reinstall `xquad`:

```sh
pip uninstall -y xqffi xqcp xqsa
pip install --force-reinstall xquad
```

`xquad` 0.6 ships the `xqffi`, `xqcp` and `xqsa` packages itself, into the
same directories the 0.5 distributions use. pip does not remove the 0.5
distributions on upgrade, and in an environment upgraded in place,
`pip uninstall` deletes files that `xquad` now owns. That is why the second
step is `--force-reinstall` and not `pip install -U xquad`.

`import xquad` and the `xquad` command refuse to run while any of the old
distributions is installed in the same environment. They raise an
`ImportError` that names the distribution and gives the two commands above:

```text
xqsa 0.5.2 is installed next to xquad. From 0.6, xquad ships the xqffi, xqcp and xqsa packages itself, and the old distributions overwrite its files. Remove them, then reinstall xquad:
    pip uninstall -y xqffi xqcp xqsa
    pip install --force-reinstall xquad
```

Two ways to hit it after a clean upgrade:

- Installing `xqsa`, `xqffi` or `xqcp` next to `xquad` 0.6, for example by
  following an old `pip install xqsa[<extra>]` hint, writes 0.5 code over
  `xquad`. `pip list` still shows `xquad` 0.6 and `pip check` reports
  nothing; the next `import xquad` fails.
- A project that still depends on `xqsa`, `xqcp` or `xqffi` by name pulls the
  0.5 distributions in. Replace the dependency with `xquad>=0.6,<0.7`.

The check is a transition aid and is removed before 1.0.0.

An environment that installed only `xqffi` or `xqcp` now also gets
`dwave-samplers` (and with it NumPy) and `click`, the base dependencies of
`xquad`. One that installed only `xqsa` gains `click`. All of them now get the
`xquad` command.

The `ImportError` raised when a solver's extra is missing now names
`pip install xquad[<extra>]` instead of `xqsa[<extra>]`. Code that matched the
old text needs updating.

The Linux wheels are built against glibc 2.17. Installing `xquad` needs
glibc 2.27 or later, the floor of the NumPy wheels it depends on. That lowers
the floor: the 0.5 `xqffi` x86_64 wheel needed glibc 2.34.

## Verifier Renamed to Checker

The bytecode verifier is now the checker, in every language.

| 0.5 | 0.6 |
|---|---|
| `xqvm::verifier` | `xqvm::checker` |
| `xqvm::verifier::verify` | `xqvm::checker::check` |
| `xqvm::verifier::Verifier` | `xqvm::checker::Checker` |
| `VerifierError`, also `xqvm::VerifierError` | `CheckerError`, also `xqvm::CheckerError` |
| `xqvm::verifier::RegType` | `xqvm::checker::RegType`; `xqvm::RegType` is unchanged |
| `xqffi.verifier.verify`, `verify_source` | `xqffi.checker.check`, `check_source` |
| `xquad.verifier` | `xquad.checker` |
| `Problem.compile()` raises `ValueError("<program> verification failed: ...")` | `ValueError("<program> check failed: ...")` |

`Phase` and the `*Phase` types keep their names.

This rename only covers the bytecode checker. A problem's generated verifier
program, such as `programs.verifier` in the examples, keeps its name; see
[The generated verifier program is not the bytecode
checker](../running/checking.md#the-generated-verifier-program-is-not-the-bytecode-checker).
A blanket replace of `verifier` with `checker` breaks code that uses that name.

The script below renames the fully qualified forms, and every bare
`VerifierError`, in the `.rs`, `.py` and `.pyi` files git tracks in the
current repository. It uses `perl`, which behaves the same on macOS and
Linux. Run it on a clean working tree and review the diff:

```sh
git grep -lzE 'verifier|VerifierError' -- '*.rs' '*.py' '*.pyi' |
  xargs -0 perl -pi -e '
    s/\bxqvm::verifier::verify\b/xqvm::checker::check/g;
    s/\bxqvm::verifier::Verifier\b/xqvm::checker::Checker/g;
    s/\bxqvm::verifier\b/xqvm::checker/g;
    s/\bVerifierError\b/CheckerError/g;
    s/\b(xqffi|xquad)\.verifier\.verify_source\b/$1.checker.check_source/g;
    s/\b(xqffi|xquad)\.verifier\.verify\b/$1.checker.check/g;
    s/\b(xqffi|xquad)\.verifier\b/$1.checker/g;
  '
```

It leaves these for you:

- Names imported in a group or from a Python module, such as
  `use xqvm::verifier::{verify, Verifier}` or
  `from xqffi.verifier import verify`. The module path is renamed, so the
  build or the import fails on the old item name. Rename the item.
- The calls made through a single imported name. The script renames
  `use xqvm::verifier::verify;` to `use xqvm::checker::check;` and
  `use xqvm::verifier::Verifier;` to `use xqvm::checker::Checker;`, but not
  the `verify(...)` or `Verifier` uses that follow. The build fails at each
  one; rename them.
- `from xqffi import verifier` or `from xquad import verifier`, and the calls
  made through that name. The import raises `ImportError`.
- A module bound to a name, such as `use xqvm::verifier;` or
  `import xqffi.verifier as v`, and the calls made through it. The script
  renames the module path but not `verifier::verify(...)` or `v.verify(...)`.
  In Rust the build fails; in Python the call raises `AttributeError` only
  when it runs.
- Code that matches the error text `verification failed`. Nothing fails here;
  search for the string.

## Removed CLI Aliases

The deprecated hidden top-level commands are gone. Use the
[`xquad vm`](../cli/vm/) subcommands:

| 0.5 | 0.6 |
|---|---|
| `xquad asm` | `xquad vm asm` |
| `xquad dism` | `xquad vm dis` |
| `xquad run` | `xquad vm run` |
| `xquad verify` | `xquad vm check` |

## Bounded Checker Analyses

The checker's data-flow analyses run under a visit budget, so a program can
no longer make them run unbounded.

- `xqvm::dataflow::solve(analysis, cfg, max_visits)` takes a visit budget as
  its third argument and returns `Result<DataFlowResult<...>, SolveError>`.
  Pass a bound and handle `SolveError::BudgetExceeded`. [Migrating custom
  data-flow analyses](../xqvm/checker.md#migrating-custom-data-flow-analyses)
  says how to pick the bound.
- `CheckerError` gains `AnalysisBudgetExceeded`. The enum is not
  `#[non_exhaustive]`, so an exhaustive match needs an arm for it.

This cannot be scripted: the bound depends on your analysis.

## Programs the Checker Accepts and Rejects

The default checker accepts some programs that 0.5 rejected, and rejects some
that 0.5 accepted:

| Program | 0.5 | 0.6 |
|---|---|---|
| Its only fault is dead code: a `DROP` on a dead path, or a read after `HALT` | Rejected | Accepted |
| Its only write before a live read sits after a terminator | Accepted | Rejected (`ReadUnsetRegister`) |
| The stack overflows inside one block and comes back down before the block ends | Accepted | Rejected (`StackOverflowRisk`) |
| A `JUMP` or `JUMPI` targets a different loop body from the jump: into a loop, out of one, into a sibling loop, or from an inner loop to an outer one, reachable or not | Accepted | Rejected (`JumpAcrossLoopBoundary`) |

`CheckerError` gains `JumpAcrossLoopBoundary { offset, target }`, its last
variant; an exhaustive match needs an arm for it. In Python,
`xqffi.checker.check` and `check_source` raise
`ValueError("JumpAcrossLoopBoundary: ...")`, and `xquad vm check` exits 1 with
that message. The rule is phase 1d in [Checker](../xqvm/checker.md).

Only `xqvm::checker::check` and `Checker::default()` run the rule. A pipeline
built with `Checker::new()` and `with_phase`, as 0.5 allowed, still compiles
after the rename but accepts these jumps. Add `JumpSpanPhase` after
`LoopNestingPhase` to reject them, and before `RegisterTypePhase`,
`UninitRegisterPhase` and `StackDepthPhase` to report the same error as the
default checker.

Programs compiled by `xqcp` are unaffected. To fix a hand-written program the
checker now rejects, keep each jump and its `TARGET` in the same loop body. To
skip the rest of an iteration, jump to a `TARGET` just before that loop's
`NEXT`.

None of this can be scripted. Run `xquad vm check` (or `xqffi.checker.check`)
over the programs you store, and fix the ones it rejects.

## A Rejected Program Can Report a Different Error

A program that both 0.5 and 0.6 reject can report a different error:

- A stack-depth fault is now the first one the traversal of the control-flow
  graph reaches, not the highest-priority class in the program, so the
  variant or offset can differ from 0.5.
- The jump-span check runs before the register and stack-depth phases. A
  program with a cross-body jump that used to fail one of those
  (`ReadUnsetRegister`, `RegisterTypeMismatch` or a stack-depth error) now
  reports `JumpAcrossLoopBoundary`.
- `RegisterTypePhase` run on its own rejects a read after a write on only one
  branch.
- `ReadUnsetRegister`'s message now reads "register rN read at byte 0x...
  before being written on every path"; it ended "before being written".

Do not depend on which error a rejected program reports, or on its text.

## Constraint Opcodes Need a Binary Model

`ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `EQUALITY`, `ATLEAST`, `ATLEASTW`
and `REDUCE` raise `NonBinaryModel` on any model whose domain is not binary:
spin, or integer of any `k`, `k = 2` included. Their penalty expansions assume
`x^2 = x`, which only holds on a binary model. Some programs that raised
`IndexOutOfBounds`, `InvalidGridDimensions` or a size or overflow error now
raise `NonBinaryModel` instead, because the domain check comes first.

Allocate the model with `BQMX`, or write the coefficients with `SETLINE`,
`ADDLINE`, `SETQUAD` or `ADDQUAD`, which work on every domain. Programs
compiled by `xqcp` are unaffected: the compiler already refuses these
constraints on a model that is not binary.

`xqvm::Error` gains `NonBinaryModel { pos, reg, domain }`; an exhaustive match
needs an arm for it. In Python, catch `xqffi.vm.NonBinaryModel`, or its base
`XqvmError`. See [Limits and Errors](../xqvm/limits-and-errors.md).

This cannot be scripted. Run the programs you store and fix the ones that
raise `NonBinaryModel`.

## Deprecated Names

Apart from the CLI aliases above, the names that 0.4 deprecated are not
removed in 0.6. They still work and still warn, and they are removed in
v0.7.0. The [Deprecated APIs](../solving/quip-network.md#deprecated-apis)
table lists each one with its replacement. Moving to the replacements now
leaves nothing to change for this when 0.7 arrives.
