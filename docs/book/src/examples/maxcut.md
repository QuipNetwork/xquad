<!--
  AUTO-GENERATED FILE. DO NOT EDIT.
  This file is regenerated from `examples/manifest.yaml and examples/*/README.md` by
  `scripts/gen-example-docs.py`.
  Edit the source README or manifest, then run `make regen-docs`.
-->

# Max-Cut

Source: [examples/maxcut/README.md](https://gitlab.com/quip.network/xquad/-/blob/main/examples/maxcut/README.md)

Find a 2-colour partition of a weighted graph that maximises the total weight
of edges crossing the partition.

## QUBO formulation

- **Input**: `num_nodes` (int), `edges` (Vec of flat `(i, j, w)` triples, `3*|E|` entries)
- **Model**: `n` binary variables, one per node. `x[v] in {0, 1}` selects the side of the cut.
- **Objective**: for each edge `(i, j, w)`, add `-w*(x_i + x_j)` and `+2w*x_i*x_j`. Minimising this minimises `-sum w*[x_i != x_j]`, i.e. maximises the cut.

## DSL methods used

- `problem.input()` -- declare typed calldata inputs
- `problem.define_model()` -- allocate binary XQMX model
- `problem.stow()` -- bind intermediate computations to named registers
- `problem.range()` -- emit RANGE loops
- `model.linear[i].add()` -- accumulate linear bias on variable i
- `model.quadratic[i, j].add()` -- accumulate quadratic coupling between variables i and j
- `problem.output()` -- declare typed output slots
- `problem.sample.getline()` -- read a row from the sample bitstring

## Pipeline overview

1. **CP** (`xqcp`) -- build a random weighted complete graph, declare binary variables (one per node), and add linear/quadratic QUBO terms per edge.
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- solver runs SA/QPU/GPU over the model
5. **Verify** -- verifier checks the sample is binary, then computes energy; this problem declares no constraints for it to check
6. **Decode** -- decoder extracts the 2-colour partition

## Usage

```sh
uv run python examples/maxcut/runner.py --seed 42
uv run python examples/maxcut/runner.py --n 6 --seed 7 -o /tmp/mc.json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of nodes in the complete graph |
| `--solver` | `dwave-cpu` | Solver backend (see Choosing a solver) |
| `--interpreter` | `python` | XQVM backend: `python` or `rust` |
| `--seed` | `42` | Random seed |
| `-o` | stdout | Write JSON result to file |

## Choosing a solver

Solver selection and install extras are the same for every example: see
[Using the Examples](using-examples.md#running-one) and
[Solving Overview](../solving/). The default is `dwave-cpu`, and a
non-default solver will not reproduce the output shown here.

The canonical output and its invariants are defined in the [source README](https://gitlab.com/quip.network/xquad/-/blob/main/examples/maxcut/README.md).
