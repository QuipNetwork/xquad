# Outputs and Decoding

A solver hands back a sample: one raw value per variable. Decoding turns
that assignment into the answer your problem is actually about -- a chosen
item set, for knapsack. This page covers a decoder program's outputs, its
read access to the sample, and when decoding outside the VM makes more
sense.

## Declaring an Output

`problem.output(name, type=Types.Vec)` declares one decoder output and
returns an `OutputRef`. Every output the DSL currently supports is a
vector; `.append(value)` grows it by one element, and the emitted decoder
allocates it with `VECI` before the block that fills it:

```python
selected = problem.output("selected", type=Types.Vec)
with problem.range(0, num_items) as i:
    selected.append(problem.sample.getline(i))
```

This is `examples/knapsack/runner.py`'s decoder in full: one output,
`selected`, filled by reading the sample's per-item assignment directly.
`problem.sample.getline(i)` reads `sample[i]`: `1` if the solver selected
item `i`, `0` otherwise. `selected` ends up the same shape as the input
item list, a `0`/`1` flag per item rather than a list of chosen indices.
Compiled, this is `; === Decode selected ===` in the decoder assembly
[Compiling](compiling.md) shows in full: `VECI r2`, a `RANGE` loop reading
`GETLINE r0` and pushing with `VECPUSH r2`, then `PUSH 0 / OUTPUT r2`.

`OutputRef` also supports random-access write and read, `out[i] = value` and
`val = out[i]`, compiling to `VECSET` and `VECGET` against the same output
register. Neither is a working substitute for `.append()`. A constant
index compiles cleanly and passes the verifier:

```python
out[0] = problem.sample.getline(0)
out[1] = problem.sample.getline(1)
```

but faults at runtime, on both backends, the moment it runs. The decoder
allocates `out` with `VECI` before this block executes, and `VECI` creates
an empty `vec<int>`; `VECSET` into an empty vector has nothing to write
into. The Rust VM raises `IndexOutOfBounds`, and the Python VM raises
`IndexError`. The index value never matters -- the vector is empty either
way.

An index built from the loop variable of an enclosing `range` does not
even get that far: it fails to compile. The decoder's loop value always
lives in a fixed register regardless of which register the loop variable
was allocated to during recording, and `output[i] = value` does not make
that substitution the way a value expression does. `compile()` catches
this rather than emitting bad bytecode: `out[i] = problem.sample.getline(i)`
inside a `problem.range` loop fails with
`ValueError: decoder verification failed: ReadUnsetRegister`. Of the three
forms on this page, `.append()` inside a loop is the only one that
actually runs.

## Reading a Sample

`problem.sample` is available once `define_model()` has run, and exposes
five read methods matching the [Grid Operations](../xqvm/instructions/grid.md)
and [Coefficient Access](../xqvm/instructions/coefficient-access.md)
instructions a `Sample` register supports:

| Call | Reads |
| --- | --- |
| `sample.getline(i)` | Variable `i`'s assignment (1D or flat) |
| `sample.rowfind(row, value)` | Column of the first match for `value` in `row` (2D) |
| `sample.colfind(col, value)` | Row of the first match for `value` in `col` (2D) |
| `sample.rowsum(row)` | Sum of every value in `row` (2D) |
| `sample.colsum(col)` | Sum of every value in `col` (2D) |

Knapsack's flat model only needs `getline`. A 2D grid model -- one variable
per `(row, col)` pair, the way a one-hot assignment problem is usually
shaped -- more often needs `colfind`: "which row has a `1` in this column"
reads directly as "which choice was made for this slot." On a
\\(2 \times 2\\) grid sample with rows `[0, 1]` and `[1, 0]`,
`sample.colfind(col=0, value=1)` returns `1` (row 1 has the `1` in column
0) and `sample.colfind(col=1, value=1)` returns `0`, each call compiling to
the column pushed, then the value to match, then `COLFIND r{sample}`.

## The Decoder Program vs. Decoding in the Host

Once a solver returns a sample, two different things can decode it, and
they are not the same operation.

The **decoder program** is XQVM bytecode: `vm.run(programs.decoder)` with
the sample and `N` on calldata, exactly like running the encoder or the
verifier. It only ever sees the sample through the instructions above, so
it runs on any surface that can execute XQVM bytecode --
[Ways to Use XQuad](../concepts/ways-to-use.md) lists six -- with no
Python or Rust object model required at the call site.

**Decoding in the host** means reading the sample object directly in
whatever language is driving the pipeline, without running the decoder
program at all. In Python, a solved sample is an ordinary `XQMX` object
with a `linear` dict, so `sample.get_linear(i)` for each item index reads
the same assignments `sample.getline(i)` does inside the decoder, just from
host code instead of from bytecode:

```python
vm.set_calldata([sample, n])
vm.set_output_slots(1)
vm.run(programs.decoder)
result = vm.outputs()[0]

decoder_selection = list(result)                            # via programs.decoder
host_selection = [sample.get_linear(i) for i in range(n)]   # direct read
```

`VM` and `VMBackend` import from `xquad.vm`, alongside the
`xquad.cp`/`xquad.types` imports [Inputs and Model Shape](inputs-and-model.md)
opens this chapter with. `set_output_slots` has to run before `vm.run`,
since the slot count defaults to `0`. On the default `VMBackend.RUST`,
`OUTPUT` against a slot that was never allocated raises `OutputIndex`
while the decoder runs; `VMBackend.PYTHON` currently accepts it silently,
so do not rely on the error to catch a missing `set_output_slots`.
`list(result)` works whether `vm.outputs()[0]` comes back as a plain
`list`, on the default `VMBackend.RUST`, or as a `Vec`, on
`VMBackend.PYTHON`. Running both against the same seed-42 knapsack
sample (`weights = [2, 1, 5, 4, 4]`, `values = [5, 4, 18, 3, 19]`,
`capacity = 18`) produces identical lists: `[1, 1, 1, 0, 1]`.
Host decoding is less code for a one-off script already holding the sample
in memory. The decoder program is the version worth keeping once decoding
needs to happen the same way regardless of which language or environment is
driving the run, or needs to be inspected and verified as an artifact in
its own right the way the encoder and verifier already are.

## Where This Page Stops

Decoding turns a sample into an answer; it says nothing about whether that
answer is any good. `valid` and `energy` come from the verifier, not the
decoder -- see
[Compiling](compiling.md#what-compile_verifier-and-compile_decoder-do) for
what a verifier's validity check does and does not cover. Choosing a solver
and judging solution quality belong to
[Solving Overview](../solving/README.md) and
[Running Programs](../running/README.md), not here.
