# Inputs and Model Shape

A `Problem` definition starts with two declarations: the runtime inputs it
reads, and the model those inputs fill. Both happen before anything else --
`spec/xqcp/SPEC.md` fixes the order as
`Problem(name) -> input()* -> define_model() -> body* -> output()* -> compile()`,
and `input()` calls after `define_model()` raise `RuntimeError`.

Every code sample in this chapter, and the rest of Part III, assumes the
same import:

```python
from xquad.cp import Domain, Problem, Types
```

It is shown once, here, and omitted everywhere after.

## Inputs

`problem.input(name, type=Types.Int)` or `type=Types.Vec` declares one
runtime value and returns an `InputRef` you use for the rest of the
problem body. `examples/knapsack/runner.py` declares four:

```python
num_items = problem.input("num_items", type=Types.Int)
weights_in = problem.input("weights", type=Types.Vec)
values_in = problem.input("values", type=Types.Vec)
capacity_in = problem.input("capacity", type=Types.Int)
```

`Types.Int` is a scalar integer, and `Types.Vec` is a vector of integers.
Every runtime input is one of these two types.

### Declaration Order Is Calldata Order

Compiling the fragment above emits this for each input:

```asm
PUSH 0
INPUT r0
PUSH 1
INPUT r1
```

`INPUT` pops a calldata slot index and clones `calldata[slot]` into the
target register. It does not check the calldata value's type against
anything ([Register I/O](../xqvm/instructions/register-io.md) covers
`INPUT` in full). XQCP's register allocator hands out registers in call
order starting at `r0`, and because `input()` must run before
`define_model()`, the first `input()` call is allocated `r0`, the second
`r1`, each next call the next register, with nothing else allocated
between them. That is why the slot index XQCP pushes before each `INPUT`
matches the input's position in your `problem.input()` call sequence:
your Nth `input()` call reads `calldata[N-1]`.

The host has to supply calldata in that same order.
`examples/knapsack/runner.py` calls `vm.set_calldata([n, weights, values, capacity])`,
matching `num_items, weights_in, values_in, capacity_in` above one for
one. Nothing checks this correspondence: `INPUT` clones whatever sits at
that slot, regardless of the input's declared type. Swap the position of
`weights` and `values` on either side and both are `Types.Vec`, so no
`RegisterType` fault stops you -- the encoder runs to completion and reads
weights where it wanted values. See [Calldata and Outputs](../xqvm/io.md)
for how a host supplies calldata to any XQVM program, XQCP-generated or
hand-written.

### Reading a Vec Input

A `Types.Vec` input supports two operations no `Types.Int` input has:

```python
values_in.get(i)      # VECGET: element at index i
values_in.veclen()    # VECLEN: the vector's length
```

Both return an expression, not a value -- see [Expressions](expressions.md)
for what you can build with the result. Calling either on a `Types.Int`
input raises `TypeError` immediately, at problem-definition time, not at
compile time:

```pycon
>>> num_items.get(0)
TypeError: Cannot index into int input 'num_items'
```

## The Model

`problem.define_model(size, domain, rows=None, cols=None)` allocates the
quadratic model the encoder builds and runs exactly once per problem.
Before it runs, `problem.model` and `problem.sample` raise `RuntimeError`;
after, both are available for the rest of the chapter. `size` is the
total variable count, as an `int` or an expression built from your
inputs -- knapsack sizes its model directly off an input:

```python
problem.define_model(size=num_items, domain=Domain.BINARY)
```

`domain` is a `Domain` member; see
[Quadratic Models](../concepts/quadratic-models.md#three-domains) for what
each domain means and how to choose between them, since that choice does
not belong to this page. Binary is the domain the running examples in this
book use. `Domain` also accepts the `XQMXDomain` it wraps, so code written
against the VM enum keeps working.

### Integer Variables

An integer variable takes one of `k` values rather than two. Give the
width directly:

```python
problem.define_model(size=num_assets, domain=Domain.INTEGER, k=4)
```

That allocates `num_assets` variables over `{0, 1, 2, 3}`. `k` is an
expression like `size`, so it may come from calldata.

Where the values you are modelling are not zero-based, give bounds
instead:

```python
problem.define_model(size=num_assets, domain=Domain.INTEGER, lo=-5, hi=5)
```

You then write coefficients over `x` in `[-5, 5]` while the model holds
`y = x - lo` in `{0, ..., 10}`, and `sample.value(i)` shifts back on the
way out. XQCP rewrites each quadratic write for you: `w * x_i * x_j`
expands to `w*y_i*y_j + w*lo*y_i + w*lo*y_j + w*lo^2` once `x = y + lo` is
substituted, so the write records `w*lo` against the linear coefficient of
both indices as well. The `w*lo^2` constant is dropped, because XQMX has
no offset field. Energies shift by the same amount for every assignment,
so the minimum is still in the same place; the number is not the
objective's true value. Linear writes need no correction.

Two consequences worth knowing before you reach for the ranged form.
Setting a coefficient is refused on it, on `quadratic[i, j] = w` and
`linear[i] = w` alike, because setting replaces a coefficient while the
corrections can only accumulate: two writes to the same pair would
disagree, and a linear set would drop whatever corrections earlier
quadratic writes had left on that index. Use `.add()`, which loses
nothing, since a coefficient starts at zero. And a runtime `lo` competes
with an output loop bound for the decoder's single calldata scalar, so
`compile()` raises naming both. Literal bounds sidestep it.

Constraints are binary-only. On a spin or integer model every
`apply_*` method is refused and only coefficient writes are supported,
because each expansion in the VM is derived under `x^2 = x`. Write the
penalty out by hand, as
[Portfolio Rebalance](../examples/portfolio_rebalance.md) does for its
budget.

### Categorical Variables

A categorical variable takes one of `k` unordered cases. There is no VM
domain for that, so XQCP records the standard encoding:

```python
problem.define_model(size=num_nodes, domain=Domain.CATEGORICAL, k=num_colors, penalty=200)
```

That is a `num_nodes` x `num_colors` binary grid with one `ONEHOTR` per
row, built through the same calls you would have written yourself. The
model is binary afterwards, so constraints work on it as usual and
coefficient access is `(variable, case)`. Read the answer back with
`sample.case(v)`, which gives the case a variable took or `-1` if its row
came back empty.

### 1D and 2D Models

A model is either flat (1D, indexed `0..size`) or a grid (2D, indexed by
`(row, col)`). Every model in this chapter is 1D: knapsack's `x_i` is one
binary decision per item, with no row/column structure to it. Pass `rows`
and `cols` to get a grid instead:

```python
problem.define_model(size=rows * cols, domain=Domain.BINARY, rows=rows, cols=cols)
```

`rows` and `cols` are both-or-neither: `define_model()` raises
`ValueError` when given exactly one, rather than building a 1D model that
fails later.

Once a model has a shape, coefficient access accepts a `(row, col)` tuple
in place of a flat index, and XQCP flattens it for you. Compiling

```python
n = problem.input("n", type=Types.Int)
problem.define_model(size=n * n, domain=Domain.BINARY, rows=n, cols=n)
problem.model.linear[(1, 2)] = 99
```

emits the block below, once `n` has claimed `r0` and `define_model()` has
claimed `r1` for cols and `r2` for the model:

```asm
PUSH 1
PUSH 2
LOAD r1
IDXGRID
PUSH 99
SETLINE r2
```

`r1` is the cols register `define_model()` allocated; this block does not
depend on `n`'s runtime value at all, only on how many calls came before it.
`IDXGRID` computes `row * cols + col`, so at run time, with `n = 3`, the
coordinate `(1, 2)` flattens to `1 * 3 + 2 = 5`, and every
coordinate-accepting call on a 2D model (coefficient access,
`apply_exclude`, `apply_implies`) goes through the same flattening. `size`
still has to equal `rows * cols` yourself; XQCP does not derive one from
the other. It does optimise the size expression itself: `size=n*n` emits a
single `SQR` instead of `LOAD`, `LOAD`, `MUL` whenever both multiplicands
are the same register, 2D model or not. Grid allocation itself --
`RESIZE` and the rest of what `BQMX r2` plus a shape actually builds -- is
[Allocators](../xqvm/instructions/allocators.md) and
[Grid Operations](../xqvm/instructions/grid.md) territory, not this page's.

### Symbolic Reference Types Introduced Here

`spec/xqcp/TYPES.md` is the normative reference for every symbolic type
XQCP hands back. The two this page introduces:

| Type | Created by | Register type |
|---|---|---|
| `InputRef` | `problem.input(name, type)` | `int` or `vec` |
| `ModelRef` | `problem.define_model(...)` | `xqmx` |

`SampleRef` -- `problem.sample`, the model's read-only counterpart used in
the decoder -- is created together with `ModelRef` but belongs to
[Outputs and Decoding](outputs-and-decoding.md), where it is actually
used. [Expressions](expressions.md) covers `LoopVar` and `RegLoad`, the
two symbolic types this page's fragments do not need yet; the full type
table, including register-allocation and error-condition details past
what this page restates, is in
[`spec/xqcp/TYPES.md`](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqcp/TYPES.md).

With inputs declared and a model allocated, the next thing every problem
body needs is a way to compute with them -- see
[Expressions](expressions.md).
