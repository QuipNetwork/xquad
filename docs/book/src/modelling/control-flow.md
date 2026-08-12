# Control Flow

XQCP gives you four constructs for anything beyond a flat sequence of
calls: `problem.range()`, `problem.iter()`, `problem.branch()`, and
`problem.stow()`. Each is a Python API you call while building a `Problem`,
and each records something into the action list that `compile()` later
turns into real XQVM instructions. That gap between the two is the first
thing to get straight, because it decides what each construct actually
buys you.

## A Python Loop and a Compiled Loop Are Different Things

`xqcp` is a Python program that runs once, when you call `problem.compile()`,
to produce an `.xqasm` string. An ordinary Python `for` loop inside your
problem definition runs during that one recording pass -- it does not exist
in the output at all. Three plain calls to `add(1)` inside `for i in range(3)`
record three separate `add_linear` actions, one per concrete index, and the
compiled encoder shows exactly that: no loop instruction, three `ADDLINE`
blocks back to back.

```python
for i in range(3):
    problem.model.linear[i].add(1)
```

```asm
PUSH 0
PUSH 1
ADDLINE r1
PUSH 1
PUSH 1
ADDLINE r1
PUSH 2
PUSH 1
ADDLINE r1
```

`problem.range()` is different: it records one `RANGE` loop that the VM
executes at run time, over a count that need not be known until calldata
arrives. `with problem.range(0, num_items) as i` compiles to a single
`RANGE`/`NEXT` pair regardless of how large `num_items` turns out to be,
because `num_items` is a runtime input, not a Python integer the recorder
can unroll:

```python
with problem.range(0, num_items) as i:
    vi = problem.stow("vi", values_in.get(i))
    problem.model.linear[i].add(-vi)
```

```asm
PUSH 0
LOAD r0
RANGE
  LVAL r5
  LOAD r5
  VECGET r2
  STOW r6
  LOAD r5
  LOAD r6
  NEG
  ADDLINE r4
NEXT
```

This is `examples/knapsack/runner.py`'s objective loop, unaltered. Use a
plain Python loop only when the count is fixed at problem-definition time
and small; use `problem.range()` whenever the count depends on an input,
which is the common case, since `num_items` in this example is not known
until calldata is set.

## `range` -- Counted Loops

`problem.range(start, end)` is a context manager yielding a `LoopVar`. It
records a `range_start` action on entry and a `range_end` action on exit,
and compiles to `RANGE`/`NEXT` around whatever the body records. The bounds
can be any expression, not just literals or inputs -- `problem.range(i + 1, n)`
inside another `range(0, n)` builds the upper-triangular pattern a pairwise
comparison needs, one `RANGE` nested inside another:

```python
with problem.range(0, n) as i:
    with problem.range(i + 1, n) as j:
        problem.model.quadratic[i, j].add(1)
```

For `n = 3` this adds `quadratic[i, j] += 1` for every pair with `i < j`:
`(0, 1)`, `(0, 2)`, `(1, 2)`, each exactly once, matching what running the
compiled encoder against `n = 3` produces. `RANGE` is documented at the
instruction level in [Loops](../xqvm/loops.md), where it pops `count` and
`start`, not `start` and an exclusive `end`. `problem.range(start, end)`
takes the exclusive end a Python range would; the compiler emits the
subtraction that turns it into a count, so a non-zero start shows a `SUB`
before `RANGE` -- compiling `problem.range(2, 5)` emits
`PUSH 2 / PUSH 5 / PUSH 2 / SUB / RANGE`. A start of `0` hides this,
because `end` and `count` coincide there, so every `RANGE` listing so far
in this chapter shows no `SUB`.

## `iter` -- Looping Over a Vector's Elements

`problem.iter(vec, start, end)` loops over a slice of a vector's actual
elements rather than a count, compiling to `ITER`/`NEXT`. It always yields a
pair, `(idx, val)`: the element's absolute position in the vector and its
value. Unpack whichever you need and discard the other with `_`:

```python
with problem.iter(weights, 0, n) as (idx, val):
    problem.model.linear[idx].add(val)
```

Running this against `weights = [7, 8, 9]` produces `linear = {0: 7, 1: 8, 2: 9}`
-- `idx` supplies the coordinate, `val` the weight added there. The compiled
form pairs `LIDX`/`LVAL` inside the loop body with exactly this reading:

```asm
PUSH 0
LOAD r0
ITER r1
  LIDX r3
  LVAL r4
  LOAD r3
  LOAD r4
  ADDLINE r2
NEXT
```

Reach for `iter` when the loop body needs the element's value directly and
would otherwise call `vec.get(idx)` on every iteration; reach for `range`
when the body needs an index into something other than the vector being
walked, the way knapsack's objective loop above indexes `values_in`
by position rather than walking it as a slice.

## `stow` -- Carrying State Across Iterations

[Expressions](expressions.md#naming-an-expression-stow) covers what
`problem.stow(name, expr)` does and why knapsack's objective loop uses it
once per iteration. Inside `range` and `iter`, `stow` has a second job:
passing an existing `RegLoad` back into it, instead of a name, reuses that
`RegLoad`'s register rather than allocating a new one, which is how a loop
carries an accumulator from one iteration to the next:

```python
acc = problem.stow("acc", 0)
with problem.range(0, n) as i:
    problem.stow(acc, acc + i)
problem.model.linear[0].add(acc)
```

The compiled loop body reads and writes the same register, `r2`, on every
iteration, and running it for `n = 4` leaves `linear[0] = 6` -- `0 + 1 + 2 + 3`:

```asm
PUSH 0
STOW r2
PUSH 0
LOAD r0
RANGE
  LVAL r3
  LOAD r2
  LOAD r3
  ADD
  STOW r2
NEXT
```

## `branch` -- Conditional Terms

`problem.branch(cond1, body1, cond2, body2, ..., default)` compiles to a
chain of `JUMPI`/`JUMP`/`TARGET` with first-match semantics. `branch` tests
conditions in order and runs the first true arm's body; no later arm runs,
even one whose condition is also true. The final argument is mandatory and
has no condition -- pass a callable for a default action, or `None` to do
nothing when no arm matches.

```python
with problem.range(0, n) as i:
    w = problem.stow("w", weights.get(i))
    problem.branch(
        w > 5, lambda: problem.model.linear[i].add(w * 10),
        w > 0, lambda: problem.model.linear[i].add(w),
        None,
    )
```

```asm
LOAD r4
PUSH 5
GT
NOT
JUMPI .1
  LOAD r3
  LOAD r4
  PUSH 10
  MUL
  ADDLINE r2
  JUMP .0
TARGET .1
LOAD r4
PUSH 0
GT
NOT
JUMPI .2
  LOAD r3
  LOAD r4
  ADDLINE r2
  JUMP .0
TARGET .2
TARGET .0
```

Each condition compiles to itself negated, then a `JUMPI` past its arm: `w > 5`
becomes `NOT` then `JUMPI .1`, so the arm runs when the condition is true and
is skipped straight to `TARGET .1` when it is false. An arm that runs ends
with `JUMP .0`, past every remaining arm, which is what makes the semantics
first-match rather than last-match. The default arm has no condition and no
skip logic -- it just falls through to `TARGET .0` if reached.

Each body is a zero-argument callable, called once while recording, under
the same recording pass a `range` body runs under, so it can reference `i`
and `w` from the enclosing scope. Running this for `weights = [7, 3, 0]` adds
`70` at index `0` (`7 > 5`), `3` at index `1` (`3 > 5` is false, `3 > 0` is
true), and nothing at index `2` (neither condition holds, and the default is
`None`): `linear = {0: 70, 1: 3}`, with index `2` absent rather than zero.

## Nesting and Ordering

`range` and `iter` nest to arbitrary depth, matching the nesting
[Loops](../xqvm/loops.md#nesting) documents at the instruction level: `LVAL`,
`LIDX` and `NEXT` always act on the innermost active loop. `branch` arms can
themselves contain `range`, `iter`, or further `branch` calls, since a body
callable can record anything a top-level problem definition can.

The encoder splits body actions into an objective block and a constraint
block by scanning for constraint calls -- see [Compiling](compiling.md) for
that partition. A `range` or `iter` body is recorded flatly, so a nested
constraint call is still visible to the scan. A `branch` arm is captured
separately: a constraint call made only inside one still constrains the
model, but prints under `; === Objective ===` instead of
`; === Constraints ===`. Call constraint methods directly inside the
enclosing `range` instead, the way
[`examples/set_cover/runner.py`](https://gitlab.com/quip.network/xquad/-/blob/main/examples/set_cover/runner.py)
does, if you want the section comment to match.
