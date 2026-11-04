# Migrating from 0.4 to 0.5

v0.5.0 is a breaking release. This page lists every change in it that can
break code written against 0.4, each with what to write instead. Upgrade
the Python packages together: `xquad`, `xqsa`, `xqcp` and `xqffi` pin each
other exactly. On the Rust side, move `xqvm` and `xqasm` to 0.5 together.

## One VM

The Python reference VM is gone. The Rust VM, reached through `xqffi`, is
the only implementation, and the `xqvm_py` distribution has no 0.5 release.

| 0.4 | 0.5 |
|---|---|
| `from xqvm_py ...` | `from xqffi.vm ...`, or the re-exports in `xquad.types` |
| `VM(backend=VMBackend.RUST)`, `vm.backend` | `VM()`. `VMBackend` and the `backend` argument are gone |
| `xquad.types.XQMX`, `XQMXDomain`, `XQMXMode`, `Vec`, `VecElem` | `XqmxModel`, `XqmxSample` and `Domain` from `xquad.types` (see [Model types](#model-types)) |
| Outputs converted to `XQMX` and `Vec` | Outputs are the `xqffi` values unchanged: `int`, `list[int]`, `XqmxModel`, `XqmxSample` or `None` |
| `SampleOutOfDomain` from `VM.set_calldata()` | `ValueError` where the `XqmxSample` is built or written; no out-of-domain sample can exist |
| `--interpreter` on the example runners | Removed; every runner uses the Rust VM |

## Model Types

`xqsa`, `xqcp` and `xquad` all use the typed model API from `xqffi.vm`.

| 0.4 | 0.5 |
|---|---|
| `XQMX.binary_model(size=4)` | `XqmxModel.binary(4)` |
| `XQMX.integer_model(size=3, k=4)` | `XqmxModel.integer(3, k=4)` |
| `XqmxModel("binary", size=4)`, `XqmxSample("spin", values=[...])` | `XqmxModel.binary(4)`, `XqmxSample.spin([...])`, or `XqmxModel(Domain.BINARY, 4)` |
| A domain string, plus `k=` for an integer domain | `Domain.BINARY`, `Domain.SPIN`, `Domain.integer(k)` |
| `model.domain == "binary"` | `model.domain == Domain.BINARY`; the getter returns a `Domain`, with `name` and `k` |
| `compute_energy(model, sample)` | `model.energy(sample)` |
| `"StepLimitExceeded" in str(e)` | `except StepLimitExceeded`. `Vm.run` raises one `XqvmError` subclass per fault, and the message is no longer the Debug form |

A model or sample that no program could build is refused when it is
constructed: a size past `MAX_ALLOCATION_SIZE` raises `InvalidAllocation`,
and a grid that `RESIZE` would refuse raises `InvalidGridDimensions`.

## XQCP Domains

`define_model()` takes an `xqcp.Domain` only. 0.4 also accepted an
`XQMXDomain` from `xqvm_py` and converted it; that type is gone with the
Python VM. `xqcp.Domain.BINARY`, `SPIN`, `INTEGER`
and `CATEGORICAL` are unchanged.

## Solvers

The four per-hardware classes become three classes on one `Solver` base.

| 0.4 | 0.5 |
|---|---|
| `SolverDWaveCPU(...)` | `SolverCPU(...)` |
| `SolverCudaGPU(...)` | `SolverGPU(engine="cuda", ...)` |
| `SolverMetalGPU(...)` | `SolverGPU(engine="metal", ...)` |
| `SolverDWaveQPU(...)` | `SolverDWave(...)`; `mode="qpu"` is the default |
| `strategy=` | `algorithm=` |
| `from xqsa.registry import SOLVERS, DEFAULT_SOLVER, build_solver` | The same names from `xqsa` or `xqsa.solvers` |
| Registry keys `dwave-cpu`, `cuda-gpu`, `metal-gpu`, `dwave-qpu` | `cpu`, `cuda`, `metal`, `dwave`; `quip` is unchanged and `cpu` is the default |
| `from xqsa.dwave_cpu import ...` and the other per-hardware modules | Import the solver classes from `xqsa` |
| An XQMX model in, an XQMX sample out | An `XqmxModel` in, an `XqmxSample` out |

`SolverQuip` keeps its class and its constructor. Two behaviours change for
the other solvers:

- An unknown keyword to `solve()` raises `TypeError` instead of being
  ignored.
- `result.metadata` holds exactly `seed`, `reads` and `params`. The
  `algorithm`, `engine` or `mode` the solver ran with is in `params`, and
  `SolverDWave` moves `solver` and `qpu_timing` under `params` as well.

## Verifier and Metering

The verifier and the VM refuse programs that 0.4 accepted:

- The verifier checks stack underflow against each instruction's pop
  count, and an underflow between two `SCLR` instructions stays an
  underflow. A program that passed the 0.4 verifier and then faulted on
  every run with a stack underflow is now rejected before it runs.
- A step charge that would carry the step counter past `2^64 - 1` raises
  `StepLimitExceeded`, whatever the step limit.

In the `xqvm` crate, `StackEffect::Delta` and `StackEffect::from_delta`
become `StackEffect::Fixed { pops, pushes }` and `StackEffect::fixed`.

## Deprecated Names

The names that 0.4 deprecated still work in 0.5 and still warn, and they are
removed in v0.6.0. The [Deprecated APIs](../solving/quip-network.md#deprecated-apis)
table lists each one with its replacement. The top-level commands `xquad asm`,
`dism`, `run` and `verify` are among them; use `xquad vm` instead (see
[xquad vm](../cli/vm/)).
