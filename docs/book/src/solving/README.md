# Solving Overview

[Backends](../concepts/backends.md) names the five solvers and what
distinguishes them. This chapter covers each one in enough depth to run
it: installation, parameters, and what its result actually contains.

`xqsa` is the package that does this. It defines one abstract interface,
`Solver`, and five classes built on it -- `SolverDWaveCPU`,
`SolverDWaveQPU`, `SolverCudaGPU`, `SolverMetalGPU`, and `SolverQuip`. A
model built from `xqcp` or by hand in XQVM bytecode does not know or care
which one samples it.

## The Solver Interface

Every backend defines the same single method:

```python
class Solver(ABC):
    @abstractmethod
    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult: ...
```

`model` is an XQMX in MODEL mode, binary or spin domain. `**kwargs`
override that solver's constructor defaults for this call only --
`solver = SolverDWaveCPU(num_reads=100)` then `solver.solve(model,
num_reads=500)` runs 500 reads without building a new solver. `solve()`
raises on failure (hardware unreachable, no embedding found, connection
lost); it never returns a partial or empty result.

The return value is a frozen dataclass:

```python
@dataclass(frozen=True)
class SolverResult:
    sample: XQMX
    energy: int
    timing: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

`sample` is the solution, an XQMX in SAMPLE mode. `energy` is the
authoritative Hamiltonian energy. `timing` is wall-clock seconds spent
solving. `metadata` defaults to an empty dict; its actual shape is
per-backend, covered below.

`energy` is never the backend's own reported value taken on faith. Every
`solve()` recomputes it with the same integer formula the XQVM `ENERGY`
opcode uses, over the model and the returned sample. See
[Energy and Precision](energy-and-precision.md) for what that buys you
and what it costs on hardware with limited precision.

`metadata`'s shape is not identical across all five backends. The three
classical simulated-annealing backends -- `dwave-cpu`, `cuda-gpu`, and
`metal-gpu` -- carry `seed` and `reads` at the top level plus a `params`
dict of solver-specific detail (sweep counts, the raw pre-recompute
energy). `dwave-qpu` carries a `params` dict the same way, but omits
`seed` (there is no seed on physical hardware) and adds `solver` and
`qpu_timing` at the top level, alongside `reads`; see
[D-Wave QPU](dwave-qpu.md#how-a-qpu-result-differs). `quip` returns a
different set entirely -- no `seed`, `reads`, or `params` at all; see
[Quip Network](quip-network.md#metadata). Check the backend you are
calling before indexing into `result.metadata`.

## Picking a Backend by Name

`build_solver` constructs any of the five from a short string, so a
caller -- an example runner, a CLI flag, a script -- can stay
backend-agnostic:

```python
from xqsa import SOLVERS, DEFAULT_SOLVER, build_solver

print(sorted(SOLVERS))
# ['cuda-gpu', 'dwave-cpu', 'dwave-qpu', 'metal-gpu', 'quip']
print(DEFAULT_SOLVER)
# dwave-cpu

solver = build_solver("dwave-cpu", seed=42)
result = solver.solve(model)
```

`seed` reaches the three classical simulated-annealing backends
(`dwave-cpu`, `cuda-gpu`, `metal-gpu`) and is ignored for `dwave-qpu`
(physical hardware has no seed) and `quip` (configured from the
environment; the miner's own randomness is out of the caller's control).

`build_solver` otherwise uses each backend's own constructor defaults,
with one exception: for `cuda-gpu` and `metal-gpu` it raises `num_reads`
to 200 and `num_sweeps` to 2000, rather than the 100 and 1000 the bare
constructor defaults to. [Local Solvers](local.md#gpu-backends-cuda-gpu-and-metal-gpu)
has the constructor defaults for every backend.

## Swapping Backends Without Changing the Model

This is the claim the whole toolchain rests on, so here it is checked
rather than asserted. `examples/maxcut/runner.py` builds one Max-Cut
model with `xqcp`, runs the encoder on the Rust XQVM, and samples the
result with whichever solver `--solver` names, built through
`build_solver`. Run it against two different backends -- `dwave-cpu`
(CPU simulated annealing, 100 reads and 1000 sweeps) and `metal-gpu` (an
Apple Silicon GPU kernel, a different process on different hardware, and
-- per `build_solver`'s override above -- 200 reads and 2000 sweeps) --
and nothing about the model or the encoder changes:

```sh
uv run python examples/maxcut/runner.py --n 6 --seed 42 --interpreter rust --solver dwave-cpu
uv run python examples/maxcut/runner.py --n 6 --seed 42 --interpreter rust --solver metal-gpu
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
from xqsa import SolverDWaveCPU
from xqvm_py.xqmx import XQMX

model = XQMX.integer_model(size=3, k=4)
SolverDWaveCPU().solve(model)
# ValueError: Unsupported domain for solving: INTEGER
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
| A baseline that runs anywhere, no hardware or credentials required | [Local Solvers](local.md), `dwave-cpu` |
| A local GPU for a bigger or faster run | [Local Solvers](local.md), `cuda-gpu` / `metal-gpu` |
| Real quantum annealing hardware | [D-Wave QPU](dwave-qpu.md) |
| A decentralised, miner-solved compute market | [Quip Network](quip-network.md) |
| What the numbers in `SolverResult` actually mean | [Energy and Precision](energy-and-precision.md) |

That baseline requirement -- runs anywhere, needs nothing -- is exactly
`DEFAULT_SOLVER`: every other backend is worth comparing against
`dwave-cpu` before you trust its answer.
