# Max-Cut

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

| Name | Hardware | Install |
|------|----------|---------|
| `dwave-cpu` | CPU (default) | `pip install xquad` |
| `dwave-qpu` | D-Wave Leap account | `pip install xquad[dwave]` |
| `cuda-gpu` | NVIDIA CUDA GPU | `pip install xquad[cuda]` |
| `metal-gpu` | Apple Silicon (macOS) | `pip install xquad[metal]` |
| `quip` | Quip Network (remote miner, env-configured) | `pip install xqsa[quip]` |

See [GPU/QPU installation](../../README.md#gpuqpu-support) for driver
prerequisites and [xqsa solver quick-starts](../../xqsa/README.md) for
per-solver parameter tuning.

Non-default solvers will not reproduce the canonical output (different
RNG/hardware). `example-smoke` always runs `dwave-cpu`.

## Canonical output

`example-smoke` validates both interpreters produce `valid == 1` with
`--seed 42 --solver dwave-cpu`. The smoke test is invariant-based --
it checks validity, not exact output.
