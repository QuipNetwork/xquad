# xquad -- Primary user-facing package for the XQuad toolchain

`xquad` is the Python distribution most users should install. It provides the interactive `Program` / `Session` / `RunResult` API, a unified `VM` wrapper that dispatches to either the Rust or Python interpreter, and re-exports the lower-level peer packages (`xqffi`, `xqcp`, `xqsa`) under a single namespace.

## Install

```sh
pip install xquad
```

Pulls `xqffi`, `xqcp`, `xqsa`, and `xqvm_py` as runtime dependencies.
Peer installs stay valid (`pip install xqffi xqcp xqsa`) if you only
need a subset.

## Interactive usage

Load a program once, run it with different calldata, inspect outputs by slot:

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

`Program.load(bytes)` takes wire-format bytecode; `Session.run()` returns a `RunResult` with dict-keyed outputs (unset slots present as `None`), residual stack, and step count.

## Full pipeline

Compile a problem via the DSL, execute, sample:

```python
from xquad import cp, sa
from xquad.vm import VM, VMBackend

problem = cp.Problem("MyProblem")
# ... define inputs, model, objective, decoder ...
programs = problem.compile()

vm = VM(backend=VMBackend.RUST)   # or VMBackend.PYTHON for the reference interpreter
vm.set_output_slots(1)
vm.run(programs.encoder)
model = vm.outputs()[0]

sample = sa.SolverDWaveCPU().solve(model).sample
```

Re-exports of peer packages are identity, not copies:

```python
import xquad, xqcp
assert xquad.cp.Problem is xqcp.Problem   # always True
```

So `isinstance` works whether the caller imported directly from the peer or via the umbrella.

## Subnamespaces

| `xquad.*` | Origin | Contents |
|-----------|--------|----------|
| `xquad.program` | first-party | Interactive API -- `Program`, `Session`, `RunResult` |
| `xquad.vm` | first-party | Unified `VM` wrapper with backend dispatch (`VMBackend.RUST` / `VMBackend.PYTHON`) |
| `xquad.types` | first-party | Canonical Python type aliases used across the API |
| `xquad.asm` | re-exports `xqffi.asm` | `parse_xqasm`, `assemble_source`, `disassemble` |
| `xquad.verifier` | re-exports `xqffi.verifier` | `verify`, `verify_source` |
| `xquad.cp` | re-exports `xqcp` | DSL -- `Problem`, `Types`, expression builders |
| `xquad.sa` | re-exports `xqsa` | Solvers -- `SolverDWaveCPU`, `Solver` |

Lower-level escape hatches remain available directly via the peer packages (`xqffi.vm.Vm` for the raw FFI one-shot surface; `xqvm_py.Executor` for the pure-Python reference VM).

## Also see

- [`xqffi`](https://gitlab.com/quip.network/xquad/-/tree/main/xqffi) -- PyO3 FFI bindings (low-level / conformance).
- [`xqvm_py`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm_py) -- pure-Python reference VM (conformance oracle).
- [`xqcp`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcp) -- constraint-programming DSL.
- [`xqsa`](https://gitlab.com/quip.network/xquad/-/tree/main/xqsa) -- solver adapters.
- [Running Programs](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/running/README.md) -- end-to-end tour including the Program/Session, `xqffi.vm`, and `xquad.vm` surfaces.

## License

AGPL-3.0-or-later.
