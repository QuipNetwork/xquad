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

The verifier replays the encoder. A constraint's operands -- the index and
coefficient vectors an `apply_equality` was handed -- are register handles,
not data: those vectors are built by `VECPUSH` instructions that run inside
the encoder at VM runtime, often nested in loops. A separate program with
its own register file cannot inherit them. So the verifier re-executes the
encoder's inputs, loops, stows, branches and vector construction, drops
every model mutation, and emits a check of the sample in place of each
constraint, at the same point in the stream. Its register and vector state
at each constraint site is then identical to the encoder's.

That is why the verifier keeps the encoder's register numbers instead of
starting a fixed layout of its own, and why it takes the encoder's
calldata. It claims eight registers above the encoder's high-water mark:
the sample, the `valid` flag, `energy`, the model's declared size, a
weighted-sum accumulator, an `ITER` position, an element, and a counter
tracking the index each `REDUCE` would have allocated. Going past `r255`
raises `RuntimeError` at compile time.

Knapsack's encoder stops at `r11`, so its verifier's sample lands on `r12`
and its `valid` flag on `r13`:

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
PUSH 4
INPUT r4
PUSH 5
INPUT r12

; === Model shape ===
LOAD r0
STOW r15

; === Validity checks ===
PUSH 1
STOW r13

; Check every declared variable is in the model's domain
PUSH 0
LOAD r15
RANGE
  LVAL r17
  LOAD r17
  GETLINE r12
  COPY
  PUSH 0
  EQ
  SWAP
  PUSH 1
  EQ
  OR
  LOAD r13
  AND
  STOW r13
NEXT

; === Objective (replayed for state, not for energy) ===
PUSH 0
LOAD r0
RANGE
  LVAL r5
  LOAD r5
  VECGET r2
  STOW r6
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

; === Constraints ===
PUSH 0
STOW r16
PUSH 0
VECLEN r7
ITER r7
  LIDX r17
  LVAL r18
  LOAD r17
  VECGET r8
  LOAD r18
  GETLINE r12
  MUL
  LOAD r16
  ADD
  STOW r16
NEXT
LOAD r16
LOAD r3
LTE
LOAD r13
AND
STOW r13

; === Energy ===
ENERGY r4 r12
STOW r14

; === Output ===
PUSH 0
OUTPUT r14
PUSH 1
OUTPUT r13
HALT
```

Three things in that listing are worth reading closely. The model is
`INPUT` into `r4`, the register the *encoder* allocated it to, so replayed
references resolve without a renumbering pass; `BQMX` is skipped, but the
size expression is replayed and stowed because the domain check needs the
bound. The objective loop stowing `r6` has no effect on the outcome and
runs anyway -- a later constraint could read that register, and the replay
does not try to work out which stows matter. And the capacity check reads
`LTE`, not `EQ`: knapsack builds its constraint with `slack()` followed by
`apply_equality`, and a slack-extended equality is an inequality over the
real variables. The `SLACK` instruction is not replayed, so `r7` and `r8`
hold the four item entries and no slack entries.

The domain check runs to the model's *declared* size, `r15`, replayed from
`define_model`. Slack and `REDUCE` auxiliary variables live past that size
and are not domain-checked. A `SPIN` model is checked against `-1` and `+1`
instead of `0` and `1`; the binary-only constraint kinds -- `onehot_row`,
`onehot_col`, `exclude`, `implies` -- raise at compile time on a spin model
rather than emit a check that does not mean anything there.

The decoder puts the sample on `r0` and `N` on `r1`, then emits one block
per `problem.output()` call, each starting with `VECI` to allocate the
output vector and ending with `PUSH {slot} / OUTPUT r{out}`. Inside a
decoder block, every scalar reference -- an input the encoder read, or a
value `problem.stow()` put in a register -- resolves to `LOAD r1`, because
one scalar is all the decoder is handed:

```asm
; === Inputs ===
PUSH 0
INPUT r0
PUSH 1
INPUT r1  ; num_items

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
the encoder, so in the decoder it becomes `LOAD r1`, the same register slot
1 was read into two lines above -- the decoder has no independent notion of
`num_items`, only of the one scalar it is passed. Which scalar that is, is
the program's choice rather than a fixed `N`, and the header comment names
it. Referencing a second, distinct scalar is rejected at `compile()`; see
[Outputs and Decoding](outputs-and-decoding.md#what-a-decoder-block-may-reference)
for the full list of what a decoder block may and may not name.

## The Calldata and Output Contract

Each program's calldata order and output slots are fixed by what it was
compiled from, and a host has to match them exactly:

| Program | Calldata (in order) | Outputs |
| --- | --- | --- |
| Encoder | One entry per `problem.input()` call, in call order | Slot `0`: the model |
| Verifier | One entry per `problem.input()` call, in call order, then the model, then the sample | Slot `0`: energy, slot `1`: valid |
| Decoder | Sample, `N` | One slot per `problem.output()` call, in declaration order |

`examples/knapsack/runner.py`'s `run()` function drives exactly this
contract: `vm.set_calldata([n, weights, values, capacity])` and
`vm.set_output_slots(1)` before the encoder, matching the four
`problem.input()` calls in `build_problem()` in order and the encoder's
one output slot; `vm.set_calldata([n, weights, values, capacity, model, sample])`
and `vm.set_output_slots(2)` before the verifier; `vm.set_calldata([sample, n])`
and `vm.set_output_slots(1)` before the decoder. The output slot count
defaults to `0`; running a program that executes `OUTPUT` against a slot
that was never allocated raises `OutputIndex` (see
[Limits and Errors](../xqvm/limits-and-errors.md)), on either
interpreter.

`Problem.verifier_calldata()` returns that order as a list of names --
`["num_items", "weights", "values", "capacity", "model", "sample"]` for
knapsack -- so a host can zip its own values against it rather than
rebuilding the order from the problem definition.

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
