# Outputs and Decoding

A solver hands back a sample: one raw value per variable. Decoding turns
that assignment into the answer your problem is actually about -- a chosen
item set, for knapsack. This page covers a decoder program's outputs, its
read access to the sample, and when decoding outside the VM makes more
sense.

## Declaring an Output

`problem.output(name, type=Types.Vec)` declares one decoder output and
returns an `OutputRef`. `type` must be `Types.Vec` -- every pipeline
output is a vector, and `problem.output()` raises `TypeError` for
anything else. `.append(value)` grows the output by one element, and the
emitted decoder allocates it with `VECI` before the block that fills it:

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

`OutputRef` does not support random-access write or read. Both raise
immediately, at problem-definition time -- `out[i] = value` with

```text
TypeError: Random-access write to output 'selected' is not supported; use selected.append(value) instead
```

and `value = out[i]` with

```text
TypeError: Reading from output 'selected' is not supported; outputs are write-only via .append(value)
```

`.append(value)` is the only way to fill an output, and every output is
write-only: the decoder builds each output vector by appending, in the
order the decoder program executes, and nothing reads a value back out
of one. Knapsack's decoder above is
already the general shape -- an `.append()` per iteration inside a
`problem.range` loop, compiling to `VECPUSH` against the register the
output was allocated with `VECI` in.

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

## What a Decoder Block May Reference

Everything recorded after the first `problem.output()` call goes to the
decoder and to nothing else, and the decoder is a separate program with a
separate register file. It runs on two pieces of calldata: the sample on
slot 0, and one scalar on slot 1. Anything a decoder block names that is
not reachable from those two is refused at `compile()` rather than
compiled into a read of whatever register happens to hold something.

A block may reference the sample through the five read methods above, its
own loop variables, and one scalar. That scalar does not have to be `N`:
`examples/graph_coloring/runner.py` stows a total variable count before
declaring its output and passes that on slot 1. The emitted decoder names
which one it resolved, so `INPUT r1  ; total_vars` tells you what the
caller has to supply. Naming a *second* one raises, because the decoder is
handed exactly one and cannot say which of the two you meant:

```text
RuntimeError: xqcp: a decoder block references two scalars, 'n' and 'acc', but the decoder is handed exactly one on calldata slot 1
```

What a block may not reference is anything the decoder was never given: a
vector input or a `problem.vec()` allocation, a model coefficient
(`problem.model.linear[i]` -- the decoder holds the sample, not the model),
or a loop variable from a loop that has closed. `problem.iter()` is refused
for the same reason: `ITER` walks a vector register, and no vector reaches
the decoder. Walk the indices with `problem.range()` and read each one from
`problem.sample`.

One more ordering rule follows from how outputs are emitted. Each output is
written to its slot as soon as its own block ends, so appending to an
earlier output after a later `problem.output()` has been declared would
land after its target had already shipped. Finish filling one output before
declaring the next.

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
since the slot count defaults to `0`. `OUTPUT` against a slot that was
never allocated raises `OutputIndex` while the decoder runs, on both
`VMBackend.RUST` and `VMBackend.PYTHON`.

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
[Solving Overview](../solving/) and
[Running Programs](../running/), not here.
