# XQuad Toolchain Specifications

Authoritative specifications for each layer of the XQuad Toolchain. Every
specification here is the single source of truth for its layer. The
`conformance/` harness does not check either implementation against this
prose: it checks the Rust and Python implementations against each other,
and against `conformance/opcodes.yaml`. Where a divergence between a spec
and an implementation is known, the relevant book page records it.

## Layers

- **[xqvm/](xqvm/)** -- X-Quadratic Virtual Machine. Bytecode format,
  instruction semantics, stack and register model, binary encoding.
  See [`xqvm/README.md`](xqvm/README.md) for the document index.
- **[xqcp/](xqcp/)** -- X-Quadratic Constraint Programming DSL. Symbolic
  problem description and compilation to XQVM assembly. See
  [`xqcp/README.md`](xqcp/README.md).
- **[xqsa/](xqsa/)** -- X-Quadratic Solver Adapters. Solver-backend
  interface and sample/energy contracts. See
  [`xqsa/README.md`](xqsa/README.md).

## Drift policy

There is no tolerated drift between spec and implementations. Any change
to the `xqvm/` spec files that affects observable behaviour must land with:

1. A matching update to `../conformance/opcodes.yaml`.
2. Updated or new conformance vectors under `../conformance/vectors/`.
3. Both `xqvm` (Rust) and `xqvm_py` (Python) passing the full conformance
   suite.

Builds fail on mismatch -- there is no `DRIFT.md`.
