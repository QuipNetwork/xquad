# Builder API

`InstructionBuilder` is a fluent Rust API for constructing XQVM bytecode
programmatically, without going through the text assembler. It lives in the
[`xqvm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm) crate.

## Basic Usage

```rust
use xqvm::InstructionBuilder;

let mut b = InstructionBuilder::new();
b.emit_push(10)
 .emit_push(32)
 .emit_add()
 .emit_halt();

let program = b.build().unwrap();
assert_eq!(program.code().len(), 6);
```

## Labels

Labels are opaque handles. Create them with `label()`, anchor them with
`place()`, and reference them in `emit_jump()`/`emit_jump_if()`. Both forward
and backward references work. `place()` returns a `Result` -- placing the
same label twice, or placing a label from a different builder, is an error.

### Backward Reference

```rust
use xqvm::InstructionBuilder;

let mut b = InstructionBuilder::new();
let loop_top = b.label();

b.emit_push(3);
b.place(loop_top).unwrap();  // anchor label at this position
b.emit_push(-1);
b.emit_add();
b.emit_copy();
b.emit_jump_if(loop_top);    // backward jump to loop_top
b.emit_pop();
b.emit_halt();

let program = b.build().unwrap();
```

### Forward Reference

```rust
use xqvm::InstructionBuilder;

let mut b = InstructionBuilder::new();
let done = b.label();

b.emit_push(0);
b.emit_jump_if(done);         // forward jump -- target not yet placed
b.emit_push(42);
b.place(done).unwrap();       // anchor here
b.emit_halt();

let program = b.build().unwrap();
```

## PUSH Auto-Sizing

`emit_push(val)` automatically selects the smallest `PUSH1`--`PUSH8` instruction:

```rust
use xqvm::InstructionBuilder;

let mut b = InstructionBuilder::new();
b.emit_push(0);         // emits PUSH1 (2 bytes)
b.emit_push(42);        // emits PUSH1 (2 bytes)
b.emit_push(1000);      // emits PUSH2 (3 bytes)
b.emit_push(i64::MAX);  // emits PUSH8 (9 bytes)
b.emit_halt();

let program = b.build().unwrap();
assert_eq!(program.code().len(), 17);  // 2 + 2 + 3 + 9 + 1 (HALT)
```

## Register Operations

Most register instructions have a corresponding method:

```rust
use xqvm::{InstructionBuilder, Register};

let mut b = InstructionBuilder::new();
b.emit_push(42)
 .emit_stow(Register(0))     // r0 <- Int(42)
 .emit_load(Register(0))     // push r0's value back onto the stack
 .emit_bqmx(Register(1))     // pop 42 as size; allocate a binary model in r1
 .emit_halt();

let program = b.build().unwrap();
```

`DROP` is available as `emit_drop()`. It resets the register to `Unset`, the
same state an unwritten register starts in -- not `Int(0)`:

```rust
use xqvm::{InstructionBuilder, Register};

let mut b = InstructionBuilder::new();
b.emit_drop(Register(5));  // r5 <- Unset
b.emit_halt();

let program = b.build().unwrap();
```

## ENERGY

The `emit_energy()` method takes two register operands:

```rust
use xqvm::{InstructionBuilder, Register};

let mut b = InstructionBuilder::new();
b.emit_energy(Register(0), Register(1));  // ENERGY r0 r1
b.emit_halt();

let program = b.build().unwrap();
```

## Raw Instruction Emit

For instructions without a dedicated method, use `emit()`:

```rust
use xqvm::{InstructionBuilder, Instruction};

let mut b = InstructionBuilder::new();
b.emit(Instruction::Copy {})
 .emit(Instruction::Halt {});

let program = b.build().unwrap();
assert_eq!(program.code().len(), 2);
```

## Build Errors

`build()` validates all labels and returns errors for:

- **`UnplacedLabel`** -- a label was used in a `JUMP`/`JUMPI` but never placed.
- **`UnusedLabel`** -- a label was placed but never referenced by any jump.
  The label at byte offset 0 is exempt from this check: the builder
  treats offset 0 as the implicit entry point, so a label placed there
  and never jumped to does not count as unused. (The `l0`/`l1` example in
  the next section relies on exactly this -- `l0` sits at offset 0 and is
  never referenced by a jump, yet `build()` succeeds.)
- **`TooManyTargets`** -- more than `u16::MAX + 1` (65,536) labels were
  placed in the program, exceeding the wire-format limit on sequential
  `TARGET` ids.
- **`FixupOutOfBounds`** -- a fixup site falls outside the assembled
  buffer. This indicates a bug in the builder itself, not a mistake in
  caller code.

Two more `Error` variants exist but are returned by `place()`, not
`build()`: `DuplicateLabel` (a label placed more than once) and
`ForeignLabel` (a label passed to a builder that did not create it).

```rust
use xqvm::InstructionBuilder;

let mut b = InstructionBuilder::new();
let ghost = b.label();
b.emit_jump(ghost).emit_halt();
assert!(b.build().is_err());  // UnplacedLabel
```

## Labels and the `JumpTable`

Every `place()` call anchors a label to the current write position and emits
an inline `TARGET` opcode there. `build()` resolves each `JUMP`/`JUMPI` fixup
against the placed `TARGET` positions, renumbers labels into stream order, and
returns a `Program`. The result of that resolution is queryable afterward:
`Program::jump_table()` returns a `JumpTable`, mapping each `TARGET`'s
sequential id (`0, 1, 2, ...` in program order) to its byte offset.

`JumpTable::len()` equals the number of distinct labels placed, and
`JumpTable::get(id)` returns the byte offset for a sequential id:

```rust
use xqvm::InstructionBuilder;

let mut b = InstructionBuilder::new();
let l0 = b.label();
let l1 = b.label();
b.place(l0).unwrap()
 .emit_nop()
 .emit_jump(l1)
 .place(l1).unwrap()
 .emit_halt();

let program = b.build().unwrap();
assert_eq!(program.jump_table().len(), 2);
assert_eq!(program.jump_table().get(0), Some(0));  // first TARGET, byte 0
assert_eq!(program.jump_table().get(1), Some(4));  // second TARGET, byte 4
```

This mapping is built once, at load time, by a single scan of the
instruction stream for `TARGET` opcodes -- it is not part of the `.xqb` wire
format. See [Execution](../xqvm/execution.md) for how the VM's run loop
resolves a `Jump` result against it.
