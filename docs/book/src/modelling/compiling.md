# Compiling

`problem.compile()` walks the action list `problem.define_model()`, your
objective terms, your constraints and `problem.output()` recorded, and
returns `CompiledPrograms(encoder, verifier, decoder)` -- three `.xqasm`
strings. [Three Programs](../concepts/three-programs.md) covers why the
split exists and what each program is for; this page covers what
`compile()` actually emits and how to read it when something is wrong.

## Three Independent Passes

`compile()` runs three separate compiler functions over the same action
list, each keeping a different subset. Nothing computed for one program
carries into another; there is no shared intermediate representation.

Compiling `examples/knapsack/runner.py`'s `build_problem()` -- the exact
code from [Constraints](constraints.md#turning-an-inequality-into-an-equality),
unaltered -- produces this encoder:

```asm
; === Inputs ===
PUSH 0
INPUT r0
PUSH 1
INPUT r1
PUSH 2
INPUT r2
PUSH 3
INPUT r3

; === Allocations ===
LOAD r0
BQMX r4

; === Objective ===
PUSH 0
LOAD r0
RANGE
  LVAL r5
  LOAD r5
  VECGET r2
  STOW r6
  LOAD r5
  LOAD r6
  NEG
  ADDLINE r4
NEXT
VEC r7
VEC r8
PUSH 0
LOAD r0
RANGE
  LVAL r9
  LOAD r9
  VECPUSH r7
  LOAD r9
  VECGET r1
  VECPUSH r8
NEXT
LOAD r0
LOAD r3
SLACK r7 r8

; === Constraints ===
LOAD r3
PUSH 0x64
EQUALITY r4 r7 r8

; === Output ===
PUSH 0
OUTPUT r4
HALT
```

`PUSH 0x64` is `100`, the penalty passed to `apply_equality` -- the compiler
renders a handful of common penalty values in hex for readability; every
other constant here prints in decimal.

This is the encoder for the exact problem shape `examples/knapsack/runner.py`
builds, independent of the item count: register numbers and instruction
count come from how many `input()`, `stow()`, `vec()` and `define_model()`
calls the Python code makes, not from `--n` at the command line.

## What `compile_encoder` Does

Per [`spec/xqcp/COMPILER.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/COMPILER.md),
the encoder partitions the action list into inputs, the model allocation,
and a body that splits again into objective and constraint blocks: a
top-level block is a constraint block if it contains any of
`onehot_row`/`onehot_col`/`exclude`/`implies`/`equality`/`atleast`/`atleastw`/`inequality`,
otherwise it is objective. `; === Objective ===` and `; === Constraints ===`
are these two blocks, in that order, regardless of the order you wrote them
in Python. Knapsack's objective loop and the `SLACK` call both come from
code written before `apply_equality`, and neither one is itself a
constraint action, so the encoder above places both in the objective
section (see [Control Flow](control-flow.md#nesting-and-ordering) for the
one case, a constraint call inside a `branch` arm, where this partition
surprises).
The output section is fixed: `PUSH 0` / `OUTPUT r{model}` / `HALT`, since an
encoder's only output, always on slot `0`, is the model it built.

Register allocation is one incrementing counter shared across the whole
program. [`spec/xqcp/COMPILER.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/COMPILER.md)
fixes the order. Inputs go first. A 2D model's cols register comes ahead
of the model register; a 1D model has no cols register, so its model
register follows the inputs directly. Loop variables, stowed values,
`vec()` registers and output registers fill in afterward, each claiming a
register in the order it is called. Knapsack's model is 1D: its four
inputs claim `r0`-`r3`, the model claims `r4`, and everything after
follows call order -- `r5`/`r6` for the objective loop's `LoopVar` and
`stow`, `r7`/`r8` for the two `vec()` calls.
[Inputs and Model Shape](inputs-and-model.md#1d-and-2d-models) shows the
2D case, where the cols register lands before the model register: one
input at `r0`, cols at `r1`, model at `r2`. Going past `r255` raises
`RuntimeError` at compile time, before any assembly is generated.

## What `compile_verifier` and `compile_decoder` Do

The verifier and the decoder do not reuse the encoder's register numbers --
each compiler function starts its own fixed layout. The verifier always
puts the model on `r0`, the sample on `r1`, `N` on `r2`, and writes `energy`
and `valid` to `r4` and `r3`. It picks its validity check from
`Problem._constraints`, the constraint-only action list every `apply_*` call
also appends to independently of the objective/constraint partition above:
a `ROWSUM` loop if any `onehot_row` constraint was applied, a `COLSUM` loop
for `onehot_col`, or a binary domain check (every value is `0` or `1`) if
neither was. Knapsack applies neither, so its verifier -- 32 instructions,
shown in full below -- falls through to the binary check:

```asm
; === Inputs ===
PUSH 0
INPUT r0
PUSH 1
INPUT r1
PUSH 2
INPUT r2

; === Validity checks ===
PUSH 1
STOW r3

; Check all variables are binary
PUSH 0
LOAD r2
RANGE
  LVAL r10
  LOAD r10
  GETLINE r1
  COPY
  PUSH 0
  EQ
  SWAP
  PUSH 1
  EQ
  OR
  LOAD r3
  AND
  STOW r3
NEXT

; === Energy ===
ENERGY r0 r1
STOW r4

; === Output ===
PUSH 0
OUTPUT r4
PUSH 1
OUTPUT r3
HALT
```

Whether the capacity constraint holds is not checked here at all -- a
binary-domain check only confirms every sample value is `0` or `1`, not that
the weighted sum obeys the capacity. `ENERGY` still recomputes the true
Hamiltonian independently of whatever produced the sample, so a solution
that violates capacity reports a high energy (see
[Constraints](constraints.md#choosing-a-penalty-weight)) rather than being
flagged invalid; nothing in the verifier's fixed layout adds a dedicated
check for `equality`, `atleast` or `atleastw` constraints the way it does
for one-hot.

The decoder puts the sample on `r0` and `N` on `r1`, then emits one block
per `problem.output()` call, each starting with `VECI` to allocate the
output vector and ending with `PUSH {slot} / OUTPUT r{out}`. Inside a
decoder block, every `InputRef` -- any input the encoder read -- resolves to
`LOAD r1`, because `N` is the only scalar input the decoder has:

```asm
; === Inputs ===
PUSH 0
INPUT r0
PUSH 1
INPUT r1

; === Decode selected ===
VECI r2
PUSH 0
LOAD r1
RANGE
  LVAL r10
  LOAD r10
  GETLINE r0
  VECPUSH r2
NEXT

; === Output ===
PUSH 0
OUTPUT r2
HALT
```

`num_items` in `with problem.range(0, num_items) as i` is an `InputRef` in
the encoder, so in the decoder it becomes `LOAD r1`, the same register `N`
was read into two lines above -- the decoder has no independent notion of
`num_items`, only of `N`.

## The Calldata and Output Contract

Each program's calldata order and output slots are fixed by what it was
compiled from, and a host has to match them exactly:

| Program | Calldata (in order) | Outputs |
| --- | --- | --- |
| Encoder | One entry per `problem.input()` call, in call order | Slot `0`: the model |
| Verifier | Model, sample, `N` | Slot `0`: energy, slot `1`: valid |
| Decoder | Sample, `N` | One slot per `problem.output()` call, in declaration order |

`examples/knapsack/runner.py`'s `run()` function drives exactly this
contract: `vm.set_calldata([n, weights, values, capacity])` and
`vm.set_output_slots(1)` before the encoder, matching the four
`problem.input()` calls in `build_problem()` in order and the encoder's
one output slot; `vm.set_calldata([model, sample, n])` and
`vm.set_output_slots(2)` before the verifier; `vm.set_calldata([sample, n])`
and `vm.set_output_slots(1)` before the decoder. The output slot count
defaults to `0`; running a program that executes `OUTPUT` against a slot
that was never allocated raises `OutputIndex` (see
[Limits and Errors](../xqvm/limits-and-errors.md)), on either
interpreter.

## Inspecting the Emitted Assembly

`programs.encoder`, `programs.verifier` and `programs.decoder` are plain
Python strings -- printing one is the fastest way to see what a problem
definition actually compiled to, which is how every listing on this page
was produced. From there, the `xquad` CLI static-inspects a single program
without needing a Python host at all: `xquad asm` and `xquad dism`
round-trip a `.xqasm` file to bytecode and back to a readable listing, and
`xquad verify` runs the same structural, jump-target, loop-nesting,
register type-state and stack-depth checks `problem.compile()` already runs
automatically through `xqffi` when that package is installed. See
[CLI](../xqvm/cli/) for the full command reference; nothing about
compiling changes it.

`xquad verify` and `xquad dism` work on any of the three programs as they
stand, but `xquad run` does not, because none of the three programs' inputs
are all plain integers -- the encoder above needs two vectors on calldata
positions `1` and `2`, and the CLI's `--calldata` flag only accepts a
comma-separated list of `i64`s. Handing it integers where a program expects
a vector does not fail to parse; it fails at run time, once the program
tries to use the value:

```
$ xquad run --text knapsack.encoder.xqasm --calldata 2,10,20,5
Error: xqvm::runtime_error

  × register r2 holds int, expected vec<int>
```

Running any of these three programs against real calldata needs a host that
can construct vectors, models and samples -- the Python `VM`/`Program`
surfaces [Ways to Use XQuad](../concepts/ways-to-use.md) describes, covered
in full in [Running Programs](../running/). The CLI's role here is
static: assemble, disassemble, and verify a program's structure before
handing it to a host that can supply calldata rich enough to run it.
