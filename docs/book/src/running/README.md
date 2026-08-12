# Running Programs

This page covers driving an already-compiled `.xqasm` or `.xqb` program from
Python: loading it, supplying calldata, running it, and reading outputs back.
[Ways to Use XQuad](../concepts/ways-to-use.md) names the surfaces; this page
is the full coverage for three of them: `xquad.program`, and the two VM
wrappers underneath and alongside it. For writing a problem with the `xqcp`
DSL, see
[Modelling](../modelling/). For what `xquad verify` checks and how
to fix a rejected program, see [Verification](verification.md).

Every example on this page ran against the Rust backend, the default for
both `xquad.program` and `xquad.vm.VM`.

## Program and Session

`xquad.program.Program` loads a program once; `Program.session()` gives you
a `Session` you call `.run()` on repeatedly, each time with fresh calldata.
Each run returns a `RunResult` with dict-keyed outputs, the residual stack,
and the step count:

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

program = Program.from_source(src)
assert program.instruction_count == 11
assert program.source is not None   # retained for debugging

session = program.session(output_slots=4)
session.set_calldata([40, 2])
result = session.run()

assert dict(result.outputs) == {0: 42, 1: None, 2: None, 3: None}
assert result.stack == []
assert result.steps == 11

# Re-run with different calldata -- sessions carry no hidden state.
session.set_calldata([100, 200])
assert dict(session.run().outputs) == {0: 300, 1: None, 2: None, 3: None}
```

`RunResult.outputs` is a dict keyed by slot index; a slot the program never
wrote reads as `None` rather than raising. `Program` is immutable and
reusable; `Session` carries the mutable calldata and output-slot count.
Deriving a second `Session` from the same `Program` gives you two runners
that share no state.

### Step limits

`Session.set_step_limit(n)` caps how many instructions a run may execute
before `run()` raises. `set_step_limit(0)` does not mean unlimited: `0`
is falsy, so `Session` never forwards it to the interpreter, and the
Rust VM's own built-in limit of 10,000,000 steps stays in force instead.
`Session` offers no way to turn the limit off entirely; it can only
raise it by passing a larger `n`.

```python
session = program.session(output_slots=1)
session.set_calldata([1, 2])
session.set_step_limit(3)
try:
    session.run()
except RuntimeError as e:
    assert "StepLimitExceeded" in str(e)

# 0 leaves the interpreter's 10,000,000-step default in place -- this
# program needs 11 steps, well under that, so it succeeds for a
# different reason than "unlimited".
session.set_step_limit(0)
assert dict(session.run().outputs) == {0: 3}
```

## Loading bytecode directly

`Program.load(bytes)` parses raw wire-format bytes -- a `.xqb` blob produced
by `xquad asm` or by `program.bytecode()`. No source is retained:

```python
blob = program.bytecode()
reloaded = Program.load(blob)
assert reloaded.source is None
assert reloaded.instruction_count == program.instruction_count
```

`Program.load` never validates its input; a malformed blob decodes
successfully as a `Program` object and only fails once a `Session` tries to
run it. A one-byte blob is too short even for the wire-format header:

```python
p = Program.load(b"\x43")
s = p.session()
try:
    s.run()
except RuntimeError as e:
    print(e)
# decode error: TruncatedHeader
```

`Program.from_source` validates at load time instead: an unassemblable
string raises `ValueError` immediately, before a `Session` exists.

## Calldata types

A calldata list may mix any of `int`, `list[int]`, `xqffi.vm.XqmxModel`,
`xqffi.vm.XqmxSample`, and `None` (unset). `Session.set_calldata` checks
every element's type as soon as you call it, not at run time:

```python
from xqffi.vm import XqmxModel, XqmxSample

model = XqmxModel("binary", size=4)
model.set_linear(0, -1)
model.set_quad(0, 1, 2)

sample = XqmxSample("spin", values=[-1, 1, -1, 1])

session.set_calldata([model, sample, [1, 2, 3], 42])
# session.run() now sees four typed input slots.

try:
    session.set_calldata([object()])
except TypeError as e:
    print(e)
# unsupported calldata element type: object; expected int, list[int],
# XqmxModel, XqmxSample, or None
```

## Inspecting a model or sample

`XqmxModel` and `XqmxSample` are pyo3 objects; there is no dict-conversion
helper in this package today. Read a model's coefficients through its own
accessors:

```python
model = XqmxModel("binary", size=4)
model.set_linear(0, -1)
model.set_linear(2, 3)
model.set_quad(0, 1, 2)

model.domain          # "binary"
model.size            # 4
list(model.linear_items())     # [(0, -1), (2, 3)]
list(model.quadratic_items())  # [((0, 1), 2)]
model.get_linear(0)   # -1
model.get_quad(0, 1)  # 2
repr(model)            # 'XqmxModel(domain=binary, size=4)'
```

`__repr__` is intentionally minimal. Convert to `xquad.types.XQMX` (the
canonical, backend-independent type covered next) if you want a Python
object you can inspect with normal attribute access, or serialise by
iterating `linear_items()` / `quadratic_items()` yourself.

## The other two VM surfaces

Two lower-level wrappers sit underneath and alongside `xquad.program`,
both worth knowing about directly.

**`xqffi.vm.Vm`** is the thinnest possible wrapper over the Rust
interpreter: construct it, call `set_calldata` / `set_output_slots`, call
`.run(bytecode)`, read `.outputs()` as a positional list. It always targets
the Rust backend. `.reset()` clears the stack, registers, loop stack, and
step counter, but calldata and output slots are untouched by it -- both
persist across runs until you call `set_calldata()` or `set_output_slots()`
again. It is what `Session.run()` builds internally:

```python
from xqffi.vm import Vm

vm_src = "PUSH 7\nPUSH 5\nADD\nSTOW r0\nPUSH 0\nOUTPUT r0\nHALT\n"
bytecode = Program.from_source(vm_src).bytecode()

vm = Vm()
vm.set_output_slots(1)
vm.run(bytecode)
assert vm.outputs() == [12]
```

[Ways to Use XQuad](../concepts/ways-to-use.md#xqffivm--the-raw-ffi)
covers when to reach for it directly instead of `Session`.

**`xquad.vm.VM`** is a separate wrapper that every example runner under
`examples/` actually uses (`examples/maxcut/runner.py`,
`examples/knapsack/runner.py`). It selects between the Rust interpreter and
the pure-Python reference VM via `VMBackend`, converts FFI
objects to and from the canonical `xquad.types.XQMX` at the boundary, and
runs `.xqasm` source text directly rather than pre-assembled bytecode:

```python
from xquad.vm import VM, VMBackend

src = "PUSH 7\nPUSH 5\nADD\nSTOW r0\nPUSH 0\nOUTPUT r0\nHALT\n"
v = VM(backend=VMBackend.RUST)  # RUST is also the default with no argument
v.set_output_slots(1)
v.run(src)
assert v.outputs() == [12]
assert v.stack() == []
```

`VM.outputs()` returns `xquad.types.XQMX` instances for model and sample
outputs, where `xqffi.vm.Vm.outputs()` and `Session.run().outputs` return
the raw pyo3 `XqmxModel` / `XqmxSample` objects. Pick `VM` when you need
Python-backend parity checking or when your calldata and outputs are
already in `xquad.types` terms; pick `Session` for the dict-keyed,
slot-sparse ergonomics shown above; pick `xqffi.vm.Vm` only if you are
building your own layer on top of the raw Rust FFI.

## A complete run across three programs

A compiled problem is three independent programs -- see [Three
Programs](../concepts/three-programs.md) for why. Driving all three from
`xquad.program` looks like this, using the exact `build_problem` function
`examples/knapsack/runner.py` defines (see [Compiling](../modelling/compiling.md)
for what `compile()` emits):

```python
from examples.knapsack.runner import build_problem
from xquad.program import Program
from xqffi.vm import XqmxSample

n = 4
weights = [2, 3, 4, 5]
values = [3, 4, 5, 8]
capacity = 8

problem = build_problem(n, weights, values, capacity)
programs = problem.compile()

# Encoder: inputs in, model out.
encoder = Program.from_source(programs.encoder)
enc_session = encoder.session(output_slots=1)
enc_session.set_calldata([n, weights, values, capacity])
model = enc_session.run().outputs[0]

# A hand-picked sample -- items 1, 2, 3 selected, no solver involved.
sample = XqmxSample("binary", values=[0, 1, 1, 1, 0, 0, 0, 0])

# Verifier: model + sample + N in, (energy, valid) out.
verifier = Program.from_source(programs.verifier)
ver_session = verifier.session(output_slots=2)
ver_session.set_calldata([model, sample, n])
ver_result = ver_session.run()
energy, valid = ver_result.outputs[0], ver_result.outputs[1]

# Decoder: sample + N in, decoded selection out.
decoder = Program.from_source(programs.decoder)
dec_session = decoder.session(output_slots=1)
dec_session.set_calldata([sample, n])
selected = dec_session.run().outputs[0]

assert (energy, valid, selected) == (-4817, 1, [0, 1, 1, 1])
```

Items 1, 2 and 3 weigh `3 + 4 + 5 = 12` against a capacity of `8` -- this
sample violates the capacity constraint, and the verifier reports it valid
anyway. That is not a bug in this pipeline;
[Verification](verification.md#the-generated-verifiers-valid-flag-does-not-check-every-constraint)
explains why the generated verifier's `valid` flag cannot catch it. Each
program's own `.set_calldata` order and `.set_output_slots` count are fixed
by what it was compiled from -- [Compiling](../modelling/compiling.md#the-calldata-and-output-contract)
has the full table. A real pipeline replaces the hand-picked `sample` above
with `xqsa.build_solver(...).solve(model).sample`; see [Solving
Overview](../solving/).

## What this page does not cover

- **Tracing.** Step-by-step execution inspection is not part of `Session`
  today.
- **Keyword calldata** (`session.set_calldata(n=4, ...)`). `Session` takes a
  positional list only; there is no input-slot labelling to key against.
- **A `_repr_html_` for notebooks.** `__repr__` is the only representation
  `Program`, `Session`, `RunResult`, `XqmxModel` and `XqmxSample` provide.
- **A `to_numpy()` helper.** Convert via `linear_items()` /
  `quadratic_items()` and `numpy.asarray(...)` on your own side; this
  package does not carry a numpy dependency.
