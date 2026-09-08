# XQVM Specification

Authoritative specification for the X-Quadratic Virtual Machine. The spec is split across focused documents; each is the single source of truth for its topic.

## Documents

| File | Content |
|------|---------|
| [SPEC.md](SPEC.md) | Machine overview, three-program architecture, state model, determinism, type system, runtime limits |
| [ISA.md](ISA.md) | Instruction set architecture: notation, opcode tables by category, semantic notes, reserved opcodes |
| [HLF.md](HLF.md) | High-level function expansion formulas and derivations (QUBO penalty terms for combinatorial constraints) |
| [ENCODING.md](ENCODING.md) | File formats (`.xqasm` text, `.xqb` bytecode), assembly syntax, binary bytecode encoding |
| [VERIFIER.md](VERIFIER.md) | Bytecode verification: phase pipeline, error semantics, composable architecture, per-opcode stack effects |
| [METERING.md](METERING.md) | Step metering: what a step is, the cost constants, the per-opcode charge table, the formulas, and conformance |
