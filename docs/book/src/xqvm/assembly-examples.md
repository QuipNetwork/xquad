# Assembly Examples

This page presents annotated XQVM assembly programs, from simple to complex.

## Hello, Stack

Push two numbers, add them, and halt. The result remains on the stack.

```asm
PUSH 10        ; stack: [10]
PUSH 32        ; stack: [10, 32]
ADD            ; stack: [42]
HALT
```

## Conditional Branch

Skip an instruction if a condition is true:

```asm
PUSH 5
PUSH 10
GT             ; 5 > 10 ? → 0 (false)
JUMPI .0       ; condition is 0, so we fall through
PUSH 99        ; this executes (condition was false)
.0: TARGET
HALT
```

## Countdown Loop

Count down from 3 to 0 using a range loop:

```asm
PUSH 3         ; start = 3
PUSH 0         ; accumulator in r0
STOW r0

PUSH 0
PUSH 3
RANGE          ; iterate 0, 1, 2
  LVAL r1      ; r1 = loop value
  LOAD r0
  LOAD r1
  ADD
  STOW r0      ; r0 += r1
NEXT

LOAD r0        ; push accumulated value (0+1+2 = 3)
HALT
```

## Fibonacci Sequence

Compute the first N Fibonacci numbers and store them in a vector:

```asm
; N is passed as calldata[0]
PUSH 0
INPUT r0       ; r0 = N

VEC r1         ; r1 = empty vec

; Push first two values
PUSH 0
VECPUSH r1     ; vec = [0]
PUSH 1
VECPUSH r1     ; vec = [0, 1]

; Compute remaining values
PUSH 2
LOAD r0
SUB            ; count = N - 2
STOW r2

PUSH 0
LOAD r2
RANGE
  LVAL r3      ; r3 = loop index (unused, just for iteration)
  VECLEN r1
  DEC
  STOW r4      ; r4 = last index

  LOAD r4
  DEC
  VECGET r1    ; stack: fib[n-2]
  LOAD r4
  VECGET r1    ; stack: fib[n-2], fib[n-1]
  ADD           ; stack: fib[n]
  VECPUSH r1   ; append to vec
NEXT

; Output the vector
PUSH 0
OUTPUT r1
HALT
```

## Building a QUBO Model

Create a simple 3-variable QUBO and set coefficients:

```asm
; Allocate a 3-variable binary model
PUSH 3
BQMX r0

; Set linear coefficients: h = [-1, -2, -3]
PUSH 0
PUSH -1
SETLINE r0     ; linear[0] = -1

PUSH 1
PUSH -2
SETLINE r0     ; linear[1] = -2

PUSH 2
PUSH -3
SETLINE r0     ; linear[2] = -3

; Set quadratic coefficient: J[0,1] = 4
PUSH 0
PUSH 1
PUSH 4
SETQUAD r0     ; quad[0,1] = 4

; Output the model
PUSH 0
OUTPUT r0
HALT
```

## Grid with One-Hot Constraints

Set up a 2x3 grid model with one-hot constraints on each row:

```asm
; 6 variables in a 2x3 grid
PUSH 6
BQMX r0
PUSH 2         ; rows
PUSH 3         ; cols
RESIZE r0

; One-hot constraint on each row with penalty = 100
PUSH 0
PUSH 2
RANGE
  LVAL r1
  LOAD r1
  PUSH 100
  ONEHOTR r0
NEXT

; Output
PUSH 0
OUTPUT r0
HALT
```
