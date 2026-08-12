# D-Wave QPU

`SolverDWaveQPU` submits the model to a physical D-Wave Advantage
quantum annealer over the D-Wave Leap cloud API. This page is derived
from
[`xqsa/dwave_qpu.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/dwave_qpu.py)
and `spec/xqsa/*`; no D-Wave Leap account was available while writing
it, so nothing on this page was run.

## Credentials

Requires the `[dwave]` extra:

```sh
pip install xqsa[dwave]
```

`SolverDWaveQPU()` resolves an API token from the `token=` constructor
argument first, then the `DWAVE_API_TOKEN` environment variable; with
neither set, construction raises `ValueError`. An optional `endpoint=`
or `DWAVE_API_ENDPOINT` overrides the default Leap API URL.

```python
import os
os.environ["DWAVE_API_TOKEN"] = "your-leap-token"

from xqsa import SolverDWaveQPU
from xqvm_py.xqmx import XQMX

model = XQMX.binary_model(4)
model.set_linear(0, -1)
model.set_quadratic(0, 1, 2)

solver = SolverDWaveQPU()              # auto-selects a Pegasus-topology Advantage system
result = solver.solve(model)
print(result.metadata["solver"])       # e.g. "Advantage_system5.4"
print(result.metadata["qpu_timing"])   # QPU timing breakdown from Leap
```

Pass `solver="Advantage_system5.4"` to target a specific system instead
of the default Pegasus-topology filter.

## What Embedding Means

A D-Wave Advantage chip is not fully connected: each physical qubit
couples only to a fixed, small set of neighbours defined by its
Pegasus topology. Your model's coupling graph is almost never a
subgraph of that hardware graph directly. `SolverDWaveQPU` wraps the
sampler in `dwave.system.EmbeddingComposite`, which finds a
*minor embedding*: each logical variable maps to a chain of one or more
physical qubits, wired together so the chain acts as a single variable
by strongly coupling its members (`chain_strength`).

Problem size on real hardware is not simply "how many variables."
A densely coupled model needs longer chains to embed, chains
compete for the chip's limited qubits, and a model that does not embed
at all raises rather than silently degrading. `EmbeddingComposite`
handles the search automatically; there is no separate embedding step
to call in `xqsa`. Contrast this with [Quip Network](quip-network.md),
where `SolverQuip` requires an exact subgraph match onto a fixed
topology and refuses to do this chain-based embedding at all.

## Parameters

| Parameter | Constructor default | Meaning |
|---|---|---|
| `token` | `None` (env fallback) | Leap API token |
| `endpoint` | `None` (env fallback) | Leap API endpoint override |
| `solver` | `None` (Pegasus filter) | Specific Advantage system name |
| `num_reads` | 100 | Annealing runs read back from the chip |
| `annealing_time` | 20 | Microseconds per anneal |

Both `num_reads` and `annealing_time` are overridable per call:
`solver.solve(model, annealing_time=100, chain_strength=2.0)`.
`chain_strength` and other `EmbeddingComposite` options pass straight
through `**kwargs` to the underlying sampler call.

## How a QPU Result Differs

The result shape is the same `SolverResult` every backend returns, but
two things are specific to this backend. `result.energy` is still
recomputed by `xqsa` in exact integer arithmetic from the returned
sample -- the chip's own reported energy is never trusted directly --
but the sample search itself ran on physical hardware subject to analog
noise and the embedding above, not an exact digital simulation. And
`result.metadata` carries QPU-specific detail that the local backends
do not report at all: `metadata["solver"]` names the physical Advantage
system that ran the job, and `metadata["qpu_timing"]` is the timing
breakdown Leap returns -- a dict when present, with keys that come from
Leap and are not fixed by `xqsa`, or `None` when the sampler response
carries no timing info at all. `solver`, `qpu_timing`, and `reads` sit
at the top level of `metadata`, alongside a `params` dict structured
the same way it is for the three simulated-annealing backends
(`params["annealing_time"]`, `params["raw_energy"]`) -- only `solver`
and `qpu_timing` sit outside `params`, and this call does not set
`metadata["seed"]` at all (there is no seed on physical hardware). See
[Solving Overview](./#the-solver-interface) for the full,
per-backend split. If you write code against `result.metadata` for
more than one backend, do not assume its shape is identical across all
five; check the backend you are calling.
