<!--
Copyright (C) 2026 Postquant Labs Incorporated
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# XQuad

> **Early public release.** The instruction set, binary format, and public API
> may still change before v1.0. Production use is not recommended yet.

XQuad is a hardware-agnostic toolchain for expressing and running quadratic
optimization problems (QUBO / Ising / discrete) across quantum annealers and
classical solvers. It ships as three Rust crates plus five Python distributions
built around a single virtual-machine specification.

Think of it as **LLVM for quadratic models** -- a common intermediate
representation that lets you write a problem once and retarget it to any
supported backend.

- **Spec-first.** Every behaviour is nailed down in [`spec/xqvm/SPEC.md`](spec/xqvm/SPEC.md)
  and cross-implementation parity is mechanically enforced: every committed
  conformance vector runs on both the Rust production VM (`xqvm`) and the
  Python reference VM (`xqvm_py`) in CI; disagreement fails the build.
- **Embeddable.** The Rust core supports `no_std + alloc`, so the same VM
  runs inside WASM runtimes, Substrate pallets, and native binaries.
- **Interactive.** The Python umbrella (`xquad`) is REPL and Jupyter-friendly:
  load a program once, run it with different calldata, inspect outputs by
  slot. Low-level FFI lives in `xqffi`; user-facing `Program` / `Session` /
  `RunResult` live in `xquad.program`.

## Install

### Rust (from crates.io)

```sh
cargo install xqcli           # gives you the `xquad` binary
```

### Python (from PyPI)

```sh
pip install xquad             # umbrella -- full pipeline
# or pick individual pieces:
pip install xqffi xqcp xqsa   # FFI bindings + DSL + solvers
```

Prebuilt wheels are available for Linux x86_64 and aarch64 (CPython >= 3.13). On macOS and Windows, pip builds the `xqffi` Rust extension from source -- this requires a [Rust toolchain](https://rustup.rs/) (>= 1.85).

### GPU/QPU support

The base install includes CPU simulated annealing only. To use GPU or quantum hardware solvers, install the corresponding extra:

| Solver | Install | Prerequisite |
|--------|---------|-------------|
| NVIDIA CUDA GPU (`cuda-gpu`) | `pip install xquad[cuda]` | CUDA 12.x driver + NVIDIA GPU |
| Apple Metal GPU (`metal-gpu`) | `pip install xquad[metal]` | Apple Silicon Mac with a Metal device |
| D-Wave Advantage QPU (`dwave-qpu`) | `pip install xquad[dwave]` | D-Wave Leap account + API token |

Extras are composable: `pip install xquad[cuda,dwave]`. See [`xqsa/README.md`](xqsa/README.md#install) for driver prerequisites and per-solver quick-starts.

For a guided walkthrough that installs XQuad, verifies it, and runs a
complete problem end to end, see
[Install XQuad](docs/book/src/start/README.md) in the book.

## Package map

Three Rust crates on **crates.io**:

| Crate | Binary | Description |
|---|---|---|
| [`xqvm`](xqvm/) | none | X-Quadratic Virtual Machine interpreter, bytecode codec, opcode table (`no_std + alloc`) |
| [`xqasm`](xqasm/) | none | `.xqasm` text-format assembler |
| [`xqcli`](xqcli/) | `xquad` | Unified CLI -- `xquad asm` / `dism` / `run` / `verify` |

Five Python distributions on **PyPI**:

| Package | Description |
|---|---|
| [`xqffi`](xqffi/) | PyO3 FFI bindings for `xqvm` + `xqasm` (`xqffi.vm`, `xqffi.asm`, `xqffi.verifier`) |
| [`xqvm_py`](xqvm_py/) | Pure-Python reference VM (conformance oracle) |
| [`xqcp`](xqcp/) | Constraint-programming DSL that compiles to `.xqasm` |
| [`xqsa`](xqsa/) | Solver adapters -- CPU/GPU annealers, D-Wave QPU, Quip network |
| [`xquad`](xquad/) | Umbrella meta-package with interactive `Program` / `Session` / `RunResult` API |

The one Rust package without a crates.io artefact is the `xqffi` pyo3
crate itself -- it ships as the `xqffi` PyPI wheel rather than a reusable
Rust library.

## Quick start

### A minimal program (CLI)

```asm
; add.xqasm -- push two integers and add them
PUSH 10
PUSH 32
ADD
HALT
```

```sh
xquad asm add.xqasm -o add.xqb && xquad run add.xqb
```

### End-to-end (Python, via the umbrella)

The recommended user-facing surface is `xquad.program.Program` +
`Session` + `RunResult`. Load once, run many times with different
calldata, inspect outputs by slot:

```python
from xquad.program import Program

program = Program.from_source("""
PUSH 0
INPUT r0
PUSH 1
INPUT r1
LOAD r0
LOAD r1
ADD
STOW r2
PUSH 0
OUTPUT r2
HALT
""")

session = program.session(output_slots=1)
session.set_calldata([40, 2])
result = session.run()
assert dict(result.outputs) == {0: 42}
```

`Program.load(bytes)` takes wire-format bytecode; `Session.run()`
returns a `RunResult` with dict-keyed outputs (unset slots present as
`None`), residual stack, and step count. See
[Running Programs](docs/book/src/running/README.md) for the full tour.

#### Low-level / conformance surface

For conformance harnesses and one-shot execution, the raw
`xqffi.vm.Vm` is still available directly:

```python
from xqffi.asm import assemble_source
from xqffi.vm import Vm

bytecode = assemble_source(src)
v = Vm()
v.set_calldata([40, 2])
v.set_output_slots(1)
v.run(bytecode)
assert v.outputs() == [42]
```

The `xquad.vm.VM` class offers a middle tier -- a unified wrapper with
backend dispatch (`VMBackend.RUST` / `VMBackend.PYTHON`) that accepts
`.xqasm` source and normalises types across the two interpreters.

### End-to-end worked example

[`examples/tsp/`](examples/tsp/) shows a full Travelling Salesman Problem
driven from the `xqcp` DSL through the VM and `xqsa` solver, runnable on
either the Python reference VM or the Rust VM:

```sh
uv run --no-sync python examples/tsp/runner.py --seed 42
uv run --no-sync python examples/maxcut/runner.py --seed 42 --interpreter rust

# Run every example on both interpreters, check each finds a valid solution:
make example-smoke
```

## Architecture

XQuad is a stack-based interpreter with a 256-slot register file. Registers
hold typed values -- integers, integer vectors, QUBO/Ising models
(`XqmxModel`), and candidate solutions (`XqmxSample`). A dedicated loop stack
drives `RANGE`/`ITER` iteration.

The opcode table is declared once in `xqvm/src/bytecode/types/table.rs` via
the `opcodes!` x-macro; `conformance/opcodes.yaml` is the machine-readable
mirror that all three representations (YAML, Rust macro, `xqvm_py.opcodes`)
are checked against at build time and in CI
(`scripts/check-opcode-parity.py`).

The binary format (`.xqb`) opens with a fixed 15-byte XQBC header --
magic, version, calldata/output-slot counts, code length, and a CRC-32
checksum of the payload -- followed by the instruction stream: an
opcode byte followed by its operands in big-endian byte order.

See [`conformance/opcodes.md`](conformance/opcodes.md) for
instruction-by-instruction semantics and
[`spec/xqvm/SPEC.md`](spec/xqvm/SPEC.md) for the normative spec.

## Development

### Prerequisites

```sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # stable Rust
make deps                                                          # dev tools
```

### Local setup

```sh
make xquad
```

This syncs the Python workspace (`xqvm_py`, `xqcp`, `xqsa`, `xqffi`,
`xquad`) into `.venv/`, builds the `xqffi` pyo3 extension via maturin,
writes a workspace `.pth` so scripts anywhere in the repo can
`import xqcp` / `xqsa` / `xqvm_py` / `xqffi` / `xquad` naturally, and
installs the `xquad` CLI binary under `~/.cargo/bin/`.

Open a REPL with everything ready:

```sh
make repl
```

### CI-equivalent locally

```sh
make all          # fmt + lint + test (what CI runs)
make conformance  # cross-impl parity suite (Rust + Python)
```

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, commit
conventions, and DCO sign-off requirements. Agent guidelines for AI coding
assistants are in [AGENTS.md](AGENTS.md); cutting a release is documented in
[RELEASING.md](RELEASING.md).

## License

Licensed under the [GNU Affero General Public License v3.0 or later](LICENSE).
