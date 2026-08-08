# Tracing VM Design

**Linear issue:** QUI-314 — Implement tracing VM
**Date:** 2026-03-27
**Scope:** Phase 1 — logging tracer. Phase 2 (interactive debugger) is out of scope.

## Goal

Add execution tracing to the XQVM interpreter. Each step logs the
instruction pointer, mnemonic, stack state, and registers read/written.
Output goes to stderr (default) or a file, in text or JSON format.

The tracing mechanism must be zero-cost when disabled, `no_std`-compatible
at the trait level, and extensible toward a future interactive debugger.

## Architecture

### Tracer Trait (`crates/vm/src/tracer.rs`, `no_std`)

A generic observer trait injected into the VM execution loop via a type
parameter. Monomorphization eliminates all tracing overhead when
`NoopTracer` is used.

```rust
/// Snapshot of VM state passed to the tracer on each step.
pub struct StepState<'a> {
    /// Byte offset of the current instruction in the code section.
    pub pos: usize,
    /// Step number (1-based).
    pub step: u64,
    /// The instruction about to be executed.
    pub instruction: &'a Instruction,
    /// Current stack contents (bottom-first).
    pub stack: &'a [i64],
    /// Registers read by this instruction (index, value at read time).
    pub read_regs: &'a [(u8, &'a RegVal)],
    /// Registers written by this instruction (index, new value after exec).
    pub written_regs: &'a [(u8, &'a RegVal)],
    /// Current loop nesting depth.
    pub loop_depth: usize,
}

/// Observer notified on every VM execution step.
pub trait Tracer {
    /// Error type for when tracing I/O fails.
    type Error;

    /// Called after each instruction executes, with the resulting state.
    fn on_step(&mut self, state: &StepState<'_>) -> Result<(), Self::Error>;
}
```

**`NoopTracer`** — zero-sized type, `Error = core::convert::Infallible`,
empty `on_step` inlined away by the compiler.

### VM Integration (`crates/vm/src/vm.rs`)

Two public methods on `Vm`:

```rust
impl Vm {
    /// Execute a program without tracing (existing API, unchanged).
    pub fn run(&mut self, program: &Program) -> Result<(), Error>;

    /// Execute a program with a tracer.
    pub fn run_trace<T: Tracer>(
        &mut self,
        tracer: &mut T,
        program: &Program,
    ) -> Result<(), Error>
    where
        T::Error: core::fmt::Display;
}
```

`run()` delegates to `run_trace(&mut NoopTracer, program)`.

**Execution sequence per step:**

1. Decode instruction from `InstructionStream`.
2. Collect read registers: `instr.read_registers()` -> snapshot `(index, &value)`.
3. Snapshot written register values (before): `instr.written_registers()` -> clone values.
4. `dispatch(pos, instr)` — existing dispatch, unchanged.
5. Compare written register snapshots -> collect those that changed: `(index, &new_value)`.
6. Build `StepState` with `read_regs` and `written_regs`.
7. `tracer.on_step(&state).map_err(|e| Error::TraceFailed { ... })?`.

**New error variant:**

```rust
Error::TraceFailed { pos: usize, message: String }
```

### Register Introspection (`crates/bytecode/`)

Two methods added to `Instruction`, derived from the opcode table via the
`opcodes!` x-macro:

```rust
impl Instruction {
    /// Register indices this instruction reads from.
    pub fn read_registers(&self) -> &[u8];
    /// Register indices this instruction writes to.
    pub fn written_registers(&self) -> &[u8];
}
```

Examples:

| Instruction    | Reads      | Writes |
|----------------|------------|--------|
| `LOAD r0`      | `[0]`      | `[]`   |
| `STOW r0`      | `[]`       | `[0]`  |
| `INPUT r0`     | `[]`       | `[0]`  |
| `ENERGY r1 r2` | `[1, 2]`  | `[]`   |
| `BQMX r0`      | `[]`       | `[0]`  |
| `VECPUSH r0`   | `[0]`      | `[0]`  |
| `ADD`           | `[]`       | `[]`   |

### Concrete Tracers (`crates/vm/src/tracer/`, `std`-only)

Both are generic over `W: std::io::Write`.

**`TextTracer<W>`** — aligned columns:

```
step  offset  instruction          stack         read-regs      written-regs
   1  0x0000  PUSH8 42             [42]
   2  0x0009  STOW r0              []                           r0=42
   3  0x000B  LOAD r0              [42]          r0=42
   4  0x000D  BQMX r1              [42]                         r1=model(0x0)
   5  0x000F  HALT                 [42]
```

- Stack shows all elements if <= 8, otherwise top 8 with `...N more` prefix.
- Complex register values show type + size (e.g., `model(8x8)`, `vec<int>(len=12)`).

**`JsonTracer<W>`** — one JSON object per line (JSONL):

```json
{"step":1,"pos":0,"instruction":"PUSH8 42","stack":[42],"changed_regs":{}}
{"step":4,"pos":19,"instruction":"STOW r0","stack":[],"read_regs":{},"written_regs":{"0":{"type":"int","value":42}}}
```

- Hand-written serialization via `write!` — no serde dependency.

### CLI Flags (`crates/vm/src/bin/xqvm.rs`)

New arguments on the `xqvm` binary:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--trace` | bool | false | Enable execution tracing |
| `--trace-format` | `text \| json` | `text` | Trace output format (requires `--trace`) |
| `--trace-file` | `PathBuf` | (none) | Write trace to file instead of stderr (requires `--trace`) |

Usage:

```
xqvm --trace program.xqb
xqvm --trace --trace-format=json program.xqb
xqvm --trace --trace-file=trace.log program.xqb
```

Dispatch in `main()`:

- `--trace` absent: `vm.run(&program)` — zero overhead.
- `--trace` present: construct `TextTracer` or `JsonTracer` wrapping
  `BufWriter<stderr>` or `BufWriter<File>`, call `vm.run_trace(...)`.

## WASM / no_std Compatibility

- The `Tracer` trait, `StepState`, and `NoopTracer` are `no_std`-compatible (no
  `std` imports, no allocator for `NoopTracer`).
- `TextTracer`, `JsonTracer`, and all CLI code are behind `#[cfg(feature = "std")]`.
- Monomorphization with `NoopTracer` produces identical code to the current
  tracer-free `run()` — no vtables, no indirect calls, no binary bloat.
- `Tracer::Error` is an associated type. `NoopTracer` uses
  `core::convert::Infallible`, which the compiler eliminates. Concrete tracers
  use `std::io::Error`.

## Testing

### Unit tests (`crates/vm/src/tracer/`, `no_std`-compatible)

1. **NoopTracer passthrough** — `vm.run()` produces identical results to before.
2. **StepState correctness** — `RecordingTracer` collects all steps into a `Vec`.
   Run `PUSH 3, PUSH 4, ADD, STOW r0, HALT` and assert:
   - 5 steps with correct `pos` offsets.
   - Correct `read_regs` / `written_regs` per step.
   - Stack snapshots match expected state.
3. **Error propagation** — tracer returns `Err` on step N, assert `run_trace`
   returns `Error::TraceFailed`.

### Unit tests (`crates/bytecode/`)

4. **`read_registers` / `written_registers`** — enumerate key instructions
   and assert correct register index sets.

### Integration tests (`crates/vm/tests/`, `std`-only)

5. **TextTracer output** — run a program with `TextTracer<Vec<u8>>`, assert
   output matches expected snapshot.
6. **JsonTracer output** — same approach, verify each JSONL line is valid JSON
   with expected fields.
7. **File output** — run with a `tempfile`, verify contents are non-empty and
   parseable.
