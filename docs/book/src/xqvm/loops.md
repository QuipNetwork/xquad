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

```
LoopKind::Range {
    current: i64,    // current iteration value
    end: i64,        // exclusive upper bound (start + count)
}
```

`RANGE` pops `count` and `start` from the stack. The loop iterates `current`
from `start` to `end - 1` (where `end = start + count`, wrapping). On each
`NEXT`, `current` is incremented. If `current < end`, execution seeks back to
`body_start`; otherwise the frame is popped and execution falls through.

```asm
PUSH 5       ; start = 5
PUSH 3       ; count = 3
RANGE        ; iterates current = 5, 6, 7
  LVAL r0    ; r0 ← Int(current)
  ; ... body ...
NEXT
```

### Iterator Loops

```
LoopKind::Iter {
    elements: IterElements,  // slice copy of vec[start..end]
    start_offset: usize,     // original `start` index, used by LIDX
    index: usize,            // current position within `elements`
}

enum IterElements {
    Int(Vec<i64>),
    Xqmx(Vec<XqmxModel>),
}
```

`ITER reg` pops `end_idx`, then `start_idx`, validates that `reg` holds
`VecInt` or `VecXqmx`, and copies `vec[start_idx..end_idx]` into a new
frame with `index = 0`. The slice is *duplicated* so that mutations to the
source vec inside the loop body do not affect what `LVAL` sees.

On each `NEXT`, `index` is incremented. If `index < elements.len()`,
execution seeks back to `body_start`; otherwise the frame is popped.

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

`ITER` errors with `IndexOutOfBounds` if either index is negative, exceeds
`vec.len()`, or if `start_idx > end_idx`.

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

Loops can be nested to arbitrary depth. Each `RANGE` or `ITER` pushes a new
frame. `LVAL` and `NEXT` always operate on the **innermost** (most recently
pushed) frame.

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

- **`NoActiveLoop`** -- `NEXT` or `LVAL` with an empty loop stack.
- **`RegisterType`** -- `ITER` on a register that is not `VecInt` or `VecXqmx`.
