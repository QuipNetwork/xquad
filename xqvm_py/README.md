# xqvm_py

> **Status -- transitional.** `xqvm_py` is the executable conformance
> oracle for the XQuad toolchain: every vector under
> [`conformance/`](https://gitlab.com/quip.network/xquad/-/tree/main/conformance) must produce identical
> observable state on both this Python reference and the Rust `xqvm`
> production runtime. The arrangement is explicitly transitional. Once
> the Rust runtime is fully battle-tested, `xqvm_py` may be **dropped
> entirely** (with conformance vectors graduating to "Rust must
> produce exactly these outputs" reference data) or **demoted to a
> prototyping sandbox** for trying out new opcodes / VM features in
> Python before they earn a spec slot. Do not build infrastructure
> that hard-depends on `xqvm_py`'s existence.

Python reference implementation of the X-Quadratic Virtual Machine.
See [`spec/xqvm/SPEC.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/SPEC.md) for the authoritative
technical specification.

## Scope

- Pure-Python **executor**, **state** model, **opcodes**, **xqmx**
  (sparse quadratic matrix), **vector**, and **tracer**.
- **No** assembler or disassembler: the Rust `xqasm` crate is the only
  implementation, exposed to Python via `xqffi.asm` (pyo3). Tests
  and the CLI shim call
  [`xqvm_py.program_from_xqasm`](https://gitlab.com/quip.network/xquad/-/blob/main/xqvm_py/program.py) to turn `.xqasm` text
  into an executable `Program`.

## Layout

```text
xqvm_py/                  <-- this directory IS the package (flat layout)
  __init__.py             re-exports the public surface (Executor, Program, ...)
  __main__.py             entry point for `python -m xqvm_py`
  executor.py             fetch-decode-execute loop
  state.py                stack, registers, loop control, jump table
  opcodes.py              Opcode enum + operand metadata
  program.py              Program dataclass + program_from_xqasm()
  xqmx.py                 sparse quadratic matrix (model + sample modes)
  vector.py               typed vec<int> / vec<xqmx>
  errors.py               typed runtime errors
  tracer.py               step-by-step execution tracer
  cli/                    python -m xqvm_py run ...
  tests/                  pytest suite (wheel-excluded)
```

## Quick start

From the xquad workspace root:

```sh
uv sync                                       # installs xqvm_py editable + xqffi via maturin
uv run pytest xqvm_py/tests                   # run the full test suite
echo "PUSH 5
PUSH 3
ADD
HALT" > /tmp/prog.xqasm
uv run python -m xqvm_py run /tmp/prog.xqasm  # CLI shim
```

Programmatic use:

```python
from xqvm_py import Executor, program_from_xqasm

prog = program_from_xqasm("PUSH 10\nPUSH 5\nADD\nSTOW r0\nHALT\n")
executor = Executor()
executor.execute(prog)
print(executor.state.get_register(0))  # 15
```

## Conformance

Behavioural parity with the Rust `xqvm` crate is enforced by the
`xquad-conformance` Rust test harness at
[`conformance/`](https://gitlab.com/quip.network/xquad/-/tree/main/conformance). New VM semantics require a new
vector. Divergence between implementations fails CI with no "drift
tracking" middle ground.
