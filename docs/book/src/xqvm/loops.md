# Loop Stack

The loop stack manages `RANGE` and `ITER` loop state. Each active loop pushes a
frame; `NEXT` either advances the loop or pops the frame when iteration
completes.

## Loop Frames

Each frame records:

- **Kind** -- `Range` or `Iter`.
- **`body_start`** -- byte offset of the first instruction after `RANGE`/`ITER`.
  This is where `NEXT` seeks back to on each iteration.

### Range Loops

A range frame tracks two values:

- **`current`** -- the current iteration value.
- **`end`** -- the exclusive upper bound (`start + count`).

`RANGE` pops `count` and `start` from the stack. The loop iterates `current`
from `start` to `end - 1` (where `end = start + count`). On each
`NEXT`, `current` is incremented. If `current < end`, execution seeks back to
`body_start`; otherwise the frame is popped and execution falls through. If
`count` is zero or negative, no frame is pushed at all: execution scans
forward past the matching `NEXT` and the body never runs.

```asm
PUSH 5       ; start = 5
PUSH 3       ; count = 3
RANGE        ; iterates current = 5, 6, 7
  LVAL r0    ; r0 ← Int(current)
  ; ... body ...
NEXT
```

### Iterator Loops

An iterator frame tracks three values:

- **`elements`** -- a copy of the slice `vec[start_idx..end_idx]`, holding
  either integers or models depending on the source register's variant.
- **`start_offset`** -- the original `start_idx`, used by `LIDX` to report
  absolute positions.
- **`index`** -- the current position within `elements`.

`ITER reg` pops `end_idx`, then `start_idx`, validates that `reg` holds
`VecInt` or `VecXqmx`, and copies `vec[start_idx..end_idx]` into a new
frame with `index = 0`. The slice is *duplicated* so that mutations to the
source vec inside the loop body do not affect what `LVAL` sees.

On each `NEXT`, `index` is incremented. If `index` is still within the
copied elements, execution seeks back to `body_start`; otherwise the frame
is popped.

```asm
; Assume r1 holds VecInt([10, 20, 30, 40, 50])
PUSH 1
PUSH 4
ITER r1            ; iterate r1[1..4] -> values 20, 30, 40
  LVAL r2          ; r2 -> Int(20), Int(30), Int(40)
  LIDX r3          ; r3 -> Int(1), Int(2), Int(3) (absolute positions)
  ; ... body ...
NEXT
```

`ITER` errors with `IndexOutOfBounds` if either index is negative or exceeds
`vec.len()`.

An empty slice skips the body, exactly like `RANGE` with a count of zero: no
frame is pushed and execution resumes after the matching `NEXT`. The condition
is `start_idx >= end_idx`, so an inverted range is empty rather than an error,
and a slice that is never taken is not bounds-checked.

## LVAL -- Reading the Loop Value

`LVAL reg` copies the current loop value into a register:

- **Range:** `reg ← Int(current)`
- **Iter over VecInt:** `reg ← Int(elements[index])` (the slice copy, not the source vec)
- **Iter over VecXqmx:** `reg ← Model(elements[index])` (cloned)

The element type is preserved: iterating over a `VecXqmx` yields `Model`
values, not integers. Because `elements` is a slice copy taken at `ITER`
time, mutating the source vec inside the loop body never changes what
subsequent `LVAL` calls return.

## LIDX -- Reading the Loop Index

`LIDX reg` copies the current loop *index* into a register:

- **Range:** `reg ← Int(current)` (identical to `LVAL` because Range values
  are themselves indices).
- **Iter:** `reg ← Int(start_offset + index)` -- the absolute position in
  the source vec, not the 0-based slice position. This lets loop bodies
  reach back into the source vec by absolute index even after slicing.

## Nesting

Each `RANGE` or `ITER` pushes a new frame, and `LVAL` and `NEXT` always
operate on the **innermost** (most recently pushed) frame. The loop stack
is capped at 8,192 frames, the same cap the value stack has carried since
the first release; a program past it fails with `LoopStackOverflow`. Real
nesting never approaches that. The cap exists because only `NEXT` pops a
frame, so a back-edge that re-enters a loop header without running its
`NEXT` grows the stack once per execution and would otherwise be bounded
only by the step budget.

```asm
PUSH 0
PUSH 3
RANGE              ; outer loop: 0, 1, 2
  LVAL r0
  PUSH 0
  PUSH 4
  RANGE            ; inner loop: 0, 1, 2, 3
    LVAL r1
    ; r0 = outer value, r1 = inner value
  NEXT
NEXT
```

## Errors

- **`NoActiveLoop`** -- `NEXT`, `LVAL`, or `LIDX` with an empty loop stack.
- **`RegisterType`** -- `ITER` on a register that is not `VecInt` or `VecXqmx`. Raised before the empty-slice check, so an `ITER` on the wrong register type faults whether the slice is empty or not.
- **`LoopStackOverflow`** -- `RANGE` or `ITER` pushing a frame past the 8,192-frame cap.
- **`IndexOutOfBounds`** -- `ITER` with a slice index outside the vector.
- **`ArithmeticOverflow`** -- `RANGE` whose `start + count` leaves the `i64` range. The bound is computed before the frame is pushed, so the loop does not run at all.
