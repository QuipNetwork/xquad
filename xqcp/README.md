# XQCP -- X-Quadratic Constraint Programmer

A constraint-programming DSL that compiles a problem description into three
XQVM assembly programs: an encoder, a verifier, and a decoder. See
[Three Programs](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/concepts/three-programs.md)
for what those three are and why there are three.

## Install

```sh
pip install xqcp
```

## A complete example

Max-Cut for three nodes -- the same graph
[Quadratic Models](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/concepts/quadratic-models.md#a-worked-example-max-cut)
derives by hand -- built with `Problem` and compiled to `.xqasm`:

```python
from xqcp import Problem, Types
from xqvm_py import XQMXDomain

problem = Problem("Triangle")
n = problem.input("n", type=Types.Int)
edges = problem.input("edges", type=Types.Vec)
problem.define_model(size=n, domain=XQMXDomain.BINARY)

edge_count = problem.stow("edge_count", edges.veclen() // 3)
with problem.range(0, edge_count) as e:
    offset = e * 3
    i = problem.stow("i", edges.get(offset))
    j = problem.stow("j", edges.get(offset + 1))
    w = problem.stow("w", edges.get(offset + 2))
    problem.model.linear[i].add(-w)
    problem.model.linear[j].add(-w)
    problem.model.quadratic[i, j].add(w * 2)

partition = problem.output("partition", type=Types.Vec)
with problem.range(0, n) as node:
    partition.append(problem.sample.getline(node))

programs = problem.compile()
print(programs.encoder.count("\n"), "lines in the compiled encoder")
# 57 lines in the compiled encoder
```

`programs.encoder` builds the model; `programs.verifier` checks a sample
against it and computes its energy; `programs.decoder` reads a sample back
into a `partition`. Each is a standalone `.xqasm` string, runnable through
any XQVM interpreter.

## How it works

`Problem` records every DSL call (`input`, `define_model`, `range`,
`stow`, `model.linear[i].add(w)`, and the rest) as an `Action`. Nothing is
assembled until `compile()` runs: it walks the recorded actions once per
program and emits assembly with automatic register allocation and loop
nesting.

## DSL reference

The full DSL -- inputs, expressions, objectives, constraints, control flow,
outputs, and what `compile()` produces -- is
[Modelling](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/modelling/README.md),
eight chapters built around this exact package. The normative reference is
[`spec/xqcp/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/SPEC.md);
this package follows it, and any divergence here is a bug.

## Examples

Fourteen complete problems built with this DSL, from
[Max-Cut](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/examples/maxcut.md)
to
[Travelling Salesman](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/examples/tsp.md),
are listed in the
[example gallery](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/examples/README.md).

## Also see

- [`xqvm_py`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm_py) -- pure-Python reference VM that runs the compiled programs.
- [`xqffi`](https://gitlab.com/quip.network/xquad/-/tree/main/xqffi) -- pyo3 FFI bindings to the Rust runtime.
- [`xqsa`](https://gitlab.com/quip.network/xquad/-/tree/main/xqsa) -- solver adapters for the models this package builds.
- [`xquad`](https://gitlab.com/quip.network/xquad/-/tree/main/xquad) -- umbrella meta-package.

## License

AGPL-3.0-or-later.
