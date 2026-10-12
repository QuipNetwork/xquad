# Solving Overview

[Backends](../concepts/backends.md) names the five solvers and what
distinguishes them. This chapter covers each one in enough depth to run
it: installation, parameters, and what its result actually contains.

`xqsa` is the package that does this. It defines one base class,
`Solver`, and four classes built on it -- `SolverCPU`, `SolverGPU`,
`SolverDWave`, and `SolverQuip` -- which cover the five backends:
`SolverGPU` runs on either a CUDA or a Metal GPU, picked by its `engine`.
A model built from `xqcp` or by hand in XQVM bytecode does not know or
care which one samples it.

## The Solver Interface

Every backend is called through the same single method:

```python
class Solver(ABC):
    def solve(self, model: XqmxModel, **kwargs: Any) -> SolverResult: ...
```

`model` is an `XqmxModel` over the binary or spin domain. `**kwargs`
override that solver's constructor defaults for this call only --
`solver = SolverCPU(num_reads=100)` then `solver.solve(model,
num_reads=500)` runs 500 reads without building a new solver. On every
backend except `quip`, a keyword the solver does not take raises
`TypeError` instead of being ignored, so a typo such as `num_raeds=500`
fails loudly. `solve()` raises on failure
(hardware unreachable, no embedding found, connection lost); it never
returns a partial or empty result.

`solve()` is the same code for every backend except `quip`: it validates
the model, runs the backend, recomputes the energy and assembles the
result. A backend only supplies the part that turns a model into a
sample, so the result has the same shape whichever one ran.

The return value is a frozen dataclass:

```python
@dataclass(frozen=True)
class SolverResult:
    sample: XqmxSample
    energy: int
    timing: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

`sample` is the solution, an `XqmxSample` with one value per variable. `energy` is the
authoritative Hamiltonian energy. `timing` is wall-clock seconds spent
solving. `metadata` holds what the run used, covered below.

`energy` is never the backend's own reported value taken on faith. Every
`solve()` recomputes it with the same integer formula the XQVM `ENERGY`
opcode uses, over the model and the returned sample. See
[Energy and Precision](energy-and-precision.md) for what that buys you
and what it costs on hardware with limited precision.

`metadata` has exactly three keys on `cpu`, `cuda`, `metal`, and
`dwave`:

| Key | Holds |
|---|---|
| `seed` | The seed the run used; `None` when unseeded, and always `None` on `dwave` (physical hardware has no seed) |
| `reads` | How many samples the backend took |
| `params` | Everything else: the selectors that chose the backend (`algorithm`, `engine`, `mode`), the parameters the run used, and diagnostics such as the raw pre-recompute energy |

```python
result = SolverCPU(seed=42).solve(model)
result.metadata["params"]["algorithm"]
# 'sa'
```

Backend-specific detail lives under `params`, so code that reads `seed`
and `reads` works on any of the four; see
[D-Wave QPU](dwave-qpu.md#how-a-qpu-result-differs) for what `dwave`
puts there. `quip` is the exception: it returns a different set of keys
entirely, with no `seed`, `reads`, or `params`; see
[Quip Network](quip-network.md#metadata).

## Picking a Backend by Name

`build_solver` constructs any of the five from a short string, so a
caller -- an example runner, a CLI flag, a script -- can stay
backend-agnostic:

```python
from xqsa import SOLVERS, DEFAULT_SOLVER, build_solver

print(sorted(SOLVERS))
# ['cpu', 'cuda', 'dwave', 'metal', 'quip']
print(DEFAULT_SOLVER)
# cpu

solver = build_solver("cpu", seed=42)
result = solver.solve(model)
```

`seed` reaches the three classical simulated-annealing backends
(`cpu`, `cuda`, `metal`) and is ignored for `dwave`
(physical hardware has no seed) and `quip` (configured from the
environment; the miner's own randomness is out of the caller's control).

`build_solver` otherwise uses each backend's own constructor defaults,
with one exception: for `cuda` and `metal` it raises `num_reads`
to 200 and `num_sweeps` to 2000, rather than the 100 and 1000 the bare
constructor defaults to. `cuda` and `metal` build
`SolverGPU(engine="cuda")` and `SolverGPU(engine="metal")`.
[Local Solvers](local.md#gpu-backends-solvergpu)
has the constructor defaults for every backend.

## Swapping Backends Without Changing the Model

This is the claim the whole toolchain rests on, so here it is checked
rather than asserted. `examples/maxcut/runner.py` builds one Max-Cut
model with `xqcp`, runs the encoder on the Rust XQVM, and samples the
result with whichever solver `--solver` names, built through
`build_solver`. Run it against two different backends -- `cpu`
(CPU simulated annealing, 100 reads and 1000 sweeps) and `metal` (an
Apple Silicon GPU kernel, a different process on different hardware, and
-- per `build_solver`'s override above -- 200 reads and 2000 sweeps) --
and nothing about the model or the encoder changes:

```sh
uv run python examples/maxcut/runner.py --n 6 --seed 42 --solver cpu
uv run python examples/maxcut/runner.py --n 6 --seed 42 --solver metal
```

Both print the same result: `"energy": -571`, `"cut_weight": 571`,
`"valid": 1`, the identical partition. The encoder produced one QUBO;
two unrelated solvers minimised it and agreed. That agreement is not
guaranteed in general -- a harder model can leave different backends in
different local optima -- but the *model* they were handed, and the
*verifier* that checked what came back, never changed.

## The Domain Every Backend Rejects

XQVM has three domains: binary, spin, and integer. Every current
`xqsa` solver's `_validate_model()` accepts binary and spin and raises
`ValueError` on integer:

```python
from xqsa import SolverCPU
from xquad.types import XqmxModel

model = XqmxModel.integer(size=3, k=4)
SolverCPU().solve(model)
# ValueError: Unsupported domain for solving: integer
```

An integer `XqmxModel` is a real thing you can build in XQVM bytecode
today -- see [Three Domains](../concepts/quadratic-models.md#three-domains)
-- but nothing in this chapter can solve one. `spec/xqsa/DOMAINS.md`
calls this "reserved": a future solver may relax the check, but none of
the current five does. Re-encoding the problem in binary or spin is the
only route around it today.

## Choosing Among the Rest

| You want | Read |
|---|---|
| A baseline that runs anywhere, no hardware or credentials required | [Local Solvers](local.md), `cpu` |
| A local GPU for a bigger or faster run | [Local Solvers](local.md), `cuda` / `metal` |
| Real quantum annealing hardware | [D-Wave QPU](dwave-qpu.md) |
| A decentralised, miner-solved compute market | [Quip Network](quip-network.md) |
| What the numbers in `SolverResult` actually mean | [Energy and Precision](energy-and-precision.md) |

That baseline requirement -- runs anywhere, needs nothing -- is exactly
`DEFAULT_SOLVER`: every other backend is worth comparing against
`cpu` before you trust its answer.
