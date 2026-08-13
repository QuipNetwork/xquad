# Using the Examples

`examples/` holds fourteen self-contained problems, one directory each,
listed on the [gallery page](./). This page covers what every
directory has in common, how to run one, and how to turn one into a
problem of your own -- the task the rest of this chapter does not cover,
because it is not specific to any single example.

## What Every Example Shares

Each `examples/<name>/` holds a `README.md`, which this chapter's other
pages are generated from, and a `runner.py`. Nothing else --
no `.xqasm` files. `problem.compile()` builds the three programs in memory
at runtime as plain strings; see
[Three Programs](../concepts/three-programs.md) for what those three
programs are and [Compiling](../modelling/compiling.md) for what
`compile()` emits. Every `runner.py` follows the same shape:

- **`build_problem(...)`** -- an `xqcp` `Problem` definition: inputs, a
  model, an objective, constraints, and outputs. This is the part that
  changes from problem to problem, and the part [Modelling](../modelling/)
  covers in full.
- **`run(programs, ...)`** -- three `VM.run()` calls against the compiled
  encoder, verifier, and decoder, with one `xqsa` solver call between the
  first two. Every example wires calldata and output slots the same way:
  encoder in, model out; model plus sample plus `N` in, energy and valid
  out; sample plus `N` in, decoded result out.
- **`main(...)`** -- an `argparse` CLI, and a JSON result printed to
  stdout or written with `-o`.

`examples/manifest.yaml` lists every directory once, under a group
(`dir`, `title`, `blurb`), and drives both the gallery grouping and
`scripts/gen-example-docs.py`'s validation that every listed directory
has a `runner.py` and a `README.md`. This page itself is exempt: it is
named in the manifest's `preserved_pages`, so `make regen-docs` leaves
it alone instead of overwriting it from a source `README.md` that does
not exist.

## Running One

```sh
uv run python examples/<name>/runner.py --seed 42
```

Every runner accepts `--seed`, `--interpreter` (`python` or `rust`,
default `python`), `--solver`, and `-o`/`--output`. Most also take `--n`
for problem size; a few split size across two flags instead (`--n` plus
`--bins`, `--colors`, `--budget`, or `--m`), or use `--num-elements`
and `--num-sets` in place of `--n`. Check the individual page's `Usage`
table for the exact flags.

`--interpreter` selects which XQVM runs the compiled programs: the
pure-Python reference VM (`xqvm_py`) or the Rust interpreter through
`xqffi`. [Backends](../concepts/backends.md) covers what else differs by
solver backend; `--interpreter` is a different axis entirely -- it picks
which VM executes the programs, not which solver samples the model. A
correct model produces the same `valid` and the same `energy` on both,
always. The decoded result itself can differ where a model has several
optima of equal energy: the solver returns one of the tied optima, not
necessarily the same one on both interpreters, and a different optimum
decodes to a different answer even though both are equally correct.
`maxcut`, `tsp`, and `knapsack` happen to have no such tie at their
default seed and so return identical decoded results either way;
`graph_coloring`, `bin_packing`, `set_cover`, and `max3sat` do not --
running `examples/graph_coloring/runner.py --seed 1` gives
`colors: [2, 0, 2, 1, 0]` on `python` and `colors: [2, 0, 2, 2, 0]` on
`rust`, both at the same `energy` and both `valid`.

## Adapting an Example

Copying and editing an existing `runner.py` is the fastest way to model
a new problem, and it is what this section walks through concretely:
turning `examples/maxcut/runner.py`'s Max-Cut into a minimum s-t cut
with two fixed terminals.

Max-Cut splits a graph's nodes into two groups to maximise the crossing
edge weight, with no constraint on which node ends up where.
[Quadratic Models](../concepts/quadratic-models.md#a-worked-example-max-cut)
derives its formulation; `build_problem`'s inner loop is the part that
matters here:

```python
with problem.range(0, edge_count) as e:
    offset = e * 3
    i = problem.stow("i", edges_in.get(offset))
    j = problem.stow("j", edges_in.get(offset + 1))
    w = problem.stow("w", edges_in.get(offset + 2))
    problem.model.linear[i].add(-w)
    problem.model.linear[j].add(-w)
    problem.model.quadratic[i, j].add(w * 2)
```

Suppose two specific nodes must end up on opposite sides -- node `0` and
node `n - 1`, say, standing in for two machines a network partition must
separate. That is a different problem, minimum s-t cut with fixed
terminals, and it is two small edits away:

```python
with problem.range(0, edge_count) as e:
    offset = e * 3
    i = problem.stow("i", edges_in.get(offset))
    j = problem.stow("j", edges_in.get(offset + 1))
    w = problem.stow("w", edges_in.get(offset + 2))
    problem.model.linear[i].add(w)          # sign flipped: minimise, not maximise
    problem.model.linear[j].add(w)
    problem.model.quadratic[i, j].add(w * -2)

# Fix node 0 to side 0 and node n-1 to side 1. 10_000 comfortably
# exceeds the total weight of every edge (at most C(n,2) * 100 for
# this graph), so neither fixed node is ever worth flipping to save
# weight elsewhere.
problem.model.linear[0].add(10_000)
problem.model.linear[num_nodes - 1].add(-10_000)
```

Everything else stays: the random weighted graph, `problem.define_model`,
the `partition` output loop, and `run()`. The DSL calls used --
`model.linear[i].add()` and `model.quadratic[i, j].add()` -- are the same
two `maxcut/README.md` already lists. `main()` needs one further edit,
covered in the next section: drop the `canonicalize_partition(partition)`
call, because this variant's two sides stop being interchangeable.

Running the original and the variant on the same seed and graph
(`--n 5 --seed 42` on both):

Unmodified `examples/maxcut/runner.py`:

```json
{"cut_weight": 354, "energy": -354, "partition": [0, 1, 0, 0, 1], "valid": 1}
```

The variant:

```json
{"cut_weight": 196, "energy": -9804, "partition": [0, 1, 1, 1, 1], "valid": 1}
```

Both interpreters return identical output for both versions. The cut
drops from `354` to `196`, confirming the variant found a smaller
crossing weight rather than reusing the maximiser's answer, and
`partition[0] == 0` with `partition[-1] == 1` confirms the two
terminals landed where they were pinned.

## Telling a Variant Wrong from a Variant Different

`valid` alone does not catch a modelling mistake here: Max-Cut and this
variant both declare no constraint the verifier checks, so `valid`
reports only that every sample value is `0` or `1` -- see
[Constraints](../modelling/constraints.md) for what a verifier's
built-in check does and does not cover. `energy` is not a safe single
number either. Get the terminal bias backwards --
`problem.model.linear[0].add(-10_000)` and
`problem.model.linear[num_nodes - 1].add(10_000)`, the two signs
swapped -- and the run reports the *same* `cut_weight` (`196`) and the
*same* `energy` (`-9804`) as the correct version. A cut and its
complement (every node's side flipped) always cost the same, so
reversing which two nodes get pinned just picks the other member of
that pair; nothing about the aggregate numbers changes.

`partition` is where the mistake shows, but only once
`canonicalize_partition(partition)` is gone from `main()`. Max-Cut calls
it because a cut and its complement are the same cut, so normalising
node `0` to side `0` throws nothing away. This variant cannot afford
that: the two terminals make the two sides distinguishable, and
canonicalising erases the one fact the pinning added. Building both
versions with that call removed and running each on the same seed and
graph (`--n 5 --seed 42`):

Correct:

```json
{"cut_weight": 196, "energy": -9804, "partition": [0, 1, 1, 1, 1], "valid": 1}
```

Swapped:

```json
{"cut_weight": 196, "energy": -9804, "partition": [1, 0, 0, 0, 0], "valid": 1}
```

`cut_weight` and `energy` still agree, for the reason above.
`partition[0]` and `partition[-1]` no longer do: `0` and `1` on the
correct run, `1` and `0` on the swapped one. Catching this mistake means
checking the property the edit was supposed to guarantee -- that the two
terminals sit on the sides they were pinned to -- against a sample
nothing downstream has normalised. The same habit generalises past this
one example: name the specific property an edit is supposed to produce,
and check that property against output no later step has adjusted, not
just `valid` and a plausible-looking `energy`.
