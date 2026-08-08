# Control Flow

Instructions for branching, looping, and program termination.

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x00` | `NOP` | -- | \\([\ldots] \to [\ldots]\\) | -- | No operation. |
| `0x01` | `TARGET` | -- | \\([\ldots] \to [\ldots]\\) | -- | Mark a valid jump destination. Required at every label that `JUMP`/`JUMPI` may target; treated as `NOP` at runtime. The assembler emits this automatically wherever a label is placed, either via the `.N:` shorthand or the explicit `TARGET .N` directive. |
| `0x02` | `JUMP2` | `label: u16` | \\([\ldots] \to [\ldots]\\) | -- | Seek the instruction stream to `jump_table[label].start`. Unconditional. Wide form: takes a `u16` label index. |
| `0x03` | `JUMPI2` | `label: u16` | \\([\ldots, c] \to [\ldots]\\) | -- | Pop \\(c\\). If \\(c \neq 0\\), seek to `jump_table[label].start`; otherwise fall through. Wide form: takes a `u16` label index. |
| `0x80` | `JUMP1` | `label: u8` | \\([\ldots] \to [\ldots]\\) | -- | Same as `JUMP2` but with a single-byte `u8` label index. Used by the assembler when the label id fits in `u8` to save one byte per call site. |
| `0x81` | `JUMPI1` | `label: u8` | \\([\ldots, c] \to [\ldots]\\) | -- | Same as `JUMPI2` but with a single-byte `u8` label index. |
| `0x04` | `NEXT` | -- | \\([\ldots] \to [\ldots]\\) | -- | Advance the active loop frame. For Range: increment current; if \\(\text{current} < \text{end}\\), seek to body start, else pop frame. For Iter: increment index; if \\(\text{index} < \text{len}\\), seek to body start, else pop frame. Errors if no loop frame is active. |
| `0x05` | `LVAL` | `reg: Register` | \\([\ldots] \to [\ldots]\\) | `write` | Copy the current loop value into `reg`. For Range: \\(\text{reg} \leftarrow \text{Int}(\text{current})\\). For Iter: \\(\text{reg} \leftarrow \text{vec}[\text{index}]\\). |
| `0x06` | `RANGE` | -- | \\([\ldots, s, n] \to [\ldots]\\) | -- | Pop \\(n\\) (count), then \\(s\\) (start). Push a Range loop frame with \\(\text{current} = s,\; \text{end} = s + n\\). |
| `0x07` | `ITER` | `reg: Register` | \\([\ldots, s, e] \to [\ldots]\\) | `read` | Pop \\(e\\) (`end_idx`), then \\(s\\) (`start_idx`). Validate that `reg` holds `VecInt` or `VecXqmx`, copy `vec[s..e]` into a new Iter loop frame with \\(\text{start\\_offset} = s\\) and \\(\text{index} = 0\\). The slice is *copied*, so mutations to the source vec inside the loop body do not affect what `LVAL` sees. Errors with `IndexOutOfBounds` if either index is negative, exceeds `vec.len()`, or if `s > e`. |
| `0x08` | `LIDX` | `reg: Register` | \\([\ldots] \to [\ldots]\\) | `write` | Copy the current loop *index* into `reg` as `Int`. For Range: \\(\text{reg} \leftarrow \text{Int}(\text{current})\\) (equivalent to `LVAL` because Range values are already indices). For Iter: \\(\text{reg} \leftarrow \text{Int}(\text{start\\_offset} + \text{index})\\), i.e. the absolute position inside the *source* vec, not the 0-based slice position. Errors with `NoActiveLoop` if no loop frame is active. |
| `0x09` | `HALT` | -- | \\([\ldots] \to [\ldots]\\) | -- | Stop execution immediately. |

## Branching

`JUMP` and `JUMPI` use label indices, not raw byte offsets. The label index
maps to a byte range via the program's jump table. At the assembly level,
labels are written as `.N` (e.g. `.0`, `.1`); the assembler resolves them to
indices automatically and picks the narrowest encoding:

- `JUMP1` / `JUMPI1` (`0x80` / `0x81`) use a single-byte `u8` label index.
  The assembler emits these whenever the label id is `< 256`, so most
  programs will use them exclusively (each call site saves one byte).
- `JUMP2` / `JUMPI2` (`0x02` / `0x03`) use a two-byte `u16` label index.
  The assembler falls back to these only for labels with id `>= 256`.

The assembly source still spells these as `JUMP .N` and `JUMPI .N`; the
narrow-vs-wide selection happens at assembly time and is transparent to
authors. Disassembled output, on the other hand, shows the explicit form
(`JUMP1 .N`, `JUMP2 .N`, etc.) so the round-tripped source preserves the
exact wire encoding.

`TARGET` must appear at every label destination. It is a no-op at runtime but
serves as a validation marker -- the VM verifies that jump targets land on
`TARGET` instructions. The assembler inserts a `TARGET` automatically wherever
a label is *placed*, so authors do not normally type it by hand. Two equivalent
spellings produce the same bytecode:

```asm
; Shorthand: label form
.0: HALT

; Explicit form: TARGET directive bound to a label
TARGET .0
HALT
```

Both compile to `[TARGET, HALT]`. Use whichever is clearer in context. A bare
`TARGET` (with no operand) emits a raw `Target` opcode without binding any
label; that is only useful for direct bytecode construction and most user
programs should prefer one of the label-bearing forms.

## Looping

XQVM provides two loop primitives:

### Range Loops

`RANGE` pops \\(n\\) and \\(s\\) from the stack and creates a loop frame that
iterates `current` from \\(s\\) to \\(s + n - 1\\). Use `LVAL` inside the
loop body to copy the current value into a register, and `NEXT` to advance:

```asm
PUSH 0       ; start
PUSH 10      ; count
RANGE
  LVAL r0    ; r0 = current iteration value (0, 1, ..., 9)
  ; ... loop body ...
NEXT
```

### Iterator Loops

`ITER` takes a register holding a `VecInt` or `VecXqmx` plus two stack
operands `start_idx` and `end_idx` (with `end_idx` on top), and iterates over
the half-open slice `vec[start_idx..end_idx]`:

```asm
PUSH 0       ; start_idx
PUSH 4       ; end_idx
ITER r1      ; r1 must hold a VecInt or VecXqmx
  LVAL r2    ; r2 = current element (from the slice copy)
  LIDX r3    ; r3 = absolute position in r1 (start_idx + index)
  ; ... loop body ...
NEXT
```

Both indices must satisfy \\(0 \le \text{start} \le \text{end} \le \text{vec.len()}\\); otherwise
`ITER` raises `IndexOutOfBounds`. To iterate the entire vec, push
\\(\text{start} = 0\\) and \\(\text{end} = \text{vec.len()}\\) (use `VECLEN` for
the latter).

`ITER` *copies* the slice into the loop frame at the time it runs, so
subsequent in-loop mutations of the source vec via `VECSET`/`VECPUSH` are not
visible to `LVAL` or `LIDX`. This makes loop bodies safe to mutate the
register they iterate over.

Loops can be nested. Each `RANGE` or `ITER` pushes a frame onto the loop stack;
`NEXT` pops the frame when the loop completes.

### Loop Index vs. Loop Value

`LVAL` reads the current loop *value*: the integer being iterated for `RANGE`
loops, or the actual vec element for `ITER` loops. `LIDX` reads the current
loop *index* into the iteration source instead. The two opcodes have
overlapping but distinct semantics:

| Loop kind | `LVAL` | `LIDX` |
|-----------|--------|--------|
| `RANGE` | `Int(current)` | `Int(current)` -- identical to `LVAL`, because the values *are* indices |
| `ITER`  | the slice element at the current index (`Int` or `Model`) | `Int(start_offset + index)` -- the absolute position in the source vec |

Use `LIDX` inside an `ITER` loop when you need to know *where* the current
element lives in the source vec -- typically for index-based lookups or
constraint generation. With slicing, `LIDX` reports the absolute index in
the source vec, not the 0-based position within the slice:

```asm
PUSH 2       ; iterate r1[2..5]
PUSH 5
ITER r1
  LIDX r2    ; r2 = 2, 3, 4 (absolute position in r1)
  LVAL r3    ; r3 = element value at that position
  ; ... loop body uses both r2 and r3 ...
NEXT
```

Calling either `LIDX` or `LVAL` outside any active loop produces a
`NoActiveLoop` runtime error.
