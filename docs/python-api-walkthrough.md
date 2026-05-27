# XQuad Python API walkthrough

A guided tour through the XQuad Python surface — enough to take a
user from zero to encode → sample → verify → decode on their own
problems. Every snippet below is runnable end-to-end against an
installed `xquad` wheel; a companion doctest suite lives at
`xquad/tests/test_program.py` and `xquad/tests/test_umbrella.py`.

The XQuad toolchain ships as five peer PyPI distributions:

| Package | Purpose |
|---------|---------|
| [`xqffi`](../xqffi/) | pyo3 FFI bindings for the Rust VM + assembler (not user-facing) |
| [`xqcp`](../xqcp/) | Constraint-programming DSL |
| [`xqsa`](../xqsa/) | Solver adapters (dwave-samplers) |
| [`xqvm_py`](../xqvm_py/) | Python reference VM (conformance oracle) |
| [`xquad`](../xquad/) | Umbrella meta-package — user-facing API surface |

Install the umbrella (`pip install xquad`) for the full pipeline.
The umbrella re-exports are identity, not copies:
`xquad.cp.Problem is xqcp.Problem` holds, so both import styles
interoperate cleanly. `xqffi` is an internal dependency — user code
should import from `xquad`, not `xqffi` directly.

## The two VM surfaces

### `xquad.program` — the interactive surface

What REPL / notebook / script users reach for. A `Program` loads
once, a `Session` handles mutable calldata and multiple runs, a
`RunResult` presents outputs as a dict keyed by slot.

```python
from xquad.program import Program

src = """
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
"""

# 1. Load the program once.
program = Program.from_source(src)
assert program.instruction_count == 11
assert program.source is not None   # retained for debugging
bytecode = program.bytecode()       # wire-format bytes, if you need them

# 2. Derive a session with 4 output slots. Sessions are mutable,
#    programs are not.
session = program.session(output_slots=4)

# 3. Run with calldata [40, 2] -> slot 0 = 42; slots 1..3 never written.
session.set_calldata([40, 2])
result = session.run()

# 4. Outputs are a dict. Unset slots are None (sparse semantics).
assert dict(result.outputs) == {0: 42, 1: None, 2: None, 3: None}
assert result.stack == []           # residual stack (bottom-to-top)
assert result.steps == 11           # instruction count for this run

# 5. Re-run with different calldata — sessions carry no hidden state.
session.set_calldata([100, 200])
assert dict(session.run().outputs) == {0: 300, 1: None, 2: None, 3: None}
```

### `xqffi.vm` — the low-level surface

What the conformance harness drives. A single one-shot `Vm` object
with a list-shaped `outputs()`. Use this when you want the
smallest-possible wrapper over `xqvm::Vm` — typically conformance
work, fuzzing, or benchmarks.

```python
from xquad.vm import Vm
from xquad.asm import assemble_source

bytecode = assemble_source(src)
vm = Vm()
vm.set_calldata([40, 2])
vm.set_output_slots(4)
vm.run(bytecode)
assert vm.outputs() == [42, None, None, None]  # positional list
```

For anything else, prefer `xquad.program`.

## Loading bytecode directly

`Program.load(bytes)` parses raw wire-format bytes (a `.xqb` blob
produced by `xquad asm` or `Program.bytecode()`). No source is
retained; the `source` attribute reads as `None`.

```python
blob = program.bytecode()
reloaded = Program.load(blob)
assert reloaded.source is None
assert reloaded.instruction_count == program.instruction_count
```

Malformed input surfaces at execution time (the wire format has no
header to validate up-front), so `Program.load` itself is
infallible; an error like `TypeMismatch` or `StackUnderflow` appears
when the session actually tries to run.

## Heterogeneous calldata

Calldata slots may hold any of:

- `int` — scalar integer
- `list[int]` — maps to `VecInt`
- `xquad.vm.XqmxModel` — quadratic model
- `xquad.vm.XqmxSample` — candidate solution
- `None` — unset

```python
from xquad.vm import XqmxModel, XqmxSample

model = XqmxModel("binary", size=4)
model.set_linear(0, -1)
model.set_quad(0, 1, 2)

sample = XqmxSample("spin", values=[-1, 1, -1, 1])

session.set_calldata([model, sample, [1, 2, 3], 42])
# Now session.run() sees four typed input slots.
```

## Inspecting models and samples

Standalone conversion functions in `xquad.vm` turn FFI objects into
plain-Python dicts for serialisation, diffing, or handing to another
library:

```python
from xquad.vm import XqmxModel, model_as_dict, sample_as_dict

model = XqmxModel("binary", size=4)
model.set_linear(0, -1)
model.set_linear(2, 3)
model.set_quad(0, 1, 2)

model_as_dict(model)
# {'domain': 'binary', 'size': 4, 'rows': 0, 'cols': 0, 'k': None,
#  'linear': {0: -1, 2: 3}, 'quadratic': {(0, 1): 2}}
```

The FFI `__repr__` is intentionally minimal (`XqmxModel(domain=binary,
size=4)`) — richer formatting belongs in the `xquad` layer when it
lands.

## End-to-end through the umbrella

The `xquad` umbrella groups all of the above under one import:

```python
from xquad import asm, vm, cp, sa
from xquad.vm import XqmxModel, XqmxSample

# encode — xqcp DSL compiles to .xqasm
problem = cp.Problem("MyProblem")
# ... DSL body ...
programs = problem.compile()
encoder_bytecode = asm.assemble_source(programs.encoder)

# execute — xquad.vm runs it
v = vm.Vm()
v.set_calldata([...])
v.run(encoder_bytecode)

# sample — xqsa drives a solver
result = sa.SolverDWaveCPU().solve(model_from_outputs)

# verify / decode — problem-specific; typically more xqasm runs
```

Each subnamespace is an identity re-export:
`xquad.vm.Vm is xqffi.vm.Vm`. Mixing direct-peer imports and
umbrella imports in the same codebase is safe; `isinstance` works
either way.

## When to reach for which surface

| If you want to... | Use |
|---|---|
| Explore a program in a notebook | `xquad.program.Program` / `Session` |
| Run one program thousands of times on different calldata | `xquad.program.Session` (multi-run, no state leak) |
| Write a conformance test or fuzzer over bytecode | `xquad.vm.Vm` (low overhead, one-shot) |
| Read / diff a model's coefficients | `xquad.vm.model_as_dict()` |
| Build the full encode → sample → decode pipeline | `xquad` umbrella |

## What this walkthrough does not cover

- **Tracing** — step-by-step execution inspection lands as a
  follow-up (QUI-464 trace hooks on `Session`).
- **Keyword calldata** (`session.set_calldata(n=4, ...)`) — requires
  input-slot labels on the program; xqcp is the natural emitter but
  the label channel is not wired yet. Positional list remains.
- **Jupyter-specific `_repr_html_`** — deferred; `__repr__` is
  notebook-friendly for the common small-model case.
- **numpy `to_numpy()` helper** — deferred to avoid a runtime numpy
  dependency; convert via `model_as_dict()` → `numpy.asarray(...)`
  on the user side for now.

These follow-ups are tracked against QUI-464 and will land on top of
this surface without API breaks.
