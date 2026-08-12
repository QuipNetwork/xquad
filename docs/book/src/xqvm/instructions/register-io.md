# Register I/O

Instructions for moving data between the value stack, the 256-slot register
file, calldata, and output slots: `LOAD`, `STOW`, `DROP`, `INPUT`, `OUTPUT`.
Byte values, operand layouts and stack effects are in the
[Register I/O](../opcodes.md#register-io) section of the opcode reference.

## Registers Default to Unset, Not Int(0)

Every register starts as `RegVal::Unset`, not `Int(0)`. Only `LOAD` and
`OUTPUT` fault `UnsetRegister` when reading an unset register; every other
instruction that reads a register instead reports `RegisterType`, with
`unset` as the actual variant held, for example `register r7 holds unset,
expected vec<int>` from `VECPUSH r7`, `register r7 holds unset, expected
model|sample` from `GETLINE r7`, or `register r7 holds unset, expected
sample` from `ENERGY r0 r7`. `LOAD r3` as the first instruction of a
program, before anything has written to `r3`, fails at runtime with
`register r3 is unset`. The verifier's uninitialised-register check
catches the same defect statically for straight-line code, one reason to
run `xquad verify` before `xquad run`: the sequence `PUSH 1 / STOW r0 /
DROP r0 / LOAD r0` is rejected by `verify` with `register r0 read at byte
0x0006 before being written`, before the runtime fault is ever reached.

`LOAD` distinguishes two failure modes for a register that is not usable
as an integer. An unset register faults `UnsetRegister`; a register holding
something other than `Int`, for example a `Model` from
[`BQMX`](allocators.md), faults `RegisterType` instead, naming the actual
variant held. `LOAD` on a register that holds a freshly allocated `Model`
fails with `register r0 holds model, expected int`, a different message
from the unset case.

## Stack-Register Bridge

The value stack holds only `i64` integers. `LOAD` pushes a register's
`Int` value onto the stack; `STOW` pops the stack top and writes it into a
register as `Int`. Neither coerces: `LOAD` on a non-`Int` register errors
rather than reinterpreting the value as an integer.

Richer types, models, vectors and samples, never touch the stack directly.
Moving them into or out of the VM goes through `INPUT` and `OUTPUT`
instead, against the calldata and output-slot arrays rather than the
stack. `INPUT reg` pops a calldata slot index and clones
`calldata[slot]` into `reg`; `OUTPUT reg` pops an output slot index and
clones `reg`'s current value into `outputs[slot]`. Either direction
transfers any `RegVal` variant, not only `Int`, since calldata and outputs
are typed as `Vec<RegVal>` on the Rust side; the `xquad` CLI's
`--calldata` flag only accepts a comma-separated integer list, so
injecting a `Model` or `Sample` through calldata is something host code
building on the `xqvm`/`xqasm` crates can do, not something the CLI
exposes directly. `PUSH 0; INPUT r0; PUSH 0; OUTPUT r0`, given
`--calldata 77`, round-trips a value through a register with no
arithmetic in between: output slot `0` ends up holding `Int(77)`.
Both `INPUT` and `OUTPUT` fault with `CallDataIndex`/`OutputIndex`
respectively when the popped slot index is out of range, and `OUTPUT`
faults `UnsetRegister` if `reg` was never written, the same as `LOAD`.

## Memory Management

`DROP reg` is the only instruction that explicitly frees a register's
allocation, resetting it to `Unset` and releasing whatever `Model`,
`Sample`, `VecInt` or `VecXqmx` the slot held. It does not leave an integer
zero behind: the register is unreadable until the next `STOW`, `INPUT` or
allocator call.
