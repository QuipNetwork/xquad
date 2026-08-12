# Ways to Use XQuad

Six separate surfaces get a problem into XQuad and a result back out.
Pick the wrong one and you find out several pages later. This page is
the map.

| Surface | What it is | Who it is for |
|---|---|---|
| `xqcp` DSL | Python constraint-programming layer; compiles a problem to XQVM assembly | Modelling a new combinatorial problem without hand-writing assembly |
| `xquad` Program/Session API | `Program` / `Session` / `RunResult`: load a program once, run it repeatedly with fresh [calldata](../xqvm/io.md) | Driving an existing compiled program from Python -- a REPL, a notebook, a script |
| `xqffi.vm` FFI | `Vm`: a thin wrapper over the Rust interpreter | Python code building its own convenience layer on top of the raw Rust VM |
| `xquad.vm` VM wrapper | `VM` / `VMBackend`: runs `.xqasm` source text directly against either the Rust interpreter or the pure-Python reference VM, with `xquad.types.XQMX` at the boundary | Backend-parity checking, or working in `xquad.types` terms; the surface every example runner under `examples/` actually uses |
| `xquad` CLI | `xquad asm` / `run` / `dism` / `verify` | Assembling, running, inspecting, or verifying one `.xqasm`/`.xqb` file, no Python involved |
| Rust embedding | `xqvm::InstructionBuilder`, building a `Program` directly, no text assembly step | Embedding XQVM in a Rust host: `no_std` target, WASM runtime, on-chain pallet |

## `xqcp` -- The DSL

You write a `Problem` in Python: declare inputs, define the model, add
objective terms and constraints, declare outputs. `problem.compile()`
returns `CompiledPrograms(encoder, verifier, decoder)` -- three `.xqasm`
programs, ready to run. See [Three Programs](three-programs.md) for what
those three are and why there are three. Every example under `examples/`
is built this way; `examples/maxcut/runner.py` is a complete pipeline you
can read start to finish.

Stop here if you have a combinatorial problem to model and no existing
`.xqasm` source. Keep reading if you already have compiled bytecode and
need to run or inspect it. Full coverage: [Modelling](../modelling/README.md).

## `xquad.program` -- Program and Session

`Program.from_source(src)` or `Program.load(bytecode)` loads a program
once; `program.session(output_slots=n)` gives you a `Session` you call
`.run()` on repeatedly, each time with fresh calldata via
`session.set_calldata(...)`. Each run returns a `RunResult` with
dict-keyed outputs (an unset slot reads as `None`), the residual stack,
and the step count. `Program.from_source` plus `Session.run()`
round-trips `40 + 2` through calldata and an [output slot](../xqvm/io.md)
to `{0: 42}`, and running the same session again returns `{0: 42}` again:
each `run()` starts clean.

Stop here if you are driving an already-compiled program from Python and
want calldata handling and output decoding done for you. Keep reading if
you are choosing between this and the raw FFI below. Full coverage:
[Running Programs](../running/README.md).

## `xqffi.vm` -- The Raw FFI

`xqffi.vm.Vm` is a thin wrapper over the Rust interpreter: construct a
`Vm`, call `set_calldata`/`set_output_slots`, call `.run(bytecode)`, read
`.outputs()`. No `Program`/`Session` layer on top, and unlike `Session`,
a `Vm` holds its stack and registers across runs: call `.run()` twice on
the same `Vm` without an intervening `.reset()` and the second run's
results sit on top of the first's. `reset()` clears the stack, registers,
and loop state; calldata and outputs both survive a `reset()` untouched,
and only a fresh `set_output_slots(n)` call replaces the output slots.
`Session` sidesteps the question entirely: `Session.run()` builds a
fresh `Vm` per call, so no state survives between runs. That is the real
reason to prefer it for anything but a single one-off program.

Reach for `Vm` directly only if you are building your own convenience
layer on top, or the `Program`/`Session` assumptions do not fit -- most
Python users want `xquad.program` or `xquad.vm` instead. Full coverage:
[Running Programs](../running/README.md).

## `xquad.vm` -- Backend Dispatch

`xquad.vm.VM` selects between the Rust interpreter and the pure-Python
reference VM via `VMBackend`, converting FFI objects to and from the
canonical `xquad.types.XQMX` at the boundary, and runs `.xqasm` source
text directly rather than pre-assembled bytecode. `VMBackend.RUST` is
the default and reaches the Rust interpreter through `xqffi.vm.Vm`
internally. Every example runner under `examples/` imports `VM` and
`VMBackend` from here, not `xqffi.vm` directly.

Stop here if you want backend-parity checking or your calldata and
outputs are already in `xquad.types` terms. Keep reading if you need
the raw FFI underneath instead. Full coverage: [Running
Programs](../running/README.md#the-other-two-vm-surfaces).

## `xquad` CLI

`xquad asm`, `xquad run`, `xquad dism`, `xquad verify` -- four
subcommands, entirely in the shell. Assemble a `.xqasm` file to bytecode, run
a `.xqb` file (or a `.xqasm` file with `--text`) against calldata,
disassemble bytecode back to a readable listing, or verify a compiled
program before running it.
`xquad asm add.xqasm -o add.xqb` followed by `xquad run add.xqb`,
`xquad dism add.xqb`, and `xquad verify add.xqb` assembles, runs,
disassembles, and verifies the four-instruction `add.xqasm` from
[Toolchain Map](README.md).

Stop here if you are working from a shell or from CI. Keep reading if you
need to drive many runs programmatically rather than one at a time. Full
coverage: [CLI](../xqvm/cli/README.md).

## Rust Embedding

`xqvm::InstructionBuilder` builds a `Program` directly from Rust, with no
`.xqasm` text step at all:

```rust
use xqvm::{InstructionBuilder, Vm};

let mut builder = InstructionBuilder::new();
builder.emit_push(10).emit_push(32).emit_add().emit_halt();
let program = builder.build().unwrap();

let mut vm = Vm::new();
vm.run(&program).unwrap();
assert_eq!(vm.stack(), &[42]);
```

The `xqvm` crate is `no_std + alloc`-compatible, so this is the surface
for embedding XQVM where Python, or even a filesystem, is not available.

Stop here if you are embedding the VM inside another Rust program.
Everyone else wants one of the five surfaces above. Full coverage:
[Builder API](../embedding/builder-api.md).

## The Orthogonal Choice: Where It Solves

Whichever surface builds your model, you still choose separately where
that model gets solved: local CPU or GPU simulated annealing, a D-Wave
QPU, or the Quip network. `xqsa.build_solver(name, seed=...)` selects a
backend by name regardless of which surface above produced the model, and
every example runner exposes this as a `--solver` flag. See
[Backends](backends.md).
