# Outputs and Decoding

A solver hands back a sample: one raw value per variable. Decoding turns
that assignment into the answer your problem is actually about -- a chosen
item set, for knapsack. This page covers a decoder program's outputs, its
read access to the sample, and when decoding outside the VM makes more
sense.

## Declaring an Output

`problem.output(name, type=Types.Vec, length=None)` declares one decoder
output and returns an `OutputRef`. `type` must be `Types.Vec` -- every
pipeline output is a vector, and `problem.output()` raises `TypeError` for
anything else. An output is filled one of two ways, chosen by `length=`.

### Appending

Without `length=`, an output starts empty and `.append(value)` grows it by
one element:

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
Compiled, this is the decoder assembly [Compiling](compiling.md) shows in
full: `VECI r2  ; selected`, a `RANGE` loop reading `GETLINE r0` and
pushing with `VECPUSH r2`, then `PUSH 0 / OUTPUT r2`.

### Writing and Reading by Index

With `length=`, the decoder starts the output as `length` zeros, and you
write and read it by index: `out[i] = value` compiles to `VECSET`, and
`out[i]` to `VECGET`. Use it when a value's position is computed rather
than reached in order. On the grid `examples/tsp/runner.py` builds, one
row per city and one column per position, `rowfind` gives the position a
city was placed at, so the tour can be written city by city:

```python
tour = problem.output("tour", type=Types.Vec, length=n)
with problem.range(0, n) as city:
    tour[problem.sample.rowfind(row=city, value=1)] = city
```

`length` is anything the decoder can evaluate before its first loop: a
literal, the scalar on calldata slot 1, arithmetic over those, or a sample
read. Slots you never write stay `0`. An `out[i]` read returns an
expression, so one output can feed another:

```python
flags = problem.output("flags", type=Types.Vec, length=n)
scores = problem.output("scores", type=Types.Vec)
with problem.range(0, n) as i:
    flags[i] = problem.sample.getline(i)
with problem.range(0, n) as i:
    scores.append(flags[i] * 10 + i)
```

### Record-Time Checks

The two modes do not mix, and both are checked when you write the
problem, not when the decoder runs. `out[i] = value` or `out[i]` on an
output without `length=` raises

```text
TypeError: Writing to output 'selected' by index needs a declared length; declare it with problem.output('selected', length=n), or fill it with selected.append(value)
```

(`Reading from` for a read), and `.append()` on an output with `length=`
raises

```text
TypeError: Output 'tour' was declared with length= and is filled by index; write tour[i] = value instead of tour.append(value)
```

A literal `length=` below `1` raises `ValueError`, and so does a literal
index outside `[0, length)` when `length=` is a literal too. A `length=`
that reads another output, such as `length=counts[0]`, raises `ValueError`
as well: every `length` is evaluated before any output is filled, so it
would read `0`. Any other
index is bounds-checked by the VM when the decoder runs, which faults with
`IndexOutOfBounds`. A `length` that evaluates to `0` or less at decode
time leaves the output empty, so the first index write faults the same
way.

`problem.output()` must be called at the top level. Inside a
`problem.range()` or `problem.iter()` block, or inside a `branch()` arm,
it raises `RuntimeError` at once:

```text
RuntimeError: problem.output('o') is called inside a range() or iter() block; declare every output at the top level, outside every loop and branch() arm
```

## Reading a Sample

`problem.sample` is available once `define_model()` has run, and exposes
seven read methods built on the [Grid Operations](../xqvm/instructions/grid.md)
and [Coefficient Access](../xqvm/instructions/coefficient-access.md)
instructions a `Sample` register supports:

| Call | Reads |
| --- | --- |
| `sample.getline(i)` | Variable `i`'s assignment (1D or flat) |
| `sample.value(i)` | Variable `i` in the domain you declared it over |
| `sample.case(v)` | Case variable `v` took, or `-1` (categorical) |
| `sample.rowfind(row, value)` | Column of the first match for `value` in `row` (2D) |
| `sample.colfind(col, value)` | Row of the first match for `value` in `col` (2D) |
| `sample.rowsum(row)` | Sum of every value in `row` (2D) |
| `sample.colsum(col)` | Sum of every value in `col` (2D) |

`getline` and `value` differ only on a model declared with `lo=`/`hi=`,
where the stored variable is `y = x - lo` and `value(i)` adds `lo` back
to give the `x` you wrote coefficients over. Everywhere else they are the
same call. `getline` is the only raw reader and `value` the only shifted
one, so nothing shifts unless you ask.

`case(v)` is `rowfind(v, 1)` under a name that says what it means on a
categorical model: the case variable `v` took, or `-1` where the solver
left that row empty. It reads the same on any 2D binary grid, whether or
not `Domain.CATEGORICAL` built it.

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

A block may reference the sample through the read methods above, its own
loop variables, one scalar, and `out[i]` on an output declared with
`length=`. That scalar does not have to be `N`:
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
for the same reason: `ITER` walks a vector register, and no input vector
reaches the decoder. Walk the indices with `problem.range()` and read each one from
`problem.sample`.

## Filling Outputs in Any Order

The decoder allocates every output before it runs anything else and ships
every output to its slot after everything else has run. Between those, it
walks every action recorded after the first `problem.output()` once, in
the order you recorded them. An output can therefore be filled from any
point after its declaration: two outputs from one loop, or an earlier
output after a later one has been declared.

```python
chosen = problem.output("chosen", type=Types.Vec)
weights = problem.output("weights", type=Types.Vec)
with problem.range(0, num_items) as i:
    chosen.append(problem.sample.getline(i))
    weights.append(problem.sample.getline(i) * 2)
```

## Conditional Outputs

`problem.branch()` works after the first `problem.output()`, with the same
first-match arms it has in the encoder. Conditions read the sample, loop
variables and the scalar, like any other decoder expression. This keeps
only the indices of the selected items instead of a flag per item:

```python
picked = problem.output("picked", type=Types.Vec)
with problem.range(0, num_items) as i:
    problem.branch(
        problem.sample.getline(i) == 1, lambda: picked.append(i),
        None,
    )
```

With items `0`, `3` and `4` selected out of five, `picked` decodes to
`[0, 3, 4]`. An arm takes everything else a decoder block takes:
`.append()`, `out[i] = value`, a `problem.range()` loop, and a nested
`problem.branch()`.

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
program at all. In Python, a solved sample is an `XqmxSample`, so
`sample.get_linear(i)` for each item index reads
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

`VM` imports from `xquad.vm`, alongside the
`xquad.cp`/`xquad.types` imports [Inputs and Model Shape](inputs-and-model.md)
opens this chapter with. `set_output_slots` has to run before `vm.run`,
since the slot count defaults to `0`. `OUTPUT` against a slot that was
never allocated raises `OutputIndex` while the decoder runs.

`vm.outputs()[0]` comes back as a plain `list` of `int`. Both reads of the
seed-42 knapsack sample (`weights = [2, 1, 5, 4, 4]`,
`values = [5, 4, 18, 3, 19]`, `capacity = 18`) produce the same list:
`[1, 1, 1, 0, 1]`.
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
