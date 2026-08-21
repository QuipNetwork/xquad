# Spec Index

The book teaches; `spec/` is what XQuad actually is. Every file below is
the normative source for its topic's intent. The conformance harness does
not check either implementation against these documents: it checks the
Rust and Python implementations against each other and against
`conformance/opcodes.yaml`. The implementation is what ships, and the two
can diverge; where a divergence is known, the relevant book page says so.
[Conformance](../embedding/conformance.md#coverage-is-per-opcode-and-incomplete)
documents a live one: `IDXTRIU` swaps its two inputs when they arrive out
of order in the spec (`spec/xqvm/ISA.md`) and in `xqvm_py`, but the Rust
VM does not, so the two implementations disagree whenever `i > j`. No
conformance vector catches it today.

Neither list is closed. For others, check the book chapter covering the
component you are working with, or search the spec files directly.

## Top Level

- [`spec/README.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/README.md)
  -- index of the three layers below, and the drift policy that ties spec
  to implementation.

## XQVM

Normative for the virtual machine: the instruction set, the wire format,
and the bytecode verifier.

- [`spec/xqvm/README.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/README.md)
  -- document index for this layer.
- [`spec/xqvm/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/SPEC.md)
  -- normative for the machine overview, the three-program architecture,
  the state model, the type system, and runtime limits.
- [`spec/xqvm/ISA.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ISA.md)
  -- normative for the instruction set: notation, per-category opcode
  tables, semantic notes, and reserved opcodes.
- [`spec/xqvm/HLF.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/HLF.md)
  -- normative for the high-level constraint expansion formulas: the QUBO
  penalty terms `ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `EQUALITY`,
  `ATLEAST`, `ATLEASTW`, and `REDUCE` inject.
- [`spec/xqvm/ENCODING.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/ENCODING.md)
  -- normative for both file formats: `.xqasm` assembly syntax and the
  `.xqb` binary encoding, including the XQBC header layout.
- [`spec/xqvm/VERIFIER.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/VERIFIER.md)
  -- normative for the bytecode verifier: its phase pipeline and every
  error it can raise.

## XQCP

Normative for the constraint-programming DSL that compiles a `Problem`
into the three XQVM programs.

- [`spec/xqcp/README.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/README.md)
  -- document index for this layer.
- [`spec/xqcp/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/SPEC.md)
  -- normative for the DSL overview, the three-program architecture as
  `xqcp` implements it, the problem lifecycle, and the compilation
  contract.
- [`spec/xqcp/TYPES.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/TYPES.md)
  -- normative for the symbolic value types, the expression tree, the
  operator algebra, and the free functions built on top of it.
- [`spec/xqcp/CONSTRAINTS.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/CONSTRAINTS.md)
  -- normative for the constraint taxonomy, each constraint method's
  signature, and its cross-reference into the matching HLF expansion.
- [`spec/xqcp/COMPILER.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/COMPILER.md)
  -- normative for the compilation pipeline: encoder/verifier/decoder
  generation, register allocation, and action recording.

## XQSA

Normative for the solver-adapter interface between a model and a backend.

- [`spec/xqsa/README.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/README.md)
  -- document index for this layer.
- [`spec/xqsa/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/SPEC.md)
  -- normative for the architecture overview, where solving sits in the
  pipeline, and the plugin model new solvers implement.
- [`spec/xqsa/INTERFACE.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/INTERFACE.md)
  -- normative for the `Solver` abstract class, the `solve()` contract,
  the `SolverResult` type, and parameter-passing conventions.
- [`spec/xqsa/ENERGY.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/ENERGY.md)
  -- normative for the energy computation formula, its precision contract,
  and the sparse representation solvers exchange it in.
- [`spec/xqsa/DOMAINS.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/DOMAINS.md)
  -- normative for the domain support matrix, sample encoding, grid
  metadata, and capability negotiation between a model and a solver.
- [`spec/xqsa/SOLVERS.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/SOLVERS.md)
  -- normative for the solver registry, its naming convention, algorithm
  families, and the dependency model behind each optional extra.

## Reading a Spec File Like a Reference, Not a Tutorial

Every file above assumes the concepts this book explains from scratch --
what a quadratic model is, why a problem becomes three programs, what a
penalty weight does. Arrive from [Concepts](../concepts/) or the
relevant chapter first; the spec files are where to go once you need the
exact rule the book page summarised, not where to start.
