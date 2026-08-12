# Backends

"Hardware-agnostic" means one concrete thing: build a model once, then
choose where it solves, from five backends behind a single interface.
Nothing about the model, the encoder, or the verifier changes with that
choice.

## The Solver Set

| Name | Class | Where it solves | Install |
|---|---|---|---|
| `dwave-cpu` (default) | `SolverDWaveCPU` | Locally, on the CPU: simulated annealing | base `xqsa` |
| `dwave-qpu` | `SolverDWaveQPU` | D-Wave Leap cloud: physical quantum annealing hardware | `xqsa[dwave]` |
| `cuda-gpu` | `SolverCudaGPU` | Locally, on an NVIDIA GPU: simulated annealing | `xqsa[cuda]` |
| `metal-gpu` | `SolverMetalGPU` | Locally, on an Apple GPU: simulated annealing or block Gibbs sampling | `xqsa[metal]` |
| `quip` | `SolverQuip` | The Quip network: submitted to a decentralised compute market, solved by a miner you do not control | `xqsa[quip]` |

`build_solver(name, seed=...)` constructs any of the five from this name,
so a caller can stay backend-agnostic. `seed` reaches the three classical
simulated-annealing backends and is ignored by `dwave-qpu` (physical
hardware has no seed) and `quip` (env-configured, no local randomness).

## What Actually Distinguishes Them

**Where the computation happens.** `dwave-cpu`, `cuda-gpu`, and
`metal-gpu` all run the identical algorithm family -- simulated annealing
-- just on different local processors; picking between them is a
performance question, not a correctness one. `dwave-qpu` hands the problem
to real annealing hardware over the network. `quip` does not solve
anything itself: it posts the model as a job to the network's mempool, a
miner solves it on hardware of its own choosing, and the adapter decodes
whatever comes back.

**Domain support.** Every backend above accepts a binary or spin model and
rejects anything else: `Solver._validate_model()` raises `ValueError` for
any domain other than `BINARY` or `SPIN`. XQVM's third domain, discrete
(`XQMX`), has no backend yet. See [Quadratic Models](quadratic-models.md)
for what the three domains are.

**The result contract is identical regardless of backend.** Every `solve()`
call returns a `SolverResult(sample, energy, timing, metadata)`. `energy`
is not simply relayed from the device: XQuad's own verifier program
recomputes it independently with the `ENERGY` opcode, so you never have to
take a backend's word for its own energy -- run the verifier and recompute
it. See [Three Programs](three-programs.md).

## Before You Choose

`dwave-cpu` needs no hardware and no credentials, so it is the default:
the reproducible baseline every other backend is compared against. The
other four need something this book cannot check for you --
a GPU for `cuda-gpu` or `metal-gpu`, D-Wave Leap credentials for
`dwave-qpu`, network configuration for `quip` -- and setting each of those
up belongs to the pages below.

## Depth Lives in Solving

This page is orientation, not a manual. Backend-specific setup, parameter
tuning, and result interpretation belong to
[Solving Overview](../solving/README.md),
[Local Solvers](../solving/local.md),
[D-Wave QPU](../solving/dwave-qpu.md),
[Quip Network](../solving/quip-network.md), and
[Energy and Precision](../solving/energy-and-precision.md).
