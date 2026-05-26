# XQCP Specification

Authoritative specification for X-Quadratic Constraint Programming. The spec is split across focused documents; each is the single source of truth for its topic.

## Documents

| File | Content |
|------|---------|
| [SPEC.md](SPEC.md) | DSL overview, three-program architecture, problem lifecycle, compilation contract |
| [TYPES.md](TYPES.md) | Symbolic value types, expression tree, operator algebra, free functions |
| [CONSTRAINTS.md](CONSTRAINTS.md) | Constraint taxonomy, method signatures, HLF expansion cross-references |
| [COMPILER.md](COMPILER.md) | Compilation pipeline: encoder/verifier/decoder generation, register allocation, action recording |

## Reference implementation

[`../../xqcp/`](../../xqcp/) -- Python package. The spec is reverse-engineered from this reference; once landed, the spec is authoritative and any divergence in the reference is a bug.

## Related specifications

- [XQVM](../xqvm/README.md) -- the virtual machine that executes the compiled programs
- [XQSA](../xqsa/README.md) -- solver adapters that sit between the encoder and verifier in the pipeline
